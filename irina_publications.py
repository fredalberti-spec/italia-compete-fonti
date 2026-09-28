"""Authorized Italia Compete publication notices, Dropbox queue -> Fred only."""
import hashlib
import json
import re
import time
import uuid
from datetime import date

from irina_inbox import PHONE_ID, WABA_ID
from irina_processor import APIError, json_request, request

BASE = '/Projects/Italia Compete/Gestione editoriale/Notifiche Irina'
MEDIA = '/Projects/Italia Compete/post_IC_new/'
TEMPLATE = 'irina_italia_compete_pubblicato_v1'
BODY = 'Italia Compete — {{1}}. Pubblicato: {{2}}. {{3}}. Irina'


def validate(job):
    if job.get('schema') != 1 or job.get('brand_id') != 7005805:
        raise ValueError('WrongBrandOrSchema')
    rubric = job.get('rubrica')
    if rubric not in ('Quotidiani', 'In agenda', 'Nel mondo', 'Il punto'):
        raise ValueError('InvalidRubric')
    day = date.fromisoformat(job['date'])
    if day < date(2026, 9, 28):
        raise ValueError('BeforeAuthorization')
    name = job.get('basename', '')
    if not re.fullmatch(day.strftime('%Y%m%d') + r'(?:_[2-9][0-9]*)?', name):
        raise ValueError('InvalidBasename')
    if job.get('png_path') != MEDIA + rubric + '/' + name + '.png':
        raise ValueError('InvalidMediaPath')
    if not re.fullmatch(r'[a-f0-9]{64}', job.get('png_sha256', '')):
        raise ValueError('MissingMediaHash')
    title = job.get('title', '')
    if not isinstance(title, str) or not 1 <= len(title) <= 180 or any(ord(c) < 32 for c in title):
        raise ValueError('InvalidTitle')
    posts = job.get('posts', [])
    if not posts or len(posts) > 2:
        raise ValueError('InvalidPosts')
    networks = set()
    for p in posts:
        n = p.get('network')
        patterns = {'instagram': r'https://www\.instagram\.com/p/[A-Za-z0-9_-]+/',
                    'linkedin': r'https://(?:www\.)?linkedin\.com/feed/update/urn:li:(?:share|activity):[0-9]+/?'}
        if n not in patterns or n in networks or p.get('status') != 'PUBLISHED':
            raise ValueError('UnverifiedPublication')
        if not re.fullmatch(patterns[n], p.get('url', '')) or not p.get('id') or not p.get('uuid'):
            raise ValueError('InvalidPublishedReference')
        networks.add(n)
    # Stable across filenames and retries; one notice per edition/rubric.
    key = hashlib.sha256((rubric + ':' + name).encode()).hexdigest()[:24]
    links = '\n'.join(p['network'].capitalize() + ': ' + p['url'] for p in posts)
    caption = f'Italia Compete | {rubric}\nPubblicato: {title}\n\n{links}\n\nIrina'
    return key, caption


def approved(wa):
    result = json_request('https://graph.facebook.com/v23.0/' + WABA_ID +
        '/message_templates?name=' + TEMPLATE + '&fields=name,status,language,components&limit=100', wa.token)
    return any(t.get('name') == TEMPLATE and t.get('status') == 'APPROVED' and t.get('language') == 'it'
        and [c.get('text') for c in t.get('components', []) if c.get('type') == 'BODY'] == [BODY]
        and [c.get('format') for c in t.get('components', []) if c.get('type') == 'HEADER'] == ['IMAGE']
        for t in result.get('data', []))


def upload_image(wa, image):
    boundary = 'Irina' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="post.png"\r\n'
        'Content-Type: image/png\r\n\r\n').encode() + image + f'\r\n--{boundary}--\r\n'.encode()
    result = json.loads(request('https://graph.facebook.com/v23.0/' + PHONE_ID + '/media', wa.token,
        body, 'multipart/form-data; boundary=' + boundary))
    if not result.get('id'):
        raise ValueError('MissingMediaId')
    return result['id']


