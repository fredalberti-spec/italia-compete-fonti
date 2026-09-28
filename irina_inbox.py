"""Private WhatsApp inbox for Fred. No Telegram client and no publication tools.

Authenticated webhook -> durable inbox -> project archive -> one reply.
The API assistant has its own journal, not access to ChatGPT conversations.
"""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs

PHONE_ID = '1320348587831246'
WABA_ID = '1753950079223989'
PROJECTS = ('italia_compete', 'off_class', 'personale')
LABELS = dict(zip(PROJECTS, ('Italia Compete', 'Off Class', 'Personale')))
LABELS['corrispondenza'] = 'Risposte dei contatti'
MAX_BODY = 1024 * 1024


def normalized_phone(value):
    value = value.strip().lstrip('+')
    if not re.fullmatch(r'[1-9][0-9]{7,14}', value):
        raise ValueError('InvalidOwnerPhone')
    return value


def owner_phones(value):
    phones = {normalized_phone(v) for v in value.split(',') if v.strip()}
    if not phones:
        raise ValueError('MissingOwnerPhones')
    return phones


def project_in(text):
    patterns = {'italia_compete': r'\bitalia\s*compete\b',
                'off_class': r'\boff\s*class\b',
                'personale': r'\bpersonale\b'}
    found = [p for p, pattern in patterns.items() if re.search(pattern, text, re.I)]
    return found[0] if len(found) == 1 else None


def note_id(wamid):
    return hashlib.sha256(wamid.encode()).hexdigest()[:20]


