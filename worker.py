"""Italia Compete: restricted PDF ingestion. No message sending or social actions."""
import asyncio
import fcntl
import hashlib
import json
import os
import re
import signal
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get('DATA_DIR', '/var/data'))
CHATS = {-1001295597629: 'Part2', -1001302683686: 'Economaniacs'}
LIMIT = 100 * 1024 * 1024


def content_hash(path):
    outer = hashlib.sha256()
    with open(path, 'rb') as source:
        while block := source.read(4 * 1024 * 1024):
            outer.update(hashlib.sha256(block).digest())
    return outer.hexdigest()


def pdf_name(name):
    return re.sub(r'[^\w. -]', '_', Path(name).name)[:140]


def allowed(message, protected=False, ttl=0):
    if protected or ttl or getattr(message, 'noforwards', False) or getattr(message, 'ttl_period', None):
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
    return db


def checkpoint(db, chat, message, digest='', path='', state='skipped'):
    with db:
        db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)', (chat, message, digest, path, state))
        db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?)', (chat, message))
        if digest:
            db.execute('INSERT OR REPLACE INTO objects VALUES (?,?)', (digest, path))


def ensure_remote(client, path, local):
    import dropbox
    digest = content_hash(local)
    try:
        meta = client.files_get_metadata(path)
    except dropbox.exceptions.ApiError as exc:
        if not (exc.error.is_path() and exc.error.get_path().is_not_found()):
            raise
        with open(local, 'rb') as source:
            client.files_upload(source.read(), path, mode=dropbox.files.WriteMode.add,
                                autorename=False, strict_conflict=True, mute=True)
        meta = client.files_get_metadata(path)
    if getattr(meta, 'content_hash', None) != digest or getattr(meta, 'size', None) != Path(local).stat().st_size:
        raise RuntimeError('RemoteFileMismatch')
    return digest


def dropbox_client():
    import dropbox
    saved = json.loads((ROOT / 'dropbox.json').read_text())
    client = dropbox.Dropbox(oauth2_refresh_token=saved['refresh_token'],
                             app_key=os.environ['DROPBOX_APP_KEY'], timeout=120)
    return client.with_path_root(dropbox.common.PathRoot.namespace_id(os.environ['DROPBOX_NAMESPACE_ID']))


async def collect(client, storage, db, stopped):
    from telethon import functions, types
    # Resolve only known chat IDs; dialogs are needed to obtain their access hashes.
    entities = {}
    async for dialog in client.iter_dialogs():
        if dialog.id in CHATS:
            entities[dialog.id] = dialog.entity
        if len(entities) == len(CHATS):
            break
    for chat, label in CHATS.items():
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
            if protected or ttl:
                status('source_restricted', chat=chat)
                continue
            row = db.execute('SELECT message FROM cursors WHERE chat=?', (chat,)).fetchone()
            params = {'min_id': row[0]} if row else {'offset_date': datetime.fromisoformat(os.environ['START_FROM'])}
            async for msg in client.iter_messages(entity, reverse=True, **params):
                if stopped.is_set():
                    return
                if not allowed(msg):
                    checkpoint(db, chat, msg.id)
                    continue
                with tempfile.TemporaryDirectory(dir=ROOT) as temp:
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
        while not stopped.is_set():
            await collect(client, storage, db, stopped)
            await pause(stopped, 900)
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
