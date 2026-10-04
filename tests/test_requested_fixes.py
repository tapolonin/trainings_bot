import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from trainings_bot.conversation import Conversation
from trainings_bot.storage import Store
from trainings_bot.reminders import send_due

PROFILE = dict(first_name='Test',last_name='Trainer',sparte='RSG',iban='DE89370400440532013000',
               rate=2000,timezone='Europe/Berlin',reminder_time='20:00')


class RequestedFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'test.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.store.close())
        self.store.claim(1,'code','code')
        self.store.setup(1,PROFILE,['Gym'])
        self.app = Conversation(self.store,'code')
        clock = patch('trainings_bot.conversation.local_today',return_value=date(2026,9,29))
        clock.start()
        self.addCleanup(clock.stop)

    def test_calendar_for_other_day_and_future_rejection(self):
        self.store.no_training(1,'2026-09-01')
        self.app.handle(1,'Log day')
        reply = self.app.handle(1,'Other day')
        self.assertTrue(reply.calendar_picker)
        self.assertEqual(reply.buttons,['Back','Cancel'])
        self.assertTrue(any('★' in label for row in reply.calendar for label,_ in row))
        with self.assertRaises(ValueError):
            self.app.calendar_action(1,next(data for row in reply.calendar for label,data in row if data.endswith(':2026-09-30')))
        old = reply.calendar[0][0][1]
        reply = self.app.calendar_action(1,old)
        self.assertIn('August',reply.calendar[0][1][0])
        with self.assertRaises(ValueError):
            self.app.calendar_action(1,old)
        reply = self.app.calendar_action(1,next(data for row in reply.calendar for label,data in row if data.endswith(':2026-08-03')))
        self.assertIn('2026-08-03',reply.text)
        self.assertEqual(reply.buttons,['Training','No training','Back','Cancel'])

    def test_new_gym_and_suggestions_by_training_day(self):
        for day,start,end in [('2026-09-28',['16','16:15'],['19','19:15','20']),
                              ('2026-09-26',['10','11'],['16']),('2026-09-27',[],[])]:
            self.app.handle(1,'Log day')
            self.app.handle(1,'Other day')
            self.app.handle(1,day)
            reply = self.app.handle(1,'Training')
            self.assertIn('Add new gym',reply.buttons)
            self.app.handle(1,'Add new gym')
            reply = self.app.handle(1,'New Gym')
            self.assertIn('Yes, add to my gyms', reply.buttons)
            reply = self.app.handle(1,'Yes, add to my gyms')
            self.assertEqual(reply.buttons,start+['Back','Cancel'])
            self.assertIn('New Gym',[g['name'] for g in self.store.gyms(1)])
            reply = self.app.handle(1,'9')
            self.assertEqual(reply.buttons,end+['Back','Cancel'])
            self.app.handle(1,'Cancel')
        self.assertEqual(self.store.entries(1,'2026-09'),[])

    def test_new_gym_can_be_used_without_saving_to_list(self):
        for text in ['Log day', 'Today', 'Training', 'Add new gym', 'One-off gym']:
            reply = self.app.handle(1, text)
        self.assertIn('Back', reply.buttons)
        self.assertNotIn('One-off gym', [g['name'] for g in self.store.gyms(1)])
        self.app.handle(1, 'Back')
        self.assertEqual(self.store.conversation(1)['flow'], 'train_new_gym')
        self.app.handle(1, 'One-off gym')
        self.app = Conversation(self.store)
        for text in ['No, just this training', '16', '19']:
            reply = self.app.handle(1, text)
        self.assertNotIn('Save and add gym', reply.buttons)
        self.app.handle(1, 'Save')
        self.assertEqual(self.store.entries(1, '2026-09')[0]['gym'], 'One-off gym')
        self.assertNotIn('One-off gym', [g['name'] for g in self.store.gyms(1)])

    def menu(self):
        self.app.handle(1,'Settings')
        return self.app.handle(1,'Notifications')

    def custom(self):
        self.menu()
        for value in ['New notification','Pack bag','Weekly','○ Monday','○ Wednesday','Done','17:15','Bring equipment on {date}','Save notification']:
            reply = self.app.handle(1,value)
        return reply

    def test_weekday_selection_restart_save_cancel_and_navigation(self):
        self.menu()
        for value in ['New notification','Pack bag','Weekly']:
            self.app.handle(1,value)
        self.assertIn('Select at least',self.app.handle(1,'Done').text)
        reply = self.app.handle(1,'○ Monday')
        self.assertTrue(reply.replace_screen)
        self.assertIn('✓ Monday',reply.buttons)
        self.app.handle(1,'○ Wednesday')
        self.store.close()
        self.store = Store(self.path)
        self.app = Conversation(self.store,'code')
        for value in ['Done','17:15','Bring equipment','Save notification']:
            reply = self.app.handle(1,value)
        custom = [r for r in self.store.schedules(1) if r['kind']=='custom'][0]
        self.assertEqual(custom['weekdays'],'0,2')
        self.assertEqual(custom['time'],'17:15')
        self.assertNotIn('Cancel',reply.buttons)
        self.app.handle(1,'Change message')
        self.app.handle(1,'Different text')
        self.app.handle(1,'Cancel')
        self.assertEqual(self.store.schedule(1,custom['id'])['message'],'Bring equipment')
        self.app.handle(1,'Turn off')
        self.assertEqual(self.store.schedule(1,custom['id'])['enabled'],0)
        self.app.handle(1,'Back')
        self.assertEqual(self.store.conversation(1)['flow'],'notification_list')
        self.app.handle(1,'Back')
        self.assertEqual(self.store.conversation(1)['flow'],'settings')

    def test_builtins_independent_and_custom_delete(self):
        reply = self.menu()
        first = next(b for b in reply.buttons if b.startswith('Evening log'))
        self.app.handle(1,first)
        for value in ['Change time','18:30','Save notification','Turn off']:
            self.app.handle(1,value)
        rows = self.store.schedules(1)
        self.assertEqual((rows[0]['time'],rows[0]['enabled']),('18:30',0))
        self.assertEqual((rows[1]['time'],rows[1]['enabled']),('20:00',1))
        self.app.handle(1,'Main menu')
        self.custom()
        self.app.handle(1,'Delete notification')
        self.app.handle(1,'Cancel')
        self.assertEqual(len(self.store.schedules(1)),3)
        self.app.handle(1,'Delete notification')
        self.app.handle(1,'Delete notification')
        self.assertEqual(len(self.store.schedules(1)),2)

    def test_monthly_custom_and_preserve_legacy_preferences(self):
        self.store.update_profile(1,'reminder_time','21:30')
        self.store.update_profile(1,'reminders_enabled',0)
        self.menu()
        self.assertTrue(all(r['time']=='21:30' and r['enabled']==0 for r in self.store.schedules(1)))
        for value in ['New notification','Month check','Monthly','31','18','Review {month}','Save notification']:
            reply = self.app.handle(1,value)
        row = [r for r in self.store.schedules(1) if r['kind']=='custom'][0]
        self.assertEqual((row['frequency'],row['month_day'],row['time']),('monthly',31,'18:00'))
        with self.assertRaises(ValueError):
            self.store.schedule(2,row['id'])


class CustomDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_weekly_monthly_independent_schedules_and_once_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'test.sqlite3')
            try:
                store.claim(1,'code','code')
                store.setup(1,PROFILE,['Gym'])
                for row in store.schedules(1):
                    row['enabled']=0
                    store.save_schedule(1,row)
                base = dict(name='Weekly',frequency='weekly',weekdays='0,2',month_day=0,time='16:00',message='Hello {date}',enabled=1)
                first = store.save_schedule(1,base)
                store.save_schedule(1,dict(base,name='Monthly',frequency='monthly',month_day=31,message='Review {month}'))
                bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=5)))
                await send_due(store,bot,datetime(2026,9,28,14,tzinfo=timezone.utc))
                self.assertEqual(bot.send_message.await_count,1)
                self.assertEqual(bot.send_message.call_args.args[1],'Hello 2026-09-28')
                await send_due(store,bot,datetime(2026,9,29,14,tzinfo=timezone.utc))
                self.assertEqual(bot.send_message.await_count,1)
                await send_due(store,bot,datetime(2026,9,30,14,tzinfo=timezone.utc))
                self.assertEqual(bot.send_message.await_count,3)
                self.assertEqual(bot.send_message.call_args.args[1],'Review 2026-09')
                await send_due(store,bot,datetime(2026,9,30,15,tzinfo=timezone.utc))
                self.assertEqual(bot.send_message.await_count,3)
                store.delete_schedule(1,first)
                self.assertNotEqual(store.save_schedule(1,base),first)
            finally:
                store.close()