class Inbox:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS inbox (
                    id TEXT PRIMARY KEY, note TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
                    stamp INTEGER NOT NULL, received REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', project TEXT, transcript TEXT,
                    result TEXT, archive TEXT, reply_id TEXT, error TEXT, attempts INTEGER DEFAULT 0,
                    next_attempt REAL DEFAULT 0, sender TEXT NOT NULL, actor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (
                    key TEXT PRIMARY KEY, source TEXT NOT NULL, recipient TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL, state TEXT DEFAULT 'queued',
                    message_id TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS publications (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
                    message_id TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT, status TEXT, stamp TEXT, PRIMARY KEY(id,status));
            ''')

    def db(self):
        db = sqlite3.connect(self.root / 'inbox.sqlite3', timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def receive(self, payload, owners, now=None, daily_limit=100):
        now = time.time() if now is None else now
        added = 0
        if payload.get('object') != 'whatsapp_business_account':
            return 0
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for entry in payload.get('entry', []):
                if entry.get('id') != WABA_ID:
                    continue
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    if change.get('field') != 'messages' or value.get('metadata', {}).get('phone_number_id') != PHONE_ID:
                        continue
                    for receipt in value.get('statuses', []):
                        # Store receipts only for known assistant replies, never other contacts.
                        if (db.execute('SELECT 1 FROM inbox WHERE reply_id=?', (receipt.get('id'),)).fetchone()
                                or db.execute('SELECT 1 FROM outbox WHERE message_id=?',(receipt.get('id'),)).fetchone()
                                or db.execute('SELECT 1 FROM publications WHERE message_id=?',(receipt.get('id'),)).fetchone()):
                            db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?,?)',
                                (receipt['id'], receipt.get('status', ''), str(receipt.get('timestamp', ''))))
                    for message in value.get('messages', []):
                        try:
                            sender = normalized_phone(message.get('from', ''))
                        except ValueError:
                            continue
                        actor = 'owner' if sender in owners else 'contact'
                        mid = message.get('id', '')
                        try:
                            stamp = int(message.get('timestamp', 0))
                        except (TypeError, ValueError):
                            continue
                        if not mid or len(mid) > 300 or stamp <= 0 or stamp > now + 300:
                            continue
                        if db.execute('SELECT 1 FROM inbox WHERE id=?', (mid,)).fetchone():
                            continue
                        count = db.execute('SELECT COUNT(*) FROM inbox WHERE received>? AND actor=?', (now-86400,actor)).fetchone()[0]
                        if count >= daily_limit:
                            raise OverflowError('InboxDailyLimit')
                        # Don't persist contact phone numbers or unrelated webhook metadata.
                        safe = {k: v for k, v in message.items() if k != 'from'}
                        for contact in value.get('contacts', []):
                            if contact.get('wa_id') == sender:
                                safe['sender_name'] = str(contact.get('profile',{}).get('name',''))[:100]
                        added += db.execute('INSERT OR IGNORE INTO inbox '
                            '(id,note,payload,stamp,received,sender,actor) VALUES (?,?,?,?,?,?,?)',
                            (mid, note_id(mid), json.dumps(safe, ensure_ascii=False), stamp, now,sender,actor)).rowcount
                        if actor == 'owner':
                            db.execute("UPDATE outbox SET state='queued' WHERE recipient=? AND kind='relay' "
                                "AND state='blocked' AND error='RelayTemplateNotApproved'", (sender,))
        return added

    def update(self, mid, **fields):
        allowed = {'state','project','transcript','result','archive','reply_id','error','attempts','next_attempt'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('InvalidUpdate')
        with self.db() as db:
            db.execute('UPDATE inbox SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',
                       (*fields.values(), mid))

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM inbox WHERE state='queued' AND next_attempt<=? ORDER BY stamp,received LIMIT 1", (now,)).fetchone()
            if row:
                db.execute("UPDATE inbox SET state='working', attempts=attempts+1 WHERE id=?", (row['id'],))
                return dict(row)

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE inbox SET state='queued' WHERE state='working'")
            # Never repeat a potentially accepted WhatsApp POST after a crash.
            db.execute("UPDATE inbox SET state='uncertain' WHERE state='sending'")
            db.execute("UPDATE outbox SET state='uncertain' WHERE state='sending'")

    def lookup(self, reference):
        with self.db() as db:
            row = db.execute('SELECT * FROM inbox WHERE note=? OR id=?', (reference,reference)).fetchone()
            if not row:
                row = db.execute('SELECT i.* FROM inbox i JOIN outbox o ON o.source=i.id WHERE o.message_id=?', (reference,)).fetchone()
        return dict(row) if row else None

    def owner_last_seen(self, owner):
        with self.db() as db:
            return db.execute("SELECT MAX(stamp) FROM inbox WHERE sender=? AND actor='owner'", (owner,)).fetchone()[0] or 0

    def queue(self, key, source, recipient, kind, body):
        with self.db() as db:
            return bool(db.execute('INSERT OR IGNORE INTO outbox (key,source,recipient,kind,body) VALUES (?,?,?,?,?)',
                                   (key,source,recipient,kind,body)).rowcount)

    def outbox_update(self, key, state, mid=None, error=None):
        with self.db() as db:
            db.execute('UPDATE outbox SET state=?,message_id=?,error=? WHERE key=?',(state,mid,error,key))

    def history(self, project):
        with self.db() as db:
            rows = db.execute('SELECT note,project,transcript,result FROM inbox '
                              'WHERE project=? AND archive IS NOT NULL ORDER BY stamp DESC LIMIT 12', (project,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def explicit_context(self, message):
        quoted = message.get('context', {}).get('id')
        with self.db() as db:
            row = db.execute("SELECT project FROM inbox WHERE (id=? OR reply_id=?) AND project IS NOT NULL AND actor='owner'",
                             (quoted, quoted)).fetchone() if quoted else None
        return row['project'] if row else None

    def assign(self, short_id, project):
        if not re.fullmatch(r'[a-f0-9]{20}', short_id) or project not in PROJECTS:
            return False
        with self.db() as db:
            return bool(db.execute("UPDATE inbox SET project=?,state='queued',result=NULL,archive=NULL,"
                "reply_id=NULL,attempts=0,next_attempt=0 WHERE note=? AND state='needs_project'",
                (project, short_id)).rowcount)


def make_app(inbox, secret, verify_token, owners):
    if len(secret) < 16 or len(verify_token) < 24:
        raise ValueError('MissingWebhookSecrets')
    owners = owner_phones(owners)

    def app(env, start):
        def response(code, body):
            start(code, [('Content-Type','text/plain; charset=utf-8'), ('Cache-Control','no-store')])
            return [body.encode()]
        path, method = env.get('PATH_INFO'), env.get('REQUEST_METHOD')
        public_pages = {'/privacy': 'privacy.html', '/data-deletion': 'data-deletion.html'}
        if path in public_pages and method in ('GET', 'HEAD'):
            body = (Path(__file__).parent / 'irina_public' / public_pages[path]).read_bytes()
            start('200 OK', [('Content-Type', 'text/html; charset=utf-8'),
                ('Content-Length', str(len(body))), ('Cache-Control', 'no-store'),
                ('X-Content-Type-Options', 'nosniff'),
                ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")])
            return [body] if method == 'GET' else []
        if path == '/healthz' and method == 'GET':
            return response('200 OK', 'ok')
        if path != '/webhook':
            return response('404 Not Found', 'not found')
        if method == 'GET':
            query = parse_qs(env.get('QUERY_STRING', ''))
            token = query.get('hub.verify_token', [''])[0]
            challenge = query.get('hub.challenge', [''])[0]
            if (query.get('hub.mode') == ['subscribe'] and hmac.compare_digest(token, verify_token)
                    and re.fullmatch(r'[0-9]{1,100}', challenge)):
                return response('200 OK', challenge)
            return response('403 Forbidden', 'verification failed')
        if method != 'POST':
            return response('405 Method Not Allowed', 'method not allowed')
        try:
            length = int(env.get('CONTENT_LENGTH', 0))
        except (TypeError, ValueError):
            return response('400 Bad Request', 'invalid length')
        if not 0 < length <= MAX_BODY:
            return response('413 Payload Too Large', 'invalid size')
        raw = env['wsgi.input'].read(length)
        expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, env.get('HTTP_X_HUB_SIGNATURE_256', '')):
            return response('403 Forbidden', 'invalid signature')
        try:
            payload = json.loads(raw)
            inbox.receive(payload, owners)
        except (ValueError, TypeError, AttributeError):
            return response('400 Bad Request', 'invalid payload')
        except OverflowError:
            return response('429 Too Many Requests', 'daily limit')
        except sqlite3.Error:
            # No acknowledgement before durable commit: Meta may redeliver safely.
            return response('503 Service Unavailable', 'storage unavailable')
        return response('200 OK', 'ok')
    return app


_app = None


def application(env, start):
    if env.get("PATH_INFO", "").startswith("/connect/dropbox-notes"):
        from irina_notes_auth import application as notes_auth
        return notes_auth(env, start)
    global _app
    if _app is None:
        _app = make_app(Inbox(os.environ['IRINA_DATA_DIR']), os.environ['META_APP_SECRET'],
                        os.environ['IRINA_VERIFY_TOKEN'], os.environ['IRINA_OWNER_PHONES'])
    return _app(env, start)
