"""Restricted Telegram ingestion for Italia Compete and OFF CLASS.

The worker never sends Telegram messages and never performs social actions.
"""
import asyncio
import fcntl
import hashlib
import json
import os
import re
import signal
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get('DATA_DIR', '/var/data'))
PDF_CHATS = {-1001295597629: 'Part2', -1001302683686: 'Economaniacs'}
OFFCLASS_CHAT_TITLE = os.environ.get('OFFCLASS_CHAT_TITLE', 'Harvard business review')
LIMIT = 200 * 1024 * 1024
UPLOAD_CHUNK = 8 * 1024 * 1024


def content_hash(path):
    outer = hashlib.sha256()
    with open(path, 'rb') as source:
        while block := source.read(4 * 1024 * 1024):
            outer.update(hashlib.sha256(block).digest())
    return outer.hexdigest()


def pdf_name(name):
    return re.sub(r'[^\w. -]', '_', Path(name).name)[:140]


def message_record(message, chat_id, chat_title):
    """Return the minimal immutable source record kept for OFF CLASS."""
    text = (getattr(message, 'message', '') or '').strip()
    urls = sorted(set(re.findall(r'https?://[^\s<>]+', text)))
    urls = sorted(set(urls + [e.url for e in (getattr(message, 'entities', None) or [])
                             if getattr(e, 'url', '').startswith(('https://', 'http://'))]))
    return {
        'source': 'Telegram',
        'chat_id': chat_id,
        'chat_title': chat_title,
        'message_id': message.id,
        'message_date': message.date.isoformat() if message.date else None,
        'text': text,
        'urls': urls,
        'has_pdf': bool(getattr(message, 'file', None) and
                        getattr(message.file, 'name', '') and
                        message.file.name.lower().endswith('.pdf')),
        'status': 'da verificare',
        'publication_authorized': False,
        'automatic_ingestion_authorized': True,
        'automatic_content_generation_authorized': False,
    }


def allowed(message, protected=False, ttl=0):
    # Chat auto-delete is distinct from protected or self-destructing media.
    if protected or getattr(message, 'noforwards', False):
        return False
    media = getattr(message, 'media', None)
    if getattr(media, 'ttl_seconds', None):
        return False
    file = getattr(message, 'file', None)
    return bool(file and file.name and file.name.lower().endswith('.pdf') and 0 < (file.size or 0) <= LIMIT)


def status(state, **details):
    data = {'state': state, 'checked_at': datetime.now(ZoneInfo('Europe/Rome')).isoformat(), **details}
    target = ROOT / 'status.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)
    print(json.dumps(data), flush=True)


def connect_db():
    db = sqlite3.connect(ROOT / 'ledger.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS cursors (chat INTEGER PRIMARY KEY, message INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS sources (chat INTEGER, message INTEGER, hash TEXT, path TEXT, state TEXT, PRIMARY KEY(chat,message))')
    db.execute('CREATE TABLE IF NOT EXISTS objects (hash TEXT PRIMARY KEY, path TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS message_sources (chat INTEGER, message INTEGER, path TEXT, state TEXT, PRIMARY KEY(chat,message))')
    db.execute('CREATE TABLE IF NOT EXISTS offclass_binding (id INTEGER PRIMARY KEY CHECK(id=1), chat INTEGER NOT NULL, start TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS offclass_objects (hash TEXT PRIMARY KEY, path TEXT NOT NULL)')
    return db


def checkpoint(db, chat, message, digest='', path='', state='skipped'):
    with db:
        db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)', (chat, message, digest, path, state))
        db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?)', (chat, message))
        if digest:
            db.execute('INSERT OR REPLACE INTO objects VALUES (?,?)', (digest, path))


def checkpoint_message(db, chat, message, path, state='verified'):
    with db:
        db.execute('INSERT OR REPLACE INTO message_sources VALUES (?,?,?,?)',
                   (chat, message, path, state))


