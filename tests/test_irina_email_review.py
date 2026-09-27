import json
import os
from unittest.mock import patch
import unittest
import test_irina_email as fixtures
EID = fixtures.EID
from irina_email_review import reconcile

class ReviewTests(unittest.TestCase):
    setUp = fixtures.EmailTests.setUp
    window = fixtures.EmailTests.window
    state = fixtures.EmailTests.state
    def spec(self):
        self.api.email['authentication']={'spf':'gray','dkim':'gray','dmarc':'fail'}
        self.api.email['message_id']='<example@example.com>'
        self.reader.step()
        return {'id':EID,'sender':'fred@example.com','message_id':'<example@example.com>',
                'subject':self.api.email['subject'],'project':'off_class',
                'context':'Off Class; Future 101 | Octopus Organization'}
    def test_exact_confirmation_preserves_auth_and_sends_no_notice(self):
        spec=self.spec()
        with patch.dict(os.environ, {'IRINA_EMAIL_CONFIRMATIONS':json.dumps([spec])}):
            reconcile(self.reader)
            reconcile(self.reader)
        row=self.inbox.lookup('resend:'+EID)
        self.assertEqual(row['actor'],'email_owner_reviewed')
        self.assertEqual(row['project'],'off_class')
        self.assertEqual(json.loads(row['payload'])['email']['authentication']['dmarc'],'fail')
        self.assertEqual(len(self.p.ai.calls),1)
        self.assertEqual(len(self.p.archive.saved),2)
        self.assertFalse(self.p.whatsapp.sent)
        self.assertEqual(self.inbox.owner_last_seen(self.p.primary),0)
    def test_mismatch_does_not_promote(self):
        spec=self.spec()
        spec['message_id']='<wrong@example.com>'
        with patch.dict(os.environ, {'IRINA_EMAIL_CONFIRMATIONS':json.dumps([spec])}):
            with self.assertRaises(ValueError):
                reconcile(self.reader)
        self.assertEqual(self.inbox.lookup('resend:'+EID)['actor'],'email_contact')
        self.assertFalse(self.p.ai.calls)
    def test_own_failed_auth_notice_suppressed_without_promoting(self):
        self.spec()
        self.window()
        self.reader.step()
        self.assertEqual(self.state(),'owner_notice_suppressed')
        self.assertFalse(self.p.whatsapp.sent)
        self.assertEqual(self.inbox.lookup('resend:'+EID)['actor'],'email_contact')
