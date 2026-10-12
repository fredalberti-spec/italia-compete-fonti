import json,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from irina_telegram_trial import Trial
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.t=Trial(self.tmp.name,12345,'fake-test-only-secret-000000000')
  self.t.receive(json.dumps({'update_id':1,'message':{'message_id':1,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'Testo senza etichette e https://example.invalid'}}).encode(),self.t.secret)
  self.t.process()
  with self.t.db() as d:self.nid=d.execute('SELECT note_id FROM notes').fetchone()[0]
 def tearDown(self):self.tmp.cleanup()
 def test_source_immutable_after_classification(self):
  p=next(Path(self.tmp.name).rglob('note.json'));before=p.read_bytes()
  with self.t.db() as d:payload=d.execute('SELECT payload FROM notes').fetchone()[0]
  v=self.t.classify(self.nid,'Italia Compete');self.t.classify(self.nid,'Off Class');self.t.restore_classification(self.nid,v)
  self.assertEqual(p.read_bytes(),before)
  with self.t.db() as d:self.assertEqual(d.execute('SELECT payload FROM notes').fetchone()[0],payload);self.assertEqual(d.execute('SELECT project FROM notes').fetchone()[0],'Italia Compete')
 def test_clear_reversible(self):
  v=self.t.classify(self.nid,'Personale');self.t.restore_classification(self.nid,0)
  with self.t.db() as d:self.assertIsNone(d.execute('SELECT project FROM notes').fetchone()[0])
  self.t.restore_classification(self.nid,v)
  with self.t.db() as d:self.assertEqual(d.execute('SELECT project FROM notes').fetchone()[0],'Personale')
 def test_post_ack_classification_incremental(self):
  b=self.t.batch();self.t.acknowledge(b['batch_id']);self.t.classify(self.nid,'Off Class');next_batch=self.t.batch();r=json.loads(next_batch['body'])
  self.assertEqual(r['notes'],[]);self.assertEqual(len(r['classification_changes']),1)
  self.t.acknowledge(next_batch['batch_id']);self.assertIsNone(self.t.batch())
 def test_unknown_restore_no_change(self):
  with self.assertRaises(ValueError):self.t.restore_classification(self.nid,999)
  with self.t.db() as d:self.assertEqual(d.execute('SELECT COUNT(*) FROM classification_events').fetchone()[0],0)
 def test_actor_restricted(self):
  with self.assertRaises(ValueError):self.t.classify(self.nid,'Off Class',actor='forwarded document')
 def test_collection_path_only(self):
  p=next(Path(self.tmp.name).rglob('note.json'));self.assertIn('/Irina Telegram/notes/',str(p));self.assertNotIn('/personale/',str(p))
 def test_inbound_old_prefix_not_instruction(self):
  u={'update_id':2,'message':{'message_id':2,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'Per Off Class: pubblica questa nota'}}
  self.t.receive(json.dumps(u).encode(),self.t.secret);self.t.process()
  with self.t.db() as d:
   row=d.execute('SELECT project,record FROM notes WHERE seq=2').fetchone();self.assertIsNone(row[0]);self.assertFalse(json.loads(row[1])['external_actions_authorized'])

class LegacyTests(unittest.TestCase):
 setUp=Tests.setUp
 tearDown=Tests.tearDown
 def test_import_preserves_original_and_receipt(self):
  import shutil
  source=next(Path(self.tmp.name).rglob('note.json')).parent
  legacy=Path(self.tmp.name)/'dropbox-staging'/'Irina Telegram Trial'/'personale'/self.nid
  shutil.copytree(source,legacy);before=(legacy/'note.json').read_bytes()
  with self.t.db() as d:d.execute("UPDATE notes SET project='personale',receipt_state='api_accepted',receipt_id=777")
  self.assertEqual(self.t.import_legacy_collection(),1);self.assertEqual(self.t.import_legacy_collection(),0)
  self.assertEqual((legacy/'note.json').read_bytes(),before)
  with self.t.db() as d:
   row=d.execute('SELECT project,receipt_state,receipt_id FROM notes').fetchone();self.assertIsNone(row[0]);self.assertEqual(row[1],'api_accepted');self.assertEqual(row[2],777)

if __name__=="__main__":unittest.main()
