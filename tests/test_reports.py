"""Report navigation and the database-to-renderer adapter."""
import os
import shutil
import tempfile
import unittest
from datetime import date, time
from pathlib import Path
from unittest.mock import patch

from trainings_bot.conversation import Conversation
from trainings_bot.reports import make_document, report_data
from trainings_bot.storage import Store


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)
        self.app = Conversation(self.store, 'test-code')
        for module in ('conversation', 'reports'):
            mock = patch(f'trainings_bot.{module}.local_today', return_value=date(2026,9,28))
            mock.start()
            self.addCleanup(mock.stop)
        self.app.handle(1,'test-code')
        for value in ['Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00','Use suggested gyms','Next']:
            self.app.handle(1,value)
        self.store.save_training(1, dict(day='2026-08-01', gym='Gym', start='09:00', end='10:30'), 'august')
        self.store.no_training(1, '2026-08-02')
        self.store.set_rate(1, '2026-09', 2500)

    def test_menu_preview_cancel_and_commands(self):
        self.assertIn('Reports', self.app.home(1).buttons)
        self.assertIn('Current month',self.app.handle(1,'Reports').buttons)
        reply = self.app.handle(1,'Previous month')
        self.assertIn('30,00 €',reply.text)
        self.assertIn('29',reply.text)
        self.assertIn('Generate PDF',reply.buttons)
        self.app.handle(1,'Choose month')
        self.assertEqual(self.app.handle(1,'wrong').buttons,['Cancel'])
        self.assertIn('2026-08',self.app.handle(1,'Cancel').text)
        reply = self.app.handle(1,'Generate PDF')
        self.assertEqual(reply.report_month,'2026-08')
        self.assertNotIn('Cancel',reply.buttons)
        self.assertIn('Current month',self.app.handle(1,'Back').buttons)
        self.assertIn('Settings',self.app.handle(1,'/settings').text)
        self.assertIn('30,00 €',self.app.handle(1,'/report 2026-08').text)
        self.assertIn('Earnings',self.app.handle(1,'/earnings 2026-08').text)

    def test_empty_future_and_private(self):
        self.assertNotIn('Generate PDF',self.app.handle(1,'/report 2026-09').buttons)
        self.assertEqual(self.app.handle(1,'Generate PDF').report_month,'')
        self.assertIn('earlier month',self.app.handle(1,'/report 2026-10').text)
        self.assertIn('first name',self.app.handle(2,'/report 2026-08').text)
        with self.assertRaises(ValueError):
            report_data(self.store,2,'2026-08')
        with self.assertRaises(ValueError):
            make_document(report_data(self.store,1,'2026-09'))

    def test_snapshot_uses_month_rate_and_current_profile(self):
        self.store.update_profile(1,'first_name','New')
        data = report_data(self.store,1,'2026-08')
        args = data['render_args']
        self.assertEqual(args['rate'],20)
        self.assertEqual(args['first_name'],'New')
        self.assertEqual(args['entries'],[dict(date=date(2026,8,1),gym='Gym',start=time(9),end=time(10,30))])
        self.assertEqual(data['missing'],29)
        self.assertEqual(data['amount'],30)
        self.assertEqual(report_data(self.store,1,'2026-09')['missing'],28)
        folders = []
        def fake_render(folder, **kwargs):
            folders.append(Path(folder))
            self.assertEqual(kwargs,args)
            path = Path(folder)/'result.pdf'
            path.write_bytes(b'%PDF-test')
            return path, {}
        with patch('trainings_bot.reports.render', side_effect=fake_render):
            content,filename,caption = make_document(data)
        self.assertEqual(content,b'%PDF-test')
        self.assertEqual(filename,'2026-08 Trainer, New.pdf')
        self.assertIn('30,00 €',caption)
        self.assertFalse(folders[0].exists())
        data['render_args']['last_name']='../../outside'
        with self.assertRaises(ValueError):
            make_document(data)

    def test_converter_failure_cleans_temporary_files(self):
        folders = []
        def fail(folder, **kwargs):
            folders.append(Path(folder))
            raise OSError('converter missing')
        with patch('trainings_bot.reports.render', side_effect=fail), self.assertRaises(OSError):
            make_document(report_data(self.store,1,'2026-08'))
        self.assertFalse(folders[0].exists())

    @unittest.skipUnless(os.environ.get('RUN_PDF_TESTS') == '1' and shutil.which('soffice'),
                         'Set RUN_PDF_TESTS=1 with LibreOffice installed')
    def test_saved_logs_to_real_pdf(self):
        from io import BytesIO
        from pypdf import PdfReader
        content, name, caption = make_document(report_data(self.store,1,'2026-08'))
        pdf = PdfReader(BytesIO(content))
        self.assertEqual(len(pdf.pages),2)
        text = pdf.pages[0].extract_text()
        self.assertIn('Test Trainer',text)
        self.assertIn('Gym',text)
        self.assertIn('30,00',text)
        self.assertEqual(name,'2026-08 Trainer, Test.pdf')
