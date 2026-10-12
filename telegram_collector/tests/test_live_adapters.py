import io,json,tempfile,unittest,urllib.error,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live_adapters import HTTP,Telegram,Dropbox,Transcriber,RemoteError,NoRedirect
from irina_telegram_trial import Trial,content_hash
from run_trial import Runner

class Response(io.BytesIO):
 def __enter__(self):return self
 def __exit__(self,*_):self.close()
class FakeOpener:
 def __init__(self,*events):self.events=list(events);self.calls=[]
 def open(self,req,timeout):
  self.calls.append(req)
  event=self.events.pop(0)
  if isinstance(event,Exception):raise event
  return Response(event if isinstance(event,bytes) else json.dumps(event).encode())
def error(code):return urllib.error.HTTPError('https://invalid.test',code,'error',{'Retry-After':'0'},None)
def client(*events):
 o=FakeOpener(*events);return HTTP(o,sleep=lambda _:None),o

class HTTPTests(unittest.TestCase):
 def test_retry_503_then_success(self):
  h,o=client(error(503),b'ok');self.assertEqual(h.request('https://invalid.test',retry=True),b'ok');self.assertEqual(len(o.calls),2)
 def test_retry_capped(self):
  h,o=client(error(429),error(429),error(429))
  with self.assertRaises(RemoteError):h.request('https://invalid.test',retry=True)
  self.assertEqual(len(o.calls),3)
 def test_no_retry_mutation(self):
  h,o=client(TimeoutError('SECRET must never appear'))
  with self.assertRaises(RemoteError) as e:h.request('https://invalid.test',body=b'POST')
  self.assertEqual(len(o.calls),1);self.assertNotIn('SECRET',str(e.exception));self.assertTrue(e.exception.uncertain)
 def test_nonretryable_403(self):
  h,o=client(error(403))
  with self.assertRaises(RemoteError):h.request('https://invalid.test',retry=True)
  self.assertEqual(len(o.calls),1)
 def test_redirect_refused(self):
  with self.assertRaises(RemoteError):NoRedirect().redirect_request(None,None,302,'',{},'https://evil.test')
 def test_size_bound(self):
  h,o=client(b'1234')
  with self.assertRaises(RemoteError):h.request('https://invalid.test',limit=3)
 def test_json_invalid_sanitized(self):
  h,o=client(b'not JSON SECRET')
  with self.assertRaises(RemoteError) as e:h.json('https://invalid.test')
  self.assertNotIn('SECRET',str(e.exception))

class TelegramTests(unittest.TestCase):
 def test_poll_parameters(self):
  h,o=client({'ok':True,'result':[]});t=Telegram('FAKE',12345,h);self.assertEqual(t.updates(77),[])
  payload=json.loads(o.calls[0].data);self.assertEqual(payload['offset'],77);self.assertEqual(payload['allowed_updates'],['message'])
 def test_download(self):
  h,o=client({'ok':True,'result':{'file_path':'voice/file.oga','file_size':3}},b'abc');self.assertEqual(Telegram('FAKE',12345,h).download('file'),b'abc')
  self.assertTrue(o.calls[1].full_url.startswith('https://api.telegram.org/file/'))
 def test_download_traversal(self):
  for path in ('../secret','https://evil.test/x','voice/%2e%2e/x','/abs'):
   h,o=client({'ok':True,'result':{'file_path':path,'file_size':3}})
   with self.assertRaises(RemoteError):Telegram('FAKE',12345,h).download('file')
   self.assertEqual(len(o.calls),1)
 def test_download_size_mismatch(self):
  h,o=client({'ok':True,'result':{'file_path':'voice/file','file_size':2}},b'abc')
  with self.assertRaises(RemoteError):Telegram('FAKE',12345,h).download('file')
 def test_send_only_owner(self):
  h,o=client()
  with self.assertRaises(ValueError):Telegram('FAKE',12345,h).send(1,'text')
  self.assertFalse(o.calls)
 def test_send_success(self):
  h,o=client({'ok':True,'result':{'message_id':123}});self.assertEqual(Telegram('FAKE',12345,h).send(12345,'text'),123)
 def test_send_timeout_no_retry(self):
  h,o=client(TimeoutError());t=Telegram('FAKE',12345,h)
  with self.assertRaises(RemoteError):t.send(12345,'text')
  self.assertEqual(len(o.calls),1)
 def test_api_failure(self):
  h,o=client({'ok':False,'description':'secret'})
  with self.assertRaises(RemoteError):Telegram('FAKE',12345,h).updates(0)

