import sqlite3
import unittest
from datetime import date, datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch
from telethon import types
import papa

DAY = date(2026, 9, 27)


def msg(number, filename, out=False):
    return Obj(id=number, file=Obj(name=filename, size=100), out=out,
               date=datetime(2026, 9, 27, 7, tzinfo=papa.ROME),
               document=Obj(id=number), noforwards=False, media=None)


class Names(unittest.TestCase):
    def test_editions(self):
        valid = {
            'Corriere_della_Sera_27_Settembre_2026.pdf': 'corriere',
            'Corriere della Sera versione_definitiva 27 Settembre 2026.pdf': 'corriere',
            'Corriere-della-Sera-27-Settembre-2026-versione-definitiva.pdf': 'corriere',
            'Il_Giorno_Legnano_Varese_27_Settembre_2026.pdf': 'giorno_legnano',
            'Il Giorno Legnano–Varese 27 Settembre 2026.pdf': 'giorno_legnano',
            'Il Giorno Legnano 27 Settembre 2026.pdf': 'giorno_legnano',
        }
        for name, expected in valid.items():
            with self.subTest(name=name):
                self.assertEqual(papa.edition(name, DAY), expected)
        for name in (
            'Corriere_della_Sera_Milano_27_Settembre_2026.pdf',
            'Corriere della Sera 26 Settembre 2026.pdf',
            'Corriere della Sera 27 Settembre 2025.pdf',
            'Corriere della Sera 127 Settembre 2026.pdf',
            'Corriere della Sera 27 Settembre 2026 Sport.pdf',
            'Corriere della Sera 27 Settembre 2026 Sette.pdf',
            'Il Giorno Milano 27 Settembre 2026.pdf',
            'Il Giorno Varese 27 Settembre 2026.pdf',
            'Il Giorno Legnano 27 Settembre 2026 Supplemento.pdf',
            'Il Giorno Legnano 27 Settembre 2026.zip',
        ):
            with self.subTest(name=name):
                self.assertIsNone(papa.edition(name, DAY))


class FakeClient:
    def __init__(self, sources, present=(), fail=False):
        self.sources = sources
        self.present = list(present)
        self.requests = []
        self.fail = fail

    async def iter_messages(self, entity):
        for m in (self.sources if entity == 'source' else self.present):
            yield m

    async def get_messages(self, entity, ids):
        return next(m for m in self.sources if m.id == ids)

    async def __call__(self, request):
        self.requests.append(request)
        original = next(m for m in self.sources if m.id == request.id[0])
        delivered = msg(1000 + original.id, original.file.name, True)
        delivered.document = original.document
        self.present.append(delivered)
        if self.fail:
            raise TimeoutError()


