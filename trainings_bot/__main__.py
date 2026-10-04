"""Run with: python -m trainings_bot (from the project root)."""
import asyncio
import logging
import json
import hashlib
import secrets
import os
from pathlib import Path
from contextvars import ContextVar
from contextlib import suppress

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message, ReplyKeyboardRemove, BufferedInputFile)

from .conversation import Conversation
from .storage import Store
from .reports import report_data, make_document
from .reminders import run_reminders, resolve_log_reminders, user_lock
from .admin import parse_admin_ids


def load_env(path=Path('.env')):
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                key, sep, value = line.partition('=')
                if not sep:
                    raise ValueError('Invalid .env line; expected KEY=value.')
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def make_dispatcher(conversation):
    router = Router()
    async def serialize_user(handler, event, data):
        user = event.from_user
        if user is None:
            return await handler(event,data)
        async with user_lock(conversation.store,user.id):
            return await handler(event,data)
    router.message.outer_middleware(serialize_user)
    router.callback_query.outer_middleware(serialize_user)
    outgoing = ContextVar('outgoing_messages')
    actor = ContextVar('actor')
    render_lock = asyncio.Lock()
    keyboard_removed = set()

    async def send_tracked(message, text, **kwargs):
        sent = await message.answer(text, **kwargs)
        uid = message.chat.id
        if isinstance(sent, Message):
            conversation.store.track_message(uid, message.chat.id, sent.message_id)
            conversation.store.snapshot_message(uid, sent.message_id, text, kwargs.get('reply_markup'))
            outgoing.get().append(sent.message_id)
        return sent

    async def finish_screen(message, uid, delete_input=False):
        if message.chat.id != uid:
            return
        state = conversation.store.conversation(uid)
        keep = set(outgoing.get())
        if state.get('flow') in ('history','day_date','admin_calendar'):
            keep.update(state.get(k) for k in ('calendar_message_id','detail_message_id'))
        old = set(conversation.store.message_ids(uid, message.chat.id)) - keep
        if delete_input:
            old.add(message.message_id)
        await remove_messages(message, old)
        await resolve_log_reminders(conversation.store, message.bot, uid)

    def screen_key(uid, calendar=False):
        state = conversation.store.conversation(uid)
        if calendar:
            return 'calendar:' + state.get('calendar_token', '')
        clean = {k:v for k,v in state.items() if k not in
                 ('calendar_message_id','detail_message_id','keyboard_message_id','detail_text','detail_token')}
        return hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()

    def action_rows(buttons, calendar=False):
        uid = actor.get()
        if not buttons or uid is None:
            return []
        screen = screen_key(uid, calendar)
        encoded = json.dumps(buttons)
        existing = conversation.store.db.execute(
            'SELECT token FROM ui_actions WHERE user_id=? AND screen=? AND buttons=?',
            (uid, screen, encoded)).fetchone()
        token = existing['token'] if existing else secrets.token_hex(6)
        with conversation.store.db:
            conversation.store.db.execute(
                'INSERT OR REPLACE INTO ui_actions VALUES (?,?,?,?)',
                (token, uid, screen, encoded))
            conversation.store.db.execute(
                'DELETE FROM ui_actions WHERE user_id=? AND rowid NOT IN (SELECT rowid FROM ui_actions WHERE user_id=? ORDER BY rowid DESC LIMIT 100)', (uid,uid))
        return [[InlineKeyboardButton(text=b, callback_data=f'act:{token}:{i+j}')
                 for j,b in enumerate(buttons[i:i+2])] for i in range(0,len(buttons),2)]

    def calendar_markup(reply):
        rows = [[InlineKeyboardButton(text=label, callback_data=data) for label,data in row]
                for row in reply.calendar]
        rows += action_rows(reply.buttons if reply.calendar_picker or reply.admin_view else ['Back', 'Main menu'], calendar=not reply.calendar_picker)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def detail_markup(reply):
        rows = [[InlineKeyboardButton(text=label, callback_data=data) for label,data in row]
                for row in reply.detail_buttons]
        return InlineKeyboardMarkup(inline_keyboard=rows + action_rows(reply.buttons))

    def calendar_text(reply):
        return reply.text + ('\n\n' + reply.calendar_title if reply.calendar_title else '')

    async def remove_old_keyboard(message, uid):
        if uid != message.chat.id or uid in keyboard_removed:
            return
        sent = await message.answer('Opening menu…', reply_markup=ReplyKeyboardRemove())
        if isinstance(sent, Message):
            await remove_messages(message, [sent.message_id])
        keyboard_removed.add(uid)

    async def remove_messages(message, message_ids):
        for message_id in set(message_ids) - {None}:
            if conversation.store.is_notification_message(message.chat.id,message_id):
                continue
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
            except TelegramAPIError:
                # Telegram may refuse deletion of old messages; at least disable buttons.
                try:
                    await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=message_id, reply_markup=None)
                except TelegramAPIError:
                    pass
            conversation.store.forget_message(message.chat.id, message.chat.id, message_id)

    async def close_calendar(message, previous_state):
        await remove_messages(message, [previous_state.get('calendar_message_id'),
                                       previous_state.get('detail_message_id')])

    async def deliver_report(message, month, target_uid=None):
        uid = message.chat.id
        progress = await send_tracked(message, 'Preparing your PDF…')
        try:
            if target_uid is not None:
                conversation.admin().require(uid)
                state = conversation.store.conversation(uid)
                if state.get('flow') != 'admin_calendar' or state.get('target_uid') != target_uid or state.get('month') != month:
                    raise ValueError('Open the Admin calendar again before downloading this report.')
            data = report_data(conversation.store, uid if target_uid is None else target_uid, month)
            async with render_lock:
                content, filename, caption = await asyncio.to_thread(make_document, data)
            # Documents are intentionally not tracked as disposable navigation screens.
            await message.answer_document(BufferedInputFile(content, filename=filename), caption=caption)
            return 'PDF sent. You can regenerate it after changing your logs.\n\n'
        except ValueError as exc:
            return str(exc) + '\n\n'
        except TelegramAPIError:
            return 'Could not send the PDF. Please choose Generate PDF to retry.\n\n'
        except Exception as exc:
            # Do not log personal data or converter command output.
            logging.getLogger(__name__).error('PDF generation failed (%s)', type(exc).__name__)
            return 'Could not create the PDF. Please try again. If it keeps failing, check the server’s PDF tools.\n\n'
        finally:
            if isinstance(progress, Message):
                await remove_messages(message, [progress.message_id])

    async def send_reply(message, reply):
        if reply.report_month:
            reply.text = await deliver_report(message, reply.report_month, reply.report_user_id) + reply.text
        if reply.calendar:
            sent = await send_tracked(message, calendar_text(reply), reply_markup=calendar_markup(reply))
            if isinstance(sent, Message):
                uid = actor.get()
                state = conversation.store.conversation(uid)
                state['calendar_message_id'] = sent.message_id
                conversation.store.save_conversation(uid, state)
            if reply.restored_details:
                markup = detail_markup(reply)
                details = await send_tracked(message, reply.restored_details, reply_markup=markup)
                if isinstance(details, Message):
                    uid = actor.get()
                    state = conversation.store.conversation(uid)
                    state.update(detail_message_id=details.message_id, detail_text=reply.restored_details,
                                 detail_token=state['calendar_token'])
                    conversation.store.save_conversation(uid,state)
            return
        rows = action_rows(reply.buttons)
        markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        chunks, current = [], ''
        for line in reply.text.splitlines(keepends=True):
            if len(current) + len(line) > 3500:
                chunks.append(current)
                current = ''
            current += line
        chunks.append(current)
        for index, chunk in enumerate(chunks):
            await send_tracked(message, chunk, reply_markup=markup if index == len(chunks)-1 else None)

    @router.callback_query()
    async def on_calendar(query: CallbackQuery):
        outgoing.set([])
        if (not isinstance(query.message, Message) or query.message.chat.type != 'private'
                or query.message.chat.id != query.from_user.id):
            await query.answer()
            return
        actor.set(query.from_user.id)
        if query.data == 'notice_delete':
            uid = query.from_user.id
            if (query.message.chat.id != uid
                    or not conversation.store.is_notification_message(uid,query.message.message_id)):
                await query.answer('This notification is not available to you.',show_alert=True)
                return
            try:
                await query.message.delete()
            except TelegramAPIError:
                # Keep the button usable after transient errors and age-limit refusals.
                await query.answer('Could not delete this message. Try again; if it is over 48 hours old, delete it manually in Telegram.',show_alert=True)
            else:
                await query.answer('Notification deleted.')
            return
        previous_state = conversation.store.conversation(query.from_user.id)
        try:
            if query.data == 'notice_home':
                reply = conversation.handle(query.from_user.id, 'Main menu')
            elif (query.data or '').startswith('adm:'):
                reply = conversation.admin_action(query.from_user.id,query.data)
            elif (query.data or '').startswith('notice:'):
                reply = conversation.notification_action(query.from_user.id, query.data, query.message.message_id)
            elif (query.data or '').startswith('act:'):
                parts = query.data.split(':')
                record = conversation.store.db.execute('SELECT * FROM ui_actions WHERE token=?', (parts[1],)).fetchone()
                if (record is None or record['user_id'] != query.from_user.id
                        or query.message.message_id not in conversation.store.message_ids(query.from_user.id, query.message.chat.id)
                        or record['screen'] != screen_key(query.from_user.id, record['screen'].startswith('calendar:'))):
                    raise ValueError('This menu has expired. Use the current message.')
                buttons = json.loads(record['buttons'])
                if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) >= len(buttons):
                    raise ValueError('Invalid button.')
                reply = conversation.handle(query.from_user.id, buttons[int(parts[2])])
            else:
                reply = conversation.calendar_action(query.from_user.id, query.data or '')
        except ValueError as exc:
            parts = (query.data or '').split(':')
            if (query.message.chat.id == query.from_user.id
                    and len(parts) == 3 and parts[0] == 'cal'
                    and (previous_state.get('flow') not in ('history','day_date','admin_calendar')
                         or parts[1] != previous_state.get('calendar_token'))):
                await remove_messages(query.message, [query.message.message_id])
            await query.answer(str(exc), show_alert=True)
            return
        if reply is not None and reply.day_details:
            markup = detail_markup(reply)
            detail_id = previous_state.get('detail_message_id')
            try:
                if detail_id:
                    if (reply.text != previous_state.get('detail_text')
                            or previous_state.get('detail_token') != previous_state.get('calendar_token')):
                        await query.bot.edit_message_text(chat_id=query.message.chat.id, message_id=detail_id,
                                                          text=reply.text, reply_markup=markup)
                        conversation.store.snapshot_message(query.from_user.id, detail_id, reply.text, markup)
                else:
                    sent = await send_tracked(query.message, reply.text, reply_markup=markup)
                    detail_id = sent.message_id
            except TelegramAPIError:
                conversation.store.save_conversation(query.from_user.id, previous_state)
                await query.answer('Could not display the day. Try again or reopen View calendar.', show_alert=True)
                return
            state = conversation.store.conversation(query.from_user.id)
            state.update(detail_message_id=detail_id, detail_text=reply.text,
                         detail_token=state['calendar_token'])
            conversation.store.save_conversation(query.from_user.id, state)
            await query.answer()
        elif reply is not None and reply.calendar and (query.data or '').startswith(('cal:','adm:')):
            try:
                markup = calendar_markup(reply)
                await query.message.edit_text(calendar_text(reply), reply_markup=markup)
                conversation.store.snapshot_message(query.from_user.id, query.message.message_id, calendar_text(reply), markup)
            except TelegramAPIError:
                # Keep the displayed calendar usable if Telegram rejects the edit.
                conversation.store.save_conversation(query.from_user.id, previous_state)
                await query.answer('Could not update this message. Try again or reopen View calendar.', show_alert=True)
                return
            state = conversation.store.conversation(query.from_user.id)
            for key in ('calendar_message_id',):
                if key in previous_state and key not in state:
                    state[key] = previous_state[key]
            conversation.store.save_conversation(query.from_user.id, state)
            await query.answer()
        elif reply is not None and reply.replace_screen:
            try:
                markup = InlineKeyboardMarkup(inline_keyboard=action_rows(reply.buttons))
                await query.message.edit_text(reply.text, reply_markup=markup)
                conversation.store.snapshot_message(query.from_user.id, query.message.message_id, reply.text, markup)
                outgoing.get().append(query.message.message_id)
            except TelegramAPIError:
                conversation.store.save_conversation(query.from_user.id, previous_state)
                await query.answer('Could not update the choices. Try again.', show_alert=True)
                return
            await query.answer()
        elif reply is not None:
            await query.answer()
            if previous_state.get('flow') in ('history','day_date','admin_calendar'):
                # Older versions may not have stored the calendar message ID.
                previous_state.setdefault('calendar_message_id', query.message.message_id)
                await close_calendar(query.message, previous_state)
            await send_reply(query.message, reply)
        else:
            await query.answer()
        if reply is not None:
            await finish_screen(query.message, query.from_user.id)

    @router.message()
    async def on_message(message: Message):
        outgoing.set([])
        if (message.chat.type != 'private' or message.from_user is None
                or message.chat.id != message.from_user.id):
            return
        actor.set(message.from_user.id)
        if message.text is None:
            await send_tracked(message, 'Please send text or choose a button below the current message.')
            return
        text = message.text
        # Telegram may append the bot username to menu commands.
        if text.startswith('/'):
            command, *rest = text.split(maxsplit=1)
            text = command.split('@')[0] + (' ' + rest[0] if rest else '')
        previous_state = conversation.store.conversation(message.from_user.id)
        reply = conversation.handle(message.from_user.id, text)
        if (reply.calendar and previous_state.get('flow') in ('history','day_date','admin_calendar')
                and previous_state.get('calendar_message_id')
                and text in ('Previous month','Next month','Previous entries','Next entries')):
            try:
                markup = calendar_markup(reply)
                await message.bot.edit_message_text(chat_id=message.chat.id,
                                                    message_id=previous_state['calendar_message_id'],
                                                    text=calendar_text(reply), reply_markup=markup)
                conversation.store.snapshot_message(message.from_user.id, previous_state['calendar_message_id'], calendar_text(reply), markup)
            except TelegramAPIError:
                conversation.store.save_conversation(message.from_user.id, previous_state)
                await send_tracked(message, 'Could not update the calendar. Try again or reopen View calendar.')
                return
            state = conversation.store.conversation(message.from_user.id)
            for key in ('calendar_message_id',):
                if key in previous_state and key not in state:
                    state[key] = previous_state[key]
            conversation.store.save_conversation(message.from_user.id, state)
            await finish_screen(message, message.from_user.id, delete_input=True)
            return
        state = conversation.store.conversation(message.from_user.id)
        if (previous_state.get('flow') in ('history','day_date','admin_calendar')
                and (state.get('flow') not in ('history','day_date','admin_calendar')
                     or state.get('calendar_token') != previous_state.get('calendar_token'))):
            await close_calendar(message, previous_state)
        await remove_old_keyboard(message, message.from_user.id)
        await send_reply(message, reply)
        await finish_screen(message, message.from_user.id, delete_input=True)

    dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
    dispatcher.include_router(router)
    return dispatcher


async def main():
    os.umask(0o077)
    load_env()
    token = os.environ.get('BOT_TOKEN', '')
    if not token:
        raise SystemExit('Set BOT_TOKEN in .env before starting the bot.')
    try:
        admin_ids = parse_admin_ids(os.environ.get('ADMIN_USER_IDS',''))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    store = Store(os.environ.get('DATABASE_PATH', 'data/trainings.sqlite3'))
    bot = Bot(token)
    reminder_task = None
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c,d in [
            ('start','Set up or open the bot'), ('day','Log a day'), ('train','Log a training'),
            ('no_training','Record a day without training'), ('log','Calendar'), ('earnings','Earnings'),
            ('myid','Show your Telegram user ID'), ('report','Get a monthly PDF'), ('settings','Change profile and preferences'), ('cancel','Cancel current action'), ('help','How to use the bot')]])
        dispatcher = make_dispatcher(Conversation(store,admin_ids=admin_ids))
        reminder_task = asyncio.create_task(run_reminders(store, bot))
        await dispatcher.start_polling(bot, allowed_updates=['message', 'callback_query'])
    finally:
        if reminder_task is not None:
            reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await reminder_task
        store.close()
        await bot.session.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
