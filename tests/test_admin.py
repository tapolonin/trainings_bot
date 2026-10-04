import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.methods import SendMessage, SendDocument
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from trainings_bot.admin import parse_admin_ids
from trainings_bot.conversation import Conversation
from trainings_bot.__main__ import make_dispatcher
from trainings_bot.storage import Store

PROFILE = dict(first_name='Target',last_name='Trainer',sparte='RSG',iban='DE89370400440532013000',
               rate=2000,timezone='Europe/Berlin',reminder_time='20:00')


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'bot.sqlite3')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        self.store.setup(1,dict(PROFILE,first_name='Admin'),['Admin gym'])
        self.store.setup(2,PROFILE,['Target gym'])
        self.store.save_training(2,dict(day='2020-08-01',gym='Target gym',start='09:00',end='11:00'),'target-entry')
        self.store.no_training(2,'2020-08-02')
        self.app = Conversation(self.store,admin_ids={1})
        self.app.handle(2,'Log day')
        self.before = self.snapshot()

    def snapshot(self):
        result = {}
        for table in ('profiles','rates','gyms','trainings','day_logs','schedules','conversations'):
            result[table] = [tuple(r) for r in self.store.db.execute(f'SELECT * FROM {table} WHERE user_id=2')]
        return result

    def open_user(self):
        menu = self.app.handle(1,'Admin')
        return self.app.handle(1,next(b for b in menu.buttons if b.endswith(' · 2')))

    def open_month(self):
        self.open_user()
        reply = self.app.handle(1,'Training calendar')
        choose = next(d for row in reply.calendar for _,d in row if d.endswith(':choosemonth'))
        self.app.admin_action(1,choose)
        return self.app.handle(1,'2020-08')

    def test_visibility_access_and_config(self):
        self.assertEqual(parse_admin_ids('1, 2'),frozenset({1,2}))
        self.assertEqual(parse_admin_ids(''),frozenset())
        for value in ('all','1,','-1','0'):
            with self.assertRaises(ValueError):
                parse_admin_ids(value)
        self.assertIn('Admin',self.app.handle(1,'/start').buttons)
        self.app.handle(2,'Cancel')
        self.assertNotIn('Admin',self.app.handle(2,'/start').buttons)
        self.assertIn('not enabled',self.app.handle(2,'/admin').text)
        self.assertIn('not enabled',self.app.handle(3,'Admin').text)
        self.assertIn('3',self.app.handle(3,'/myid').text)
        disabled = Conversation(self.store)
        self.assertIn('not enabled',disabled.handle(1,'/admin').text)

    def test_views_calendar_pdf_request_and_no_target_writes(self):
        reply = self.open_user()
        self.assertIn('Target Trainer',reply.text)
        self.assertNotIn(PROFILE['iban'],reply.text)
        reply = self.app.handle(1,'Notification status')
        self.assertIn('Evening log reminder',reply.text)
        self.assertNotIn('Turn off',reply.buttons)
        self.app.handle(1,'Back')
        calendar = self.open_month()
        self.assertIn('40,00 €',calendar.text)
        self.assertIn('Download PDF',calendar.buttons)
        data = next(d for row in calendar.calendar for _,d in row if d.endswith(':2020-08-01'))
        reply = self.app.admin_action(1,data)
        self.assertIn('Target gym',reply.text)
        self.assertIn('09:00–11:00',reply.text)
        self.assertNotIn('Edit training',reply.buttons)
        self.assertIn('viewing option',self.app.handle(1,'Delete training').text)
        reply = self.app.handle(1,'Download PDF')
        self.assertEqual((reply.report_user_id,reply.report_month),(2,'2020-08'))
        self.assertEqual(self.snapshot(),self.before)

    def test_permission_revocation_stale_callbacks_and_cancel(self):
        calendar = self.open_month()
        data = next(d for row in calendar.calendar for _,d in row if d.endswith(':choosemonth'))
        reply = self.app.admin_action(1,data)
        self.assertEqual(reply.buttons,['Cancel'])
        reply = self.app.handle(1,'Cancel')
        self.assertIn('2020-08',reply.text)
        with self.assertRaises(ValueError):
            self.app.admin_action(1,data)
        fresh = reply.calendar[0][0][1]
        with self.assertRaises(ValueError):
            self.app.admin_action(2,fresh)
        revoked = Conversation(self.store)
        self.assertIn('not enabled',revoked.handle(1,'Download PDF').text)
        with self.assertRaises(ValueError):
            revoked.admin_action(1,fresh)
        self.assertEqual(self.snapshot(),self.before)

    def test_user_pagination_and_admin_without_trainer_profile(self):
        for uid in range(3,12):
            self.store.setup(uid,dict(PROFILE,first_name=f'Person {uid}'),[])
        app = Conversation(self.store,admin_ids={99})
        reply = app.handle(99,'/admin')
        self.assertIn('Next users',reply.buttons)
        reply = app.handle(99,'Next users')
        self.assertIn('Previous users',reply.buttons)
        reply = app.handle(99,next(b for b in reply.buttons if b.endswith(' · 11')))
        reply = app.handle(99,'Training calendar')
        app.admin_action(99,next(d for row in reply.calendar for _,d in row if d.endswith(':choosemonth')))
        self.assertTrue(app.handle(99,'Cancel').calendar)
        self.assertIsNone(self.store.profile(99))


class AdminDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_target_pdf_goes_only_to_admin_and_forged_buttons_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'bot.sqlite3')
            for uid in (1,2):
                store.setup(uid,dict(PROFILE,first_name=f'User {uid}'),[])
            store.save_training(2,dict(day='2020-08-01',gym='Target gym',start='09:00',end='10:00'),'pdf')
            app = Conversation(store,admin_ids={1})
            dispatcher = make_dispatcher(app)
            bot = Bot('123456789:local_test_token_only')
            methods, screens = [], []
            async def respond(method, **kwargs):
                methods.append(method)
                if isinstance(method,SendMessage):
                    msg = Message(message_id=100+len(screens),date=datetime.now(timezone.utc),chat=Chat(id=method.chat_id,type='private'),
                                  text=method.text,reply_markup=method.reply_markup if hasattr(method.reply_markup,'inline_keyboard') else None)
                    screens.append(msg)
                    return msg
                return True
            async def render(function,data):
                self.assertEqual(data['render_args']['first_name'],'User 2')
                return b'%PDF-target','User 2.pdf','20,00 €'
            try:
                app.admin().calendar(1,2,'2020-08')
                message = Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),
                                  from_user=User(id=1,is_bot=False,first_name='Admin'),text='Download PDF')
                with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=respond), patch('trainings_bot.__main__.asyncio.to_thread',side_effect=render):
                    await dispatcher.feed_update(bot,Update(update_id=1,message=message))
                    docs = [m for m in methods if isinstance(m,SendDocument)]
                    self.assertEqual(len(docs),1)
                    self.assertEqual(docs[0].chat_id,1)
                    self.assertEqual(docs[0].document.filename,'User 2.pdf')
                    calendar = screens[-1]
                    button = next(b for row in calendar.reply_markup.inline_keyboard for b in row if b.text=='Download PDF')
                    query = CallbackQuery(id='forged',from_user=User(id=2,is_bot=False,first_name='Other'),chat_instance='test',data=button.callback_data,
                                          message=calendar.model_copy(update={'chat':Chat(id=2,type='private')}))
                    await dispatcher.feed_update(bot,Update(update_id=2,callback_query=query))
                    self.assertEqual(len([m for m in methods if isinstance(m,SendDocument)]),1)
                    self.assertEqual(store.conversation(2),{})
            finally:
                await dispatcher.storage.close()
                await bot.session.close()
                store.close()