class Flow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        papa.setup(self.db)
        self.events = []
        self.report = lambda state, **data: self.events.append((state, data))
        self.patcher = patch.object(papa, 'resolve', AsyncMock(return_value=('source', 'dest')))
        self.patcher.start()
        self.corriere = msg(1, 'Corriere_della_Sera_27_Settembre_2026.pdf')
        self.final = msg(2, 'Corriere_della_Sera_27_Settembre_2026_versione_definitiva.pdf')
        self.giorno = msg(3, 'Il_Giorno_Legnano_Varese_27_Settembre_2026.pdf')

    def tearDown(self):
        self.patcher.stop()
        self.db.close()

    async def test_only_correct_individual_messages_and_no_duplicate(self):
        other = msg(4, 'Il_Giorno_Milano_27_Settembre_2026.pdf')
        client = FakeClient([self.corriere, self.final, self.giorno, other])
        await papa.run_day(client, self.db, DAY, self.report)
        self.assertEqual([r.id for r in client.requests], [[2], [3]])
        self.assertTrue(all(r.to_peer == 'dest' for r in client.requests))
        await papa.run_day(client, self.db, DAY, self.report)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(self.events[-1][0], 'papa_daily_result')

    async def test_one_already_present(self):
        present = msg(10, self.corriere.file.name, True)
        client = FakeClient([self.final, self.giorno], [present])
        await papa.run_day(client, self.db, DAY, self.report)
        self.assertEqual([r.id for r in client.requests], [[3]])

    async def test_ambiguous_success_reconciles_without_resend(self):
        client = FakeClient([self.corriere], fail=True)
        with self.assertRaises(TimeoutError):
            await papa.run_day(client, self.db, DAY, self.report)
        client.fail = False
        await papa.run_day(client, self.db, DAY, self.report)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(self.events[-1][1]['missing'], ['giorno_legnano'])

    async def test_pending_not_visible_is_never_resent(self):
        self.db.execute('INSERT INTO papa_deliveries VALUES (?,?,?,?,?,NULL)',
                        (DAY.isoformat(), 'corriere', 1, 123, 'pending'))
        self.db.commit()
        client = FakeClient([self.corriere])
        await papa.run_day(client, self.db, DAY, self.report)
        self.assertEqual(client.requests, [])
        self.assertEqual(self.events[-1][0], 'papa_result_uncertain')

    async def test_missing_is_recorded_without_substitution(self):
        client = FakeClient([msg(4, 'Il_Giorno_Milano_27_Settembre_2026.pdf')])
        await papa.run_day(client, self.db, DAY, self.report)
        self.assertEqual(client.requests, [])
        self.assertEqual(self.events[-1][1]['result'], 'missing')


    async def test_second_slot_sends_late_paper_only_and_survives_restart(self):
        client = FakeClient([self.giorno])
        await papa.run_scheduled(client, self.db, datetime(2026, 9, 27, 8, 30, tzinfo=papa.ROME), self.report)
        client.sources.append(self.corriere)
        for hour, minute in ((8, 45), (9, 0), (9, 29)):
            await papa.run_scheduled(client, self.db, datetime(2026, 9, 27, hour, minute, tzinfo=papa.ROME), self.report)
        self.assertEqual([r.id for r in client.requests], [[3]])
        papa.setup(self.db)
        await papa.run_scheduled(client, self.db, datetime(2026, 9, 27, 9, 30, tzinfo=papa.ROME), self.report)
        self.assertEqual([r.id for r in client.requests], [[3], [1]])
        self.assertEqual(self.events[-1][1]['slot'], '09:30')
        self.assertTrue(self.events[-1][1]['final'])
        self.assertEqual(self.events[-1][1]['missing'], [])
        papa.setup(self.db)
        await papa.run_scheduled(client, self.db, datetime(2026, 9, 27, 9, 30, 40, tzinfo=papa.ROME), self.report)
        self.assertEqual(len(client.requests), 2)

    async def test_today_legacy_partial_does_not_block_second_slot(self):
        self.db.execute('INSERT INTO papa_days VALUES (?,?)', (DAY.isoformat(), 'partial'))
        self.db.execute('INSERT INTO papa_deliveries VALUES (?,?,?,?,?,?)',
                        (DAY.isoformat(), 'giorno_legnano', 3, 456, 'verified', 1003))
        self.db.commit()
        present = msg(1003, self.giorno.file.name, True)
        client = FakeClient([self.corriere, self.giorno], [present])
        await papa.run_day(client, self.db, DAY, self.report, '08:30')
        self.assertEqual(client.requests, [])
        await papa.run_day(client, self.db, DAY, self.report, '09:30')
        self.assertEqual([r.id for r in client.requests], [[1]])

    async def test_pending_first_slot_not_resent_at_second(self):
        self.db.execute('INSERT INTO papa_deliveries VALUES (?,?,?,?,?,NULL)',
                        (DAY.isoformat(), 'corriere', 1, 123, 'pending'))
        self.db.commit()
        client = FakeClient([self.corriere, self.giorno])
        await papa.run_day(client, self.db, DAY, self.report, '09:30')
        self.assertEqual([r.id for r in client.requests], [[3]])
        self.assertEqual(self.events[-1][0], 'papa_result_uncertain')
        self.assertEqual(self.events[-1][1]['uncertain'], ['corriere'])

    async def test_availability_checks_never_send(self):
        client = FakeClient([self.corriere, self.giorno])
        await papa.check_available(client, DAY, self.report)
        self.assertEqual(client.requests, [])
        self.assertEqual(set(self.events[-1][1]['available']), set(papa.PAPERS))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM papa_deliveries').fetchone()[0], 0)

    async def test_no_early_intermediate_or_late_sends(self):
        client = FakeClient([self.corriere, self.giorno])
        for hour, minute in ((7, 59), (8, 0), (8, 29), (8, 31), (9, 29), (9, 31), (12, 0)):
            await papa.run_scheduled(client, self.db, datetime(2026, 9, 27, hour, minute, tzinfo=papa.ROME), self.report)
        self.assertEqual(client.requests, [])

    async def test_completed_first_slot_no_duplicates_at_second(self):
        client = FakeClient([self.corriere, self.giorno])
        await papa.run_day(client, self.db, DAY, self.report, '08:30')
        await papa.run_day(client, self.db, DAY, self.report, '09:30')
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(self.events[-1][1]['sent'], [])
        self.assertEqual(self.events[-1][1]['result'], 'complete')


class Schedule(unittest.TestCase):
    def test_rome_window_and_slots_across_daylight_saving(self):
        from datetime import timezone
        for month, day, utc_hour in ((9, 28, 6), (10, 26, 7)):
            at = datetime(2026, month, day, utc_hour, 30, tzinfo=timezone.utc)
            self.assertEqual(papa.scheduled_slot(at), '08:30')
            self.assertEqual(papa.scheduled_slot(at.replace(hour=utc_hour+1)), '09:30')
        for hour, minute, expected in ((7, 59, False), (8, 0, True), (9, 30, True), (9, 31, False)):
            self.assertEqual(papa.check_window(datetime(2026, 9, 28, hour, minute, tzinfo=papa.ROME)), expected)
        self.assertFalse(papa.check_window(datetime(2026, 9, 26, 8, 30, tzinfo=papa.ROME)))


class Recipient(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_name_and_nonprivate_recipient_rejected(self):
        source = types.Channel(id=1295597629, title='Part 2', photo=None, date=None)
        for target in (types.User(id=8836718451, first_name='Quotidiani'),
                       types.Chat(id=8836718451, title='Papà', photo=None,
                                  participants_count=1, date=None, version=1)):
            class Client:
                async def iter_dialogs(self):
                    yield Obj(id=papa.SOURCE, entity=source)
                    yield Obj(id=papa.DESTINATION, entity=target)
            with self.assertRaises(RuntimeError):
                await papa.resolve(Client())


if __name__ == '__main__':
    unittest.main()
