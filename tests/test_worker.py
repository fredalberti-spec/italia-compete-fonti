import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
import worker

class Tests(unittest.TestCase):
    def test_restrictions(self):
        msg = Obj(file=Obj(name='daily.pdf', size=20), media=None)
        self.assertTrue(worker.allowed(msg))
        self.assertFalse(worker.allowed(msg, protected=True))
        self.assertFalse(worker.allowed(msg, ttl=86400))
        msg.media=Obj(ttl_seconds=30)
        self.assertFalse(worker.allowed(msg))

    def test_hash_and_ambiguous_upload_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            local=Path(temp)/'a.pdf'
            local.write_bytes(b'%PDF-test')
            digest=hashlib.sha256(hashlib.sha256(local.read_bytes()).digest()).hexdigest()
            self.assertEqual(worker.content_hash(local), digest)
            client=Mock()
            client.files_get_metadata.return_value=Obj(content_hash=digest,size=local.stat().st_size)
            self.assertEqual(worker.ensure_remote(client,'/a.pdf',local),digest)
            client.files_upload.assert_not_called()
            client.files_get_metadata.return_value=Obj(content_hash='wrong',size=local.stat().st_size)
            with self.assertRaises(RuntimeError):
                worker.ensure_remote(client,'/a.pdf',local)
            client.files_upload.assert_not_called()

    def test_ledger_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(worker,'ROOT',Path(temp)):
            db=worker.connect_db()
            worker.checkpoint(db,-1001295597629,12,'abc','/a.pdf','verified')
            db.close()
            db=worker.connect_db()
            self.assertEqual(db.execute('SELECT message FROM cursors').fetchone()[0],12)
            self.assertEqual(db.execute('SELECT path FROM objects WHERE hash=?',('abc',)).fetchone()[0],'/a.pdf')
            db.close()

if __name__ == '__main__':
    unittest.main()