def ensure_remote(client, path, local):
    import dropbox
    digest = content_hash(local)
    try:
        meta = client.files_get_metadata(path)
    except dropbox.exceptions.ApiError as exc:
        if not (exc.error.is_path() and exc.error.get_path().is_not_found()):
            raise
        upload_file(client, path, local)
        meta = client.files_get_metadata(path)
    if getattr(meta, 'content_hash', None) != digest or getattr(meta, 'size', None) != Path(local).stat().st_size:
        raise RuntimeError('RemoteFileMismatch')
    return digest


def upload_file(client, path, local):
    import dropbox
    commit = dropbox.files.CommitInfo(path=path, mode=dropbox.files.WriteMode.add,
                                    autorename=False, strict_conflict=True, mute=True)
    with open(local, 'rb') as source:
        started = client.files_upload_session_start(source.read(UPLOAD_CHUNK))
        cursor = dropbox.files.UploadSessionCursor(session_id=started.session_id, offset=source.tell())
        while chunk := source.read(UPLOAD_CHUNK):
            client.files_upload_session_append_v2(chunk, cursor)
            cursor.offset = source.tell()
        client.files_upload_session_finish(b'', cursor, commit)


def day_start(now=None):
    local = (now or datetime.now(ZoneInfo('Europe/Rome'))).astimezone(ZoneInfo('Europe/Rome'))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def collection_window(now=None):
    local = (now or datetime.now(ZoneInfo('Europe/Rome'))).astimezone(ZoneInfo('Europe/Rome'))
    return 8 <= local.hour < 12


def dropbox_client():
    import dropbox
    saved = json.loads((ROOT / 'dropbox.json').read_text())
    client = dropbox.Dropbox(oauth2_refresh_token=saved['refresh_token'],
                             app_key=os.environ['DROPBOX_APP_KEY'], timeout=120)
    return client.with_path_root(dropbox.common.PathRoot.namespace_id(os.environ['DROPBOX_NAMESPACE_ID']))


async def resolve_sources(client, db):
    """Resolve fixed Italia Compete IDs and the exact OFF CLASS title."""
    entities = {}
    candidates = []
    related = []
    binding = db.execute('SELECT chat FROM offclass_binding WHERE id=1').fetchone()
    pinned = int(os.environ.get('OFFCLASS_CHAT_ID') or (binding[0] if binding else 0))
    async for dialog in client.iter_dialogs():
        import unicodedata
        normalized = unicodedata.normalize('NFKC', dialog.name or '').casefold()
        if 'harvard' in normalized or re.search(r'\bhbr\b', normalized):
            related.append({'chat_id': dialog.id, 'title': dialog.name})
        if dialog.id in PDF_CHATS:
            entities[dialog.id] = dialog.entity
        if (pinned and dialog.id == pinned) or (not pinned and
                dialog.name.strip().casefold() == OFFCLASS_CHAT_TITLE.strip().casefold()):
            candidates.append((dialog.id, dialog.entity, dialog.name))
    # Never guess among identically named chats.
    offclass = candidates[0] if len(candidates) == 1 else None
    if not offclass and os.environ.get('OFFCLASS_ENABLED') == 'true':
        status('offclass_resolution_needed', exact_matches=len(candidates), related=related)
    if offclass and not binding:
        start = os.environ.get('OFFCLASS_START_FROM') or day_start().isoformat()
        parsed = datetime.fromisoformat(start)
        if not parsed.tzinfo:
            raise ValueError('OffclassStartNeedsTimezone')
        with db:
            db.execute('INSERT INTO offclass_binding VALUES (1,?,?)', (offclass[0], start))
    return entities, offclass


