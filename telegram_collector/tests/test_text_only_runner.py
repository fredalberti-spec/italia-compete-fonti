import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from irina_telegram_trial import Trial
from run_trial import Runner
class TextOnlyTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.t=Trial(self.tmp.name,12345,'A'*32)
 def tearDown(self):self.tmp.cleanup()
 def update(self,uid=1,owner=12345,media=False):
  m={'message_id':uid,'from':{'id':owner,'is_bot':False},'chat':{'id':owner,'type':'private'},'text':'test'}
  if media:m['voice']={'file_id':'FAKE_FILE','file_size':1,'duration':1}
  return {'update_id':uid,'message':m}
 def test_media_deferred_not_downloaded_or_duplicated(self):
  updates=[self.update(media=True),self.update(2),self.update(3,owner=999,media=True)]
  class TG:
   def updates(self,offset):return updates
  r=Runner(self.t,TG(),None,text_only=True);r.poll();r.poll()
  with self.t.db() as d:
   self.assertEqual(d.execute('SELECT COUNT(*) FROM deferred_media').fetchone()[0],1)
   self.assertEqual(d.execute('SELECT COUNT(*) FROM notes').fetchone()[0],1)
 def test_old_receipt_suppressed_new_receipt_once(self):
  self.t.receive(json.dumps(self.update()).encode(),self.t.secret);self.t.process()
  calls=[]
  class TG:
   def updates(self,offset):return []
   def send(self,*a,**k):calls.append(a);return 20
   def download(self,*a):raise AssertionError('NoTextDownload')
  class DB:
   base='/'
   def read_ack(self):return None
  r=Runner(self.t,TG(),DB(),text_only=True,suppress_existing_receipts=True)
  self.t.receive(json.dumps(self.update(2)).encode(),self.t.secret)
  with patch('run_trial.mirror',return_value={}):r.cycle();r.cycle()
  self.assertEqual(len(calls),1)
  status=json.loads((self.t.root/'dropbox-staging/Irina Telegram/control/collector-status.json').read_text())
  self.assertTrue(status['text_only']);self.assertFalse(status['paid_ai_enabled']);self.assertFalse(status['chat_schedule_active'])
  with patch('run_trial.mirror',return_value={}):
   restarted=Runner(self.t,TG(),DB(),text_only=True,suppress_existing_receipts=True);restarted.cycle()
  next_status=json.loads((self.t.root/'dropbox-staging/Irina Telegram/control/collector-status.json').read_text())
  self.assertNotEqual(status['instance_id'],next_status['instance_id']);self.assertEqual(len(calls),1)
  with self.t.db() as d:self.assertEqual(d.execute('SELECT receipt_state FROM notes WHERE seq=1').fetchone()[0],'pending')
 def test_existing_media_preserved_without_download(self):
  self.t.receive(json.dumps(self.update(media=True)).encode(),self.t.secret)
  class TG:
   def updates(self,offset):return []
   def download(self,*a):raise AssertionError('MediaDownloadForbidden')
  class DB:
   base='/'
   def read_ack(self):return None
  with patch('run_trial.mirror',return_value={}):Runner(self.t,TG(),DB(),text_only=True).cycle()
  with self.t.db() as d:self.assertEqual(d.execute('SELECT state FROM notes').fetchone()[0],'deferred_media')
 def test_text_only_refuses_transcriber(self):
  with self.assertRaises(ValueError):Runner(self.t,None,None,lambda b:'paid',text_only=True)
