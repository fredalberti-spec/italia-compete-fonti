import sys,unittest,tempfile,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from transfer_registry import pending
class RegistryTest(unittest.TestCase):
 def test_uploaded_same_bytes_skipped_and_changed_original_refused(self):
  with tempfile.TemporaryDirectory() as td:
   r=Path(td);(r/'a.json').write_bytes(b'original')
   (r/'transfer-ledger.json').write_text(json.dumps({'files':{'a.json':{'state':'uploaded','sha256':hashlib.sha256(b'original').hexdigest()}}}))
   self.assertFalse(pending(r,'a.json'));self.assertFalse(pending(r,'a.json'))
   self.assertTrue(pending(r,'new.json'))
   (r/'a.json').write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'TransferredOriginalChanged'):pending(r,'a.json')
 def test_scoped_ack_replay_and_mismatch(self):
  from transfer_registry import acknowledge_read
  with tempfile.TemporaryDirectory() as td:
   r=Path(td);(r/'a.json').write_bytes(b'original')
   ledger={'batch_id':'batch-test','note_id':'note-test','files':{'a.json':{'state':'uploaded','sha256':hashlib.sha256(b'original').hexdigest()}}}
   (r/'transfer-ledger.json').write_text(json.dumps(ledger))
   with self.assertRaises(ValueError):acknowledge_read(r,'wrong',source_thread_id='parent',confirmed_note_id='note-test')
   self.assertTrue(acknowledge_read(r,'batch-test',source_thread_id='parent',confirmed_note_id='note-test'))
   self.assertFalse(acknowledge_read(r,'batch-test',source_thread_id='parent',confirmed_note_id='note-test'))
   ack=json.loads((r/'ack-batch-test.json').read_text());self.assertFalse(ack['general_cursor_advanced'])
   self.assertEqual((r/'a.json').read_bytes(),b'original')
