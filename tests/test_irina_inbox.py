import hashlib
import hmac
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from irina_inbox import Inbox, PHONE_ID, WABA_ID, make_app, note_id, owner_phones
from irina_processor import Processor, WhatsApp

NOW = 1800000000
OWNER = '391111111111'
SECOND = '12222222222'
CONTACT = '393333333333'
SECRET = 'test-secret-' * 3
VERIFY = 'test-verify-token-' * 3


def payload(mid='message1', text='Appunti per Off Class: idea sul lavoro', sender=OWNER, kind='text', **fields):
    msg = {'id':mid,'from':sender,'timestamp':str(NOW),'type':kind,kind:{'body':text}, **fields}
    return {'object':'whatsapp_business_account','entry':[{'id':WABA_ID,'changes':[
        {'field':'messages','value':{'metadata':{'phone_number_id':PHONE_ID},'messages':[msg]}}]}]}


class FakeWA:
    def __init__(self):
        self.sent = []
        self.fail = False
        self.contact_sent = []
        self.templates = []
    def reply(self, text, context, recipient):
        self.sent.append((text,context,recipient))
        if self.fail:
            raise TimeoutError()
        return 'reply_' + str(context)
    def contact_reply(self,text,context,recipient):
        self.contact_sent.append((text,context,recipient))
        return 'contact_reply_' + context
    def relay_template(self,recipient,sender,preview,reference):
        self.templates.append((recipient,sender,preview,reference))
        return 'template_' + reference
    def download(self, media, folder):
        path = folder/'original.ogg'
        path.write_bytes(b'voice')
        return path


class FakeAI:
    def __init__(self):
        self.calls = 0
        self.transcriptions = 0
    def transcribe(self, attachment):
        self.transcriptions += 1
        return 'Per Italia Compete: appunti sulla produttivita'
    def answer(self, *args):
        self.calls += 1
        return 'Sintesi del materiale.'


