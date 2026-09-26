"""Daily native Telegram forwards to one pinned private recipient.

Uses the worker's existing client/session. Never downloads, reuploads or sends
text. A persisted uncertain attempt is reconciled from history, never resent.
"""
import asyncio
import secrets
import unicodedata
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')
SOURCE = -1001295597629
DESTINATION = 8836718451
START_DATE = '2026-09-27'
MONTHS = ('gennaio febbraio marzo aprile maggio giugno luglio agosto settembre '
          'ottobre novembre dicembre').split()
PAPERS = ('corriere', 'giorno_legnano')


def normalized(value):
    text = unicodedata.normalize('NFKD', value.casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def edition(filename, day):
    if not filename or not filename.lower().endswith('.pdf'):
        return None
    text = normalized(filename[:-4])
    date = rf'0?{day.day} (?:{MONTHS[day.month-1]}|0?{day.month}) {day.year}'
    # A full match rejects other editions, supplements and additional dates.
    final = r'(?: versione definitiva)?'
    national = r'(?: (?:edizione )?nazionale)?'
    if re.fullmatch(rf'corriere della sera{national}{final} {date}{final}', text):
        return 'corriere'
    if re.fullmatch(rf'il giorno (?:edizione )?legnano(?: varese)? {date}', text):
        return 'giorno_legnano'
    return None


def eligible(message, day):
    file = getattr(message, 'file', None)
    if not file or not (file.size or 0) > 0:
        return None
    if getattr(message, 'noforwards', False):
        return None
    if getattr(getattr(message, 'media', None), 'ttl_seconds', None):
        return None
    return edition(file.name, day)


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS papa_days '
               '(day TEXT PRIMARY KEY, result TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS papa_deliveries '
               '(day TEXT, paper TEXT, source_message INTEGER, random_id INTEGER, '
               'state TEXT, destination_message INTEGER, PRIMARY KEY(day,paper))')
    db.commit()


async def resolve(client):
    from telethon import types, utils
    found = {}
    async for dialog in client.iter_dialogs():
        if dialog.id in (SOURCE, DESTINATION):
            found[dialog.id] = dialog.entity
        if len(found) == 2:
            break
    source, dest = found.get(SOURCE), found.get(DESTINATION)
    if not isinstance(source, types.Channel) or utils.get_peer_id(source) != SOURCE:
        raise RuntimeError('PapaSourceUnavailable')
    if normalized(source.title) != 'part 2' or getattr(source, 'noforwards', False):
        raise RuntimeError('PapaSourceIdentityOrProtection')
    if not isinstance(dest, types.User) or dest.id != DESTINATION or dest.bot or dest.deleted:
        raise RuntimeError('PapaRecipientUnavailable')
    if normalized(utils.get_display_name(dest)) != 'papa':
        raise RuntimeError('PapaRecipientNameMismatch')
    return source, dest


async def recent_papers(client, entity, day, outgoing=False):
    result = {}
    # Includes documents uploaded the previous evening with tomorrow's date.
    cutoff = datetime.combine(day, datetime.min.time(), ROME) - timedelta(days=3)
    async for message in client.iter_messages(entity):
        if message.date < cutoff:
            break
        if outgoing and not message.out:
            continue
        paper = eligible(message, day)
        if not paper:
            continue
        old = result.get(paper)
        rank = ('versione definitiva' in normalized(message.file.name), message.id)
        if old is None or rank > ('versione definitiva' in normalized(old.file.name), old.id):
            result[paper] = message
    return result


async def run_day(client, db, day, report):
    from telethon import functions
    key = day.isoformat()
    if db.execute('SELECT 1 FROM papa_days WHERE day=?', (key,)).fetchone():
        return
    source, dest = await resolve(client)
    present = await recent_papers(client, dest, day, outgoing=True)
    candidates = await recent_papers(client, source, day)
    sent = []
    uncertain = []
    for paper in PAPERS:
        if paper in present:
            with db:
                db.execute('UPDATE papa_deliveries SET state=?,destination_message=? '
                           'WHERE day=? AND paper=?', ('verified', present[paper].id, key, paper))
            continue
        previous = db.execute('SELECT state FROM papa_deliveries WHERE day=? AND paper=?',
                              (key, paper)).fetchone()
        if previous:
            # Includes a verified message later removed: never send it again.
            uncertain.append(paper)
            continue
        message = candidates.get(paper)
        if not message:
            continue
        # Refresh the exact source and destination before each individual send.
        source, dest = await resolve(client)
        message = await client.get_messages(source, ids=message.id)
        if not message or eligible(message, day) != paper:
            raise RuntimeError('PapaSourceChanged')
        present.update(await recent_papers(client, dest, day, outgoing=True))
        if paper in present:
            continue
        random_id = secrets.randbits(63) or 1
        with db:
            db.execute('INSERT INTO papa_deliveries VALUES (?,?,?,?,?,NULL)',
                       (key, paper, message.id, random_id, 'pending'))
        report('papa_forward_started', day=key, paper=paper,
               source_message=message.id, recipient=DESTINATION)
        # Exactly one message, one pinned recipient; never expands an album.
        try:
            await client(functions.messages.ForwardMessagesRequest(
                from_peer=source, id=[message.id], random_id=[random_id], to_peer=dest))
        except Exception as exc:
            report('papa_forward_uncertain', day=key, paper=paper,
                   error_type=type(exc).__name__)
            raise
        present.update(await recent_papers(client, dest, day, outgoing=True))
        delivered = present.get(paper)
        if not delivered or delivered.document.id != message.document.id:
            uncertain.append(paper)
            continue
        with db:
            db.execute('UPDATE papa_deliveries SET state=?,destination_message=? '
                       'WHERE day=? AND paper=?', ('verified', delivered.id, key, paper))
        sent.append(paper)
        report('papa_forward_verified', day=key, paper=paper, filename=delivered.file.name,
               recipient=DESTINATION, destination_message=delivered.id)
    missing = [p for p in PAPERS if p not in present]
    if uncertain:
        report('papa_result_uncertain', day=key, present=sorted(present), uncertain=uncertain,
               missing=missing, recipient=DESTINATION)
        return
    result = 'complete' if not missing else 'partial' if present else 'missing'
    with db:
        db.execute('INSERT INTO papa_days VALUES (?,?)', (key, result))
    report('papa_daily_result', day=key, result=result, sent=sent,
           present=sorted(present), missing=missing, recipient=DESTINATION,
           whatsapp='not_configured')


async def service(client, db, stopped, report):
    setup(db)
    retry_after = 0
    try:
        await resolve(client)
        report('papa_api_ready', recipient=DESTINATION, source=SOURCE,
               time='08:30', timezone='Europe/Rome', start_date=START_DATE)
    except Exception as exc:
        report('papa_preflight_error', error_type=type(exc).__name__)
    while not stopped.is_set():
        now = datetime.now(ROME)
        if now.date().isoformat() >= START_DATE and (now.hour, now.minute) >= (8, 30):
            if asyncio.get_running_loop().time() >= retry_after:
                try:
                    await run_day(client, db, now.date(), report)
                    retry_after = asyncio.get_running_loop().time() + 300
                except Exception as exc:
                    report('papa_api_error', day=now.date().isoformat(), error_type=type(exc).__name__)
                    retry_after = asyncio.get_running_loop().time() + max(60, getattr(exc, 'seconds', 0))
        try:
            await asyncio.wait_for(stopped.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
