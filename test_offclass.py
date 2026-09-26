"""OFF CLASS ingestion: protection, source resolution and retry behaviour."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
import worker


class Client:
    def __init__(self, messages=(), dialogs=()):
        self.messages, self.dialogs = messages, dialogs

    async def iter_dialogs(self):
        for d in self.dialogs:
            yield d

    async def iter_messages(self, entity, **kwargs):
        for m in self.messages:
            if m.id > kwargs.get('min_id', 0):
                yield m

    async def download_media(self, message, file):
        Path(file).write_bytes(b'%PDF-test')


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = patch.object(worker, 'ROOT', Path(self.tmp.name))
        self.root.start()
        self.env = patch.dict(os.environ, {
            'OFFCLASS_DROPBOX_BASE': '/Off Class/Fonti',
            'OFFCLASS_DROPBOX_NAMESPACE_ID': '123',
            'OFFCLASS_START_FROM': '2026-09-26T00:00:00+02:00'})
        self.env.start()
        self.db = worker.connect_db()
        self.db.execute("INSERT INTO offclass_binding VALUES (1,-42,'2026-09-26T00:00:00+02:00')")
        self.db.commit()
        self.storage = Mock()
        self.storage.with_path_root.return_value = self.storage
        self.remote = {}
        self.source = (-42, Obj(noforwards=False), 'Harvard business review')

    def tearDown(self):
        self.db.close()
        self.env.stop()
        self.root.stop()
        self.tmp.cleanup()

    def msg(self, ident, pdf=False):
        return Obj(id=ident, date=datetime(2026,9,26,tzinfo=timezone.utc),
                   message='An article', entities=[Obj(url='https://hbr.org/article')],
                   noforwards=False, media=None,
                   file=Obj(name='article.pdf',size=9) if pdf else None)

    def upload(self, storage, path, local):
        payload = Path(local).read_bytes()
        if path in self.remote:
            assert self.remote[path] == payload
        self.remote[path] = payload
        return worker.content_hash(local)

    async def test_reposts_deduplicate_pdf_and_restart_skips_completed(self):
        client = Client([self.msg(1,True),self.msg(2,True)])
        with patch.object(worker,'ensure_remote',self.upload):
            await worker.collect_offclass(client,self.storage,self.db,asyncio.Event(),self.source)
            self.assertEqual(len([p for p in self.remote if p.endswith('.pdf')]),1)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM message_sources').fetchone()[0],2)
            count=len(self.remote)
            self.db.close()
            self.db=worker.connect_db()
            await worker.collect_offclass(client,self.storage,self.db,asyncio.Event(),self.source)
            self.assertEqual(len(self.remote),count)
            records=[json.loads(v) for p,v in self.remote.items() if '/Schede messaggio/' in p]
            self.assertEqual(records[0]['urls'],['https://hbr.org/article'])

    async def test_failed_record_upload_does_not_advance_cursor(self):
        client=Client([self.msg(1),self.msg(2)])
        def fail(storage,path,local):
            if '/Schede messaggio/' in path:
                raise OSError('simulated failure')
            return self.upload(storage,path,local)
        with patch.object(worker,'ensure_remote',fail):
            await worker.collect_offclass(client,self.storage,self.db,asyncio.Event(),self.source)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM message_sources').fetchone()[0],0)
        with patch.object(worker,'ensure_remote',self.upload):
            await worker.collect_offclass(client,self.storage,self.db,asyncio.Event(),self.source)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM message_sources').fetchone()[0],2)

    async def test_protected_chat_and_messages_are_not_copied(self):
        with patch.object(worker,'ensure_remote',self.upload):
            await worker.collect_offclass(Client([self.msg(1)]),self.storage,self.db,
                asyncio.Event(),(-42,Obj(noforwards=True),'Harvard business review'))
            self.assertFalse(self.remote)
            msg=self.msg(1); msg.noforwards=True
            await worker.collect_offclass(Client([msg]),self.storage,self.db,asyncio.Event(),self.source)
            self.assertFalse(any('/Schede messaggio/' in p for p in self.remote))

    async def test_binding_rejects_ambiguous_names_and_survives_rename(self):
        self.db.execute('DELETE FROM offclass_binding'); self.db.commit()
        ds=[Obj(id=-42,name='Harvard business review',entity=Obj()),
            Obj(id=-43,name='Harvard business review',entity=Obj())]
        _,src=await worker.resolve_sources(Client(dialogs=ds),self.db)
        self.assertIsNone(src)
        _,src=await worker.resolve_sources(Client(dialogs=ds[:1]),self.db)
        self.assertEqual(src[0],-42)
        ds[0].name='Renamed HBR'
        _,src=await worker.resolve_sources(Client(dialogs=ds),self.db)
        self.assertEqual(src[0],-42)


if __name__ == '__main__':
    unittest.main()