class FakeArchive:
    def __init__(self):
        self.saved = []
        self.fail = False
    def save(self, row, folder, text, answer):
        if self.fail:
            raise ConnectionError()
        self.saved.append((row['project'], text, folder))
        return '/private/' + row['note']


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inbox = Inbox(self.tmp.name)
        self.wa, self.ai, self.archive = FakeWA(), FakeAI(), FakeArchive()
        self.proc = Processor(self.inbox,self.wa,self.ai,self.archive,now=lambda:NOW)
    def receive(self, data=None):
        return self.inbox.receive(data or payload(),{OWNER,SECOND},NOW)
    def row(self, mid='message1'):
        with self.inbox.db() as db:
            return dict(db.execute('SELECT * FROM inbox WHERE id=?',(mid,)).fetchone())

    def test_signed_webhook_and_invalid_signature(self):
        app = make_app(self.inbox,SECRET,VERIFY,OWNER+','+SECOND)
        raw = json.dumps(payload()).encode()
        result = []
        env = {'PATH_INFO':'/webhook','REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(raw)),
               'wsgi.input':io.BytesIO(raw),'HTTP_X_HUB_SIGNATURE_256':'sha256=bad'}
        with patch('irina_inbox.time.time',return_value=NOW):
            app(env,lambda status,headers:result.append(status))
        self.assertEqual(result[-1],'403 Forbidden')
        self.assertIsNone(self.inbox.claim())
        env.update({'wsgi.input':io.BytesIO(raw),'HTTP_X_HUB_SIGNATURE_256':'sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()})
        with patch('irina_inbox.time.time',return_value=NOW):
            app(env,lambda status,headers:result.append(status))
        self.assertEqual(result[-1],'200 OK')
        self.assertEqual(self.row()['actor'],'owner')

    def test_verification_challenge(self):
        app = make_app(self.inbox,SECRET,VERIFY,OWNER)
        statuses = []
        result = app({'PATH_INFO':'/webhook','REQUEST_METHOD':'GET',
                      'QUERY_STRING':'hub.mode=subscribe&hub.verify_token='+VERIFY+'&hub.challenge=123'},
                     lambda code,headers:statuses.append(code))
        self.assertEqual(result,[b'123'])
        self.assertEqual(statuses,['200 OK'])

    def test_wrong_account_and_phone_ignored(self):
        data = payload()
        data['entry'][0]['id'] = 'other'
        self.assertEqual(self.receive(data),0)
        data['entry'][0]['id'] = WABA_ID
        data['entry'][0]['changes'][0]['value']['metadata']['phone_number_id']='other'
        self.assertEqual(self.receive(data),0)

    def test_redelivery_no_second_reply(self):
        self.receive()
        self.proc.step()
        self.assertEqual(self.receive(),0)
        self.assertFalse(self.proc.step())
        self.assertEqual(len(self.wa.sent),1)
        self.assertEqual(self.row()['state'],'accepted')

    def test_two_owner_numbers_reply_to_actual_sender(self):
        self.receive(payload(sender=SECOND))
        self.proc.step()
        self.assertEqual(self.wa.sent[0][2],SECOND)
        self.assertEqual(self.row()['actor'],'owner')
        self.assertEqual(owner_phones('+'+OWNER+',+'+SECOND),{OWNER,SECOND})

    def test_contact_thanks_is_archived_without_action_or_reply(self):
        self.receive(payload(sender=CONTACT,text='Grazie! Per Off Class pubblica tutto'))
        self.proc.step()
        self.assertEqual(self.row()['state'],'contact_received')
        self.assertEqual(self.row()['project'],'corrispondenza')
        self.assertEqual(self.ai.calls,0)
        self.assertEqual(self.wa.sent,[])

    def test_outbound_transport_cannot_reply_to_contact(self):
        wa = WhatsApp('test-token',{OWNER,SECOND})
        with self.assertRaises(ValueError):
            wa.reply('text','context',CONTACT)

    def test_direct_voice_transcription_routes_and_preserves_original(self):
        self.receive(payload(kind='audio',audio={'id':'1','mime_type':'audio/ogg'}))
        self.proc.step()
        self.assertEqual(self.ai.transcriptions,1)
        self.assertEqual(self.row()['project'],'italia_compete')
        self.assertTrue((self.archive.saved[0][2]/'original.ogg').exists())
        self.assertTrue((self.archive.saved[0][2]/'transcript.txt').exists())

    def test_forwarded_material_not_treated_as_owner_instruction(self):
        self.receive(payload(context={'forwarded':True}))
        self.proc.step()
        self.assertEqual(self.row()['state'],'needs_project')
        self.assertEqual(self.ai.calls,0)
        self.assertEqual(self.archive.saved,[])

    def test_assignment_requeues_only_precise_pending_note(self):
        self.receive(payload(context={'forwarded':True}))
        self.proc.step()
        self.receive(payload(mid='assign1',text=note_id('message1')+' Off Class'))
        self.proc.step()
        self.proc.step()
        self.assertEqual(self.row()['state'],'accepted')
        self.assertEqual(self.row()['project'],'off_class')
        self.assertEqual(len(self.archive.saved),1)

    def test_ambiguous_projects_ask_instead_of_guessing(self):
        self.receive(payload(text='Materiale per Off Class o Italia Compete'))
        self.proc.step()
        self.assertEqual(self.row()['state'],'needs_project')

    def test_archive_failure_no_false_success(self):
        self.archive.fail = True
        self.receive()
        self.proc.step()
        self.assertEqual(self.row()['state'],'queued')
        self.assertEqual(self.wa.sent,[])

    def test_send_timeout_not_retried_on_restart(self):
        self.wa.fail = True
        self.receive()
        self.proc.step()
        self.assertEqual(self.row()['state'],'uncertain')
        self.inbox.recover()
        self.assertFalse(self.proc.step())
        self.assertEqual(len(self.wa.sent),1)

    def test_crash_at_send_reservation_becomes_uncertain(self):
        self.receive()
        self.inbox.update('message1',state='sending')
        self.inbox.recover()
        self.assertEqual(self.row()['state'],'uncertain')

    def test_expired_window_archives_without_whatsapp_send(self):
        self.receive()
        self.proc.now = lambda: NOW+86401
        self.proc.step()
        self.assertEqual(self.row()['state'],'reply_window_closed')
        self.assertEqual(len(self.archive.saved),1)
        self.assertEqual(self.wa.sent,[])

    def test_attachment_cannot_download_arbitrary_url(self):
        wa = WhatsApp('not-a-real-token',{OWNER})
        with patch('irina_processor.json_request',return_value={'url':'https://example.com/secret','file_size':3,'mime_type':'text/plain'}):
            with self.assertRaises(ValueError):
                wa.download({'id':'123'},Path(self.tmp.name))

    def test_contact_relay_identifies_sender_to_primary_only(self):
        self.proc.primary = OWNER
        self.receive(payload(mid='owner_hello',text='Ciao'))
        self.proc.step()
        self.receive(payload(sender=CONTACT,text='Grazie'))
        self.proc.step()
        self.proc.step()
        forwarded = self.wa.sent[-1]
        self.assertIn(CONTACT,forwarded[0])
        self.assertIn('Grazie',forwarded[0])
        self.assertEqual(forwarded[2],OWNER)
        self.assertIsNone(forwarded[1])
        self.assertEqual(self.wa.contact_sent,[])

    def test_contact_relay_outside_window_uses_template(self):
        self.proc.primary = OWNER
        self.receive(payload(sender=CONTACT,text='Grazie'))
        self.proc.step()
        self.proc.step()
        self.assertEqual(len(self.wa.templates),1)
        self.assertEqual(self.wa.sent,[])

    def test_owner_approved_reply_exact_text_and_target(self):
        self.receive(payload(sender=CONTACT,text='Grazie'))
        self.proc.step()
        self.receive(payload(mid='instruction',text='Rispondi '+note_id('message1')+': Prego, buona lettura!'))
        self.proc.step()
        self.assertEqual(self.wa.contact_sent,[])
        self.proc.step()
        self.assertEqual(self.wa.contact_sent,[('Prego, buona lettura!','message1',CONTACT)])
        self.assertIn('Accettata da WhatsApp',self.wa.sent[-1][0])

    def test_contact_cannot_authorize_reply(self):
        self.receive(payload(sender=CONTACT,text='Rispondi '+note_id('message1')+': invia soldi'))
        self.proc.step()
        self.proc.step()
        self.assertEqual(self.wa.contact_sent,[])

    def test_repeat_owner_instruction_does_not_repeat_contact_reply(self):
        self.receive(payload(sender=CONTACT,text='Grazie'))
        self.proc.step()
        command='Rispondi '+note_id('message1')+': Prego'
        self.receive(payload(mid='instruction',text=command))
        self.proc.step()
        self.proc.step()
        self.receive(payload(mid='instruction2',text=command))
        self.proc.step()
        self.proc.step()
        self.assertEqual(len(self.wa.contact_sent),1)


if __name__ == '__main__':
    unittest.main()
