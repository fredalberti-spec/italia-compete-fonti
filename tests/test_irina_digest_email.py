import hashlib
import json
import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from pypdf import PdfWriter
from irina_inbox import Inbox
from irina_digest import BASE,validate
from irina_digest_email import DigestEmail,free_quota,EmailBlocked,Resend,ResendHTTPError,sender_parts


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

    def test_sender_name_formats_from_preserves_signature_and_dedup_address(self):
        self.env['IRINA_DIGEST_EMAIL_FROM']='Example Producer <irina@example.org>'
        self.email.process(self.job)
        payload=self.provider.call.call_args.args[1]
        self.assertEqual(payload['from'],'Example Producer <irina@example.org>')
        self.assertTrue(payload['text'].endswith('\nIrina'))
        self.assertEqual(json.loads(self.row()['payload'])['sender'],'irina@example.org')
        self.env['IRINA_DIGEST_EMAIL_FROM']='Renamed Producer <irina@example.org>'
        self.provider.call.return_value={'to':['owner@example.org'],'last_event':'delivered'}
        self.email.process(self.job)
        self.assertEqual(self.row()['state'],'delivered')
        self.assertEqual(sum(len(c.args)>1 for c in self.provider.call.call_args_list),1)

    def test_real_transport_4xx_rejected_5xx_and_timeout_uncertain_never_retry(self):
        import urllib.error
        for error,state in [(urllib.error.HTTPError('https://api.resend.com/emails',403,'secret',{},
                io.BytesIO(b'{"name":"invalid_permission","message":"fixture"}')),'rejected'),
                (urllib.error.HTTPError('https://api.resend.com/emails',503,'secret',{},
                io.BytesIO(b'{"name":"service_unavailable"}')),'uncertain'),(TimeoutError(),'uncertain')]:
            with self.subTest(state=state),patch('irina_digest_email.urllib.request.build_opener') as build:
                with self.inbox.db() as db:db.execute('DELETE FROM digest_emails')
                self.email.provider=Resend('fixture-token');build.return_value.open.side_effect=error
                self.email.process(self.job);self.email.process(self.job)
                self.assertEqual(self.row()['state'],state);build.return_value.open.assert_called_once()

    def test_status_does_not_probe_accounts_before_safe_configuration(self):
        self.email.status();self.provider.call.assert_not_called()

