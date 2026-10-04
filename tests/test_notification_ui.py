"""Notification ordering and resolution, separate from schedule timing tests."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from trainings_bot.conversation import Conversation
from trainings_bot.storage import Store
from trainings_bot.reminders import send_due, resolve_log_reminders


class NotificationUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        app = Conversation(self.store, 'code')
        app.handle(1, 'code')
        for value in ['Test', 'Trainer', 'RSG', 'DE89370400440532013000', '20',
                      'Europe/Berlin', '20:00', 'Use suggested gyms', 'Next']:
            app.handle(1, value)
        self.counter = 100
        async def send(*args, **kwargs):
            self.counter += 1
            return SimpleNamespace(message_id=self.counter)
        self.bot = SimpleNamespace(send_message=AsyncMock(side_effect=send),
                                   delete_message=AsyncMock(), edit_message_text=AsyncMock(),
                                   edit_message_reply_markup=AsyncMock())

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def deliver(self, day=28):
        await send_due(self.store, self.bot, datetime(2026, 9, day, 18, tzinfo=timezone.utc))

    async def test_current_prompt_moves_below_notification_and_preserves_draft(self):
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='Back', callback_data='act:existing')]])
        self.store.track_message(1, 1, 50)
        self.store.snapshot_message(1, 50, 'Choose or enter start time.', markup)
        state = {'flow': 'start', 'day': '2026-09-28', 'keyboard_message_id': 50}
        self.store.save_conversation(1, state)
        await self.deliver()
        calls = self.bot.send_message.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn('2026-09-28', calls[0].args[1])
        self.assertEqual(calls[1].args[1], 'Choose or enter start time.')
        self.assertTrue(calls[1].kwargs['disable_notification'])
        self.assertEqual(calls[1].kwargs['reply_markup'], markup)
        self.assertEqual(self.store.conversation(1), dict(state, keyboard_message_id=102))
        self.bot.delete_message.assert_awaited_once_with(chat_id=1, message_id=50)
        self.assertEqual(self.store.message_ids(1, 1), [102])

    async def test_first_notification_has_separate_menu(self):
        await self.deliver()
        call = self.bot.send_message.await_args_list[-1]
        self.assertEqual(call.kwargs['reply_markup'].inline_keyboard[0][0].text, 'Main menu')
        self.assertTrue(call.kwargs['disable_notification'])

    async def test_any_recording_resolves_daily_prompt_once(self):
        for day, training in [(28, True), (29, False)]:
            await self.deliver(day)
            period = f'2026-09-{day}'
            msg = self.store.notification(1, 'evening', period)['message_id']
            if training:
                self.store.save_training(1, dict(day=period, gym='Gym', start='09:00', end='10:00'), period)
            else:
                self.store.no_training(1, period)
            self.bot.delete_message.reset_mock()
            await resolve_log_reminders(self.store, self.bot, 1)
            await resolve_log_reminders(self.store, self.bot, 1)
            self.bot.delete_message.assert_awaited_once_with(chat_id=1, message_id=msg)
            self.assertEqual(self.store.notification(1, 'evening', period)['message_id'], msg)

    async def test_combined_reminder_keeps_monthly_action(self):
        await self.deliver(30)
        self.store.no_training(1, '2026-09-30')
        await resolve_log_reminders(self.store, self.bot, 1)
        call = self.bot.edit_message_text.await_args
        buttons = call.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, 'notice:month_end:2026-09')
        self.assertNotIn('Log this day', str(buttons))

    async def test_builtin_schedule_cannot_be_deleted(self):
        schedule = next(s for s in self.store.schedules(1) if s['kind'] == 'evening')
        with self.assertRaises(ValueError):
            self.store.delete_schedule(1, schedule['id'])
        self.assertTrue(any(s['id'] == schedule['id'] for s in self.store.schedules(1)))
