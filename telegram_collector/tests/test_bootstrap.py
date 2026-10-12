import sys,tempfile,unittest,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from irina_telegram_trial import Trial
from bootstrap import apply
class BootstrapTests(unittest.TestCase):
 def test_pending_preserved_already_read_skipped_and_cursor_waits(self):
  with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
   t=Trial(a,12345,'A'*32)
   for i in (1,2):t.receive(json.dumps({'update_id':i,'message':{'message_id':i,'from':{'id':12345,'is_bot':False},'chat':{'id':12345,'type':'private'},'text':'test'}}).encode(),t.secret)
   t.process();t.process()
   with t.db() as d:
    d.execute("UPDATE notes SET state='queued',record=NULL,receipt_state='not_generated' WHERE seq=1")
    notes=[dict(x) for x in d.execute('SELECT * FROM notes')]
   p={'version':1,'owner_id':12345,'notes':notes,'scoped_read_receipts':[{'note_id':notes[1]['note_id'],'batch_id':'scoped-test','source_thread_id':'parent'}]}
   body=json.dumps(p).encode();sha=hashlib.sha256(body).hexdigest();fresh=Trial(b,12345,'A'*32)
   with self.assertRaises(ValueError):apply(fresh,body,'0'*64)
   self.assertTrue(apply(fresh,body,sha));self.assertFalse(apply(fresh,body,sha));self.assertIsNone(fresh.batch())
   fresh.process();batch=fresh.batch();self.assertEqual([n['id'] for n in json.loads(batch['body'])['notes']],[notes[0]['note_id']]);self.assertEqual(batch['end'],2)
   with fresh.db() as d:self.assertEqual(d.execute('SELECT seq FROM cursor WHERE id=1').fetchone()[0],0)
   fresh.acknowledge(batch['batch_id'])
   with fresh.db() as d:self.assertEqual(d.execute('SELECT seq FROM cursor WHERE id=1').fetchone()[0],2)
 def test_owner_mismatch_rollback(self):
  with tempfile.TemporaryDirectory() as b:
   t=Trial(b,12345,'A'*32);body=b'{"version":1,"owner_id":999,"notes":[]}'
   with self.assertRaises(ValueError):apply(t,body,hashlib.sha256(body).hexdigest())
