"""Authorized Italia Compete publication notices, Dropbox queue -> Fred only."""
import hashlib
import json
import re
import time
import uuid
import urllib.request
import urllib.error
from datetime import date

from irina_inbox import PHONE_ID, WABA_ID
from irina_processor import APIError, NoRedirect, json_request, request

BASE = '/Projects/Italia Compete/Gestione editoriale/Notifiche Irina'
MEDIA = '/Projects/Italia Compete/post_IC_new/'
TEMPLATE = 'irina_italia_compete_pubblicato_v1'
BODY = ('Italia Compete: aggiornamento sulle pubblicazioni della rubrica {{1}}.\n'
        'È stato pubblicato il contenuto: {{2}}.\n'
        'Puoi visualizzare e ripostare i post da questi link: {{3}}.\n'
        'In allegato trovi la grafica pubblicata.\nIrina')
# First submission returned HTTP 400 and exact-name lookup confirmed no template.
# A revised body may be submitted once; uncertain attempts are never retried.
SUBMISSION_KEY = TEMPLATE + ':expanded_body'


class TemplateAPIError(APIError):
    def __init__(self, status, detail):
        super().__init__(status)
        self.detail = {k: detail.get(k) for k in ('code', 'error_subcode', 'error_user_title') if k in detail}


def template_json(url, token, payload=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=90) as response:
            return json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(65536)).get('error', {})
        except Exception:
            detail = {}
        raise TemplateAPIError(exc.code, detail) from None


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


def sample_handle(wa, image):
    base = 'https://graph.facebook.com/v23.0/'
    session = template_json(base + 'app/uploads?file_length=' + str(len(image)) +
        '&file_type=image%2Fpng&file_name=post.png', wa.token, {})
    sid = session.get('id', '')
    if not re.fullmatch(r'upload:[A-Za-z0-9_:=?&.%-]+', sid):
        raise ValueError('InvalidUploadSession')
    req = urllib.request.Request(base + sid, data=image, headers={
        'Authorization': 'OAuth ' + wa.token, 'file_offset': '0', 'Content-Type': 'image/png'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=90) as response:
            result = json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        raise APIError(exc.code) from None
    if not result.get('h'):
        raise ValueError('MissingSampleHandle')
    return result['h']


class Publications:
    def __init__(self, processor, client=None):
        import dropbox
        self.p = processor
        self.client = client or processor.archive.client.with_path_root(dropbox.common.PathRoot.namespace_id('2166447024'))
        # Existing Notes OAuth grants read access; original archive token grants writes.
        # Reuse both configured grants without requesting or changing their scopes.
        self.reader = (processor.notes.factory().with_path_root(dropbox.common.PathRoot.namespace_id('2166447024'))
                       if client is None and getattr(processor, 'notes', None) else self.client)
        self.last_reports = {}
        self.next_template_check = 0
        with processor.inbox.db() as db:
            db.execute("UPDATE publications SET state='uncertain', error='InterruptedSend' WHERE state='sending'")

    def image(self, job):
        meta = self.reader.files_get_metadata(job['png_path'])
        if not 0 < meta.size <= 5*1024*1024:
            raise ValueError('ImageTooLarge')
        _, response = self.reader.files_download(job['png_path'])
        image = response.content
        if not image.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(image).hexdigest() != job['png_sha256']:
            raise ValueError('ImageHashMismatch')
        return image

    def ensure_template(self, job):
        """Submit once; reconcile Meta review without altering other templates."""
        import dropbox
        if time.time() < self.next_template_check:
            return
        self.next_template_check = time.time() + 300
        validate(job)
        url = 'https://graph.facebook.com/v23.0/' + WABA_ID + '/message_templates'
        token = self.p.whatsapp.token
        report = {'name': TEMPLATE, 'language': 'it'}
        try:
            report['stage'] = 'lookup'
            found = template_json(url + '?name=' + TEMPLATE + '&fields=id,name,status,language&limit=100', token)
            match = next((t for t in found.get('data', []) if t.get('name') == TEMPLATE and t.get('language') == 'it'), None)
            if match:
                report.update(id=match.get('id'), status=match.get('status'))
            else:
                with self.p.inbox.db() as db:
                    attempted = db.execute('SELECT value FROM settings WHERE key=?', (SUBMISSION_KEY,)).fetchone()
                if attempted:
                    try:
                        report.update(json.loads(attempted['value']))
                    except (ValueError, TypeError):
                        report.update(status='SUBMISSION_UNCERTAIN')
                else:
                    report['stage'] = 'sample_upload'
                    handle = sample_handle(self.p.whatsapp, self.image(job))
                    links = ' '.join(p['network'].capitalize() + ': ' + p['url'] for p in job['posts'])
                    payload = {'name': TEMPLATE, 'language': 'it', 'category': 'UTILITY', 'components': [
                        {'type': 'HEADER', 'format': 'IMAGE', 'example': {'header_handle': [handle]}},
                        {'type': 'BODY', 'text': BODY, 'example': {'body_text': [[job['rubrica'], job['title'], links]]}}]}
                    with self.p.inbox.db() as db:
                        changed = db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)',
                                             (SUBMISSION_KEY, 'submission_started')).rowcount
                    if changed:
                        report['stage'] = 'submission'
                        result = template_json(url, token, payload)
                        report.update(id=result.get('id'), status=result.get('status', 'PENDING'))
                    else:
                        report.update(status='SUBMISSION_UNCERTAIN')
        except Exception as exc:
            report.update(status='CHECK_FAILED', error=type(exc).__name__)
            if isinstance(exc, APIError):
                report['http_status'] = exc.status
            if isinstance(exc, TemplateAPIError):
                report.update(exc.detail)
        if report.get('stage') == 'submission':
            with self.p.inbox.db() as db:
                db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(report), SUBMISSION_KEY))
        body = json.dumps(report, ensure_ascii=False, indent=2).encode()
        if self.last_reports.get('template') != body:
            self.client.files_upload(body, BASE + '/Esiti/template.json', mode=dropbox.files.WriteMode.overwrite, mute=True)
            self.last_reports['template'] = body

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
        mid = upload_image(self.p.whatsapp, self.image(job))
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
        result = self.reader.files_list_folder(BASE + '/Coda', recursive=False)
        entries = result.entries
        while result.has_more:
            result = self.reader.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)
        for entry in entries:
            if not isinstance(entry, dropbox.files.FileMetadata) or not entry.name.endswith('.json') or entry.size > 16384:
                continue
            try:
                _, response = self.reader.files_download(entry.path_lower)
                job = json.loads(response.content)
                self.process(job)
                self.ensure_template(job)
            except Exception as exc:
                print(json.dumps({'event':'irina_publication_error','file':entry.name,'error':type(exc).__name__, 'required_scopes':[x for x in ('files.metadata.read','files.content.read','files.content.write') if x in str(exc)]}), flush=True)
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
            print(json.dumps({'event':'irina_publications_error','error':type(exc).__name__, 'required_scopes':[x for x in ('files.metadata.read','files.content.read','files.content.write') if x in str(exc)]}), flush=True)
        stop.wait(60)
