"""Separate, opt-in weekly digest delivery. Never generates editorial content."""
import hashlib
import io
import json
import os
import re
import time
import uuid
from datetime import date, datetime, time as clock, timedelta
from zoneinfo import ZoneInfo

from irina_inbox import PHONE_ID, WABA_ID
from irina_processor import APIError, json_request, request
from irina_digest_costs import Budget, CostBlocked, renew_rates

BASE = '/Projects/Digest Fred'
TEMPLATE = 'irina_digest_settimanale_v1'
BODY = ('CONTROLUCE, il digest economico settimanale richiesto, è pronto: edizione {{1}}.\n'
        'In allegato il PDF di due pagine con notizie e sintesi interpretativa.\nIrina')
MAX_BYTES = 10 * 1024 * 1024


def validate(job):
    expected = {'schema', 'edition', 'pdf_path', 'pdf_sha256', 'editorial_verified', 'sources', 'edition_number'}
    if not isinstance(job, dict) or set(job) != expected or job['schema'] != 1:
        raise ValueError('InvalidDigestSchema')
    if type(job['edition_number']) is not int or job['edition_number'] < 1:
        raise ValueError('InvalidEditionNumber')
    day = date.fromisoformat(job['edition'])
    if day.weekday() != 5 or day < date(2026, 10, 3):
        raise ValueError('InvalidSaturdayEdition')
    if job['pdf_path'] != BASE + '/PDF/' + day.isoformat() + '.pdf':
        raise ValueError('InvalidDigestPath')
    if not re.fullmatch('[a-f0-9]{64}', job['pdf_sha256']):
        raise ValueError('InvalidDigestHash')
    if job['editorial_verified'] is not True or not isinstance(job['sources'], list) or not job['sources']:
        raise ValueError('EditorialVerificationRequired')
    for source in job['sources']:
        if not isinstance(source, dict) or set(source) != {'url', 'title', 'published_date'}:
            raise ValueError('InvalidSource')
        if not isinstance(source['url'], str) or not re.fullmatch(r'https://[^\s]+', source['url']):
            raise ValueError('InvalidSourceURL')
        if not isinstance(source['title'], str) or not 1 <= len(source['title']) <= 500:
            raise ValueError('InvalidSourceTitle')
        if date.fromisoformat(source['published_date']) > day:
            raise ValueError('FutureSource')
    due = datetime.combine(day, clock(8), ZoneInfo('Europe/Rome'))
    return day.isoformat(), due.timestamp()


def validate_pdf(data, digest):
    from pypdf import PdfReader
    if not 0 < len(data) <= MAX_BYTES or not data.startswith(b'%PDF-'):
        raise ValueError('InvalidPDFBytes')
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('PDFHashMismatch')
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) != 2:
        raise ValueError('PDFMustHaveTwoUnencryptedPages')
    for page in reader.pages:
        box = page.mediabox
        crop = page.cropbox
        if (abs(float(box.width)-595.276) > 1 or abs(float(box.height)-841.89) > 1
                or any(abs(float(a)-float(b)) > 1 for a,b in zip(box, crop))
                or page.get('/Rotate', 0) % 360 or float(page.get('/UserUnit', 1)) != 1):
            raise ValueError('PDFMustBePortraitA4')
    # Delivery validates bytes/layout; producer is responsible for factual and visual QA.
    return data


def approved_template(wa):
    result = json_request('https://graph.facebook.com/v23.0/' + WABA_ID +
        '/message_templates?name=' + TEMPLATE + '&fields=name,status,language,category,components&limit=100', wa.token)
    return next((t for t in result.get('data', []) if t.get('name') == TEMPLATE and t.get('status') == 'APPROVED' and t.get('language') == 'it'
        and [c.get('text') for c in t.get('components', []) if c.get('type') == 'BODY'] == [BODY]
        and [c.get('format') for c in t.get('components', []) if c.get('type') == 'HEADER'] == ['DOCUMENT']
        and all(c.get('type') in ('BODY','HEADER','FOOTER') for c in t.get('components', []))
        ), None)


def approved(wa):
    return approved_template(wa) is not None


