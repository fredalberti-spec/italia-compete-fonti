"""Daily native Telegram forwards to one pinned private recipient.

Uses the worker's existing client/session. Never downloads, reuploads or sends
text. A persisted uncertain attempt is reconciled from history, never resent.
"""
import asyncio
import secrets
import unicodedata
import re
import papa_whatsapp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')
SOURCE = -1001295597629
DESTINATION = 8836718451
START_DATE = '2026-09-27'
MONTHS = ('gennaio febbraio marzo aprile maggio giugno luglio agosto settembre '
          'ottobre novembre dicembre').split()
PAPERS = ('corriere', 'giorno_legnano')
SEND_TIMES = ('08:30', '09:30')
CHECK_INTERVAL = 300


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
    db.execute('CREATE TABLE IF NOT EXISTS papa_runs '
               '(day TEXT, slot TEXT, result TEXT NOT NULL, PRIMARY KEY(day,slot))')
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


async def run_day(client, db, day, report, slot='08:30'):
    from telethon import functions
    key = day.isoformat()
    if slot not in SEND_TIMES:
        raise ValueError('PapaInvalidSlot')
    if db.execute('SELECT 1 FROM papa_runs WHERE day=? AND slot=?', (key, slot)).fetchone():
        return
    # The original worker already completed the first run on migration day.
    # Preserve that result, but let the new 09:30 run revisit missing papers.
    legacy = db.execute('SELECT result FROM papa_days WHERE day=?', (key,)).fetchone()
    if slot == '08:30' and legacy:
        with db:
            db.execute('INSERT INTO papa_runs VALUES (?,?,?)', (key, slot, legacy[0]))
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
        report('papa_forward_started', day=key, slot=slot, paper=paper,
               source_message=message.id, recipient=DESTINATION)
        # Exactly one message, one pinned recipient; never expands an album.
        try:
            await client(functions.messages.ForwardMessagesRequest(
                from_peer=source, id=[message.id], random_id=[random_id], to_peer=dest))
        except Exception as exc:
            report('papa_forward_uncertain', day=key, slot=slot, paper=paper,
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
        report('papa_forward_verified', day=key, slot=slot, paper=paper, filename=delivered.file.name,
               recipient=DESTINATION, destination_message=delivered.id)
    missing = [p for p in PAPERS if p not in present]
    if uncertain:
        report('papa_result_uncertain', day=key, slot=slot, present=sorted(present), uncertain=uncertain,
               missing=missing, recipient=DESTINATION)
        return
    result = 'complete' if not missing else 'partial' if present else 'missing'
    with db:
        db.execute('INSERT INTO papa_runs VALUES (?,?,?)', (key, slot, result))
        db.execute('INSERT OR REPLACE INTO papa_days VALUES (?,?)', (key, result))
    report('papa_daily_result', day=key, slot=slot, final=(slot == '09:30'),
           result=result, sent=sent,
           present=sorted(present), missing=missing, recipient=DESTINATION,
           whatsapp='check_following_whatsapp_event' if papa_whatsapp.enabled() else 'disabled')


def check_window(now):
    local = now.astimezone(ROME)
    return (local.date().isoformat() >= START_DATE
            and (8, 0) <= (local.hour, local.minute) <= (9, 30))


def scheduled_slot(now):
    local = now.astimezone(ROME)
    slot = local.strftime('%H:%M')
    return slot if check_window(local) and slot in SEND_TIMES else None


async def run_scheduled(client, db, now, report):
    slot = scheduled_slot(now)
    if slot:
        day = now.astimezone(ROME).date()
        try:
            await run_day(client, db, day, report, slot)
        finally:
            if papa_whatsapp.enabled():
                try:
                    # Re-read the destination with the SAME Telegram client;
                    # no notice based only on a cached ledger or attempted forward.
                    _, dest = await resolve(client)
                    present = await recent_papers(client, dest, day, outgoing=True)
                    await papa_whatsapp.notify(db, day, slot, present, report)
                except Exception as exc:
                    # WhatsApp failure must never break Telegram or its scheduler.
                    report('papa_whatsapp_blocked', day=day.isoformat(), slot=slot,
                           reason='verification_or_notification_error', error_type=type(exc).__name__)


async def check_available(client, day, report):
    source, dest = await resolve(client)
    available = await recent_papers(client, source, day)
    present = await recent_papers(client, dest, day, outgoing=True)
    report('papa_availability', day=day.isoformat(), recipient=DESTINATION,
           available={paper: message.file.name for paper, message in available.items()},
           present=sorted(present), missing=[p for p in PAPERS if p not in present])


async def retry_whatsapp(client, db, now, report):
    """Recheck only a blocked WhatsApp preflight, using the existing client."""
    slot = papa_whatsapp.retry_slot(now)
    if not slot:
        return
    day = now.astimezone(ROME).date()
    key = day.isoformat()
    # Any send reservation is terminal for today, including a crash or rejection.
    if db.execute('SELECT 1 FROM papa_whatsapp_notices WHERE day=?', (key,)).fetchone():
        return
    with db:
        claimed = db.execute('INSERT OR IGNORE INTO papa_whatsapp_retry_checks VALUES (?,?,?)',
                             (key, slot, 'checking')).rowcount
    if not claimed:
        return
    report('papa_whatsapp_retry_started', day=key, slot=slot)
    result = 'blocked'
    try:
        # Read only: never calls run_day or forwards additional PDF documents.
        _, dest = await asyncio.wait_for(resolve(client), timeout=20)
        present = await asyncio.wait_for(recent_papers(client, dest, day, outgoing=True), timeout=20)
        result = await papa_whatsapp.notify(db, day, slot, present, report, retry_at=now)
    except Exception as exc:
        report('papa_whatsapp_blocked', day=key, slot=slot,
               reason='verification_or_notification_error', error_type=type(exc).__name__)
    with db:
        db.execute('UPDATE papa_whatsapp_retry_checks SET result=? WHERE day=? AND slot=?',
                   (result, key, slot))
    report('papa_whatsapp_retry_result', day=key, slot=slot, result=result)


async def service(client, db, stopped, report):
    setup(db)
    papa_whatsapp.setup(db)
    report('papa_whatsapp_ready', enabled=papa_whatsapp.enabled(),
           token_configured=bool(papa_whatsapp.os.environ.get('IRINA_WHATSAPP_TOKEN')),
           recipient='papa_pinned', sender=papa_whatsapp.SENDER,
           introduction_once=True, max_notices_per_day=1,
           delivery_tracking='api_acceptance_only')
    report('papa_whatsapp_retry_ready',
           date=papa_whatsapp.os.environ.get('PAPA_WHATSAPP_RETRY_DATE', ''),
           start_hour=papa_whatsapp.os.environ.get('PAPA_WHATSAPP_RETRY_START_HOUR', ''),
           timezone='Europe/Rome', end='23:59', interval_seconds=3600,
           whatsapp_only=True, max_notices_per_day=1)
    retry_after = 0
    next_check = 0
    try:
        await resolve(client)
        report('papa_api_ready', recipient=DESTINATION, source=SOURCE,
               times=list(SEND_TIMES), check_window=['08:00', '09:30'],
               check_interval_seconds=CHECK_INTERVAL,
               timezone='Europe/Rome', start_date=START_DATE)
    except Exception as exc:
        report('papa_preflight_error', error_type=type(exc).__name__)
    while not stopped.is_set():
        now = datetime.now(ROME)
        clock = asyncio.get_running_loop().time()
        slot = scheduled_slot(now)
        # Sending slots take priority over the read-only availability scan.
        if slot and clock >= retry_after:
            try:
                await run_scheduled(client, db, now, report)
                retry_after = asyncio.get_running_loop().time() + 60
                next_check = asyncio.get_running_loop().time() + CHECK_INTERVAL
            except Exception as exc:
                report('papa_api_error', day=now.date().isoformat(), slot=slot,
                       error_type=type(exc).__name__)
                retry_after = asyncio.get_running_loop().time() + max(60, getattr(exc, 'seconds', 0))
                next_check = max(next_check, retry_after)
        elif check_window(now) and not slot and clock >= max(next_check, retry_after):
            try:
                # Bound read-only scans so they cannot occupy a sending minute.
                await asyncio.wait_for(check_available(client, now.date(), report), timeout=20)
                next_check = asyncio.get_running_loop().time() + CHECK_INTERVAL
            except Exception as exc:
                report('papa_check_error', day=now.date().isoformat(),
                       error_type=type(exc).__name__)
                next_check = asyncio.get_running_loop().time() + max(60, getattr(exc, 'seconds', 0))
                if getattr(exc, 'seconds', 0):
                    retry_after = max(retry_after, next_check)
        elif papa_whatsapp.retry_slot(now) and clock >= retry_after:
            await retry_whatsapp(client, db, now, report)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
