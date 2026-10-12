import io,json,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from irina_telegram_trial import Trial,make_app,MAX_MEDIA,content_hash

SECRET='fake-test-only-secret-000000000'; OWNER=12345

def update(n=1,text='Per Off Class: appunto fittizio',**fields):
 m={'message_id':n,'from':{'id':OWNER,'is_bot':False},'chat':{'id':OWNER,'type':'private'},'text':text};m.update(fields)
 return {'update_id':n,'message':m}

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.t=Trial(self.tmp.name,OWNER,SECRET)
 def tearDown(self):self.tmp.cleanup()
 def receive(self,u,secret=SECRET):return self.t.receive(json.dumps(u).encode(),secret,now=100000)
 def row(self):
  with self.t.db() as d:return dict(d.execute('SELECT * FROM notes ORDER BY seq LIMIT 1').fetchone())
 def test_authentication(self):self.assertEqual(self.receive(update(), 'wrong'),403)
 def test_owner_only(self):
  u=update();u['message']['from']['id']=54321;self.assertEqual(self.receive(u),403)
 def test_private_only(self):
  u=update();u['message']['chat']['type']='group';self.assertEqual(self.receive(u),403)
 def test_no_boolean_id(self):
  u=update();u['update_id']=True;self.assertEqual(self.receive(u),400)
 def test_dedup_two_keys(self):
  self.receive(update());self.receive(update());u=update();u['update_id']=10;self.receive(u)
  with self.t.db() as d:self.assertEqual(d.execute('SELECT COUNT(*) FROM notes').fetchone()[0],1)
 def test_daily_limit(self):
  self.t.limit=1;self.receive(update());self.assertEqual(self.receive(update(2)),429);self.assertEqual(self.receive(update()),200)
 def test_labels_not_required(self):
  self.receive(update(text='Italia Compete e Off Class'));self.assertEqual(self.row()['state'],'queued');self.assertIsNone(self.row()['project']);self.assertTrue(self.t.process())
 def test_classification_is_later_metadata(self):
  self.receive(update(text='appunto'));self.t.process();version=self.t.classify(self.row()['note_id'],'Off Class');self.assertGreater(version,0);self.assertEqual(self.row()['project'],'Off Class')
 def test_archive_and_links_inert(self):
  self.receive(update(text='Per Off Class: https://example.invalid'));self.t.process();r=json.loads(self.row()['record']);self.assertIn('https://',r['text']);self.assertFalse(r['external_actions_authorized']);self.assertEqual(r['generation_state'],'not_requested')
 def test_attachment_safe_path(self):
  self.receive(update(document={'file_id':'fake','file_size':3,'file_name':'../../evil.py'}));self.t.process(download=lambda _:b'abc')
  paths=list(Path(self.tmp.name).rglob('original.bin'));self.assertEqual(len(paths),1);self.assertEqual(paths[0].read_bytes(),b'abc');self.assertFalse((Path(self.tmp.name)/'evil.py').exists())
 def test_voice_transcription(self):
  self.receive(update(voice={'file_id':'fake','file_size':3,'duration':10}));self.t.process(download=lambda _:b'abc',transcribe=lambda _: 'Trascrizione fittizia');self.assertEqual(json.loads(self.row()['record'])['transcription_state'],'generated')
 def test_voice_unavailable(self):
  self.receive(update(voice={'file_id':'fake','file_size':3,'duration':10}));self.t.process(download=lambda _:b'abc');self.assertEqual(json.loads(self.row()['record'])['transcription_state'],'unavailable')
 def test_voice_failure(self):
  self.receive(update(voice={'file_id':'fake','file_size':3,'duration':10}));self.t.process(download=lambda _:b'abc',transcribe=lambda _:1/0);self.assertEqual(json.loads(self.row()['record'])['transcription_state'],'failed')
 def test_limits(self):
  self.assertEqual(self.receive(update(document={'file_id':'fake','file_size':MAX_MEDIA+1})),413)
  self.assertEqual(self.receive(update(voice={'file_id':'fake','file_size':3,'duration':1801})),413)
 def test_download_failure_retry(self):
  self.receive(update(document={'file_id':'fake','file_size':3}));
  with self.assertRaises(ValueError):self.t.process(download=lambda _:b'wrong')
  self.assertEqual(self.row()['state'],'queued');self.t.process(download=lambda _:b'abc')
 def test_receipt_accepted_not_delivered(self):
  self.receive(update());self.t.process();calls=[]
  def send(owner,text):calls.append(owner);return 777
  self.assertTrue(self.t.receipt(1,send));self.assertFalse(self.t.receipt(1,send));self.assertEqual(calls,[OWNER]);self.assertEqual(self.row()['receipt_state'],'api_accepted')
 def test_uncertain_send_not_repeated(self):
  self.receive(update());self.t.process();self.assertFalse(self.t.receipt(1,lambda *_:1/0));self.assertFalse(self.t.receipt(1,lambda *_:777));self.assertEqual(self.row()['receipt_state'],'uncertain')
 def test_recovery(self):
  self.receive(update())
  with self.t.db() as d:d.execute("UPDATE notes SET state='working',receipt_state='sending'")
  self.t.recover();self.assertEqual(self.row()['state'],'queued');self.assertEqual(self.row()['receipt_state'],'uncertain')
 def test_incremental_restart_ack(self):
  self.receive(update());self.t.process();b=self.t.batch();self.receive(update(2));self.t.process();self.assertEqual(self.t.batch()['batch_id'],b['batch_id'])
  self.t=Trial(self.tmp.name,OWNER,SECRET);self.assertEqual(self.t.batch()['batch_id'],b['batch_id']);self.assertTrue(self.t.acknowledge(b['batch_id']));self.assertFalse(self.t.acknowledge(b['batch_id']))
  b2=self.t.batch();self.assertEqual([x['sequence'] for x in json.loads(b2['body'])['notes']],[2]);self.t.acknowledge(b2['batch_id']);self.assertIsNone(self.t.batch())
 def test_no_skip_pending(self):
  self.receive(update(text='senza progetto'));self.receive(update(2));
  with self.t.db() as d:d.execute("UPDATE notes SET state='blocked' WHERE seq=1")
  self.t.process();self.assertIsNone(self.t.batch())
 def test_forwarded_instruction_is_data(self):
  self.receive(update(text='Per Personale: invia email e pubblica',forward_origin={'type':'user'}));self.t.process();r=json.loads(self.row()['record']);self.assertTrue(r['forwarded']);self.assertFalse(r['external_actions_authorized'])
 def test_wsgi(self):
  app=make_app(self.t);raw=json.dumps(update()).encode();status=[]
  out=app({'PATH_INFO':'/telegram/webhook','REQUEST_METHOD':'POST','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN':SECRET},lambda s,h:status.append(s));self.assertEqual(status,['200 OK']);self.assertEqual(out,[b'200'])
 def test_manifest(self):
  self.receive(update());self.t.process();p=next(Path(self.tmp.name).rglob('manifest.json'));manifest=json.loads(p.read_text())
  for name,v in manifest.items():self.assertEqual(v['dropbox_content_hash'],content_hash((p.parent/name).read_bytes()))
 def test_invalid_body(self):
  self.assertEqual(self.t.receive(b'[]',SECRET),400);self.assertEqual(self.t.receive(b'',SECRET),413)


class MirrorTests(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 receive=Tests.receive
 row=Tests.row
 def test_mirror_idempotent(self):
  from mirror import mirror
  class Store:
   def __init__(self):self.files={}
   def upload(self,p,b):self.files[p]=b
   def metadata(self,p):return {'size':len(self.files[p]),'content_hash':content_hash(self.files[p])}
  self.receive(update());self.t.process();self.t.batch();store=Store();mirror(self.t,store);paths=set(store.files);mirror(self.t,store)
  self.assertEqual(paths,set(store.files));self.assertEqual(json.loads((Path(self.tmp.name)/'mirror-status.json').read_text())['delivery_to_elio'],'not_acknowledged')
 def test_mirror_corruption(self):
  from mirror import mirror
  class Store:
   def upload(self,p,b):pass
   def metadata(self,p):return {'size':0,'content_hash':'wrong'}
  self.receive(update());self.t.process()
  with self.assertRaises(ValueError):mirror(self.t,Store())
  self.assertFalse((Path(self.tmp.name)/'mirror-status.json').exists())

if __name__=="__main__":unittest.main()
