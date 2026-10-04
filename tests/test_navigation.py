import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from trainings_bot.conversation import Conversation
from trainings_bot.storage import Store

PROFILE = dict(first_name='Test', last_name='Trainer', sparte='RSG',
               iban='DE89370400440532013000', rate=2000,
               timezone='Europe/Berlin', reminder_time='20:00')


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.store = Store(self.path)
        self.store.claim(1,'code','code')
        self.store.setup(1,PROFILE,['Gym'])
        self.app = Conversation(self.store,'code')
        self.today = patch('trainings_bot.conversation.local_today',return_value=date(2026,9,28))
        self.today.start()

    def tearDown(self):
        self.today.stop()
        self.store.close()
        self.tmp.cleanup()

    def action_buttons(self, reply):
        self.assertIn('Cancel',reply.buttons)
        self.assertNotIn('Main menu',reply.buttons)
        self.assertNotIn('Back',reply.buttons)

    def test_browsing_has_back_and_home_and_back_moves_one_level(self):
        self.assertNotIn('Back',self.app.handle(1,'/start').buttons)
        for text in ['Settings','Gyms']:
            reply = self.app.handle(1,text)
            self.assertIn('Back',reply.buttons)
            self.assertIn('Main menu',reply.buttons)
            self.assertNotIn('Cancel',reply.buttons)
        self.app.handle(1,'Back')
        self.assertEqual(self.store.conversation(1)['flow'],'settings')
        self.app.handle(1,'Back')
        self.assertEqual(self.store.conversation(1),{})
        reply = self.app.handle(1,'Calendar')
        data = next(data for row in reply.calendar for label,data in row if data.endswith(':2026-09-01'))
        self.app.calendar_action(1,data)
        reply = self.app.handle(1,'Back')
        self.assertTrue(reply.calendar)
        self.assertNotIn('selected_day',self.store.conversation(1))
        self.app.handle(1,'Main menu')
        self.assertEqual(self.store.conversation(1),{})

    def test_cancel_setting_and_gym_action_returns_to_their_menu(self):
        for text in ['Settings','First name']:
            reply = self.app.handle(1,text)
        self.action_buttons(reply)
        reply = self.app.handle(1,'Cancel')
        self.assertEqual(self.store.conversation(1)['flow'],'settings')
        self.assertEqual(self.store.profile(1)['first_name'],'Test')
        self.app.handle(1,'Gyms')
        self.action_buttons(self.app.handle(1,'Add gym'))
        self.app.handle(1,'Cancel')
        self.assertEqual(self.store.conversation(1)['flow'],'gym_menu')
        self.assertEqual(len(self.store.gyms(1)),1)

    def test_active_action_cannot_be_abandoned_by_navigation(self):
        for text in ['Log day','Today','Training','Gym','9']:
            reply = self.app.handle(1,text)
            self.assertIn('Cancel', reply.buttons)
            self.assertNotIn('Main menu', reply.buttons)
        before = self.store.conversation(1)
        for text in ['Main menu','/start','/settings','/log']:
            reply = self.app.handle(1,text)
            self.assertIn('choose Cancel',reply.text)
            self.assertIn('Back', reply.buttons)
            self.assertEqual(self.store.conversation(1),before)
        self.app.handle(1,'Cancel')
        self.assertEqual(self.store.conversation(1),{})
        self.assertEqual(self.store.entries(1,'2026-09'),[])

    def test_training_back_retraces_steps_and_allows_corrections(self):
        for text in ['Log day', 'Today', 'Training', 'Wettkampf']:
            reply = self.app.handle(1, text)
        self.assertIn('Back', reply.buttons)
        self.assertIn('Choose a gym', self.app.handle(1, 'Back').text)
        self.app.handle(1, 'Add new gym')
        self.assertIn('Choose a gym', self.app.handle(1, 'Back').text)
        for text in ['Wettkampf', 'Berlin', '16', '19']:
            self.app.handle(1, text)
        self.app = Conversation(self.store)
        for flow in ['train_end', 'train_start', 'competition_city']:
            self.app.handle(1, 'Back')
            self.assertEqual(self.store.conversation(1)['flow'], flow)
        for text in ['Hamburg', '10', '12']:
            reply = self.app.handle(1, text)
        self.assertIn('Wettkampf Hamburg', reply.text)
        self.assertIn('10:00–12:00', reply.text)
        self.app.handle(1, 'Save')
        self.assertEqual(len(self.store.entries(1, '2026-09')), 1)

    def test_back_restores_other_day_calendar_and_no_training_choice(self):
        self.app.handle(1, 'Log day')
        calendar = self.app.handle(1, 'Other day')
        callback = next(data for row in calendar.calendar for label,data in row if data.endswith(':2026-09-01'))
        self.app.calendar_action(1, callback)
        self.app.handle(1, 'No training')
        self.app.handle(1, 'Back')
        self.assertEqual(self.store.conversation(1)['flow'], 'day_status')
        restored = self.app.handle(1, 'Back')
        self.assertTrue(restored.calendar_picker)
        self.assertEqual(restored.calendar, [[list(button) for button in row] for row in calendar.calendar])
        self.app.handle(1, 'Back')
        self.assertEqual(self.store.conversation(1)['flow'], 'day_choice')
        self.app.handle(1, 'Cancel')
        self.assertEqual(self.store.conversation(1), {})

    def test_cancel_month_choice_keeps_earnings_month(self):
        reply = self.app.handle(1,'/earnings 2026-08')
        callback = next(data for row in reply.calendar for label,data in row if label=='Choose month')
        self.action_buttons(self.app.calendar_action(1,callback))
        reply = self.app.handle(1,'Cancel')
        self.assertIn('Earnings for 2026-08',reply.text)
        self.assertEqual(self.store.conversation(1)['view'],'earnings')

    def test_cancel_edit_restores_selected_day_even_after_restart(self):
        self.store.save_training(1,dict(day='2026-09-01',gym='Gym',start='09:00',end='10:00'),'test')
        calendar = self.app.handle(1,'Calendar')
        callback = next(data for row in calendar.calendar for label,data in row if data.endswith(':2026-09-01'))
        self.app.calendar_action(1,callback)
        for text in ['Edit training','Change start','8']:
            self.action_buttons(self.app.handle(1,text))
        self.store.close()
        self.store = Store(self.path)
        self.app = Conversation(self.store,'code')
        reply = self.app.handle(1,'Cancel')
        self.assertTrue(reply.calendar)
        self.assertIn('09:00–10:00',reply.restored_details)
        self.assertEqual(self.store.conversation(1)['selected_day'],'2026-09-01')
        self.assertEqual(self.store.entries(1,'2026-09')[0]['start'],'09:00')
        self.assertNotIn('Cancel',reply.buttons)
        self.app.handle(1,'Main menu')
        self.assertEqual(self.store.conversation(1),{})

    def test_cancel_delete_preserves_training(self):
        self.store.save_training(1,dict(day='2026-09-01',gym='Gym',start='09:00',end='10:00'),'test')
        calendar = self.app.handle(1,'Calendar')
        callback = next(data for row in calendar.calendar for label,data in row if data.endswith(':2026-09-01'))
        self.app.calendar_action(1,callback)
        self.action_buttons(self.app.handle(1,'Delete training'))
        self.app.handle(1,'Cancel')
        self.assertEqual(len(self.store.entries(1,'2026-09')),1)
        self.assertEqual(self.store.conversation(1)['selected_day'],'2026-09-01')

    def test_help_version_and_whats_new_navigation(self):
        from trainings_bot.version import VERSION, RELEASE_NOTES
        help_reply = self.app.handle(1, 'Help')
        self.assertIn(f'Version {VERSION}', help_reply.text)
        self.assertIn("What's new", help_reply.buttons)
        notes = self.app.handle(1, "What's new")
        self.assertIn(RELEASE_NOTES.strip(), notes.text)
        self.assertEqual(notes.buttons, ['Back', 'Main menu'])
        self.assertIn("What's new", self.app.handle(1, 'Back').buttons)
        self.app.handle(1, "What's new")
        self.assertIn('Log day', self.app.handle(1, 'Main menu').buttons)
