import tempfile
import unittest
from pathlib import Path

from trainings_bot.conversation import Conversation
from trainings_bot.storage import Store


class SetupGymsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'bot.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda:self.store.close())
        self.app = Conversation(self.store)

    def reach_gyms(self, uid=1):
        for text in ['/start','Test','Trainer','RSG','DE89370400440532013000','20','Europe/Berlin','20:00']:
            reply = self.app.handle(uid,text)
        return reply

    def test_add_several_remove_and_finish_explicitly(self):
        self.assertIn('Next',self.reach_gyms().buttons)
        self.app.handle(1,'Add gym')
        reply = self.app.handle(1,'Gym A; Gym B; Gym A')
        self.assertIsNone(self.store.profile(1))
        self.assertEqual(self.store.conversation(1)['gym_draft'],['Gym A','Gym B'])
        self.assertIn('Gym B',reply.text)
        self.app.handle(1,'Remove gym')
        reply = self.app.handle(1,'Remove: Gym A')
        self.assertTrue(reply.replace_screen)
        self.app.handle(1,'Done')
        self.app.handle(1,'Gym C')
        reply = self.app.handle(1,'Next')
        self.assertIn('Setup complete',reply.text)
        self.assertEqual([g['name'] for g in self.store.gyms(1)],['Gym B','Gym C'])

    def test_restart_and_two_users_have_separate_draft_lists(self):
        for uid,gym in [(1,'Gym A'),(2,'Gym B')]:
            self.reach_gyms(uid)
            self.app.handle(uid,gym)
        self.store.close()
        self.store = Store(self.path)
        self.app = Conversation(self.store)
        self.app.handle(1,'Remove gym')
        self.app.handle(1,'Remove: Gym A')
        self.app.handle(1,'Next')
        self.app.handle(2,'Next')
        self.assertEqual(self.store.gyms(1),[])
        self.assertEqual([g['name'] for g in self.store.gyms(2)],['Gym B'])

    def test_suggestions_can_be_changed_and_cancel_saves_no_profile(self):
        self.reach_gyms()
        self.app.handle(1,'Use suggested gyms')
        self.assertIsNone(self.store.profile(1))
        self.app.handle(1,'Remove gym')
        self.app.handle(1,'Remove: Herschelschule')
        self.app.handle(1,'Cancel')
        self.assertIsNone(self.store.profile(1))
        self.assertEqual(self.store.gyms(1),[])
        self.assertEqual(self.store.conversation(1),{})

    def test_limit_and_invalid_batch_do_not_change_draft(self):
        self.reach_gyms()
        self.app.handle(1,'Gym A')
        self.app.handle(1,'Gym B;;Gym C')
        self.assertEqual(self.store.conversation(1)['gym_draft'],['Gym A'])
        self.app.handle(1,';'.join(f'Gym {i}' for i in range(29)))
        self.assertIn('at most 30',self.app.handle(1,'Overflow').text)
        self.assertEqual(len(self.store.conversation(1)['gym_draft']),30)

    def test_setup_suggestions_follow_current_step(self):
        for text in ['/start','Test','Trainer']:
            reply = self.app.handle(1,text)
        self.assertEqual(reply.buttons,['RSG','Cancel'])
        self.app.handle(1,'RSG')
        self.app.handle(1,'DE89370400440532013000')
        reply = self.app.handle(1,'20')
        self.assertEqual(reply.buttons,['Europe/Berlin','Cancel'])
        reply = self.app.handle(1,'Europe/Berlin')
        self.assertEqual(reply.buttons,['20:00','Cancel'])
