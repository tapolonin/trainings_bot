import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from trainings_bot.conversation import Conversation
from trainings_bot.domain import clock_time, iban_value, quick_entry, rate_cents, time_range, training_date
from trainings_bot.storage import Store

PROFILE = dict(first_name='Test', last_name='Trainer', sparte='RSG', iban='DE89370400440532013000', rate=2000, timezone='Europe/Berlin', reminder_time='20:00')
CODE = 'test-private-code'


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'bot.sqlite3'
        self.store = Store(self.path)
        self.app = Conversation(self.store, CODE)
        self.today_patch = patch('trainings_bot.conversation.local_today', return_value=date(2026,9,28))
        self.today_patch.start()

    def tearDown(self):
        self.store.close()
        self.today_patch.stop()
        self.tmp.cleanup()

    def setup_user(self):
        self.app.handle(1, CODE)
        for value in ['Test', 'Trainer', 'RSG', PROFILE['iban'], '20', 'Europe/Berlin', '20:00', 'Use suggested gyms','Next']:
            reply = self.app.handle(1, value)
        self.assertIn('Setup complete', reply.text)

    def test_open_setup_and_isolation(self):
        reply = self.app.handle(2, '/start')
        self.assertIn('first name',reply.text)
        other_draft = self.store.conversation(2)
        self.setup_user()
        self.assertEqual(self.store.profile(1)['timezone'], 'Europe/Berlin')
        self.assertEqual(self.store.conversation(2),other_draft)
        self.assertIsNone(self.store.profile(2))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_wizard_restart_and_repeated_save(self):
        self.setup_user()
        for value in ['/train','today','Herschelschule','16','20']:
            reply = self.app.handle(1,value)
        self.assertIn('2026-09-28',reply.text)
        self.assertEqual(self.store.entries(1,'2026-09'),[])
        self.store.close()
        self.store = Store(self.path)
        self.app = Conversation(self.store,CODE)
        self.assertEqual(self.app.handle(1,'Save').text,'Training saved.')
        self.app.handle(1,'Save')
        self.app.handle(1,'today Herschelschule 16:00-20:00')
        self.assertIn('already saved',self.app.handle(1,'Save').text)
        self.assertEqual(len(self.store.entries(1,'2026-09')),1)
        self.assertEqual(self.store.entries(2,'2026-09'),[])

    def test_gym_rename_archive_and_one_off(self):
        self.setup_user()
        self.app.handle(1,'today Herschelschule 16:00-20:00')
        self.app.handle(1,'Save')
        gym_id = self.store.gyms(1)[0]['id']
        for value in ['/settings','Gyms','Rename gym',f'{gym_id} New gym']:
            self.app.handle(1,value)
        self.assertEqual(self.store.entries(1,'2026-09')[0]['gym'],'New gym')
        for value in ['Archive gym',str(gym_id)]:
            self.app.handle(1,value)
        self.assertNotIn(gym_id,[g['id'] for g in self.store.gyms(1)])
        self.assertEqual(len(self.store.entries(1,'2026-09')),1)
        self.app.handle(1,'Main menu')
        self.app.handle(1,'26.09 Special event 12:00-13:00')
        self.app.handle(1,'Save')
        self.assertNotIn('Special event',[g['name'] for g in self.store.gyms(1)])
        self.app.handle(1,'27.09 Special event 12:00-13:00')
        self.app.handle(1,'Save and add gym')
        self.assertIn('Special event',[g['name'] for g in self.store.gyms(1)])

    def test_rate_history_and_settings_preserve_entries(self):
        self.setup_user()
        self.app.handle(1,'today Herschelschule 16:00-20:00')
        self.app.handle(1,'Save')
        for value in ['/settings','Hourly rate','25,50','2026-10','Apply rate']:
            self.app.handle(1,value)
        self.assertEqual(self.store.rate(1,'2026-09'),2000)
        self.assertEqual(self.store.rate(1,'2026-10'),2550)
        self.assertEqual(self.store.rate(1,'2020-01'),2000)
        for value in ['First name','Changed']:
            self.app.handle(1,value)
        self.assertEqual(self.store.profile(1)['first_name'],'Changed')
        self.assertEqual(len(self.store.entries(1,'2026-09')),1)

    def test_no_training_cancel_and_later_training(self):
        self.setup_user()
        for value in ['/no_training','today','Confirm no training']:
            self.app.handle(1,value)
        self.assertEqual(self.store.db.execute('SELECT status FROM day_logs').fetchone()[0],'no_training')
        self.app.handle(1,'today Herschelschule 16:00-20:00')
        self.app.handle(1,'Cancel')
        self.assertEqual(self.store.entries(1,'2026-09'),[])
        self.app.handle(1,'today Herschelschule 16:00-20:00')
        self.app.handle(1,'Save')
        self.assertEqual(self.store.db.execute('SELECT status FROM day_logs').fetchone()[0],'training')
        for value in ['/no_training','today']:
            self.app.handle(1,value)
        self.assertIn('already has training',self.app.handle(1,'Confirm no training').text)

    def test_bad_input_does_not_advance(self):
        self.setup_user()
        for value in ['/train','31.02']:
            self.app.handle(1,value)
        self.assertEqual(self.store.conversation(1)['flow'],'train_date')
        for value in ['today','Gym','20:00-16:00']:
            self.app.handle(1,value)
        self.assertEqual(self.store.conversation(1)['flow'],'train_start')
        self.assertIn('finish', self.app.handle(1, '16').text)
        self.app.handle(1, '15')
        self.assertEqual(self.store.conversation(1)['flow'], 'train_end')
        self.assertIn('16:00–17:30', self.app.handle(1, '17:30').text)

    def test_log_shows_session_amounts_and_month_total(self):
        self.setup_user()
        for entry in ['today Gym 9-10:30', '27.09 Gym 12-13:15']:
            self.app.handle(1, entry)
            self.app.handle(1, 'Save')
        self.store.set_rate(1, '2026-10', 3000)
        reply = self.app.handle(1, 'Earnings')
        self.assertIn('09:00–10:30 · 30,00 €', reply.text)
        self.assertIn('12:00–13:15 · 25,00 €', reply.text)
        self.assertIn('Total earned: 55,00 €', reply.text)
        self.assertIn('Total earned: 0,00 €', self.app.handle(1, '/earnings 2026-08').text)

    def test_manual_today_training(self):
        self.setup_user()
        reply = self.app.handle(1, 'Log day')
        self.assertEqual(reply.buttons, ['Today', 'Other day', 'Cancel'])
        self.assertIn('2026-09-28', self.app.handle(1, 'Today').text)
        self.assertIn('Wettkampf', self.app.handle(1, 'Training').buttons)
        for value in ['Herschelschule', '9', '12', 'Save']:
            reply = self.app.handle(1, value)
        self.assertIn('Log day', reply.buttons)
        self.assertEqual(self.store.entries(1, '2026-09')[0]['day'], '2026-09-28')

    def test_manual_other_day_no_training(self):
        self.setup_user()
        for value in ['Log day', 'Other day', '31.02']:
            self.app.handle(1, value)
        self.assertEqual(self.store.conversation(1)['flow'], 'day_date')
        self.app.handle(1, '27.09')
        reply = self.app.handle(1, 'No training')
        self.assertIn('2026-09-27', reply.text)
        self.assertIsNone(self.store.db.execute('SELECT * FROM day_logs').fetchone())
        self.app.handle(1, 'Confirm no training')
        row = self.store.db.execute('SELECT day,status FROM day_logs').fetchone()
        self.assertEqual(tuple(row), ('2026-09-27', 'no_training'))

    def test_competition_asks_city_and_saves_location(self):
        self.setup_user()
        self.app.handle(1, '/train')
        self.assertIn('Wettkampf', self.app.handle(1, 'today').buttons)
        self.assertIn('Which city', self.app.handle(1, 'Wettkampf').text)
        self.app.handle(1, ' ')
        self.assertEqual(self.store.conversation(1)['flow'], 'competition_city')
        self.app.handle(1, 'Bad Oeynhausen')
        self.app.handle(1, '10')
        self.assertIn('Wettkampf Bad Oeynhausen', self.app.handle(1, '14').text)
        self.app.handle(1, 'Save')
        self.assertEqual(self.store.entries(1, '2026-09')[0]['gym'], 'Wettkampf Bad Oeynhausen')

    def test_quick_competition_missing_city(self):
        self.setup_user()
        self.assertIn('Which city', self.app.handle(1, 'today Wettkämpf 10:00-14:00').text)
        self.assertIn('10:00–14:00', self.app.handle(1, 'Bielefeld').text)
        self.app.handle(1, 'Save')
        self.assertEqual(self.store.entries(1, '2026-09')[0]['gym'], 'Wettkampf Bielefeld')

    def test_storage_tenant_scope(self):
        self.setup_user()
        self.store.setup(2, PROFILE, ['Other'])
        gym_id = self.store.gyms(1)[0]['id']
        self.store.change_gym(2,gym_id,'Hacked')
        self.assertEqual(self.store.gyms(1)[0]['name'],'Herschelschule')
        self.assertEqual(self.store.entries(2,'2026-09'),[])


