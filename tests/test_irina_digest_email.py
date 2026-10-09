import hashlib
import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from pypdf import PdfWriter
from irina_inbox import Inbox
from irina_digest import BASE,validate
from irina_digest_email import DigestEmail,free_quota,EmailBlocked


class Email(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.inbox=Inbox(tmp.name)
        writer=PdfWriter();writer.add_blank_page(width=595.276,height=841.89);writer.add_blank_page(width=595.276,height=841.89)
        stream=io.BytesIO();writer.write(stream);self.pdf=stream.getvalue()
        self.job={'schema':1,'edition':'2026-10-10','edition_number':2,
            'pdf_path':BASE+'/PDF/2026-10-10.pdf','pdf_sha256':hashlib.sha256(self.pdf).hexdigest(),
            'editorial_verified':True,'sources':[{'url':'https://example.org/fixture','title':'Fixture only','published_date':'2026-10-09'}]}
        self.now=validate(self.job)[1];self.reader=Mock();self.writer=Mock();self.provider=Mock()
        self.reader.files_get_metadata.return_value=SimpleNamespace(size=len(self.pdf))
        self.reader.files_download.return_value=(None,SimpleNamespace(content=self.pdf))
        self.env={'IRINA_RESEND_API_KEY':'fixture','IRINA_DIGEST_EMAIL_ENABLED':'true',
                  'IRINA_DIGEST_EMAIL_FROM':'irina@example.org','IRINA_DIGEST_EMAIL_TO':'owner@example.org'}
        self.email=DigestEmail(self.inbox,self.reader,self.writer,lambda:self.now,self.env,self.provider)
        self.email.preflight=Mock(return_value={'sender_verified':True,'zero_cost_quota_verified':True})
        self.provider.call.return_value={'id':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'}

    def row(self):
        with self.inbox.db() as db:return dict(db.execute('SELECT * FROM digest_emails').fetchone())

    def test_disabled_early_and_001_never_send(self):
        self.now-=1;self.email.process(self.job)
        self.now+=1;self.env['IRINA_DIGEST_EMAIL_ENABLED']='false';self.email.process(self.job)
        self.env['IRINA_DIGEST_EMAIL_ENABLED']='true'
        old=dict(self.job,edition='2026-10-03',pdf_path=BASE+'/PDF/2026-10-03.pdf',edition_number=1,
                 sources=[{'url':'https://example.org/fixture','title':'Fixture only','published_date':'2026-10-02'}])
        self.email.process(old);self.provider.call.assert_not_called();self.reader.files_download.assert_not_called()

    def test_same_pdf_idempotent_send_then_delivered_receipt(self):
        self.email.process(self.job);self.assertEqual(self.row()['state'],'accepted')
        payload=self.provider.call.call_args.args[1]
        self.assertEqual(payload['to'],['owner@example.org']);self.assertNotIn('cc',payload)
        import base64
        self.assertEqual(base64.b64decode(payload['attachments'][0]['content']),self.pdf)
        self.assertTrue(self.provider.call.call_args.kwargs['idempotency'].startswith('controluce-2026-10-10-'))
        self.provider.call.return_value={'to':['owner@example.org'],'last_event':'delivered'}
        self.email.process(self.job);self.assertEqual(self.row()['state'],'delivered')
        self.email.process(self.job)
        self.assertEqual(sum(len(c.args)>1 for c in self.provider.call.call_args_list),1)

    def test_uncertain_crash_never_retries(self):
        self.provider.call.side_effect=TimeoutError;self.email.process(self.job);self.email.process(self.job)
        self.assertEqual(self.row()['state'],'uncertain');self.provider.call.assert_called_once()
        with self.inbox.db() as db:db.execute("UPDATE digest_emails SET state='sending'")
        recovered=DigestEmail(self.inbox,self.reader,self.writer,lambda:self.now,self.env,self.provider)
        recovered.process(self.job);self.assertEqual(self.row()['state'],'uncertain');self.provider.call.assert_called_once()

    def test_bad_hash_unknown_cost_and_sender_block_before_post(self):
        self.email.preflight.return_value={'sender_verified':True,'zero_cost_quota_verified':False}
        self.email.process(self.job);self.assertEqual(self.row()['state'],'blocked')
        self.reader.files_download.assert_not_called();self.provider.call.assert_not_called()
        self.email.preflight.return_value={'sender_verified':False,'zero_cost_quota_verified':True}
        self.email.process(self.job);self.provider.call.assert_not_called()

    def test_changed_manifest_or_target_conflicts(self):
        self.email.process(self.job)
        with self.assertRaisesRegex(EmailBlocked,'EmailEditionConflict'):self.email.process(dict(self.job,pdf_sha256='0'*64))
        self.env['IRINA_DIGEST_EMAIL_TO']='someoneelse@example.org'
        with self.assertRaises(EmailBlocked):self.email.process(self.job)

    def test_only_free_tier_available_quota_is_accepted(self):
        usage={'emails':{'daily':{'used':5,'limit':100},'monthly':{'used':100,'limit':3000}}}
        self.assertTrue(free_quota(usage))
        for limit,used in [(None,5),(100,100),(100,True)]:
            usage['emails']['daily']={'used':used,'limit':limit}
            with self.assertRaises(EmailBlocked):free_quota(usage)

    def test_status_does_not_probe_accounts_before_safe_configuration(self):
        self.email.status();self.provider.call.assert_not_called()

if __name__=='__main__':unittest.main()
