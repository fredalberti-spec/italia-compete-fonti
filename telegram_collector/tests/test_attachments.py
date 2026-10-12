import sys,unittest,tempfile,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from attachments import validate,hint,MAX_BYTES,RejectedAttachment,MIME
from irina_telegram_trial import Trial,content_hash
from run_trial import Runner
from mirror import mirror
from attachment_fixtures import pdf,docx,epub,png,wav,ogg
class AttachmentsTests(unittest.TestCase):
 def meta(self,ext,body):return {'file_id':'FAKE_FILE','file_name':'sample.'+ext,'file_size':len(body),'mime_type':MIME[ext]}
 def test_allowed_signature_mime_and_extraction(self):
  for ext,body in [('pdf',pdf()),('docx',docx()),('epub',epub()),('png',png()),('wav',wav()),('ogg',ogg())]:
   v=validate(body,self.meta(ext,body),'document');self.assertEqual(v['extension'],ext)
   if ext in ('docx','epub'):self.assertIn('IRINA SYNTHETIC',v['extracted_text']);self.assertNotIn('SHOULD_NOT',v['extracted_text'])
 def test_protected_epub_original_only(self):
  b=epub(protected=True);v=validate(b,self.meta('epub',b),'document');self.assertTrue(v['protected_metadata']);self.assertIsNone(v['extracted_text'])
 def test_wrong_extension_mime_signature_paths_size(self):
  b=pdf()
  for a in [self.meta('png',b),{**self.meta('pdf',b),'mime_type':'image/png'},{**self.meta('pdf',b),'file_name':'../evil.pdf'},{**self.meta('pdf',b),'file_name':'C:\\evil.pdf'},{**self.meta('pdf',b),'file_name':'evil.exe'},{**self.meta('pdf',b),'file_size':MAX_BYTES+1}]:
   with self.assertRaises(RejectedAttachment):validate(b,a,'document')
  with self.assertRaises(RejectedAttachment):validate(b'MZ executable',self.meta('pdf',b'MZ executable'),'document')
 def test_zip_traversal_bomb_xml_external_macro(self):
  bad=[docx({'../evil':'x'}),docx({'huge.txt':'A'*1000000}),docx({'word/settings.xml':'<!DOCTYPE a [<!ENTITY e SYSTEM "file:///secret">]><a>&e;</a>'}),docx({'word/vbaProject.bin':'x'}),epub({'META-INF/container.xml':'<!DOCTYPE a SYSTEM "https://invalid.test"><a/>'})]
  for b in bad:
   ext='epub' if b==bad[-1] else 'docx'
   with self.assertRaises(RejectedAttachment):validate(b,self.meta(ext,b),'document')
 def update(self,i,ext,body,kind='document',owner=12345):
  a=self.meta(ext,body)
  if kind=='voice':a.pop('file_name');a['duration']=1
  if kind=='photo':a={'file_id':'FAKE_FILE','file_size':len(body),'width':1,'height':1}
  return {'update_id':i,'message':{'message_id':i,'from':{'id':owner,'is_bot':False},'chat':{'id':owner,'type':'private'},'caption':'caption unchanged',kind:[a] if kind=='photo' else a}}
 def test_pipeline_all_formats_owner_dedup_hash_caption_no_ai(self):
  with tempfile.TemporaryDirectory() as td:
   t=Trial(td,12345,'A'*32,attachments=True)
   for i,(ext,body,kind) in enumerate([('pdf',pdf(),'document'),('docx',docx(),'document'),('epub',epub(),'document'),('png',png(),'photo'),('ogg',ogg(),'voice')],1):
    u=self.update(i,ext,body,kind);self.assertEqual(t.receive(json.dumps(u).encode(),t.secret),200);self.assertEqual(t.receive(json.dumps(u).encode(),t.secret),200)
    self.assertTrue(t.process(lambda f:body,lambda b:(_ for _ in ()).throw(AssertionError('NO PAID AI'))))
    with t.db() as d:r=json.loads(d.execute('SELECT record FROM notes WHERE seq=?',(i,)).fetchone()[0])
    self.assertEqual(r['text'],'caption unchanged');self.assertFalse(r['external_actions_authorized']);self.assertEqual(r['project'],None)
    original=Path(td)/'dropbox-staging/Irina Telegram/notes'/r['id']/r['original_file'];self.assertEqual(original.read_bytes(),body)
    manifest=json.loads((original.parent/'manifest.json').read_text());self.assertEqual(manifest[r['original_file']]['sha256'],hashlib.sha256(body).hexdigest())
    if kind=='voice':self.assertEqual(r['transcription_state'],'transcription_pending')
   self.assertEqual(t.receive(json.dumps(self.update(99,'pdf',pdf(),owner=999)).encode(),t.secret),403)
   self.assertEqual(len(json.loads(t.batch()['body'])['notes']),5)
 def test_deferred_migration_without_lost_note(self):
  with tempfile.TemporaryDirectory() as td:
   t=Trial(td,12345,'A'*32,attachments=True);u=self.update(1,'ogg',ogg(),'voice')
   class TG:
    def updates(self,offset):return [u] if offset==0 else []
   old=Runner(t,TG(),None,text_only=True);old.poll()
   new=Runner(t,TG(),None,text_only=True,attachments=True);new.recover_deferred();new.recover_deferred()
   with t.db() as d:self.assertEqual(d.execute('SELECT COUNT(*) FROM notes').fetchone()[0],1)
 def test_download_failure_no_archive_no_receipt(self):
  with tempfile.TemporaryDirectory() as td:
   t=Trial(td,12345,'A'*32,attachments=True);t.receive(json.dumps(self.update(1,'pdf',pdf())).encode(),t.secret)
   with self.assertRaises(ValueError):t.process(lambda f:b'bad')
   with t.db() as d:self.assertEqual(d.execute('SELECT state FROM notes').fetchone()[0],'queued')
 def test_png_webp_pixel_bombs_and_generic_zip_rejected(self):
  import struct
  b=bytearray(png());b[16:24]=struct.pack('>II',100000,100000)
  with self.assertRaises(RejectedAttachment):validate(bytes(b),self.meta('png',b),'document')
  payload=b'\x00\x00\x00\x00'+b'\xff\xff\xff'*2;b=b'RIFF'+(4+8+len(payload)).to_bytes(4,'little')+b'WEBPVP8X'+len(payload).to_bytes(4,'little')+payload
  with self.assertRaises(RejectedAttachment):validate(b,self.meta('webp',b),'document')
  with self.assertRaises(RejectedAttachment):validate(docx(),{'file_id':'X','file_name':'archive.zip','mime_type':'application/zip'},'document')
 def test_html_doctype_safe_without_scripts_or_remote_fetch(self):
  b=epub({'OEBPS/chapter.xhtml':'<!DOCTYPE html><html><body><p>safe</p><script>danger</script></body></html>'})
  self.assertEqual(validate(b,self.meta('epub',b),'document')['extracted_text'].strip(),'safe')
 def test_runner_original_audio_receipt_once_and_restart_pending(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as td:
   t=Trial(td,12345,'A'*32,attachments=True);u=self.update(1,'wav',wav(),'audio');u['message']['audio']['duration']=1
   sends=[]
   class TG:
    def updates(self,offset):return [u] if offset==0 else []
    def download(self,f,max_bytes):return wav()
    def send(self,*a,**k):sends.append(a);return 10
   class DB:
    base='/'
    def read_ack(self):return None
   r=Runner(t,TG(),DB(),text_only=True,attachments=True,suppress_existing_receipts=True)
   with patch('run_trial.mirror',return_value={}):r.cycle();r.cycle();Runner(t,TG(),DB(),text_only=True,attachments=True,suppress_existing_receipts=True).cycle()
   self.assertEqual(len(sends),1)
   with t.db() as d:self.assertEqual(d.execute('SELECT COUNT(*) FROM notes').fetchone()[0],1)
 def test_pending_after_restart_gets_one_receipt(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as td:
   t=Trial(td,12345,'A'*32,attachments=True)
   class TG:
    def updates(self,offset):return []
    def download(self,f,max_bytes):return pdf()
    def send(self,*a,**k):calls.append(a);return 33
   class DB:
    base='/'
    def read_ack(self):return None
   calls=[];Runner(t,TG(),DB(),text_only=True,attachments=True,suppress_existing_receipts=True)
   t.receive(json.dumps(self.update(1,'pdf',pdf())).encode(),t.secret)
   with patch('run_trial.mirror',return_value={}):
    r=Runner(t,TG(),DB(),text_only=True,attachments=True,suppress_existing_receipts=True);r.cycle();r.cycle()
   self.assertEqual(len(calls),1)
 def test_other_standard_signature_headers_and_mime_aliases(self):
  jpeg=b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xd9'
  payload=b'\x2f\x00\x00\x00\x00';webp=b'RIFF'+(4+8+6).to_bytes(4,'little')+b'WEBPVP8L'+(5).to_bytes(4,'little')+payload+b'\x00'
  for ext,b in [('jpg',jpeg),('webp',webp),('mp3',b'\xff\xfb\x90\x64'+b'\x00'*30),('flac',b'fLaC'+b'\x80\x00\x00\x22'+b'\x00'*34),('m4a',b'\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom')]:
   self.assertEqual(validate(b,self.meta(ext,b),'document')['extension'],ext)
  b=wav();self.assertEqual(validate(b,{**self.meta('wav',b),'mime_type':'audio/x-wav'},'document')['extension'],'wav')
  with self.assertRaises(RejectedAttachment):validate(pdf(),{**self.meta('pdf',pdf()),'mime_type':'audio/x-wav'},'document')
