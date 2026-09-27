import os
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta, datetime
from unittest.mock import patch, AsyncMock
import papa_whatsapp as wa
import papa

DAY = date(2026, 9, 28)


class FakeGraph:
    def __init__(self, approved=None, failure=None, identity=None):
        self.approved = list(wa.TEMPLATES) if approved is None else approved
        self.failure, self.identity, self.posts = failure, identity, []

    async def call(self, method, resource, payload=None):
        if method == 'POST':
            self.posts.append(payload)
            if self.failure:
                raise self.failure
            return {'messages': [{'id': 'wamid.test'}]}
        if 'message_templates' in resource:
            return {'data': [{'name': n, 'status': 'APPROVED', 'language': 'it',
                             'components': [{'type': 'BODY', 'text': wa.TEMPLATES[n]}]}
                            for n in self.approved]}
        return self.identity or {'id': wa.PHONE_ID, 'display_phone_number': '+1 929-557-4726',
                                'verified_name': wa.NAME, 'status': 'CONNECTED'}


class Notices(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/ledger.sqlite3'
        self.db = sqlite3.connect(self.path)
        wa.setup(self.db)
        self.events = []
        self.report = lambda event, **data: self.events.append((event, data))
        self.env = patch.dict(os.environ, {'PAPA_WHATSAPP_ENABLED': 'true', 'PAPA_WHATSAPP_TO': '390000000000'})
        self.env.start()
        self.pin = patch.object(wa, 'RECIPIENT_HASH', hashlib.sha256(b'390000000000').hexdigest())
        self.pin.start()

    def tearDown(self):
        self.env.stop()
        self.pin.stop()
        self.db.close()
        self.tmp.cleanup()

    async def send(self, graph, day=DAY, papers=('corriere',), slot='08:30'):
        return await wa.notify(self.db, day, slot, papers, self.report, graph)

    async def test_intro_once_pinned_recipient_and_partial_exactness(self):
        graph = FakeGraph()
        self.assertEqual(await self.send(graph, papers=('giorno_legnano',)), 'accepted')
        first = graph.posts[0]
        self.assertEqual(first['to'], '390000000000')
        self.assertEqual(first['template']['name'], wa.INTRO)
        self.assertEqual(first['template']['components'][0]['parameters'][0]['text'],
                         'Il Giorno edizione Legnano–Varese')
        self.assertEqual(self.events[-1][1]['delivery'], 'not_verified')
        # Restart and a late second paper cannot produce a second notice.
        self.db.close()
        self.db = sqlite3.connect(self.path)
        wa.setup(self.db)
        await self.send(graph, papers=('corriere', 'giorno_legnano'), slot='09:30')
        self.assertEqual(len(graph.posts), 1)
        await self.send(graph, day=DAY+timedelta(days=1))
        self.assertNotEqual(graph.posts[-1]['template']['name'], wa.INTRO)

    async def test_variation_and_no_second_introduction(self):
        graph = FakeGraph()
        for offset in range(8):
            await self.send(graph, day=DAY+timedelta(days=offset))
        names = [p['template']['name'] for p in graph.posts]
        self.assertEqual(names.count(wa.INTRO), 1)
        self.assertTrue(all(a != b for a, b in zip(names, names[1:])))

    async def test_none_or_unrecognized_papers_never_notify(self):
        graph = FakeGraph()
        for papers in ((), ('sole',), ('corriere', 'sole')):
            await self.send(graph, papers=papers)
        self.assertEqual(graph.posts, [])

    async def test_unapproved_templates_no_reservation_then_second_slot_recovers(self):
        graph = FakeGraph(approved=[])
        self.assertEqual(await self.send(graph), 'blocked')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM papa_whatsapp_notices').fetchone()[0], 0)
        graph.approved = list(wa.TEMPLATES)
        await self.send(graph, slot='09:30')
        self.assertEqual(len(graph.posts), 1)

    async def test_uncertain_intro_is_never_repeated_even_on_later_day(self):
        graph = FakeGraph(failure=TimeoutError())
        self.assertEqual(await self.send(graph), 'uncertain')
        graph.failure = None
        await self.send(graph, slot='09:30')
        await self.send(graph, day=DAY+timedelta(days=1))
        self.assertEqual(len(graph.posts), 1)

    async def test_crash_after_reservation_never_reposts(self):
        self.db.execute('INSERT INTO papa_whatsapp_notices VALUES (?,?,?,?,?,?,?)',
                        (DAY.isoformat(), '08:30', wa.INTRO, '[]', 'pending', None, ''))
        self.db.commit()
        graph = FakeGraph()
        await self.send(graph)
        await self.send(graph, day=DAY+timedelta(days=1))
        self.assertEqual(graph.posts, [])

    async def test_rejection_is_not_success_and_intro_retried_only_next_day(self):
        graph = FakeGraph(failure=wa.GraphError(400, 131042))
        self.assertEqual(await self.send(graph), 'rejected')
        await self.send(graph, slot='09:30')
        graph.failure = None
        await self.send(graph, day=DAY+timedelta(days=1))
        self.assertEqual(len(graph.posts), 2)
        self.assertEqual(graph.posts[-1]['template']['name'], wa.INTRO)

    async def test_wrong_sender_and_disabled_never_send(self):
        graph = FakeGraph(identity={'id': 'wrong'})
        await self.send(graph)
        self.assertEqual(graph.posts, [])
        with patch.dict(os.environ, {'PAPA_WHATSAPP_ENABLED': 'false'}):
            self.assertEqual(await self.send(FakeGraph()), 'disabled')

    async def test_telegram_failure_still_checks_verified_partial_and_is_not_hidden(self):
        with patch.object(papa, 'run_day', AsyncMock(side_effect=RuntimeError('Telegram error'))), \
             patch.object(papa, 'resolve', AsyncMock(return_value=('source', 'dest'))), \
             patch.object(papa, 'recent_papers', AsyncMock(return_value={'giorno_legnano': object()})), \
             patch.object(wa, 'notify', AsyncMock()) as notify:
            with self.assertRaises(RuntimeError):
                await papa.run_scheduled(None, self.db, datetime(2026, 9, 28, 8, 30, tzinfo=papa.ROME), self.report)
            self.assertEqual(notify.await_args.args[3].keys(), {'giorno_legnano'})

    async def test_telegram_verification_failure_cannot_notify(self):
        with patch.object(papa, 'run_day', AsyncMock()), \
             patch.object(papa, 'resolve', AsyncMock(side_effect=RuntimeError('Identity'))), \
             patch.object(wa, 'notify', AsyncMock()) as notify:
            await papa.run_scheduled(None, self.db, datetime(2026, 9, 28, 8, 30, tzinfo=papa.ROME), self.report)
            notify.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