class Transport(unittest.TestCase):
    def response(self,value):
        response=Mock();response.read.return_value=value
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        return response

    def error(self,code,body):
        import urllib.error
        return urllib.error.HTTPError('https://api.resend.com/domains',code,'raw-secret-message',{},io.BytesIO(body))

    def test_sender_parser_rejects_injection_and_multiple_mailboxes(self):
        self.assertEqual(sender_parts('Example Producer <irina@example.org>'),('Example Producer','irina@example.org'))
        self.assertEqual(sender_parts('irina@example.org'),('','irina@example.org'))
        for value in ['Example\r\nBcc: victim@example.org <irina@example.org>',
                      'Example <irina@example.org>, Another <another@example.org>',
                      ' Example <irina@example.org>','Example <invalid>',None]:
            with self.subTest(value=value),self.assertRaises(EmailBlocked):sender_parts(value)

    def test_requests_identification_timeout_redirect_and_idempotency(self):
        from irina_processor import NoRedirect
        with patch('irina_digest_email.urllib.request.build_opener') as build:
            build.return_value.open.return_value=self.response(b'{"id":"fixture"}')
            client=Resend('fixture-token');client.call('/domains?limit=100')
            request=build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(),'GET');self.assertEqual(request.data,None)
            self.assertEqual(request.get_header('User-agent'),'Irina-CONTROLUCE/1.0')
            self.assertEqual(request.get_header('Accept'),'application/json')
            self.assertEqual(build.call_args.args,(NoRedirect,))
            self.assertEqual(build.return_value.open.call_args.kwargs,{'timeout':25})
            client.call('/emails',{'to':['fixture@example.org']},idempotency='stable-fixture')
            request=build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(),'POST')
            self.assertEqual(request.get_header('Idempotency-key'),'stable-fixture')
            self.assertEqual(json.loads(request.data),{'to':['fixture@example.org']})

    def test_error_categories_are_bounded_and_never_include_provider_text(self):
        cases=[(b'{"name":"invalid_permission","message":"fixture-token"}','invalid_permission'),
               (b'{"name":"untrusted","message":"fixture-token"}','unknown'),
               (b'{"name":[]}','unknown'),(b'[]','unknown'),(b'<html>fixture-token</html>','unknown'),
               (b'{"name":"invalid_permission"}'+b' '*8193,'unknown')]
        for body,category in cases:
            with self.subTest(category=category,len=len(body)),patch('irina_digest_email.urllib.request.build_opener') as build:
                error=self.error(403,body);error.read=Mock(wraps=error.read)
                build.return_value.open.side_effect=error
                with self.assertRaises(ResendHTTPError) as caught:Resend('fixture-token').call('/domains')
                self.assertEqual(str(caught.exception),'ResendHTTP403')
                self.assertEqual(caught.exception.category,category)
                error.read.assert_called_once_with(8193)
                self.assertNotIn('fixture-token',repr(caught.exception))

    def test_success_response_is_bounded_and_malformed_json_fails(self):
        for body,exception in [(b' '*2_000_001,EmailBlocked),(b'{',ValueError)]:
            with patch('irina_digest_email.urllib.request.build_opener') as build:
                response=self.response(body);build.return_value.open.return_value=response
                with self.assertRaises(exception):Resend('fixture-token').call('/usage')
                response.read.assert_called_once_with(2_000_001)

    def test_preflight_real_transport_only_gets_verified_domain_and_free_quota(self):
        with tempfile.TemporaryDirectory() as tmp,patch('irina_digest_email.urllib.request.build_opener') as build:
            inbox=Inbox(tmp);writer=Mock()
            env={'IRINA_RESEND_API_KEY':'fixture-token','IRINA_DIGEST_EMAIL_FROM':'Example Producer <elio@example.org>',
                 'IRINA_DIGEST_EMAIL_TO':'owner@example.net','IRINA_DIGEST_EMAIL_ENABLED':'false',
                 'IRINA_DIGEST_EMAIL_PREFLIGHT':'true'}
            domain={'data':[{'name':'example.org','status':'verified','capabilities':{'sending':'enabled'}}]}
            quota={'emails':{'daily':{'used':3,'limit':100},'monthly':{'used':10,'limit':3000}}}
            build.return_value.open.side_effect=[self.response(json.dumps(v).encode()) for v in [domain,quota]]
            email=DigestEmail(inbox,Mock(),writer,env=env);email.status()
            result=json.loads(writer.files_upload.call_args.args[0])
            self.assertFalse(result['enabled']);self.assertTrue(result['sender_verified'])
            self.assertTrue(result['zero_cost_quota_verified'])
            self.assertEqual(result['sending_address'],'elio@example.org')
            self.assertEqual(result['sending_name'],'Example Producer')
            self.assertEqual(result['sending_from'],'Example Producer <elio@example.org>')
            requests=[c.args[0] for c in build.return_value.open.call_args_list]
            self.assertEqual([r.get_method() for r in requests],['GET','GET'])
            self.assertEqual([r.full_url for r in requests],['https://api.resend.com/domains?limit=100','https://api.resend.com/usage'])

    def test_preflight_reports_only_sanitized_http_category(self):
        with tempfile.TemporaryDirectory() as tmp,patch('irina_digest_email.urllib.request.build_opener') as build:
            build.return_value.open.side_effect=self.error(403,b'{"name":"invalid_permission","message":"fixture-token"}')
            writer=Mock();email=DigestEmail(Inbox(tmp),Mock(),writer,env={'IRINA_RESEND_API_KEY':'fixture-token',
                'IRINA_DIGEST_EMAIL_PREFLIGHT':'true','IRINA_DIGEST_EMAIL_ENABLED':'false'})
            email.status();result=json.loads(writer.files_upload.call_args.args[0])
            self.assertEqual(result,{'enabled':False,'blocked':'ResendHTTP403','error_category':'invalid_permission'})


if __name__=='__main__':unittest.main()
