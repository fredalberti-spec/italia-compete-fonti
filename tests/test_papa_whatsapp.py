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

    def retry_env(self):
        return patch.dict(os.environ, {'PAPA_WHATSAPP_RETRY_DATE': DAY.isoformat(),
            'PAPA_WHATSAPP_RETRY_START_HOUR': '11', 'IRINA_WHATSAPP_TOKEN': 'test-only'})

    async def hourly(self, graph, hour, papers=('corriere', 'giorno_legnano')):
        now = datetime(2026, 9, 28, hour, 3, tzinfo=papa.ROME)
        with self.retry_env(), patch.object(wa, 'Graph', return_value=graph), \
             patch.object(wa, 'datetime') as clock, \
             patch.object(papa, 'resolve', AsyncMock(return_value=('source', 'dest'))), \
             patch.object(papa, 'recent_papers', AsyncMock(return_value=dict.fromkeys(papers))), \
             patch.object(papa, 'run_day', AsyncMock()) as forwarding:
            clock.now.return_value = now
            await papa.retry_whatsapp(object(), self.db, now, self.report)
            forwarding.assert_not_awaited()

    def test_hourly_window_is_opt_in_local_and_expires(self):
        with self.retry_env():
            for value in ('2026-09-28T10:59:59+02:00', '2026-09-29T00:00:00+02:00',
                          '2026-09-27T12:00:00+02:00'):
                self.assertIsNone(wa.retry_slot(datetime.fromisoformat(value)))
            self.assertEqual(wa.retry_slot(datetime.fromisoformat('2026-09-28T09:03:00+00:00')), '11:00')
            self.assertEqual(wa.retry_slot(datetime.fromisoformat('2026-09-28T23:59:59+02:00')), '23:00')
            for bad in ('', 'yesterday', '2026-09-29'):
                with patch.dict(os.environ, {'PAPA_WHATSAPP_RETRY_DATE': bad}):
                    self.assertIsNone(wa.retry_slot(datetime(2026, 9, 28, 12, tzinfo=papa.ROME)))
            with patch.dict(os.environ, {'PAPA_WHATSAPP_ENABLED': 'false'}):
                self.assertIsNone(wa.retry_slot(datetime(2026, 9, 28, 12, tzinfo=papa.ROME)))

    async def test_hourly_approval_recovery_persists_checks_and_stops_after_send(self):
        graph = FakeGraph(approved=[])
        await self.hourly(graph, 11)
        self.assertEqual(graph.posts, [])
        self.assertEqual(self.db.execute('SELECT result FROM papa_whatsapp_retry_checks').fetchone()[0], 'blocked')
        self.db.close()
        self.db = sqlite3.connect(self.path)
        wa.setup(self.db)
        graph.approved = list(wa.TEMPLATES)
        await self.hourly(graph, 11)
        self.assertEqual(graph.posts, [])  # Same hour remains claimed after restart.
        await self.hourly(graph, 12)
        self.assertEqual(len(graph.posts), 1)
        await self.hourly(graph, 13)
        self.assertEqual(len(graph.posts), 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM papa_whatsapp_retry_checks').fetchone()[0], 2)

    async def test_hourly_never_retries_any_reserved_notice(self):
        for state in ('pending', 'uncertain', 'rejected', 'accepted'):
            with self.subTest(state=state):
                with self.db:
                    self.db.execute('DELETE FROM papa_whatsapp_notices')
                    self.db.execute('INSERT INTO papa_whatsapp_notices VALUES (?,?,?,?,?,?,?)',
                                    (DAY.isoformat(), '08:30', wa.INTRO, '[]', state, None, ''))
                graph = FakeGraph()
                await self.hourly(graph, 11)
                self.assertEqual(graph.posts, [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM papa_whatsapp_retry_checks').fetchone()[0], 0)

    async def test_hourly_cannot_notify_without_current_telegram_documents(self):
        graph = FakeGraph()
        await self.hourly(graph, 11, papers=())
        self.assertEqual(graph.posts, [])
        self.assertEqual(self.events[-1][1]['result'], 'no_verified_papers')

    async def test_hourly_uncertain_post_never_repeats(self):
        graph = FakeGraph(failure=TimeoutError())
        await self.hourly(graph, 11)
        graph.failure = None
        await self.hourly(graph, 12)
        self.assertEqual(len(graph.posts), 1)
        self.assertEqual(self.db.execute('SELECT state FROM papa_whatsapp_notices').fetchone()[0], 'uncertain')

    async def test_hourly_verification_error_blocks_sending(self):
        now = datetime(2026, 9, 28, 11, tzinfo=papa.ROME)
        with self.retry_env(), patch.object(papa, 'resolve', AsyncMock(side_effect=RuntimeError())), \
             patch.object(wa, 'notify', AsyncMock()) as notify:
            await papa.retry_whatsapp(None, self.db, now, self.report)
            notify.assert_not_awaited()
        self.assertEqual(self.events[-1][1]['result'], 'blocked')

    async def test_hourly_slow_preflight_cannot_cross_midnight(self):
        now = datetime(2026, 9, 28, 23, 59, 59, tzinfo=papa.ROME)
        graph = FakeGraph()
        with self.retry_env(), patch.object(wa, 'datetime') as clock:
            clock.now.return_value = now + timedelta(seconds=2)
            result = await wa.notify(self.db, DAY, '23:00', ('corriere',), self.report,
                                     graph, retry_at=now)
        self.assertEqual(result, 'expired')
        self.assertEqual(graph.posts, [])


if __name__ == '__main__':
    unittest.main()