def upload_pdf(wa, data):
    boundary = 'Irina' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="digest.pdf"\r\n'
        'Content-Type: application/pdf\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    result = json.loads(request('https://graph.facebook.com/v23.0/' + PHONE_ID + '/media', wa.token,
        body, 'multipart/form-data; boundary=' + boundary))
    if not result.get('id'):
        raise ValueError('MissingMediaId')
    return result['id']


class Digest:
    def __init__(self, processor, client=None, now=time.time, enabled=False):
        import dropbox
        self.p = processor
        self.client = client or processor.archive.client.with_path_root(dropbox.common.PathRoot.namespace_id('2166447024'))
        self.reader = (processor.notes.factory().with_path_root(dropbox.common.PathRoot.namespace_id('2166447024'))
                       if client is None and getattr(processor, 'notes', None) else self.client)
        self.last_reports = {}
        self.now, self.enabled = now, enabled
        self.budget = Budget(self.p.inbox, now)
        with self.p.inbox.db() as db:
            db.execute("UPDATE digests SET state='uncertain', error='InterruptedSend' WHERE state='sending'")
            db.execute("UPDATE digest_tests SET state='uncertain', error='InterruptedSend' WHERE state='sending'")

    def report(self, key):
        import dropbox
        with self.p.inbox.db() as db:
            row = dict(db.execute('SELECT * FROM digests WHERE key=?', (key,)).fetchone())
            cost=db.execute('SELECT year,gross_micros,category,reserved_at FROM digest_costs WHERE key=?',('edition:'+key,)).fetchone()
            row['cost_reservation']=dict(cost) if cost else None
            row['receipts'] = [dict(r) for r in db.execute('SELECT status,stamp FROM receipts WHERE id=?', (row['message_id'],))]
        row['manifest'] = json.loads(row.pop('payload'))
        body = json.dumps(row, ensure_ascii=False, indent=2).encode()
        if self.last_reports.get(key) != body:
            self.client.files_upload(body, BASE + '/Esiti/' + key + '.json', mode=dropbox.files.WriteMode.overwrite, mute=True)
            self.last_reports[key] = body

    def state(self, key, state, error=None):
        with self.p.inbox.db() as db:
            db.execute('UPDATE digests SET state=?,error=? WHERE key=?', (state,error,key))
        self.report(key)

    def process(self, job):
        key, due = validate(job)
        if self.p.primary not in self.p.whatsapp.owners:
            raise ValueError('PrimaryNotOwner')
        payload = json.dumps(job, sort_keys=True, ensure_ascii=False)
        with self.p.inbox.db() as db:
            db.execute('INSERT OR IGNORE INTO digests(key,payload,state) VALUES (?,?,?)', (key,payload,'queued'))
            row = dict(db.execute('SELECT * FROM digests WHERE key=?', (key,)).fetchone())
        if row['payload'] != payload:
            raise ValueError('EditionManifestConflict')
        if row['state'] not in ('queued','blocked'):
            self.report(key)
            return
        if self.now() < due:
            self.report(key)
            return
        # Catch up only during Saturday; stale jobs never become unexpected weekday sends.
        if datetime.fromtimestamp(self.now(), ZoneInfo('Europe/Rome')).date() != date.fromisoformat(key):
            self.state(key, 'expired', 'SaturdayWindowMissed')
            return
        with self.p.inbox.db() as db:
            active=db.execute("SELECT value FROM settings WHERE key='digest_delivery_active_v1'").fetchone()
        if not (self.enabled or (active and active['value']=='true')):
            self.state(key, 'blocked', 'DeliveryNotActivated')
            return
        window = self.now() - self.p.inbox.owner_last_seen(self.p.primary) < 23*3600+55*60
        template = None if window else approved_template(self.p.whatsapp)
        if not window and not template:
            self.state(key, 'blocked', 'DocumentTemplateNotApproved')
            return
        try:
            meta = self.reader.files_get_metadata(job['pdf_path'])
            if not 0 < meta.size <= MAX_BYTES:
                raise ValueError('InvalidPDFSize')
            _, response = self.reader.files_download(job['pdf_path'])
            data = validate_pdf(response.content, job['pdf_sha256'])
        except Exception as exc:
            self.state(key, 'blocked', type(exc).__name__ if not isinstance(exc, ValueError) else str(exc))
            return
        try:
            micros, category = self.budget.quote('SERVICE' if window else template.get('category'), self.p.primary)
        except CostBlocked as exc:
            self.state(key, 'blocked', str(exc))
            return
        mid = upload_pdf(self.p.whatsapp, data)
        document = {'id':mid, 'filename':'CONTROLUCE-' + key + '.pdf'}
        payload = {'messaging_product':'whatsapp', 'to':self.p.primary, 'type':'document',
                   'document':dict(document, caption='CONTROLUCE | ' + key + '\nIrina')}
        if not window:
            payload = {'messaging_product':'whatsapp','to':self.p.primary,'type':'template',
                'template':{'name':TEMPLATE,'language':{'code':'it'},'components':[
                    {'type':'header','parameters':[{'type':'document','document':document}]},
                    {'type':'body','parameters':[{'type':'text','text':key}]}]}}
        try:
            with self.p.inbox.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self.budget.reserve(db, 'edition:'+key, micros, category)
                changed = db.execute("UPDATE digests SET state='sending',error=NULL WHERE key=? AND state IN ('queued','blocked')",(key,)).rowcount
        except CostBlocked as exc:
            self.state(key, 'blocked', str(exc))
            return
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
            db.execute('UPDATE digests SET state=?,message_id=?,error=? WHERE key=?',(state,sent,error,key))
        self.report(key)

    def step(self):
        try:
            refreshed=renew_rates(self.p.inbox,self.now())
            if refreshed:print(json.dumps({'event':'irina_digest_rates',**refreshed}),flush=True)
        except Exception as exc:
            result={'blocked':str(exc) if isinstance(exc,CostBlocked) else type(exc).__name__}
            if result!=getattr(self,'last_rate_result',None):
                print(json.dumps({'event':'irina_digest_rates',**result}),flush=True)
            self.last_rate_result=result
        # Existing digest loop finishes an explicitly requested provision only
        # after exact Meta approval, verified gross cost, and a delivered test.
        from types import SimpleNamespace
        from irina_digest_admin import continue_activation
        try:
            result=continue_activation(SimpleNamespace(inbox=self.p.inbox,whatsapp=self.p.whatsapp,
                primary=self.p.primary,reader=self.reader,writer=self.client))
            if result and result!=getattr(self,'last_activation_result',None):
                print(json.dumps({'event':'irina_digest_activation',**result}),flush=True)
            self.last_activation_result=result
        except Exception as exc:
            result={'blocked':str(exc) if isinstance(exc,CostBlocked) else type(exc).__name__}
            if result!=getattr(self,'last_activation_result',None):
                print(json.dumps({'event':'irina_digest_activation',**result}),flush=True)
            self.last_activation_result=result
        import dropbox
        result = self.reader.files_list_folder(BASE + '/Coda', recursive=False)
        entries = list(result.entries)
        while result.has_more:
            result = self.reader.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)
        for entry in entries:
            if not isinstance(entry, dropbox.files.FileMetadata) or not entry.name.endswith('.json') or entry.size > 32768:
                continue
            try:
                _, response = self.reader.files_download(entry.path_lower)
                self.process(json.loads(response.content))
            except Exception as exc:
                # Never print queue content, paths, phone numbers or SDK exception details.
                print(json.dumps({'event':'irina_digest_job_error','error':type(exc).__name__}), flush=True)
        with self.p.inbox.db() as db:
            keys = [r['key'] for r in db.execute('SELECT key FROM digests')]
        for key in keys:
            self.report(key)


def run(processor, stop):
    digest = Digest(processor, enabled=os.environ.get('IRINA_DIGEST_ENABLED') == 'true')
    print(json.dumps({'event':'irina_digest_ready','enabled':digest.enabled,'timezone':'Europe/Rome'}), flush=True)
    while not stop.is_set():
        try:
            digest.step()
        except Exception as exc:
            print(json.dumps({'event':'irina_digest_error','error':type(exc).__name__}), flush=True)
        stop.wait(60)
