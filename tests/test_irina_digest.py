import hashlib
import io
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
from pypdf import PdfWriter
from irina_inbox import Inbox, WABA_ID, PHONE_ID
from irina_digest import BASE, Digest, validate, validate_pdf


def pdf(pages=2, width=595.276):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=841.89)
    out = io.BytesIO(); writer.write(out)
    return out.getvalue()


def job(data):
    return dict(schema=1, edition_number=1, edition='2026-10-03', pdf_path=BASE+'/PDF/2026-10-03.pdf',
                pdf_sha256=hashlib.sha256(data).hexdigest(), editorial_verified=True,
                sources=[dict(url='https://example.org/source',title='Test fixture only',published_date='2026-10-02')])


class Delivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.inbox = Inbox(self.tmp.name)
        self.inbox.owner_last_seen = Mock(return_value=10**12)
        self.processor = SimpleNamespace(inbox=self.inbox, primary='390000000001',
            whatsapp=SimpleNamespace(owners={'390000000001'}, token='fixture'))
        self.data = pdf(); self.j = job(self.data); self.client = Mock()
        self.client.files_get_metadata.return_value=SimpleNamespace(size=len(self.data))
        self.client.files_download.return_value=(None,SimpleNamespace(content=self.data))
        self.now = validate(self.j)[1]
        self.d = Digest(self.processor,self.client,now=lambda:self.now,enabled=True)
        self.d.budget.quote=Mock(return_value=(65800,'MARKETING'))

    def state(self):
        with self.inbox.db() as db:
            return dict(db.execute('SELECT * FROM digests').fetchone())

    def test_calendar_and_dst(self):
        for edition, hour in [('2026-10-03',6),('2026-10-31',7),('2027-04-03',6)]:
            j=dict(self.j,edition=edition,pdf_path=BASE+'/PDF/'+edition+'.pdf')
            self.assertEqual(datetime.fromtimestamp(validate(j)[1],ZoneInfo('UTC')).hour,hour)

    @patch('irina_digest.json_request')
    @patch('irina_digest.upload_pdf')
    def test_early_disabled_and_expired_no_network(self,upload,send):
        self.now-=1;self.d.process(self.j);self.assertEqual(self.state()['state'],'queued')
        self.now+=1;self.d.enabled=False;self.d.process(self.j)
        self.assertEqual(self.state()['error'],'DeliveryNotActivated')
        self.now+=86400;self.d.process(self.j);self.assertEqual(self.state()['state'],'expired')
        upload.assert_not_called();send.assert_not_called()

    @patch('irina_digest.json_request',return_value={'messages':[{'id':'wamid.digest'}]})
    @patch('irina_digest.upload_pdf',return_value='media')
    def test_send_dedup_receipt(self,upload,send):
        self.d.process(self.j);self.d.process(self.j);send.assert_called_once()
        self.assertEqual(send.call_args.args[2]['type'],'document')
        payload={'object':'whatsapp_business_account','entry':[{'id':WABA_ID,'changes':[
            {'field':'messages','value':{'metadata':{'phone_number_id':PHONE_ID},
             'statuses':[{'id':'wamid.digest','status':'delivered','timestamp':'123'}]}}]}]}
        self.inbox.receive(payload,set());self.d.report('2026-10-03')
        with self.inbox.db() as db:
            self.assertEqual(db.execute('SELECT status FROM receipts').fetchone()[0],'delivered')
        with self.assertRaises(ValueError):self.d.process(dict(self.j,pdf_sha256='0'*64))

    @patch('irina_digest.json_request',side_effect=TimeoutError)
    @patch('irina_digest.upload_pdf',return_value='media')
    def test_uncertain_and_crash_no_retry(self,upload,send):
        self.d.process(self.j)
        self.d=Digest(self.processor,self.client,now=lambda:self.now,enabled=True)
        self.d.budget.quote=Mock(return_value=(65800,'MARKETING'))
        self.d.process(self.j);send.assert_called_once();self.assertEqual(self.state()['state'],'uncertain')
        with self.inbox.db() as db:db.execute("UPDATE digests SET state='sending'")
        Digest(self.processor,self.client);self.assertEqual(self.state()['error'],'InterruptedSend')

    @patch('irina_digest.approved_template',return_value=None)
    @patch('irina_digest.upload_pdf')
    def test_unapproved_blocks(self,upload,approved):
        self.inbox.owner_last_seen.return_value=0;self.d.process(self.j)
        self.assertEqual(self.state()['error'],'DocumentTemplateNotApproved');upload.assert_not_called()

    @patch('irina_digest.approved_template',return_value={'category':'MARKETING'})
    @patch('irina_digest.json_request',return_value={'messages':[{'id':'wamid.template'}]})
    @patch('irina_digest.upload_pdf',return_value='media')
    def test_document_template(self,upload,send,approved):
        self.inbox.owner_last_seen.return_value=0;self.d.process(self.j)
        payload=send.call_args.args[2]
        self.assertEqual(payload['type'],'template')
        self.assertEqual(payload['template']['components'][0]['parameters'][0]['type'],'document')

    def test_bad_pdf_and_manifest(self):
        for data in [pdf(1),pdf(3),pdf(width=612),b'not pdf']:
            with self.assertRaises(Exception):validate_pdf(data,hashlib.sha256(data).hexdigest())
        with self.assertRaises(ValueError):validate_pdf(self.data,'0'*64)
        for change in [dict(editorial_verified=False),dict(sources=[]),dict(edition='2026-10-04'),dict(pdf_path='/foreign.pdf')]:
            with self.assertRaises(ValueError):validate(dict(self.j,**change))
        self.processor.whatsapp.owners=set()
        with self.assertRaises(ValueError):self.d.process(self.j)

if __name__=='__main__':unittest.main()
