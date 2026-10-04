import tempfile
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessage
from trainings_bot.conversation import Conversation
from trainings_bot.reminders import send_due
from trainings_bot.storage import Store


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'bot.sqlite3'
        self.store = Store(self.path)
        self.app = Conversation(self.store,'private-code')
        self.app.handle(1,'private-code')
        for value in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            self.app.handle(1,value)
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)))

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def now(self, value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

    async def test_evening_local_time_once_and_restart(self):
        await send_due(self.store,self.bot,self.now('2026-09-28T17:59'))
        self.bot.send_message.assert_not_awaited()
        await send_due(self.store,self.bot,self.now('2026-09-28T18:00'))
        self.assertIn('2026-09-28',self.bot.send_message.call_args.args[1])
        self.store.close()
        self.store = Store(self.path)
        await send_due(self.store,self.bot,self.now('2026-09-28T19:00'))
        self.assertEqual(self.bot.send_message.await_count,1)
        await send_due(self.store,self.bot,self.now('2026-09-29T18:00'))
        self.assertEqual(self.bot.send_message.await_count,2)

    async def test_logged_days_disabled_and_unconfigured(self):
        self.store.no_training(1,'2026-09-28')
        await send_due(self.store,self.bot,self.now('2026-09-28T18:00'))
        self.store.save_training(1,dict(day='2026-09-29',gym='Gym',start='09:00',end='10:00'),'x')
        await send_due(self.store,self.bot,self.now('2026-09-29T18:00'))
        self.store.update_profile(1,'reminders_enabled',0)
        await send_due(self.store,self.bot,self.now('2026-09-30T18:00'))
        self.bot.send_message.assert_not_awaited()
        empty = Store(Path(self.tmp.name)/'empty.sqlite3')
        try:
            await send_due(empty,self.bot,self.now('2026-09-30T18:00'))
            empty.claim(2,'code','code')
            await send_due(empty,self.bot,self.now('2026-09-30T18:00'))
        finally:
            empty.close()
        self.bot.send_message.assert_not_awaited()

    async def test_month_end_combines_or_sends_when_day_logged(self):
        await send_due(self.store,self.bot,self.now('2026-09-30T18:00'))
        markup = self.bot.send_message.call_args.kwargs['reply_markup']
        self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row],
                         ['notice:evening:2026-09-30','notice:month_end:2026-09','notice_delete'])
        await send_due(self.store,self.bot,self.now('2026-09-30T20:00'))
        self.assertEqual(self.bot.send_message.await_count,1)
        self.store.no_training(1,'2026-10-31')
        await send_due(self.store,self.bot,self.now('2026-10-31T19:00'))
        buttons = self.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(len(buttons),2)
        self.assertEqual(buttons[0][0].text,'Review monthly report')
        await send_due(self.store,self.bot,self.now('2026-11-01T19:00'))
        self.assertNotIn('month_end',str(self.bot.send_message.call_args))

    async def test_dst_repeated_hour_custom_time_and_timezone(self):
        self.store.update_profile(1,'reminder_time','02:30')
        await send_due(self.store,self.bot,self.now('2026-10-25T00:30'))
        await send_due(self.store,self.bot,self.now('2026-10-25T01:30'))
        self.assertEqual(self.bot.send_message.await_count,1)
        # Spring's skipped 02:30 is caught at 03:00.
        await send_due(self.store,self.bot,self.now('2027-03-28T01:00'))
        self.assertEqual(self.bot.send_message.await_count,2)
        self.store.update_profile(1,'timezone','America/New_York')
        self.store.update_profile(1,'reminder_time','20:00')
        await send_due(self.store,self.bot,self.now('2026-10-01T00:00'))
        self.assertIn('2026-09-30',self.bot.send_message.call_args.args[1])
        self.assertIn('end of 2026-09',self.bot.send_message.call_args.args[1])

    async def test_failed_send_retry_and_no_historical_backlog(self):
        now = self.now('2026-09-28T18:00')
        self.bot.send_message.side_effect = TelegramBadRequest(method=SendMessage(chat_id=1,text='x'),message='test')
        with self.assertLogs('trainings_bot.reminders',level='WARNING'):
            await send_due(self.store,self.bot,now)
        self.assertIsNone(self.store.notification(1,'evening','2026-09-28')['message_id'])
        self.bot.send_message.side_effect = None
        await send_due(self.store,self.bot,now+timedelta(minutes=1))
        self.assertEqual(self.bot.send_message.await_count,1)
        await send_due(self.store,self.bot,now+timedelta(minutes=5))
        self.assertEqual(self.bot.send_message.await_count,2)
        await send_due(self.store,self.bot,self.now('2026-10-01T10:00'))
        self.assertEqual(self.bot.send_message.await_count,2)

    async def test_rate_limit_respected(self):
        now = self.now('2026-09-28T18:00')
        self.bot.send_message.side_effect = TelegramRetryAfter(method=SendMessage(chat_id=1,text='x'),message='wait',retry_after=600)
        await send_due(self.store,self.bot,now)
        await send_due(self.store,self.bot,now+timedelta(minutes=5))
        self.assertEqual(self.bot.send_message.await_count,1)
        self.bot.send_message.side_effect = None
        await send_due(self.store,self.bot,now+timedelta(minutes=10))
        self.assertEqual(self.bot.send_message.await_count,2)

    async def test_reminders_leave_draft_intact_and_buttons_never_generate(self):
        self.app.handle(1,'Log day')
        before = self.store.conversation(1)
        await send_due(self.store,self.bot,self.now('2026-09-30T18:00'))
        self.assertEqual(self.store.conversation(1),before)
        with self.assertRaisesRegex(ValueError,'Finish your current'):
            self.app.notification_action(1,'notice:month_end:2026-09',900)
        self.assertEqual(self.store.conversation(1),before)
        self.app.handle(1,'Cancel')
        with patch('trainings_bot.reports.local_today',return_value=date(2026,10,1)):
            reply = self.app.notification_action(1,'notice:month_end:2026-09',900)
        self.assertEqual(reply.report_month,'')
        with self.assertRaises(ValueError):
            self.app.notification_action(2,'notice:month_end:2026-09',900)
        with self.assertRaises(ValueError):
            self.app.notification_action(1,'notice:month_end:2026-09',901)
        with patch('trainings_bot.conversation.local_today',return_value=date(2026,10,1)):
            reply = self.app.notification_action(1,'notice:evening:2026-09-30',900)
        self.assertIn('2026-09-30',reply.text)
        self.app.handle(1,'Cancel')
        self.assertEqual(self.store.conversation(1)['flow'],'report_preview')