class Publications:
    def __init__(self, processor, client=None):
        import dropbox
        self.p = processor
        self.client = client or processor.archive.client.with_path_root(dropbox.common.PathRoot.namespace_id('2166447024'))
        self.last_reports = {}
        with processor.inbox.db() as db:
            db.execute("UPDATE publications SET state='uncertain', error='InterruptedSend' WHERE state='sending'")

    def report(self, key):
        import dropbox
        with self.p.inbox.db() as db:
            row = dict(db.execute('SELECT * FROM publications WHERE key=?', (key,)).fetchone())
            receipts = [dict(r) for r in db.execute('SELECT status,stamp FROM receipts WHERE id=?', (row['message_id'],))]
        job = json.loads(row.pop('payload'))
        row.update(rubrica=job['rubrica'], date=job['date'], basename=job['basename'], posts=job['posts'], receipts=receipts)
        body = json.dumps(row, ensure_ascii=False, indent=2).encode()
        if self.last_reports.get(key) != body:
            self.client.files_upload(body, BASE + '/Esiti/' + key + '.json', mode=dropbox.files.WriteMode.overwrite, mute=True)
            self.last_reports[key] = body

    def process(self, job):
        key, caption = validate(job)
        if self.p.primary not in self.p.whatsapp.owners:
            raise ValueError('PrimaryNotOwner')
        with self.p.inbox.db() as db:
            db.execute('INSERT OR IGNORE INTO publications (key,payload,state) VALUES (?,?,?)',
                       (key, json.dumps(job, ensure_ascii=False), 'queued'))
            row = dict(db.execute('SELECT * FROM publications WHERE key=?', (key,)).fetchone())
        if row['state'] not in ('queued', 'blocked'):
            self.report(key)
            return
        # Persisted first payload is immutable even if the queue file is edited.
        job = json.loads(row['payload'])
        _, caption = validate(job)
        window = time.time() - self.p.inbox.owner_last_seen(self.p.primary) < 23*3600 + 55*60
        if not window and not approved(self.p.whatsapp):
            with self.p.inbox.db() as db:
                db.execute("UPDATE publications SET state='blocked',error='ImageTemplateNotApproved' WHERE key=?", (key,))
            self.report(key)
            return
        meta = self.client.files_get_metadata(job['png_path'])
        if not 0 < meta.size <= 5*1024*1024:
            raise ValueError('ImageTooLarge')
        _, response = self.client.files_download(job['png_path'])
        image = response.content
        if not image.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(image).hexdigest() != job['png_sha256']:
            raise ValueError('ImageHashMismatch')
        mid = upload_image(self.p.whatsapp, image)
        payload = {'messaging_product': 'whatsapp', 'to': self.p.primary, 'type': 'image',
                   'image': {'id': mid, 'caption': caption}}
        if not window:
            links = ' '.join(p['network'].capitalize() + ': ' + p['url'] for p in job['posts'])
            payload = {'messaging_product': 'whatsapp', 'to': self.p.primary, 'type': 'template',
                'template': {'name': TEMPLATE, 'language': {'code': 'it'}, 'components': [
                    {'type': 'header', 'parameters': [{'type': 'image', 'image': {'id': mid}}]},
                    {'type': 'body', 'parameters': [{'type': 'text', 'text': v} for v in (job['rubrica'], job['title'], links)]}]}}
        with self.p.inbox.db() as db:
            changed = db.execute("UPDATE publications SET state='sending',error=NULL WHERE key=? AND state IN ('queued','blocked')", (key,)).rowcount
        if not changed:
            return
        state, sent, error = 'uncertain', None, None
        try:
            result = json_request('https://graph.facebook.com/v23.0/' + PHONE_ID + '/messages', self.p.whatsapp.token, payload)
            sent = result.get('messages', [{}])[0].get('id')
            if not sent:
                raise ValueError('MissingMessageId')
            state = 'accepted'
        except APIError as exc:
            state, error = ('rejected' if 400 <= exc.status < 500 else 'uncertain'), 'WhatsAppAPIError'
        except Exception:
            error = 'SendUncertain'
        with self.p.inbox.db() as db:
            db.execute('UPDATE publications SET state=?,message_id=?,error=? WHERE key=?', (state,sent,error,key))
        print(json.dumps({'event': 'irina_publication_result', 'key': key, 'state': state}), flush=True)
        self.report(key)

    def step(self):
        import dropbox
        result = self.client.files_list_folder(BASE + '/Coda', recursive=False)
        entries = result.entries
        while result.has_more:
            result = self.client.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)
        for entry in entries:
            if not isinstance(entry, dropbox.files.FileMetadata) or not entry.name.endswith('.json') or entry.size > 16384:
                continue
            try:
                _, response = self.client.files_download(entry.path_lower)
                self.process(json.loads(response.content))
            except Exception as exc:
                print(json.dumps({'event':'irina_publication_error','file':entry.name,'error':type(exc).__name__}), flush=True)
        # Reconcile receipts even after a queue file is archived.
        with self.p.inbox.db() as db:
            keys = [r['key'] for r in db.execute('SELECT key FROM publications')]
        for key in keys:
            self.report(key)


def run(processor, stop):
    notices = Publications(processor)
    print(json.dumps({'event':'irina_publications_ready','recipient':'primary_owner','poll_seconds':60}), flush=True)
    while not stop.is_set():
        try:
            notices.step()
        except Exception as exc:
            print(json.dumps({'event':'irina_publications_error','error':type(exc).__name__}), flush=True)
        stop.wait(60)