class ValidationTests(unittest.TestCase):
    def test_dates_and_quick_input(self):
        today = date(2026,9,28)
        self.assertEqual(training_date('28.09',today),'2026-09-28')
        self.assertEqual(training_date('31.12.2025',today),'2025-12-31')
        self.assertEqual(quick_entry('today F.-N. Schule 16:00-20:00',today)['gym'],'F.-N. Schule')
        for value in ['31.02','29.09','2026-13-01']:
            with self.assertRaises(ValueError): training_date(value,today)

    def test_money_and_times(self):
        self.assertEqual(clock_time('9'), '09:00')
        self.assertEqual(clock_time('0'), '00:00')
        self.assertEqual(clock_time('9:15'), '09:15')
        self.assertEqual(time_range('9-16'), ('09:00', '16:00'))
        self.assertEqual(quick_entry('today Gym 9-16', date(2026,9,28))['start'], '09:00')
        for value in ['24', '99', '930', '9:5', '-1', '16.5']:
            with self.assertRaises(ValueError): clock_time(value)
        self.assertEqual(rate_cents('20,50'),2050)
        for value in ['NaN','Infinity','-2','0','1.001','abc']:
            with self.assertRaises(ValueError): rate_cents(value)
        for value in ['20:00-20:00','23:00-01:00','25:00-26:00']:
            with self.assertRaises(ValueError): time_range(value)

    def test_iban(self):
        for value in ['DE89 3704 0044 0532 0130 00', 'de89-3704-0044-0532-0130-00',
                      'DE89370400440532013001', 'My account details']:
            self.assertEqual(iban_value('  ' + value + '  '), value)
        for value in ['', '   ', 'x' * 101, 'DE89\n3704']:
            with self.assertRaises(ValueError):
                iban_value(value)
