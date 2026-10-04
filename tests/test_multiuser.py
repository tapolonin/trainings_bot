"""Open registration, legacy-data preservation, and isolation between real users."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage, SendDocument, DeleteMessage, EditMessageText, EditMessageReplyMarkup
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from trainings_bot.__main__ import make_dispatcher
from trainings_bot.conversation import Conversation
from trainings_bot.reminders import send_due
from trainings_bot.reports import report_data
from trainings_bot.storage import Store

PROFILE = dict(first_name='Alice',last_name='Trainer',sparte='RSG',iban='DE89370400440532013000',
               rate=2000,timezone='Europe/Berlin',reminder_time='20:00')


class MultiuserDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'bot.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda:self.store.close())
        self.app = Conversation(self.store)

    def test_interleaved_registration_and_isolated_reports_and_records(self):
        for uid in (1,2):
            self.assertIn('first name',self.app.handle(uid,'/start').text)
        values = [('Alice','Bob'),('Trainer','Coach'),('RSG','Football'),
                  (PROFILE['iban'],PROFILE['iban']),('20','30'),('Europe/Berlin','America/New_York'),
                  ('20:00','19:00'),('Gym A','Gym B'),('Next','Next')]
        for a,b in values:
            self.app.handle(1,a)
            self.app.handle(2,b)
        self.assertEqual(self.store.user_ids(),[1,2])
        for uid in (1,2):
            self.store.save_training(uid,dict(day='2020-08-01',gym=f'Gym {uid}',start='09:00',end='10:00'),f'entry-{uid}')
        a,b = report_data(self.store,1,'2020-08'),report_data(self.store,2,'2020-08')
        self.assertEqual((a['amount'],b['amount']),(20,30))
        self.assertEqual(a['render_args']['first_name'],'Alice')
        self.assertEqual(b['render_args']['first_name'],'Bob')
        self.assertEqual(a['render_args']['entries'][0]['gym'],'Gym 1')
        self.assertEqual(b['render_args']['entries'][0]['gym'],'Gym 2')
        other_entry = self.store.entries(2,'2020-08')[0]
        with self.assertRaises(ValueError):
            self.store.delete_training(1,other_entry['id'])
        with self.assertRaises(ValueError):
            self.store.edit_training(1,other_entry['id'],dict(day='2020-08-02',gym='Stolen',start='09:00',end='10:00'))
        other_schedule = self.store.schedules(2)[0]
        with self.assertRaises(ValueError):
            self.store.save_schedule(1,other_schedule)
        gym = self.store.gyms(2)[0]
        self.store.change_gym(1,gym['id'],'Wrong')
        self.assertEqual(self.store.gyms(2)[0]['name'],gym['name'])
        calendar1 = self.app.handle(1,'Calendar')
        self.app.handle(2,'Calendar')
        with self.assertRaises(ValueError):
            self.app.calendar_action(2,calendar1.calendar[0][0][1])
        self.assertEqual(len(self.store.entries(2,'2020-08')),1)

    def test_legacy_owner_keeps_data_while_new_user_registers(self):
        self.store.claim(1,'old-code','old-code')
        self.store.setup(1,PROFILE,['Old Gym'])
        self.store.save_training(1,dict(day='2020-08-01',gym='Old Gym',start='09:00',end='10:00'),'legacy')
        self.store.close()
        self.store = Store(self.path)
        self.app = Conversation(self.store)
        self.assertIn('Welcome back',self.app.handle(1,'/start').text)
        self.assertIn('first name',self.app.handle(2,'/start').text)
        self.assertEqual(self.store.profile(1)['first_name'],'Alice')
        self.assertEqual(len(self.store.entries(1,'2020-08')),1)
        self.assertEqual(self.store.entries(2,'2020-08'),[])
        self.app.handle(2,'Cancel')
        self.assertIn('first name',self.app.handle(2,'/start').text)


class MultiuserTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'bot.sqlite3')
        for uid in (1,2):
            self.store.setup(uid,dict(PROFILE,first_name=f'User {uid}'),[f'Gym {uid}'])
        self.dispatcher = make_dispatcher(Conversation(self.store))
        self.bot = Bot('123456789:local_test_token_only')
        self.counter = 0
        self.ids = {1:100,2:100}  # Telegram message IDs can coincide across private chats.
        self.screens = {}
        self.methods = []

    async def asyncTearDown(self):
        await self.bot.session.close()
        await self.dispatcher.storage.close()
        self.store.close()
        self.tmp.cleanup()

    async def respond(self, method, **kwargs):
        self.methods.append(method)
        await asyncio.sleep(0)  # Force interleaving of the two users' requests.
        if isinstance(method,SendMessage):
            uid = method.chat_id
            self.ids[uid] += 1
            msg = Message(message_id=self.ids[uid],date=datetime.now(timezone.utc),chat=Chat(id=uid,type='private'),
                          text=method.text,reply_markup=method.reply_markup if hasattr(method.reply_markup,'inline_keyboard') else None)
            if msg.reply_markup:
                self.screens[uid]=msg
            return msg
        return True

    async def feed(self, uid, text=None, data=None, message=None):
        self.counter += 1
        user = User(id=uid,is_bot=False,first_name='Test')
        if text is not None:
            update = Update(update_id=self.counter,message=Message(message_id=self.counter,date=datetime.now(timezone.utc),
                            chat=Chat(id=uid,type='private'),from_user=user,text=text))
        else:
            update = Update(update_id=self.counter,callback_query=CallbackQuery(id=str(self.counter),from_user=user,
                            chat_instance='test',message=message or self.screens[uid],data=data))
        await self.dispatcher.feed_update(self.bot,update)

    def button(self, uid, label):
        return next(b.callback_data for row in self.screens[uid].reply_markup.inline_keyboard for b in row if b.text==label)

    async def test_concurrent_menus_callbacks_cleanup_and_token_retention(self):
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=self.respond):
            await asyncio.gather(self.feed(1,text='/start'),self.feed(2,text='/start'))
            one_button = self.button(1,'Log day')
            two_button = self.button(2,'Settings')
            await asyncio.gather(self.feed(1,data=one_button),self.feed(2,data=two_button))
            self.assertEqual(self.store.conversation(1)['flow'],'day_choice')
            self.assertEqual(self.store.conversation(2)['flow'],'settings')
            foreign = self.button(2,'First name')
            before = self.store.conversation(1)
            await self.feed(1,data=foreign)
            self.assertEqual(self.store.conversation(1),before)
            self.assertEqual(self.store.conversation(2)['flow'],'settings')
            self.methods.clear()
            await self.feed(1,text='Cancel')
            self.assertTrue(all(m.chat_id==1 for m in self.methods if isinstance(m,(DeleteMessage,EditMessageText,EditMessageReplyMarkup))))
            # Simulate user 2 already having many button mappings; user 1's next
            # menu must prune only user 1's rows.
            with self.store.db:
                for i in range(110):
                    self.store.db.execute('INSERT INTO ui_actions VALUES (?,?,?,?)',(f'test-{i}',2,'test','[]'))
            await self.feed(1,text='Settings')
            self.assertIsNotNone(self.store.db.execute('SELECT 1 FROM ui_actions WHERE token=?',(foreign.split(':')[1],)).fetchone())
            await self.feed(2,data=foreign)
            self.assertEqual(self.store.conversation(2)['flow'],'setting')
            self.assertEqual(self.store.conversation(2)['key'],'first_name')

    async def test_reminders_all_users_and_failure_isolation(self):
        self.store.update_profile(2,'timezone','America/New_York')
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)))
        now = datetime(2026,9,28,18,tzinfo=timezone.utc)
        await send_due(self.store,bot,now, refresh_ui=False)
        self.assertEqual([c.args[0] for c in bot.send_message.call_args_list],[1])
        await send_due(self.store,bot,datetime(2026,9,29,0,tzinfo=timezone.utc), refresh_ui=False)
        self.assertEqual([c.args[0] for c in bot.send_message.call_args_list],[1,2])
        self.store.update_profile(2,'timezone','Europe/Berlin')
        async def blocked(uid, text, **kwargs):
            if uid==1:
                raise TelegramForbiddenError(method=SendMessage(chat_id=uid,text=text),message='blocked')
            return SimpleNamespace(message_id=901)
        bot.send_message.side_effect=blocked
        with self.assertLogs('trainings_bot.reminders',level='WARNING'):
            await send_due(self.store,bot,datetime(2026,9,29,18,tzinfo=timezone.utc), refresh_ui=False)
        self.assertEqual(self.store.notification(2,'evening','2026-09-29')['message_id'],901)
        self.assertIsNone(self.store.notification(1,'evening','2026-09-29')['message_id'])

    async def test_concurrent_pdf_requests_use_own_data_and_serialize_rendering(self):
        for uid in (1,2):
            self.store.save_training(uid,dict(day='2020-08-01',gym=f'Gym {uid}',start='09:00',end='10:00'),f'pdf-{uid}')
        self.store.set_rate(2,'0001-01',3000)
        active, peak = 0, 0
        async def render_worker(function, data):
            nonlocal active, peak
            active += 1
            peak = max(peak,active)
            await asyncio.sleep(0)
            name = data['render_args']['first_name']
            active -= 1
            return name.encode(),f'{name}.pdf',str(data['amount'])
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=self.respond):
            await asyncio.gather(self.feed(1,text='/report 2020-08'),self.feed(2,text='/report 2020-08'))
            first,second = self.button(1,'Generate PDF'),self.button(2,'Generate PDF')
            with patch('trainings_bot.__main__.asyncio.to_thread',side_effect=render_worker):
                await asyncio.gather(self.feed(1,data=first),self.feed(2,data=second))
        documents = {m.chat_id:m for m in self.methods if isinstance(m,SendDocument)}
        self.assertEqual(set(documents),{1,2})
        self.assertEqual(documents[1].document.data,b'User 1')
        self.assertEqual(documents[2].document.data,b'User 2')
        self.assertEqual(documents[1].caption,'20')
        self.assertEqual(documents[2].caption,'30')
        self.assertEqual(peak,1)