async def collect_pdf_chats(client, storage, db, stopped, entities):
    from telethon import functions, types
    for chat, label in PDF_CHATS.items():
        if stopped.is_set():
            return
        try:
            if chat not in entities:
                raise RuntimeError('ChatUnavailable')
            entity = entities[chat]
            if not isinstance(entity, types.Channel):
                raise RuntimeError('UnexpectedChatType')
            full = await client(functions.channels.GetFullChannelRequest(entity))
            protected = bool(getattr(entity, 'noforwards', False))
            ttl = getattr(full.full_chat, 'ttl_period', 0)
            if protected:
                status('source_restricted', chat=chat, reason='content_protection')
                continue
            status('source_started', chat=chat, auto_delete_seconds=ttl or 0)
            # Revisit today's skips, including PDFs excluded by the old size limit.
            params = {'offset_date': day_start()}
            async for msg in client.iter_messages(entity, reverse=True, **params):
                if stopped.is_set():
                    return
                if not collection_window():
                    status('collection_window_closed')
                    return
                if msg.date < day_start():
                    continue
                prior = db.execute('SELECT state FROM sources WHERE chat=? AND message=?', (chat, msg.id)).fetchone()
                if prior and prior[0] == 'verified':
                    continue
                if not allowed(msg):
                    checkpoint(db, chat, msg.id)
                    continue
                with tempfile.TemporaryDirectory(dir=ROOT) as temp:
                    status('file_downloading', chat=chat, message=msg.id, bytes=msg.file.size)
                    local = Path(temp) / 'source.pdf'
                    await client.download_media(msg, file=str(local))
                    if local.stat().st_size != msg.file.size or local.stat().st_size > LIMIT:
                        raise RuntimeError('IncompleteDownload')
                    with local.open('rb') as check:
                        if check.read(5) != b'%PDF-':
                            checkpoint(db, chat, msg.id, state='invalid_pdf')
                            continue
                    digest = content_hash(local)
                    existing = db.execute('SELECT path FROM objects WHERE hash=?', (digest,)).fetchone()
                    base = os.environ['DROPBOX_BASE'].rstrip('/')
                    path = existing[0] if existing else f'{base}/{label}-{msg.id}-{digest[:12]}-{pdf_name(msg.file.name)}'
                    await asyncio.to_thread(ensure_remote, storage, path, local)
                    checkpoint(db, chat, msg.id, digest, path, 'verified')
                    status('file_verified', chat=chat, message=msg.id)
            status('source_checked', chat=chat)
        except Exception as exc:
            # Never log exception text: API responses can contain credentials/private data.
            status('source_error', chat=chat, error_type=type(exc).__name__)
            if hasattr(exc, 'seconds'):
                await pause(stopped, max(int(exc.seconds), 1))


