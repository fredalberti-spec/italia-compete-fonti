"""Read-only, owner-only search of Fred's Exporter mirror in Dropbox.

Only Markdown is indexed. No Apple credentials, remote writes, public endpoints,
attachment execution, or automatic WhatsApp messages. Dropbox is the source of truth.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
from zoneinfo import ZoneInfo

ROOT = '/Irina/Apple Notes'
NAMESPACE = '13529493'
MAX_FILE = 2 * 1024 * 1024
STOP = set('a ad al alla alle allo ai agli anche ancora apple appunto appunti annotato annotata annotazioni avevo abbiamo ci che chi come con cosa da dal dalla dalle degli dei del della delle di dimmi e ed fammi fare fra gli hai ho i il in io irina la le leggi lo ma me mi mie miei mio mia nelle nella nei nel negli nota note nostro nuova nuovi nuovo nuove o per puoi qualche quali quale quando quanto questa queste questi questo riassumi cerca cercami ricerca sapere scrivi se si sono su sui sul sulla sulle tra trova trovami tu un una uno vorrei vedere the a an and are can could find for from in is it me my notes of on please read search show summarize tell that to what with you your'.split())


def words(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))
    return re.findall(r'[a-z0-9]+', text)


def wants_notes(text):
    t = ' '.join(words(text))
    return bool(re.search(r'\b(?:not[ae]|notes|appunti|annotat\w*|annotations)\b', t) and
                (re.search(r'\b(?:cerca\w*|trov\w*|legg\w*|riassum\w*|consult\w*|cosa|quali|mostra\w*|avevo|search|find|read|summar\w*|what|show)\b', t)
                 or '?' in text or 'nelle mie note' in t or 'apple note' in t))


def eligible(path):
    return (path.lower().startswith(ROOT.lower() + '/') and path.lower().endswith('.md')
            and not any(p.startswith('.') or p.lower() in ('attachments', 'bases')
                        for p in path[len(ROOT)+1:].split('/')))


def content_hash(body):
    return hashlib.sha256(b''.join(hashlib.sha256(body[i:i+4194304]).digest()
                                  for i in range(0, len(body), 4194304))).hexdigest()


def parse_note(path, text):
    title, modified, body = Path(path).stem, '', text
    if text.startswith('---\n') and '\n---\n' in text[4:]:
        header, body = text[4:].split('\n---\n', 1)
        for line in header.splitlines():
            key, sep, value = line.partition(':')
            if sep and key in ('title', 'modified'):
                value = value.strip()
                try:
                    value = json.loads(value) if value.startswith('"') else value
                except ValueError:
                    pass
                if key == 'title':
                    title = str(value)
                else:
                    modified = str(value)
    return title, modified, body


class NotesIndex:
    def __init__(self, root, client_factory=None, now=time.time):
        self.path = Path(root) / 'apple_notes.sqlite3'
        self.now, self.factory = now, client_factory
        self.local = threading.local()
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, path TEXT, rev TEXT,
                    title TEXT, modified TEXT, bytes INTEGER);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                    id UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2');
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
            ''')

    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def put(self, fid, path, rev, text):
        if not eligible(path):
            raise ValueError('NotesPathOutsideScope')
        title, modified, body = parse_note(path, text)
        # Overlapping passages keep long notes searchable without sending entire archives to AI.
        passages = [body[i:i+2200] for i in range(0, len(body), 1900)] or ['']
        with self.db() as db:
            db.execute('DELETE FROM chunks WHERE id=?', (fid,))
            db.execute('INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?)',
                       (fid, path, rev, title, modified, len(text.encode())))
            db.executemany('INSERT INTO chunks(id,title,body) VALUES (?,?,?)',
                           [(fid, title, passage) for passage in passages])

    def mark_complete(self, seen, skipped=0):
        with self.db() as db:
            for row in db.execute('SELECT id FROM notes').fetchall():
                if row['id'] not in seen:
                    db.execute('DELETE FROM chunks WHERE id=?', (row['id'],))
                    db.execute('DELETE FROM notes WHERE id=?', (row['id'],))
            for key, value in (('synced', str(self.now())), ('skipped', str(skipped))):
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, value))

    def status(self):
        with self.db() as db:
            s = dict(db.execute('SELECT key,value FROM state').fetchall())
            s['count'] = db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]
        return s

    def download(self, meta):
        if not hasattr(self.local, 'client'):
            self.local.client = self.factory()
        got, response = self.local.client.files_download(meta.id)
        try:
            body = response.content
        finally:
            response.close()
        if (not eligible(got.path_display) or got.size > MAX_FILE or len(body) > MAX_FILE
                or got.content_hash != content_hash(body)):
            raise ValueError('NotesDownloadMismatch')
        return got.id, got.path_display, got.rev, body.decode('utf-8-sig')

    def sync(self, stop=None):
        import dropbox
        client = self.factory()
        result = client.files_list_folder(ROOT, recursive=True, include_mounted_folders=False, limit=2000)
        entries = list(result.entries)
        while result.has_more:
            result = client.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)
        metas = [e for e in entries if isinstance(e, dropbox.files.FileMetadata) and eligible(e.path_display)]
        if len(metas) > 20000:
            raise ValueError('NotesArchiveTooLarge')
        with self.db() as db:
            cached = {r['id']: (r['rev'], r['path']) for r in db.execute('SELECT id,rev,path FROM notes')}
        supported = [m for m in metas if m.size <= MAX_FILE]
        pending = [m for m in supported if cached.get(m.id) != (m.rev, m.path_display)]
        done = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            # map propagates a failed download: never declare an incomplete scan complete.
            for values in pool.map(self.download, pending):
                if stop and stop.is_set():
                    return
                self.put(*values)
                done += 1
                if done % 200 == 0:
                    print(json.dumps({'event':'irina_notes_progress', 'indexed':done, 'pending_total':len(pending)}), flush=True)
        self.mark_complete({m.id for m in supported}, len(metas)-len(supported))
        print(json.dumps({'event':'irina_notes_ready', 'count':len(supported),
                          'changed':done, 'skipped_large':len(metas)-len(supported),
                          'owner_only':True, 'read_only':True}), flush=True)

    def search(self, text, actor):
        if actor != 'owner':
            raise PermissionError('NotesOwnerOnly')
        s = self.status()
        synced = float(s.get('synced', 0))
        if not synced:
            return {'status':'initial_sync', 'indexed':s['count'], 'sources':[]}
        if self.now()-synced > 3600:
            return {'status':'sync_unavailable', 'sources':[], 'last_sync':synced}
        terms = list(dict.fromkeys(w for w in words(text) if w not in STOP and len(w)>1))[:16]
        with self.db() as db:
            if terms:
                query = ' OR '.join('"' + t + '"*' for t in terms)
                rows = db.execute('SELECT c.id,c.body,n.title,n.path,n.modified FROM chunks c '
                    'JOIN notes n ON n.id=c.id WHERE chunks MATCH ? '
                    'ORDER BY bm25(chunks,0,6,1) LIMIT 40', (query,)).fetchall()
            else:
                rows = db.execute('SELECT n.id,c.body,n.title,n.path,n.modified FROM notes n '
                    'JOIN chunks c ON c.id=n.id ORDER BY n.modified DESC LIMIT 40').fetchall()
        sources, per_note = [], {}
        for row in rows:
            if per_note.get(row['id'],0) >= 2:
                continue
            per_note[row['id']] = per_note.get(row['id'],0)+1
            sources.append({'title':row['title'], 'path':row['path'], 'modified':row['modified'],
                            'excerpt':row['body'], 'partial_note':True})
            if len(sources) >= 6:
                break
        return {'status':'ok', 'indexed':s['count'], 'skipped_large':int(s.get('skipped',0)),
                'last_sync':datetime.fromtimestamp(synced, ZoneInfo('Europe/Rome')).isoformat(),
                'selection':'keyword search' if terms else 'recent notes sample',
                'sources':sources, 'attachments_read':False}


def run(index, stop):
    while not stop.is_set():
        try:
            index.sync(stop)
        except Exception as exc:
            # Provider text can contain private paths: log only allowlisted diagnostic categories.
            message = str(exc).lower()
            reason = next((label for needle, label in (
                ('required scope', 'required_scope'), ('missing_scope', 'required_scope'),
                ('expired_access_token', 'expired_token'), ('invalid_access_token', 'invalid_token'),
                ('not_found', 'path_not_found'), ('invalid_root', 'invalid_namespace'),
                ('select-user', 'team_member_selection'), ('rate_limit', 'rate_limit'),
                ('too_many', 'rate_limit'), ('invalid_grant', 'refresh_rejected'),
                ('disallowed', 'disallowed'), ('not permitted', 'not_permitted')) if needle in message), 'other')
            scopes = [s for s in ('files.metadata.read','files.content.read') if s in message]
            print(json.dumps({'event':'irina_notes_sync_error','error_type':type(exc).__name__,
                              'reason':reason,'required_scopes':scopes}), flush=True)
        stop.wait(120)


def make_index(root, refresh, app_key):
    def factory():
        import dropbox
        return dropbox.Dropbox(oauth2_refresh_token=refresh, app_key=app_key, timeout=45,
                               max_retries_on_error=2, max_retries_on_rate_limit=2).with_path_root(
                                   dropbox.common.PathRoot.namespace_id(NAMESPACE))
    return NotesIndex(root, factory)
