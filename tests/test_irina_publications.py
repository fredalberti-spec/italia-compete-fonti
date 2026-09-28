import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from irina_inbox import Inbox
from irina_publications import Publications, validate, MEDIA


def job():
    return dict(schema=1, brand_id=7005805, rubrica='Quotidiani', date='2026-09-28', basename='20260928',
        png_path=MEDIA+'Quotidiani/20260928.png', png_sha256=hashlib.sha256(b'\x89PNG\r\n\x1a\nfixture').hexdigest(),
        title='I progetti hanno bisogno di persone', posts=[dict(network='instagram', status='PUBLISHED',
        id='123', uuid='456', url='https://www.instagram.com/p/Example/')])


class Notices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        inbox = Inbox(self.tmp.name)
        inbox.owner_last_seen = Mock(return_value=10**12)
        self.p = SimpleNamespace(inbox=inbox, primary='390000000001', whatsapp=SimpleNamespace(owners={'390000000001'},token='test'))
        self.client = Mock()
        self.client.files_get_metadata.return_value = SimpleNamespace(size=15)
        self.client.files_download.return_value = (None,SimpleNamespace(content=b'\x89PNG\r\n\x1a\nfixture'))
        self.n = Publications(self.p, self.client)

    def state(self):
        with self.p.inbox.db() as db:
            return dict(db.execute('SELECT * FROM publications').fetchone())

    @patch('irina_publications.upload_image', return_value='image1')
    @patch('irina_publications.json_request', return_value={'messages':[{'id':'wamid.test'}]})
    def test_image_caption_exact_and_dedup(self, send, upload):
        self.n.process(job()); self.n.process(job())
        send.assert_called_once()
        payload = send.call_args.args[2]
        self.assertEqual(payload['to'], self.p.primary)
        self.assertEqual(payload['image']['id'],'image1')
        self.assertIn('https://www.instagram.com/p/Example/', payload['image']['caption'])
        self.assertEqual(self.state()['state'],'accepted')

    @patch('irina_publications.approved', return_value=False)
    @patch('irina_publications.upload_image')
    def test_closed_window_blocks_without_upload(self, upload, approved):
        self.p.inbox.owner_last_seen.return_value=0
        self.n.process(job())
        upload.assert_not_called()
        self.assertEqual(self.state()['state'],'blocked')

    @patch('irina_publications.upload_image', return_value='image1')
    @patch('irina_publications.json_request', side_effect=TimeoutError)
    def test_uncertain_never_retried_after_restart(self, send, upload):
        self.n.process(job())
        self.n = Publications(self.p,self.client)
        self.n.process(job())
        send.assert_called_once()
        self.assertEqual(self.state()['state'],'uncertain')

    @patch('irina_publications.upload_image')
    def test_changed_image_cannot_send(self, upload):
        j=job();j['png_sha256']='0'*64
        with self.assertRaises(ValueError): self.n.process(j)
        upload.assert_not_called()

    def test_rejects_unpublished_wrong_brand_and_external_image(self):
        for field,value in [('brand_id',7005677),('png_path','/other.png')]:
            j=job();j[field]=value
            with self.assertRaises(ValueError): validate(j)
        j=job();j['posts'][0]['status']='PENDING'
        with self.assertRaises(ValueError): validate(j)

    @patch('irina_publications.approved',return_value=True)
    @patch('irina_publications.upload_image',return_value='image1')
    @patch('irina_publications.json_request',return_value={'messages':[{'id':'wamid.template'}]})
    def test_closed_window_approved_image_template(self, send, upload, approved):
        self.p.inbox.owner_last_seen.return_value=0
        self.n.process(job())
        self.assertEqual(send.call_args.args[2]['type'],'template')
        self.assertEqual(self.state()['state'],'accepted')

    @patch('irina_publications.sample_handle', return_value='sample')
    @patch('irina_publications.json_request')
    def test_template_submitted_once_even_after_ambiguous_result(self, api, upload):
        api.side_effect = [{'data': []}, TimeoutError(), {'data': []}]
        self.n.ensure_template(job())
        self.n = Publications(self.p, self.client)
        self.n.ensure_template(job())
        self.assertEqual(len([c for c in api.call_args_list if len(c.args) == 3]), 1)
        upload.assert_called_once()

    @patch('irina_publications.sample_handle')
    @patch('irina_publications.json_request')
    def test_existing_template_is_never_recreated(self, api, upload):
        from irina_publications import TEMPLATE
        api.return_value={'data':[{'name':TEMPLATE,'language':'it','status':'APPROVED','id':'123'}]}
        self.n.ensure_template(job())
        upload.assert_not_called()
        api.assert_called_once()

if __name__ == '__main__': unittest.main()