class DropboxTests(unittest.TestCase):
 def test_upload_metadata(self):
  h,o=client({'metadata':{'name':'trial'}},b'{}',{'size':3,'content_hash':content_hash(b'abc')});d=Dropbox('FAKE',http=h)
  d.upload('/Irina Telegram/note.json',b'abc');self.assertEqual(d.metadata('/Irina Telegram/note.json')['size'],3)
  args=json.loads(o.calls[1].headers['Dropbox-api-arg']);self.assertEqual(args['mode'],'overwrite');self.assertFalse(args['autorename'])
  d.upload('/Irina Telegram/note.json',b'abc');self.assertEqual(len(o.calls),3)
 def test_folder_conflict_verified(self):
  h,o=client(error(409),{'.tag':'folder'},b'{}');Dropbox('FAKE',http=h).upload('/Irina Telegram/x',b'abc');self.assertEqual(len(o.calls),3)
 def test_outside_folder(self):
  h,o=client();d=Dropbox('FAKE',http=h)
  for path in ('/other/note','/Irina Telegram/../secret'):
   with self.assertRaises(ValueError):d.upload(path,b'abc')
  self.assertFalse(o.calls)
 def test_read_ack(self):
  h,o=client({'action':'acknowledge_read','batch_id':'00000001-00000003'});self.assertEqual(Dropbox('FAKE',http=h).read_ack(),'00000001-00000003')
 def test_ack_absent(self):
  h,o=client(error(409));self.assertIsNone(Dropbox('FAKE',http=h).read_ack())
 def test_app_folder_root_layout_restricted(self):
  h,o=client();d=Dropbox('FAKE',base='/',http=h)
  self.assertEqual(d.path('/notes/id/note.json'),'/notes/id/note.json')
  self.assertEqual(d.path('/control/readability-probe.json'),'/control/readability-probe.json')
  for path in ('/other/data','//notes/data','/notes/../secret'):
   with self.assertRaises(ValueError):d.path(path)
 def test_ack_invalid(self):
  h,o=client({'action':'publish','batch_id':'1'})
  with self.assertRaises(RemoteError):Dropbox('FAKE',http=h).read_ack()

class TranscriptionTests(unittest.TestCase):
 def test_multipart(self):
  h,o=client({'text':'appunto italiano'});a=Transcriber('FAKE',h,encoder=lambda b:b'converted-mp3');self.assertEqual(a(b'ogg'),'appunto italiano')
  self.assertIn(b'gpt-4o-mini-transcribe',o.calls[0].data);self.assertIn(b'converted-mp3',o.calls[0].data);self.assertIn(b'voice.mp3',o.calls[0].data)
 def test_timeout_no_paid_retry(self):
  h,o=client(TimeoutError())
  with self.assertRaises(RemoteError):Transcriber('FAKE',h,encoder=lambda b:b)(b'ogg')
  self.assertEqual(len(o.calls),1)
 def test_empty_transcript(self):
  h,o=client({'text':''})
  with self.assertRaises(RemoteError):Transcriber('FAKE',h,encoder=lambda b:b)(b'ogg')
 def test_encoder_failure_before_network(self):
  h,o=client()
  with self.assertRaises(ValueError):Transcriber('FAKE',h,encoder=lambda b:(_ for _ in ()).throw(ValueError()))(b'ogg')
  self.assertFalse(o.calls)
 def test_codec_command(self):
  with patch('live_adapters.subprocess.check_output',return_value=b'12.0'),patch('live_adapters.subprocess.run') as run:
   def create(cmd,**kwargs):Path(cmd[-1]).write_bytes(b'mp3')
   run.side_effect=create;self.assertEqual(Transcriber.encode(b'ogg'),b'mp3')
   self.assertIn('-protocol_whitelist',run.call_args[0][0])
 def test_codec_long_before_api(self):
  with patch('live_adapters.subprocess.check_output',return_value=b'1801'):
   with self.assertRaises(RemoteError):Transcriber.encode(b'ogg')

class RunnerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.t=Trial(self.tmp.name,12345,'fake-test-only-secret-000000000')
 def tearDown(self):self.tmp.cleanup()
 def test_paid_cache_and_budget(self):
  calls=[];r=Runner(self.t,None,None,lambda b:calls.append(b) or 'text',budget_seconds=1800)
  self.assertEqual(r.transcribe(b'abc'),'text');self.assertEqual(r.transcribe(b'abc'),'text');self.assertEqual(len(calls),1)
  with self.assertRaises(ValueError):r.transcribe(b'other')
 def test_paid_uncertainty_persistent(self):
  calls=[]
  def ai(b):calls.append(b);raise TimeoutError()
  r=Runner(self.t,None,None,ai)
  with self.assertRaises(ValueError):r.transcribe(b'abc')
  r=Runner(self.t,None,None,ai)
  with self.assertRaises(ValueError):r.transcribe(b'abc')
  self.assertEqual(len(calls),1)
 def test_poll_offset_durable(self):
  u={'update_id':5,'message':{'message_id':5,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'Per Off Class: test'}}
  class TG:
   def updates(self,offset):return [u]
  r=Runner(self.t,TG(),None);r.poll();r.poll()
  with self.t.db() as d:
   self.assertEqual(d.execute('SELECT COUNT(*) FROM notes').fetchone()[0],1);self.assertEqual(d.execute("SELECT value FROM runtime WHERE key='offset'").fetchone()[0],'6')
 def test_retry_processing_three_then_block(self):
  class TG:
   def updates(self,offset):return []
   def download(self,f):raise TimeoutError()
  class DB:
   base='/Irina Telegram'
   def read_ack(self):return None
  u={'update_id':5,'message':{'message_id':5,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'Per Off Class: test','document':{'file_id':'fake','file_size':3}}}
  self.t.receive(json.dumps(u).encode(),self.t.secret);r=Runner(self.t,TG(),DB())
  with patch('run_trial.mirror',return_value={}):
   for _ in range(3):
    with self.t.db() as d:d.execute('UPDATE processing SET next=0')
    r.cycle()
  with self.t.db() as d:self.assertEqual(d.execute('SELECT state FROM notes').fetchone()[0],'blocked')


