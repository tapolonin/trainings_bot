"""Timezone-aware reminders. Sending a reminder never changes a conversation."""
import asyncio
import logging
from calendar import monthrange
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

RETRY_SECONDS = 300


def user_lock(store, uid):
    if not hasattr(store, '_ui_locks'):
        store._ui_locks = {}
    return store._ui_locks.setdefault(uid, asyncio.Lock())


async def resolve_log_reminders(store, bot, uid):
    """Resolve delivered daily prompts after any path records that day."""
    rows = store.db.execute('''SELECT n.* FROM notifications n
        JOIN day_logs d ON d.user_id=n.user_id AND d.day=n.period
        LEFT JOIN resolved_reminders r ON r.user_id=n.user_id AND r.period=n.period
        WHERE n.user_id=? AND n.kind='evening' AND n.message_id IS NOT NULL
        AND r.period IS NULL''', (uid,)).fetchall()
    for row in rows:
        other = store.db.execute("SELECT * FROM notifications WHERE user_id=? AND message_id=? AND kind='month_end'",
                                 (uid,row['message_id'])).fetchone()
        try:
            if other:
                schedule = next(s for s in store.schedules(uid) if s['kind'] == 'month_end')
                text = schedule['message'].replace('{date}',row['period']).replace('{month}',other['period'])
                markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='Review monthly report',callback_data=f"notice:month_end:{other['period']}")],
                    [InlineKeyboardButton(text='Delete notification',callback_data='notice_delete')]])
                await bot.edit_message_text(chat_id=uid,message_id=row['message_id'],text=text,reply_markup=markup)
            else:
                try:
                    await bot.delete_message(chat_id=uid,message_id=row['message_id'])
                except TelegramAPIError as exc:
                    if 'message to delete not found' not in str(exc).lower():
                        # Older Telegram messages cannot be deleted by the bot.
                        await bot.edit_message_text(chat_id=uid,message_id=row['message_id'],
                                                    text=f"Information recorded for {row['period']}.",reply_markup=None)
        except TelegramAPIError:
            continue
        with store.db:
            store.db.execute('INSERT OR IGNORE INTO resolved_reminders VALUES (?,?)',(uid,row['period']))


async def move_menu_below_notifications(store, bot, uid):
    """Re-send the current screens without resetting navigation or a draft."""
    rows = store.db.execute('''SELECT s.* FROM ui_snapshots s JOIN ui_messages m
        ON m.user_id=s.user_id AND m.message_id=s.message_id AND m.chat_id=s.user_id
        WHERE s.user_id=? ORDER BY s.message_id''',(uid,)).fetchall()
    if not rows:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Main menu',callback_data='notice_home')]])
        sent = await bot.send_message(uid,'Open the main menu to continue.',reply_markup=markup,disable_notification=True)
        store.track_message(uid,uid,sent.message_id)
        store.snapshot_message(uid,sent.message_id,'Open the main menu to continue.',markup)
        return
    for row in rows:
        markup = InlineKeyboardMarkup.model_validate_json(row['markup']) if row['markup'] else None
        sent = await bot.send_message(uid,row['text'],reply_markup=markup,disable_notification=True)
        store.track_message(uid,uid,sent.message_id)
        store.snapshot_message(uid,sent.message_id,row['text'],markup)
        state = store.conversation(uid)
        for key in ('calendar_message_id','detail_message_id','keyboard_message_id'):
            if state.get(key) == row['message_id']:
                state[key] = sent.message_id
        if state:
            store.save_conversation(uid,state)
        try:
            await bot.delete_message(chat_id=uid,message_id=row['message_id'])
        except TelegramAPIError:
            try:
                await bot.edit_message_reply_markup(chat_id=uid,message_id=row['message_id'],reply_markup=None)
            except TelegramAPIError:
                pass
        store.forget_message(uid,uid,row['message_id'])


async def send_due(store, bot, now=None, *, refresh_ui=True):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Reminder clock must include a timezone.')
    for uid in store.user_ids():
        try:
            async with user_lock(store,uid):
                await resolve_log_reminders(store,bot,uid)
                await send_user_due(store,bot,uid,now,refresh_ui=refresh_ui)
        except Exception as exc:
            logging.getLogger(__name__).error('User reminder check failed (%s)',type(exc).__name__)


async def send_user_due(store, bot, uid, now, *, refresh_ui=True):
    profile = store.profile(uid)
    if not profile:
        return
    local = now.astimezone(ZoneInfo(profile['timezone']))
    day, month = local.strftime('%Y-%m-%d'), local.strftime('%Y-%m')
    due = []
    for schedule in store.schedules(uid):
        if not schedule['enabled'] or local.strftime('%H:%M') < schedule['time']:
            continue
        if schedule['frequency'] == 'weekly':
            if str(local.weekday()) not in schedule['weekdays'].split(','):
                continue
            period = day
        else:
            last = monthrange(local.year,local.month)[1]
            scheduled_day = min(schedule['month_day'] or last,last)
            if local.day != scheduled_day:
                continue
            period = month
        kind = schedule['kind'] if schedule['kind'] != 'custom' else f"custom_{schedule['id']}"
        if kind == 'evening' and day in store.day_statuses(uid,month):
            continue
        row = store.notification(uid,kind,period)
        if row is None or (row['message_id'] is None and row['retry_at'] <= now.timestamp()):
            due.append((kind,period,schedule))
    # Keep built-in prompts together; each custom message is delivered separately.
    groups = [[item for item in due if item[2]['kind'] != 'custom']]
    groups += [[item] for item in due if item[2]['kind'] == 'custom']
    delivered = False
    for group in groups:
        if not group:
            continue
        lines,buttons = [],[]
        for kind,period,schedule in group:
            lines.append(schedule['message'].replace('{date}',day).replace('{month}',month))
            if kind in ('evening','month_end'):
                label = 'Log this day' if kind == 'evening' else 'Review monthly report'
                buttons.append([InlineKeyboardButton(text=label,callback_data=f'notice:{kind}:{period}')])
            store.defer_notification(uid,kind,period,now.timestamp()+RETRY_SECONDS)
        buttons.append([InlineKeyboardButton(text='Delete notification',callback_data='notice_delete')])
        try:
            sent = await bot.send_message(uid,'\n\n'.join(lines),
                                          reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None)
        except TelegramRetryAfter as exc:
            for kind,period,_ in group:
                store.defer_notification(uid,kind,period,now.timestamp()+max(RETRY_SECONDS,exc.retry_after))
            break
        except TelegramAPIError as exc:
            logging.getLogger(__name__).warning('Reminder delivery failed (%s); will retry',type(exc).__name__)
            continue
        for kind,period,_ in group:
            store.mark_notification(uid,kind,period,sent.message_id)
        delivered = True
    if delivered and refresh_ui:
        await move_menu_below_notifications(store,bot,uid)


async def run_reminders(store, bot):
    while True:
        try:
            await send_due(store, bot)
        except Exception as exc:
            logging.getLogger(__name__).error('Reminder check failed (%s)', type(exc).__name__)
        await asyncio.sleep(30)
