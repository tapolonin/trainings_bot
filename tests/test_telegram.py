"""Exercise aiogram dispatch without making network requests."""
import tempfile
import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageReplyMarkup, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from trainings_bot.__main__ import make_dispatcher
from trainings_bot.conversation import Conversation, Reply
from trainings_bot.storage import Store


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.sqlite3')
        self.app = Conversation(self.store,'test-private-code')
        self.dispatcher = make_dispatcher(self.app)
        self.bot = Bot('123456789:local_test_token_only')
        self.counter = 0

    async def asyncTearDown(self):
        await self.bot.session.close()
        await self.dispatcher.storage.close()
        self.store.close()
        self.tmp.cleanup()

    async def send(self, text, uid=1, chat_type='private'):
        self.counter += 1
        message = Message(message_id=self.counter, date=datetime.now(timezone.utc),
                          chat=Chat(id=uid if chat_type=='private' else -100, type=chat_type),
                          from_user=User(id=uid,is_bot=False,first_name='Test'),text=text)
        with patch.object(Bot,'__call__',new_callable=AsyncMock,return_value=True) as send:
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,message=message))
            self.calls = [call.args[0] for call in send.call_args_list]
            return [call for call in self.calls if not isinstance(call, DeleteMessage) and getattr(call, "text", "") != "Opening menu…"]

    async def test_private_registration_and_commands(self):
        self.assertEqual(await self.send('/start',chat_type='group'),[])
        replies = await self.send('/start@local_bot')
        self.assertIn('first name',replies[0].text)
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        replies = await self.send('/train@local_bot')
        self.assertIn('Which day',replies[0].text)
        self.assertTrue(replies[0].reply_markup.inline_keyboard)
        replies = await self.send('/settings',uid=2)
        self.assertIn('first name',replies[0].text)

    async def test_long_logs_are_split(self):
        with patch.object(self.app,'handle',return_value=Reply('A training entry\n'*600)):
            replies = await self.send('/log')
        self.assertGreater(len(replies),1)
        self.assertTrue(all(len(r.text)<=3500 for r in replies))

    async def test_calendar_is_below_list_and_handles_taps(self):
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        replies = await self.send('View calendar')
        self.assertEqual(len(replies), 1)
        self.assertEqual([b.text for b in replies[0].reply_markup.inline_keyboard[-1]], ['Back', 'Main menu'])
        self.assertNotIn('Total earned:', replies[0].text)
        self.assertIn('Monthly calendar', replies[0].text)
        keyboard = replies[0].reply_markup.inline_keyboard
        self.assertEqual(len(keyboard[1]), 7)
        data = next(button.callback_data for row in keyboard[2:] for button in row
                    if button.text.strip() and not button.text.endswith('·'))
        self.counter += 1
        query = CallbackQuery(id='calendar-test', from_user=User(id=1,is_bot=False,first_name='Test'),
                              chat_instance='test', data=data,
                              message=Message(message_id=100,date=datetime.now(timezone.utc),
                                              chat=Chat(id=1,type='private'),text='Calendar'))
        async def result(method, **kwargs):
            if isinstance(method, SendMessage):
                return Message(message_id=200, date=datetime.now(timezone.utc),
                               chat=Chat(id=1,type='private'), text=method.text)
            return True
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=result) as send:
            await self.dispatcher.feed_update(self.bot, Update(update_id=self.counter,callback_query=query))
        calls = [call.args[0] for call in send.call_args_list]
        self.assertIn('Log this day', [b.text for row in calls[0].reply_markup.inline_keyboard for b in row])
        self.assertIn('Missing information', calls[0].text)
        self.assertEqual(calls[1].callback_query_id, 'calendar-test')
        self.assertEqual(self.store.conversation(1)['flow'], 'history')
        self.assertEqual(self.store.conversation(1)['detail_message_id'], 200)

        # Browse a different past day without cancelling or sending another message.
        from datetime import date, timedelta
        first_day = date.fromisoformat(data.rsplit(':',1)[-1])
        second_day = first_day + timedelta(days=1)
        with patch('trainings_bot.conversation.local_today', return_value=second_day):
            query = query.model_copy(update={'data':data.rsplit(':',1)[0] + ':' + second_day.isoformat()})
            self.counter += 1
            with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=result) as send:
                await self.dispatcher.feed_update(self.bot, Update(update_id=self.counter,callback_query=query))
        calls = [call.args[0] for call in send.call_args_list]
        self.assertIsInstance(calls[0], EditMessageText)
        self.assertEqual(calls[0].message_id, 200)
        self.assertIn(second_day.isoformat(), calls[0].text)
        self.assertFalse(any(isinstance(call,SendMessage) for call in calls))

    async def test_month_arrows_edit_current_message_and_recover_from_failure(self):
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        replies = await self.send('View trainings')
        data = replies[0].reply_markup.inline_keyboard[0][2].callback_data
        before = self.store.conversation(1)
        query = CallbackQuery(id='next-month', from_user=User(id=1,is_bot=False,first_name='Test'),
                              chat_instance='test', data=data,
                              message=Message(message_id=100,date=datetime.now(timezone.utc),
                                              chat=Chat(id=1,type='private'),text=replies[0].text))
        with patch.object(Bot,'__call__',new_callable=AsyncMock,return_value=True) as send:
            self.counter += 1
            await self.dispatcher.feed_update(self.bot, Update(update_id=self.counter,callback_query=query))
        calls = [call.args[0] for call in send.call_args_list]
        edits = [call for call in calls if isinstance(call, EditMessageText)]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].message_id, 100)
        self.assertNotIn('Total earned:', edits[0].text)
        self.assertIn('Monthly calendar', edits[0].text)
        self.assertFalse(any(isinstance(call, SendMessage) for call in calls))
        self.assertNotEqual(self.store.conversation(1)['month'], before['month'])

        query = query.model_copy(update={'data':edits[0].reply_markup.inline_keyboard[0][0].callback_data})
        before = self.store.conversation(1)
        async def fail_edit(method, **kwargs):
            if isinstance(method, EditMessageText):
                raise TelegramBadRequest(method=method,message='message cannot be edited')
            return True
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=fail_edit) as send:
            self.counter += 1
            await self.dispatcher.feed_update(self.bot, Update(update_id=self.counter,callback_query=query))
        self.assertEqual(self.store.conversation(1), before)
        self.assertFalse(any(isinstance(call.args[0], SendMessage) for call in send.call_args_list))

    async def test_leaving_or_replacing_calendar_removes_old_buttons(self):
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        for action in ['Cancel', 'Main menu', 'View calendar', '/train']:
            await self.send('View calendar')
            state = self.store.conversation(1)
            state.update(calendar_message_id=100, detail_message_id=200)
            self.store.save_conversation(1, state)
            calls = await self.send(action)
            removed = [c for c in self.calls if isinstance(c, DeleteMessage)]
            self.assertTrue({100,200}.issubset({c.message_id for c in removed}))
            await self.send('/cancel')

    async def test_choose_month_closes_calendar_and_stale_tap_removes_buttons(self):
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        replies = await self.send('View calendar')
        data = next(b.callback_data for row in replies[0].reply_markup.inline_keyboard for b in row if b.text == 'Choose month')
        query = CallbackQuery(id='choose-month', from_user=User(id=1,is_bot=False,first_name='Test'),
                              chat_instance='test', data=data,
                              message=Message(message_id=100,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),text='Calendar'))
        for _ in range(2):
            self.counter += 1
            with patch.object(Bot,'__call__',new_callable=AsyncMock,return_value=True) as send:
                await self.dispatcher.feed_update(self.bot, Update(update_id=self.counter,callback_query=query))
            removed = [call.args[0] for call in send.call_args_list if isinstance(call.args[0],DeleteMessage)]
            self.assertEqual(len(removed),1)
            self.assertEqual(removed[0].message_id,100)

    async def test_cleanup_keeps_current_screen_and_training_data(self):
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        self.store.save_training(1,dict(day='2026-09-01',gym='Gym',start='09:00',end='10:00'),'cleanup-test')
        sent_id = 1000
        async def result(method, **kwargs):
            nonlocal sent_id
            if isinstance(method, SendMessage):
                sent_id += 1
                return Message(message_id=sent_id,date=datetime.now(timezone.utc),
                               chat=Chat(id=1,type='private'),text=method.text)
            return True
        previous_ids = set()
        for text in ['Calendar', 'Earnings', 'Main menu']:
            self.counter += 1
            incoming = Message(message_id=self.counter,date=datetime.now(timezone.utc),
                               chat=Chat(id=1,type='private'),from_user=User(id=1,is_bot=False,first_name='Test'),text=text)
            with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=result) as calls:
                await self.dispatcher.feed_update(self.bot, Update(update_id=self.counter,message=incoming))
            deleted = {c.args[0].message_id for c in calls.call_args_list if isinstance(c.args[0],DeleteMessage)}
            current_ids = set(self.store.message_ids(1,1))
            self.assertTrue(previous_ids.issubset(deleted))
            self.assertIn(incoming.message_id, deleted)
            self.assertTrue(current_ids.isdisjoint(deleted))
            self.assertEqual(len(current_ids), 1)
            self.assertEqual(len(self.store.entries(1,'2026-09')),1)
            previous_ids = current_ids

    async def test_inline_actions_navigation_stale_buttons_and_restart(self):
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        sent_id = 2000
        screens = []
        async def result(method, **kwargs):
            nonlocal sent_id
            if isinstance(method, SendMessage):
                sent_id += 1
                msg = Message(message_id=sent_id,date=datetime.now(timezone.utc),
                              chat=Chat(id=1,type='private'),text=method.text,
                              reply_markup=method.reply_markup if hasattr(method.reply_markup,'inline_keyboard') else None)
                screens.append(msg)
                return msg
            return True
        async def click(msg, label, uid=1):
            data = next(b.callback_data for row in msg.reply_markup.inline_keyboard for b in row if b.text==label)
            self.assertLessEqual(len(data.encode()),64)
            self.counter += 1
            query = CallbackQuery(id=str(self.counter),from_user=User(id=uid,is_bot=False,first_name='Test'),
                                  chat_instance='test',data=data,message=msg)
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,callback_query=query))
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=result):
            self.counter += 1
            incoming = Message(message_id=self.counter,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),
                               from_user=User(id=1,is_bot=False,first_name='Test'),text='/start')
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,message=incoming))
            home = screens[-1]
            await click(home,'Log day',uid=2)
            self.assertEqual(self.store.conversation(1),{})
            await click(home,'Log day')
            date_screen = screens[-1]
            await click(date_screen,'Today')
            status_screen = screens[-1]
            before = self.store.conversation(1)
            await click(date_screen,'Other day')
            self.assertEqual(self.store.conversation(1),before)
            await self.dispatcher.storage.close()
            self.dispatcher = make_dispatcher(self.app)
            await click(status_screen,'Cancel')
            self.assertEqual(self.store.conversation(1),{})
            self.assertIn('Calendar',[b.text for row in screens[-1].reply_markup.inline_keyboard for b in row])

    async def test_report_delivery_retry_and_document_survives_navigation(self):
        from aiogram.methods import SendDocument, AnswerCallbackQuery
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        self.store.save_training(1,dict(day='2020-08-01',gym='Gym',start='09:00',end='10:00'),'report-entry')
        sent_id = 3000
        screens, documents, methods = [], [], []
        fail_delivery = False
        async def result(method, **kwargs):
            nonlocal sent_id
            methods.append(method)
            if isinstance(method,(SendMessage,SendDocument)):
                if isinstance(method,SendDocument) and fail_delivery:
                    raise TelegramBadRequest(method=method,message='test upload failure')
                sent_id += 1
                msg = Message(message_id=sent_id,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),
                              text=getattr(method,'text',None),
                              reply_markup=method.reply_markup if hasattr(method.reply_markup,'inline_keyboard') else None)
                (documents if isinstance(method,SendDocument) else screens).append(msg)
                return msg
            return True
        async def feed(text=None, screen=None, label=None):
            self.counter += 1
            if text:
                msg = Message(message_id=self.counter,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),
                              from_user=User(id=1,is_bot=False,first_name='Test'),text=text)
                update = Update(update_id=self.counter,message=msg)
            else:
                data = next(b.callback_data for row in screen.reply_markup.inline_keyboard for b in row if b.text==label)
                query = CallbackQuery(id=str(self.counter),from_user=User(id=1,is_bot=False,first_name='Test'),
                                      chat_instance='test',data=data,message=screen)
                update = Update(update_id=self.counter,callback_query=query)
            await asyncio.wait_for(self.dispatcher.feed_update(self.bot,update), timeout=10)
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=result):
            await feed(text='/report 2020-08')
            with patch('trainings_bot.__main__.make_document',side_effect=OSError('converter missing')):
                with self.assertLogs('trainings_bot.__main__',level='ERROR'):
                    await feed(screen=screens[-1],label='Generate PDF')
            self.assertIn('Could not create',screens[-1].text)
            fail_delivery = True
            with patch('trainings_bot.__main__.make_document',return_value=(b'%PDF-test','2020-08 Trainer, Test.pdf','20,00 €')):
                await feed(screen=screens[-1],label='Generate PDF')
                self.assertIn('Could not send',screens[-1].text)
                fail_delivery = False
                methods.clear()
                preview = screens[-1]
                await feed(screen=preview,label='Generate PDF')
                self.assertIsInstance(methods[0],AnswerCallbackQuery)
                document = next(m for m in methods if isinstance(m,SendDocument))
                self.assertEqual(document.document.filename,'2020-08 Trainer, Test.pdf')
                self.assertEqual(document.document.data,b'%PDF-test')
                self.assertIn('PDF sent',screens[-1].text)
                await feed(screen=preview,label='Generate PDF')
                self.assertEqual(len(documents),1)
            doc_id = documents[0].message_id
            self.assertNotIn(doc_id,self.store.message_ids(1,1))
            await feed(screen=screens[-1],label='Main menu')
            deleted = {m.message_id for m in methods if isinstance(m,DeleteMessage)}
            self.assertNotIn(doc_id,deleted)
            self.assertEqual(len(self.store.entries(1,'2020-08')),1)

    async def test_month_reminder_opens_preview_without_generating_or_losing_daily_button(self):
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        from aiogram.methods import SendDocument
        await self.send('/start')
        for text in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(text)
        self.store.save_training(1,dict(day='2020-08-01',gym='Gym',start='09:00',end='10:00'),'notice-entry')
        for kind,period in [('evening','2020-08-31'),('month_end','2020-08')]:
            self.store.defer_notification(1,kind,period,0)
            self.store.mark_notification(1,kind,period,900)
        msg = Message(message_id=900,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),text='Reminder',
                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                          [InlineKeyboardButton(text='Log this day',callback_data='notice:evening:2020-08-31')],
                          [InlineKeyboardButton(text='Review monthly report',callback_data='notice:month_end:2020-08')]]))
        query = CallbackQuery(id='reminder',from_user=User(id=1,is_bot=False,first_name='Test'),
                              chat_instance='test',data='notice:month_end:2020-08',message=msg)
        with patch.object(Bot,'__call__',new_callable=AsyncMock,return_value=True) as calls:
            self.counter += 1
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,callback_query=query))
        methods = [c.args[0] for c in calls.call_args_list]
        self.assertFalse(any(isinstance(m,SendDocument) for m in methods))
        preview = next(m for m in methods if isinstance(m,SendMessage))
        self.assertIn('20,00 €',preview.text)
        self.assertIn('Generate PDF',[b.text for row in preview.reply_markup.inline_keyboard for b in row])
        self.assertFalse(any(isinstance(m,EditMessageReplyMarkup) for m in methods))
        self.assertFalse(any(isinstance(m,DeleteMessage) and m.message_id==900 for m in methods))
        self.app.handle(1,'Main menu')
        self.app.handle(1,'Log day')
        before = self.store.conversation(1)
        with patch.object(Bot,'__call__',new_callable=AsyncMock,return_value=True) as calls:
            self.counter += 1
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,callback_query=query))
        self.assertEqual(self.store.conversation(1),before)
        self.assertEqual(len(calls.call_args_list),1)
        self.assertTrue(calls.call_args.args[0].show_alert)

    async def real_result(self, method, **kwargs):
        self.real_methods.append(method)
        if isinstance(method,SendMessage):
            self.real_id += 1
            msg = Message(message_id=self.real_id,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),
                          text=method.text,reply_markup=method.reply_markup if hasattr(method.reply_markup,'inline_keyboard') else None)
            self.real_screens.append(msg)
            return msg
        return True

    async def real_feed(self, text=None, screen=None, label=None):
        self.counter += 1
        if text is not None:
            msg = Message(message_id=self.counter,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'),
                          from_user=User(id=1,is_bot=False,first_name='Test'),text=text)
            update = Update(update_id=self.counter,message=msg)
        else:
            data = next(b.callback_data for row in screen.reply_markup.inline_keyboard for b in row if b.text==label)
            query = CallbackQuery(id=str(self.counter),from_user=User(id=1,is_bot=False,first_name='Test'),
                                  chat_instance='test',data=data,message=screen)
            update = Update(update_id=self.counter,callback_query=query)
        await self.dispatcher.feed_update(self.bot,update)

    async def test_registration_removes_superseded_setup_messages(self):
        self.real_id, self.real_screens, self.real_methods = 4000, [], []
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=self.real_result):
            await self.real_feed(text='/start')
            await self.real_feed(text='Test')
            old_ids = {m.message_id for m in self.real_screens} | {1,2}
            # Setup and message cleanup survive a restart.
            await self.dispatcher.storage.close()
            self.dispatcher = make_dispatcher(self.app)
            await self.real_feed(text='Trainer')
        deleted = {m.message_id for m in self.real_methods if isinstance(m,DeleteMessage)}
        self.assertTrue((old_ids|{3}).issubset(deleted))
        self.assertIn('department',self.real_screens[-1].text)
        self.assertNotIn(self.real_screens[-1].message_id,deleted)

    async def test_date_picker_and_weekday_choices_edit_in_place(self):
        from datetime import date
        await self.send('/start')
        for value in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(value)
        self.real_id, self.real_screens, self.real_methods = 4000, [], []
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=self.real_result), patch('trainings_bot.conversation.local_today',return_value=date(2026,9,29)):
            await self.real_feed(text='Log day')
            await self.real_feed(screen=self.real_screens[-1],label='Other day')
            calendar = self.real_screens[-1]
            labels = [b.text for row in calendar.reply_markup.inline_keyboard for b in row]
            self.assertIn('Cancel',labels)
            self.assertNotIn('Main menu',labels)
            self.real_methods.clear()
            await self.real_feed(screen=calendar,label='‹')
            edit = next(m for m in self.real_methods if isinstance(m,EditMessageText))
            self.assertEqual(edit.message_id,calendar.message_id)
            self.assertFalse(any(isinstance(m,SendMessage) for m in self.real_methods))
            calendar = calendar.model_copy(update={'reply_markup':edit.reply_markup,'text':edit.text})
            await self.real_feed(screen=calendar,label='3')
            self.assertIn('2026-08-03',self.real_screens[-1].text)
            await self.real_feed(screen=self.real_screens[-1],label='Cancel')
            for label in ['Settings','Notifications','New notification']:
                await self.real_feed(screen=self.real_screens[-1],label=label)
            await self.real_feed(text='Weekly note')
            await self.real_feed(screen=self.real_screens[-1],label='Weekly')
            days = self.real_screens[-1]
            for label in ['○ Monday','○ Wednesday','✓ Monday']:
                self.real_methods.clear()
                await self.real_feed(screen=days,label=label)
                edit = next(m for m in self.real_methods if isinstance(m,EditMessageText))
                self.assertEqual(edit.message_id,days.message_id)
                self.assertFalse(any(isinstance(m,SendMessage) for m in self.real_methods))
                days = days.model_copy(update={'reply_markup':edit.reply_markup,'text':edit.text})
            self.assertEqual(self.store.conversation(1)['draft']['weekdays'],'2')

    async def test_reminder_delete_survives_restart_drafts_and_failure(self):
        await self.send('/start')
        for value in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(value)
        self.store.defer_notification(1,'evening','2020-08-31',0)
        self.store.mark_notification(1,'evening','2020-08-31',900)
        await self.send('Log day')
        before = self.store.conversation(1)
        await self.dispatcher.storage.close()
        self.dispatcher = make_dispatcher(self.app)
        query = CallbackQuery(id='dismiss',from_user=User(id=1,is_bot=False,first_name='Test'),chat_instance='test',
                              data='notice_delete',message=Message(message_id=900,date=datetime.now(timezone.utc),
                              chat=Chat(id=1,type='private'),text='Reminder'))
        async def fail_delete(method, **kwargs):
            if isinstance(method,DeleteMessage):
                raise TelegramBadRequest(method=method,message='message cannot be deleted')
            return True
        with patch.object(Bot,'__call__',new_callable=AsyncMock,side_effect=fail_delete) as calls:
            self.counter += 1
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,callback_query=query))
        methods = [c.args[0] for c in calls.call_args_list]
        self.assertFalse(any(isinstance(m,EditMessageReplyMarkup) for m in methods))
        self.assertIn('48 hours',methods[-1].text)
        with patch.object(Bot,'__call__',new_callable=AsyncMock,return_value=True) as calls:
            self.counter += 1
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,callback_query=query))
        self.assertEqual(calls.call_args_list[0].args[0].message_id,900)
        self.assertIsInstance(calls.call_args_list[0].args[0],DeleteMessage)
        self.assertEqual(self.store.conversation(1),before)
        self.assertEqual(self.store.notification(1,'evening','2020-08-31')['message_id'],900)
        outsider = query.model_copy(update={'from_user':User(id=2,is_bot=False,first_name='Other')})
        with patch.object(Bot,'__call__',new_callable=AsyncMock,return_value=True) as calls:
            self.counter += 1
            await self.dispatcher.feed_update(self.bot,Update(update_id=self.counter,callback_query=outsider))
        self.assertFalse(any(isinstance(c.args[0],DeleteMessage) for c in calls.call_args_list))

    async def test_navigation_never_removes_delivered_notification(self):
        await self.send('/start')
        for value in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            await self.send(value)
        self.store.defer_notification(1,'evening','2020-08-31',0)
        self.store.mark_notification(1,'evening','2020-08-31',900)
        # Even an older cleanup record must not remove the reminder.
        self.store.track_message(1,1,900)
        await self.send('Calendar')
        self.assertFalse(any(isinstance(m,DeleteMessage) and m.message_id==900 for m in self.calls))