class IntegrationTests(unittest.TestCase):
 def test_real_adapters_simulated_http_two_batches(self):
  from run_trial import Runner
  import hashlib
  class Server:
   def __init__(self):self.files={};self.sends=0;self.ai_calls=0;self.requests=[];self.new=1
   def open(self,req,timeout):
    self.requests.append(req);url=req.full_url
    if url.endswith('/getUpdates'):
     offset=json.loads(req.data)['offset'];updates=[]
     if offset<=self.new:
      updates=[{'update_id':self.new,'message':{'message_id':self.new,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'Per Personale: vocale','voice':{'file_id':'fake','file_size':3,'duration':10}}}]
     result={'ok':True,'result':updates}
    elif url.endswith('/getFile'):result={'ok':True,'result':{'file_path':'voice/file.oga','file_size':3}}
    elif '/file/bot' in url:return Response(b'ogg')
    elif url.endswith('/sendMessage'):
     self.sends+=1;result={'ok':True,'result':{'message_id':900+self.sends}}
    elif url.endswith('/audio/transcriptions'):self.ai_calls+=1;result={'text':'trascrizione HTTP fittizia'}
    elif url.endswith('/create_folder_v2'):result={'metadata':{'.tag':'folder'}}
    elif url.endswith('/files/upload'):
     args=json.loads(req.headers['Dropbox-api-arg']);self.files[args['path']]=req.data;result={}
    elif url.endswith('/files/get_metadata'):
     path=json.loads(req.data)['path'];body=self.files[path];result={'size':len(body),'content_hash':content_hash(body)}
    elif url.endswith('/files/download'):
     path=json.loads(req.headers['Dropbox-api-arg'])['path']
     if path not in self.files:raise error(409)
     return Response(self.files[path])
    else:raise AssertionError('UnexpectedEndpoint')
    return Response(json.dumps(result).encode())
  with tempfile.TemporaryDirectory() as tmp:
   t=Trial(tmp,12345,'fake-test-only-secret-000000000');server=Server();h=HTTP(server,sleep=lambda _:None)
   r=Runner(t,Telegram('FAKE',12345,h),Dropbox('FAKE',http=h),Transcriber('FAKE',h,encoder=lambda b:b'mp3'))
   r.cycle();batch=t.batch();self.assertEqual(server.sends,1);self.assertEqual(server.ai_calls,1)
   body=json.loads(batch['body']);self.assertEqual(body['notes'][0]['archive_state'],'dropbox_verified');self.assertEqual(body['notes'][0]['receipt_state'],'api_accepted')
   status=json.loads(next(v for k,v in server.files.items() if k.endswith('/status.json')));self.assertEqual(status['telegram_delivery'],'not_observable')
   r.cycle();self.assertEqual(server.sends,1);self.assertEqual(server.ai_calls,1)
   server.files['/Irina Telegram/control/ack.json']=json.dumps({'action':'acknowledge_read','batch_id':batch['batch_id']}).encode()
   r.cycle();self.assertIsNone(t.batch())
   server.new=2;r.cycle();b2=t.batch();self.assertEqual([n['sequence'] for n in json.loads(b2['body'])['notes']],[2]);self.assertEqual(server.sends,2);self.assertEqual(server.ai_calls,1)
   self.assertTrue(any(k.endswith('.md') and '/handoff/' in k for k in server.files))
 def test_mirror_failure_prevents_receipt(self):
  class TG:
   def updates(self,offset):return []
   def send(self,*_):raise AssertionError('MustNotSendBeforeVerifiedArchive')
   def download(self,*_):raise AssertionError('NoMediaExpected')
  class DB:base='/Irina Telegram'
  with tempfile.TemporaryDirectory() as tmp:
   t=Trial(tmp,12345,'fake-test-only-secret-000000000')
   t.receive(json.dumps({'update_id':1,'message':{'message_id':1,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'Per Off Class: dato'}}).encode(),t.secret)
   with patch('run_trial.mirror',side_effect=RemoteError()):
    with self.assertRaises(RemoteError):Runner(t,TG(),DB()).cycle()
   with t.db() as d:self.assertEqual(d.execute('SELECT receipt_state FROM notes').fetchone()[0],'pending')

class ControlTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.t=Trial(self.tmp.name,12345,'fake-test-only-secret-000000000')
 def tearDown(self):self.tmp.cleanup()
 def test_text_without_label_is_queued(self):
  self.t.receive(json.dumps({'update_id':1,'message':{'message_id':1,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'senza progetto'}}).encode(),self.t.secret)
  with self.t.db() as d:self.assertEqual(d.execute('SELECT state FROM notes').fetchone()[0],'queued')
 def test_forwarded_routing_command_not_executed(self):
  self.t.receive(json.dumps({'update_id':1,'message':{'message_id':1,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'senza progetto'}}).encode(),self.t.secret)
  with self.t.db() as d:nid=d.execute('SELECT note_id FROM notes').fetchone()[0]
  class TG:
   def updates(self,offset):return [{'update_id':2,'message':{'message_id':2,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':f'/progetto {nid} personale','forward_origin':{'type':'user'}}}]
  Runner(self.t,TG(),None).poll()
  with self.t.db() as d:self.assertIsNone(d.execute('SELECT project FROM notes WHERE note_id=?',(nid,)).fetchone()[0])
 def test_http_cancellation_no_call(self):
  import threading
  h,o=client();h.cancelled=threading.Event();h.cancelled.set()
  with self.assertRaises(RemoteError):h.request('https://invalid.test',retry=True)
  self.assertFalse(o.calls)
 def test_main_without_live_no_calls(self):
  import run_trial
  with patch('sys.argv',['run_trial.py']),patch('run_trial.Telegram') as tg:
   with self.assertRaises(SystemExit):run_trial.main()
   tg.assert_not_called()
 def test_missing_config_no_calls(self):
  import run_trial
  with patch.dict('os.environ',{},clear=True),patch('sys.argv',['run_trial.py','--live']),patch('run_trial.Telegram') as tg:
   with self.assertRaises(SystemExit):run_trial.main()
   tg.assert_not_called()

if __name__=="__main__":unittest.main()
