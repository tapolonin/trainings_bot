import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from trainings_bot.conversation import Conversation
from trainings_bot.storage import Store

PROFILE = dict(first_name='Test', last_name='Trainer', sparte='RSG',
               iban='DE89370400440532013000', rate=2000,
               timezone='Europe/Berlin', reminder_time='20:00')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'bot.sqlite3'
        self.store = Store(self.path)
        self.store.claim(1, 'code', 'code')
        self.store.setup(1, PROFILE, ['Gym'])
        self.app = Conversation(self.store, 'code')
        self.today = patch('trainings_bot.conversation.local_today', return_value=date(2026,9,28))
        self.today.start()

    def tearDown(self):
        self.today.stop()
        self.store.close()
        self.tmp.cleanup()

    def add(self, day='2026-09-28', start='09:00', end='10:00'):
        self.store.save_training(1, dict(day=day, gym='Gym', start=start, end=end), uuid4().hex)
        return next(r['id'] for r in self.store.entries(1, day[:7]) if r['day'] == day and r['start'] == start)

    def open_entry(self, entry_id, action='Edit training', month='2026-09'):
        calendar = self.app.handle(1, '/log ' + month)
        day = self.store.entry(1, entry_id)['day']
        callback = next(data for row in calendar.calendar for label,data in row if data.endswith(':' + day))
        self.app.calendar_action(1, callback)
        return self.app.handle(1, action)

    def test_overview_completeness_and_future_days(self):
        self.add(day='2026-09-01')
        self.store.no_training(1, '2026-09-02')
        reply = self.app.handle(1, 'View trainings')
        cells = {data.rsplit(':',1)[-1]: label for row in reply.calendar[2:] for label,data in row}
        self.assertEqual(cells['2026-09-01'], '★𝟭')
        self.assertEqual(cells['2026-09-02'], '★𝟮')
        self.assertEqual(cells['2026-09-28'], '28')
        self.assertEqual(cells['2026-09-29'], '29·')
        self.assertEqual(sum(len(row) == 7 for row in reply.calendar), 6)
        self.assertEqual(reply.calendar[2][0][0], ' ')
        self.assertEqual(reply.calendar[2][1][0], '★𝟭')
        self.assertNotIn('Missing information:', reply.text)
        self.assertEqual(reply.buttons, ['Back', 'Main menu'])

    def test_month_navigation_year_boundary_and_leap_year(self):
        self.app.handle(1, '/log 2026-01')
        self.assertIn('2025-12', self.app.handle(1, 'Previous month').text)
        self.assertIn('2026-01', self.app.handle(1, 'Next month').text)
        self.app.handle(1, 'Choose month')
        self.app.handle(1, '2024-02')
        self.assertEqual(self.store.conversation(1)['month'], '2024-02')
        reply = self.app.handle(1, '/log 2024-02')
        labels = [label for row in reply.calendar[2:] for label,data in row]
        self.assertIn('29', labels)
        self.assertNotIn('30', labels)
        reply = self.app.handle(1, '/log 2026-10')
        self.assertTrue(all(label.endswith('·') for row in reply.calendar[2:] if len(row) == 7 for label,data in row if label.strip()))

    def test_calendar_navigation_day_selection_and_stale_taps(self):
        reply = self.app.handle(1, 'View trainings')
        old_data = reply.calendar[2][1][1]
        reply = self.app.calendar_action(1, reply.calendar[0][0][1])
        self.assertIn('August 2026', reply.calendar[0][1][0])
        with self.assertRaises(ValueError):
            self.app.calendar_action(1, old_data)
        day_data = next(data for row in reply.calendar[2:] for label,data in row if label == '1')
        reply = self.app.calendar_action(1, day_data)
        self.assertIn('2026-08-01', reply.text)
        self.assertIn('Missing information', reply.text)
        self.assertEqual(self.store.conversation(1)['flow'], 'history')
        self.assertTrue(self.app.calendar_action(1, day_data).day_details)
        self.app.handle(1, '/train')
        with self.assertRaises(ValueError):
            self.app.calendar_action(1, day_data)

    def test_day_details_allow_switching_and_show_earnings(self):
        self.add(day='2026-09-01')
        self.store.no_training(1, '2026-09-02')
        calendar = self.app.handle(1, 'View trainings')
        days = {data.rsplit(':',1)[-1]:data for row in calendar.calendar for label,data in row}
        reply = self.app.calendar_action(1, days['2026-09-01'])
        self.assertIn('09:00–10:00', reply.text)
        self.assertNotIn('€', reply.text)
        self.assertIn('No training recorded.', self.app.calendar_action(1, days['2026-09-02']).text)
        reply = self.app.calendar_action(1, days['2026-09-03'])
        self.assertIn('Missing information', reply.text)
        self.app.handle(1, 'Log this day')
        self.assertEqual(self.store.conversation(1)['flow'], 'day_status')

    def test_calendar_actions_appear_only_after_selecting_day(self):
        calendar = self.app.handle(1, 'View calendar')
        self.assertEqual(calendar.buttons, ['Back', 'Main menu'])
        day_data = next(data for row in calendar.calendar for label,data in row if data.endswith(':2026-09-01'))
        details = self.app.calendar_action(1, day_data)
        self.assertIn('Edit training', details.buttons)
        self.assertIn('Log this day', details.buttons)
        self.assertEqual(self.store.conversation(1)['buttons'], details.buttons)
        calendar = self.app.calendar_action(1, calendar.calendar[0][0][1])
        self.assertEqual(calendar.buttons, ['Back', 'Main menu'])
        self.assertNotIn('selected_day', self.store.conversation(1))
        choose = next(data for row in calendar.calendar for label,data in row if label == 'Choose month')
        reply = self.app.calendar_action(1, choose)
        self.assertIn('Which month?', reply.text)
        self.assertEqual(reply.buttons, ['Cancel'])
        self.assertIn('choose Cancel', self.app.handle(1, 'Main menu').text)
        self.app.handle(1, 'Cancel')
        self.assertIn('Calendar', self.app.handle(1, 'Main menu').buttons)

    def test_calendar_and_earnings_are_separate_sections(self):
        self.add(day='2026-09-01')
        main = self.app.handle(1, '/start')
        self.assertIn('Calendar', main.buttons)
        self.assertIn('Earnings', main.buttons)
        calendar = self.app.handle(1, 'Calendar')
        self.assertNotIn('€', calendar.text)
        self.assertTrue(any(len(row) == 7 for row in calendar.calendar))
        earnings = self.app.handle(1, 'Earnings')
        self.assertIn('Total earned: 20,00 €', earnings.text)
        self.assertFalse(any(len(row) == 7 for row in earnings.calendar))
        next_month = self.app.calendar_action(1, earnings.calendar[0][2][1])
        self.assertIn('Earnings for 2026-10', next_month.text)
        self.assertIn('Total earned: 0,00 €', next_month.text)
        self.app.handle(1, 'Choose month')
        self.assertIn('Earnings for 2026-09', self.app.handle(1, '2026-09').text)

    def test_selected_day_keyboard_edits_only_that_days_entries(self):
        first = self.add(day='2026-09-01')
        self.add(day='2026-09-02')
        calendar = self.app.handle(1, 'View trainings')
        day_data = next(data for row in calendar.calendar for label,data in row if data.endswith(':2026-09-01'))
        self.app.calendar_action(1, day_data)
        reply = self.app.handle(1, 'Edit training')
        self.assertIn('Save changes', reply.buttons)
        self.assertEqual(self.store.conversation(1)['id'], first)
        self.assertNotIn('#', reply.text)

    def test_calendar_auth_and_future_taps_preserve_state(self):
        reply = self.app.handle(1, 'View trainings')
        future = next(data for row in reply.calendar[2:] for label,data in row if label == '30·')
        before = self.store.conversation(1)
        with self.assertRaises(ValueError): self.app.calendar_action(1, future)
        with self.assertRaises(ValueError): self.app.calendar_action(2, future)
        self.assertEqual(self.store.conversation(1), before)
        self.assertIsNone(self.app.calendar_action(1, reply.calendar[1][0][1]))

    def test_pagination_preserves_month_total(self):
        for day in range(1,13):
            self.add(day=f'2026-09-{day:02d}')
        reply = self.app.handle(1, 'Earnings')
        self.assertIn('Total earned: 240,00 €', reply.text)
        self.assertIn('Entries page 1 of 2', reply.text)
        self.assertEqual(len(self.store.conversation(1)['ids']), 10)
        reply = self.app.handle(1, 'Next entries')
        self.assertIn('Entries page 2 of 2', reply.text)
        self.assertIn('Total earned: 240,00 €', reply.text)
        self.assertEqual(len(self.store.conversation(1)['ids']), 2)

    def test_edit_date_location_times_and_rate_across_month(self):
        entry_id = self.add()
        self.store.set_rate(1, '2026-09', 3000)
        self.open_entry(entry_id)
        for text in ['Change date', '31.08.2026', 'Change gym', 'Wettkampf', 'Bielefeld',
                     'Change start', '8', 'Change finish', '10:30']:
            self.app.handle(1, text)
        self.assertEqual(self.store.entry(1, entry_id)['day'], '2026-09-28')
        reply = self.app.handle(1, 'Save changes')
        self.assertIn('Training updated.', reply.text)
        self.assertIn('Total earned: 50,00 €', self.app.handle(1, '/earnings 2026-08').text)
        saved = self.store.entry(1, entry_id)
        self.assertEqual((saved['day'],saved['gym'],saved['start'],saved['end']),
                         ('2026-08-31','Wettkampf Bielefeld','08:00','10:30'))
        self.assertNotIn('2026-09-28', self.store.day_statuses(1, '2026-09'))
        self.assertEqual(self.store.day_statuses(1, '2026-08')['2026-08-31'], 'training')

    def test_cancel_edit_does_not_change_record(self):
        entry_id = self.add()
        self.open_entry(entry_id)
        self.app.handle(1, 'Change start')
        self.app.handle(1, '8')
        self.app.handle(1, 'Cancel')
        self.assertEqual(self.store.entry(1,entry_id)['start'],'09:00')

    def test_invalid_edit_and_duplicate_leave_original_unchanged(self):
        entry_id = self.add()
        self.add(day='2026-09-27',start='11:00',end='12:00')
        self.open_entry(entry_id)
        for text in ['Change start', '11', 'Save changes']:
            reply = self.app.handle(1,text)
        self.assertIn('Finish must be after start',reply.text)
        self.assertIn('Change finish',reply.buttons)
        for text in ['Change finish','12','Change date','27.09.2026','Save changes']:
            reply = self.app.handle(1,text)
        self.assertIn('already has a training',reply.text)
        self.assertEqual(self.store.entry(1,entry_id)['start'],'09:00')
        self.assertEqual(len(self.store.entries(1,'2026-09')),2)

    def test_delete_requires_confirmation_and_keeps_other_sessions(self):
        first = self.add()
        second = self.add(day='2026-09-27',start='11:00',end='12:00')
        self.open_entry(first,'Delete training')
        self.app.handle(1,'Cancel')
        self.assertEqual(len(self.store.entries(1,'2026-09')),2)
        self.open_entry(first,'Delete training')
        self.app.handle(1,'Confirm delete')
        self.assertNotIn('2026-09-28',self.store.day_statuses(1,'2026-09'))
        self.assertEqual(self.store.day_statuses(1,'2026-09')['2026-09-27'],'training')
        self.open_entry(second,'Delete training')
        self.app.handle(1,'Confirm delete')
        self.assertNotIn('2026-09-28',self.store.day_statuses(1,'2026-09'))
        self.app.handle(1,'Confirm delete')
        self.assertEqual(self.store.entries(1,'2026-09'),[])

    def test_edit_draft_survives_restart(self):
        entry_id = self.add()
        self.open_entry(entry_id)
        self.app.handle(1,'Change finish')
        self.app.handle(1,'12')
        self.store.close()
        self.store = Store(self.path)
        self.app = Conversation(self.store,'code')
        self.app.handle(1,'Save changes')
        self.assertEqual(self.store.entry(1,entry_id)['end'],'12:00')

    def test_clear_no_training_requires_confirmation(self):
        self.store.no_training(1,'2026-09-02')
        for text in ['View trainings','Correct day','2','Clear no-training record']:
            self.app.handle(1,text)
        self.assertEqual(self.store.day_statuses(1,'2026-09')['2026-09-02'],'no_training')
        self.app.handle(1,'Confirm clear')
        self.assertNotIn('2026-09-02',self.store.day_statuses(1,'2026-09'))

    def test_correct_missing_day_and_reject_future(self):
        for text in ['View trainings','Correct day','30']:
            self.app.handle(1,text)
        self.assertEqual(self.store.conversation(1)['flow'],'history_day')
        for text in ['2','Log this day','No training','Confirm no training']:
            self.app.handle(1,text)
        self.assertEqual(self.store.day_statuses(1,'2026-09')['2026-09-02'],'no_training')

    def test_cross_user_record_changes_are_denied(self):
        entry_id = self.add()
        self.store.setup(2,PROFILE,['Other'])
        with self.assertRaises(ValueError): self.store.delete_training(2,entry_id)
        with self.assertRaises(ValueError):
            self.store.edit_training(2,entry_id,dict(day='2026-09-28',gym='Other',start='08:00',end='10:00'))
        self.assertEqual(self.store.entry(1,entry_id)['gym'],'Gym')
        self.assertEqual(self.store.day_statuses(2,'2026-09'),{})

    def test_moving_session_updates_both_day_statuses(self):
        entry_id = self.add()
        self.store.no_training(1,'2026-09-02')
        self.open_entry(entry_id)
        for text in ['Change date','02.09.2026','Save changes']:
            self.app.handle(1,text)
        statuses = self.store.day_statuses(1,'2026-09')
        self.assertEqual(statuses['2026-09-02'],'training')
        self.assertNotIn('2026-09-28',statuses)

    def test_second_training_on_same_day_is_rejected_without_overwriting(self):
        entry_id = self.add()
        with self.assertRaisesRegex(ValueError, 'already has a training'):
            self.add(start='12:00',end='13:00')
        self.assertEqual(self.store.entry(1,entry_id)['start'], '09:00')
        self.assertEqual(len(self.store.entries(1,'2026-09')),1)
        self.assertNotIn('#', self.app.handle(1,'Earnings').text)

    def test_existing_multiple_entries_survive_upgrade(self):
        first = self.add()
        self.store.db.execute('DROP TRIGGER one_training_per_day_insert')
        with self.store.db:
            self.store.db.execute("INSERT INTO trainings(user_id,day,gym_id,start,end,request_id) SELECT user_id,day,gym_id,'11:00','12:00','legacy-second' FROM trainings WHERE id=?", (first,))
        self.store.close()
        self.store = Store(self.path)
        self.app = Conversation(self.store,'code')
        self.assertEqual(len(self.store.entries(1,'2026-09')),2)
        reply = self.open_entry(first)
        self.assertIn('09:00–10:00 · Gym', reply.buttons)
        self.assertTrue(all('#' not in b for b in reply.buttons))
        self.app.handle(1, '09:00–10:00 · Gym')
        self.assertEqual(self.store.conversation(1)['id'], first)
        with self.assertRaises(ValueError):
            self.add(start='14:00',end='15:00')