async def collect_offclass(client, storage, db, stopped, source):
    """Archive new HBR messages as source records and any permitted PDFs."""
    if not source or stopped.is_set():
        status('offclass_source_error', source=OFFCLASS_CHAT_TITLE,
               error_type='ChatUnavailable')
        return
    chat, entity, title = source
    try:
        if getattr(entity, 'noforwards', False):
            status('offclass_source_restricted', chat=chat)
            return
        import dropbox
        storage = storage.with_path_root(dropbox.common.PathRoot.namespace_id(
            os.environ['OFFCLASS_DROPBOX_NAMESPACE_ID']))
        base = os.environ['OFFCLASS_DROPBOX_BASE'].rstrip('/')
        # A successful read proves access to the intended archive namespace.
        await asyncio.to_thread(storage.files_get_metadata, base)
        config = {'project': 'OFF CLASS', 'chat_id': chat, 'configured_title': OFFCLASS_CHAT_TITLE,
                  'poll_interval_seconds': 900, 'publish_social': False,
                  'generate_posts': False}
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            local = Path(temp) / 'connection.json'
            local.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            await asyncio.to_thread(ensure_remote, storage, f'{base}/collegamento-{chat}.json', local)
        prior = db.execute('SELECT MAX(message) FROM message_sources WHERE chat=?',
                           (chat,)).fetchone()[0]
        params = {'reverse': True, 'limit': 100}
        if prior:
            params['min_id'] = prior
        else:
            # First activation starts with the current local day; older history
            # is acquired only through an explicit, separate backfill.
            start = db.execute('SELECT start FROM offclass_binding WHERE id=1').fetchone()[0]
            params['offset_date'] = datetime.fromisoformat(start)
        added = 0
        async for msg in client.iter_messages(entity, **params):
            if stopped.is_set():
                return
            if getattr(msg, 'noforwards', False) or getattr(getattr(msg, 'media', None), 'ttl_seconds', None):
                checkpoint_message(db, chat, msg.id, '', 'restricted')
                continue
            record = message_record(msg, chat, title)
            # Ignore empty service events; retain text/link messages and PDFs.
            if not record['text'] and not record['has_pdf']:
                checkpoint_message(db, chat, msg.id, '', 'skipped')
                continue
            stem = f"{msg.date.astimezone(ZoneInfo('Europe/Rome')):%Y%m%d-%H%M}-{msg.id}"
            record['pdf_status'] = 'absent'
            if record['has_pdf'] and allowed(msg):
                with tempfile.TemporaryDirectory(dir=ROOT) as temp:
                    local = Path(temp) / 'source.pdf'
                    await client.download_media(msg, file=str(local))
                    with local.open('rb') as check:
                        valid_pdf = check.read(5) == b'%PDF-'
                    if local.stat().st_size != msg.file.size or not valid_pdf:
                        raise RuntimeError('IncompleteOrInvalidPDF')
                    digest = content_hash(local)
                    # Content-addressed files survive retries and reposts without duplicates.
                    pdf_path = f"{base}/PDF/{digest}.pdf"
                    await asyncio.to_thread(ensure_remote, storage, pdf_path, local)
                    record.update(pdf_path=pdf_path, pdf_hash=digest, pdf_name=msg.file.name,
                                  pdf_status='integrity_verified')
                    with db:
                        db.execute('INSERT OR REPLACE INTO offclass_objects VALUES (?,?)', (digest, pdf_path))
            elif record['has_pdf']:
                record['pdf_status'] = 'not_acquired_size_or_format'
            payload = json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')
            revision = hashlib.sha256(payload).hexdigest()[:16]
            record_path = f'{base}/Schede messaggio/{stem}-{revision}.json'
            with tempfile.TemporaryDirectory(dir=ROOT) as temp:
                local_record = Path(temp) / 'message.json'
                local_record.write_bytes(payload)
                await asyncio.to_thread(ensure_remote, storage, record_path, local_record)
            checkpoint_message(db, chat, msg.id, record_path)
            added += 1
            status('offclass_source_verified', chat=chat, message=msg.id)
        status('offclass_source_checked', chat=chat, acquired=added,
               next_check_seconds=900, archive=base)
    except Exception as exc:
        status('offclass_source_error', chat=chat, error_type=type(exc).__name__)
        if hasattr(exc, 'seconds'):
            await pause(stopped, max(int(exc.seconds), 1))


async def pause(stopped, seconds):
    try:
        await asyncio.wait_for(stopped.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def main():
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    if os.environ.get('INGESTION_ENABLED') != 'true':
        status('disabled')
        await stopped.wait()
        return
    from telethon import TelegramClient
    lock = (ROOT / 'session.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = TelegramClient(str(ROOT / 'telegram'), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'], receive_updates=False)
    db = connect_db()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            status('telegram_authorization_required')
            await stopped.wait()
            return
        storage = dropbox_client()
        next_offclass = 0
        status('worker_ready', offclass_enabled=os.environ.get('OFFCLASS_ENABLED') == 'true')
        while not stopped.is_set():
            try:
                entities, offclass = await resolve_sources(client, db)
            except Exception as exc:
                status('source_resolution_error', error_type=type(exc).__name__)
                await pause(stopped, max(getattr(exc, 'seconds', 0), 60))
                continue
            if collection_window():
                await collect_pdf_chats(client, storage, db, stopped, entities)
            if os.environ.get('OFFCLASS_ENABLED') == 'true' and time.monotonic() >= next_offclass:
                await collect_offclass(client, storage, db, stopped, offclass)
                next_offclass = time.monotonic() + 900
            await pause(stopped, 900 if collection_window() else 60)
    finally:
        await client.disconnect()
        db.close()
        lock.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        status('configuration_or_connection_error', error_type=type(exc).__name__)
        raise SystemExit(1)
