import tempfile
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from irina_inbox import Inbox
import irina_digest_admin as admin
from irina_digest_costs import CostBlocked


class Admin(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.p=SimpleNamespace(inbox=Inbox(tmp.name),primary='390000000001',
            whatsapp=SimpleNamespace(token='fixture',owners={'390000000001'}),writer=Mock())

    @patch('irina_digest_admin.template_json',side_effect=[{'data':[]},{'status':'PENDING'}])
    @patch('irina_digest_admin.sample_handle',return_value='fixture-handle')
    @patch('irina_digest_admin.mockup',return_value=b'fixture')
    def test_submission_and_uncertain_marker_prevent_repeat(self,pdf,handle,api):
        self.assertEqual(admin.template(self.p)['status'],'PENDING')
        payload=api.call_args.args[2]
        self.assertEqual(payload['category'],'MARKETING')
        self.assertEqual(payload['components'][0]['format'],'DOCUMENT')
        self.assertIn('PROVA',payload['components'][1]['example']['body_text'][0][0])
        api.side_effect=None;api.return_value={'data':[]}
        self.assertEqual(admin.template(self.p)['status'],'SUBMISSION_UNCERTAIN')
        handle.assert_called_once()

    @patch('irina_digest_admin.json_request',return_value={'messages':[{'id':'wamid.test'}]})
    @patch('irina_digest_admin.upload_pdf',return_value='fixture-media')
    @patch('irina_digest_admin.mockup',return_value=b'fixture')
    @patch('irina_digest_admin.approved_template',return_value={'category':'MARKETING'})
    @patch('irina_digest_admin.Budget')
    def test_test_once_is_explicitly_mockup(self,budget,approved,pdf,upload,send):
        budget.return_value.quote.return_value=(65800,'MARKETING')
        self.assertEqual(admin.test(self.p)['state'],'accepted')
        admin.test(self.p);send.assert_called_once()
        payload=send.call_args.args[2]
        self.assertEqual(payload['type'],'template')
        self.assertIn('PROVA',payload['template']['components'][0]['parameters'][0]['document']['filename'])
        self.assertIn('illustrativi',payload['template']['components'][1]['parameters'][0]['text'])
        with self.p.inbox.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM digests').fetchone()[0],0)

    @patch('irina_digest_admin.upload_pdf')
    @patch('irina_digest_admin.approved_template',return_value={'category':'MARKETING'})
    @patch('irina_digest_admin.Budget')
    def test_cost_block_never_uploads_or_sends(self,budget,approved,upload):
        budget.return_value.quote.side_effect=CostBlocked('GrossCostPolicyNotVerified')
        with self.assertRaises(CostBlocked):admin.test(self.p)
        upload.assert_not_called()

    @patch('irina_digest_admin.Budget')
    @patch('irina_digest_admin.approved_template',return_value={'category':'MARKETING'})
    def test_activation_requires_delivered_and_cost(self,approved,budget):
        with self.assertRaisesRegex(ValueError,'TestDeliveryNotVerified'):admin.activate(self.p)
        with self.p.inbox.db() as db:
            db.execute('INSERT INTO digest_tests(key,state,message_id) VALUES (?,?,?)',(admin.TEST_KEY,'accepted','wamid.fixture'))
            db.execute('INSERT INTO receipts VALUES (?,?,?)',('wamid.fixture','delivered',123))
        budget.return_value.quote.side_effect=CostBlocked('MetaRatesChanged')
        with self.assertRaises(CostBlocked):admin.activate(self.p)
        with self.p.inbox.db() as db:
            self.assertIsNone(db.execute('SELECT value FROM settings WHERE key=?',(admin.ACTIVATION,)).fetchone())
        budget.return_value.quote.side_effect=None
        self.assertTrue(admin.activate(self.p)['digest_delivery_enabled'])

    @patch('irina_digest_admin.activate')
    @patch('irina_digest_admin.test')
    @patch('irina_digest_admin.approved_template',return_value=None)
    def test_automatic_finish_waits_for_approval_and_delivered(self,approved,send,activate):
        with self.p.inbox.db() as db:
            db.execute('INSERT INTO settings VALUES (?,?)',(admin.ACTIVATION_REQUEST,json.dumps(
                {'test_file':admin.TEST_FILE,'test_sha256':admin.TEST_HASH})))
        self.assertEqual(admin.continue_activation(self.p)['state'],'waiting_for_template')
        send.assert_not_called();activate.assert_not_called()
        approved.return_value={'category':'MARKETING'}
        send.return_value={'state':'accepted','receipts':[]}
        self.assertEqual(admin.continue_activation(self.p)['state'],'waiting_for_delivery_receipt')
        activate.assert_not_called()
        send.return_value={'state':'uncertain','receipts':[]}
        self.assertEqual(admin.continue_activation(self.p)['state'],'uncertain')
        activate.assert_not_called()
        send.return_value={'state':'accepted','receipts':[{'status':'delivered'}]}
        activate.return_value={'digest_delivery_enabled':True}
        self.assertTrue(admin.continue_activation(self.p)['digest_delivery_enabled'])
        activate.assert_called_once()


if __name__=='__main__':unittest.main()
