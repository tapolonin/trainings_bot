"""Timezone-aware reminders. Sending a reminder never changes a conversation."""
import asyncio
import logging
from calendar import monthrange
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

RETRY_SECONDS = 300


async def send_due(store, bot, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Reminder clock must include a timezone.')
    for uid in store.user_ids():
        try:
            await send_user_due(store,bot,uid,now)
        except Exception as exc:
            logging.getLogger(__name__).error('User reminder check failed (%s)',type(exc).__name__)


async def send_user_due(store, bot, uid, now):
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
            return
        except TelegramAPIError as exc:
            logging.getLogger(__name__).warning('Reminder delivery failed (%s); will retry',type(exc).__name__)
            continue
        for kind,period,_ in group:
            store.mark_notification(uid,kind,period,sent.message_id)


async def run_reminders(store, bot):
    while True:
        try:
            await send_due(store, bot)
        except Exception as exc:
            logging.getLogger(__name__).error('Reminder check failed (%s)', type(exc).__name__)
        await asyncio.sleep(30)
