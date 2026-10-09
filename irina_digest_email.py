"""Opt-in delivery of the same verified digest PDF via the existing Resend key.

No received-mail reads, new credentials, domains, plans, or email for edition 001.
"""
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date,datetime
from email.utils import formataddr
from zoneinfo import ZoneInfo
from irina_digest import BASE,validate,validate_pdf,MAX_BYTES
from irina_processor import NoRedirect

MIN_EDITION=date(2026,10,10)


class EmailBlocked(ValueError):
    pass


ERROR_CATEGORIES=frozenset({'missing_api_key','restricted_api_key','suspended_api_key',
    'invalid_api_key','invalid_permission','validation_error','rate_limit_exceeded',
    'application_error','service_unavailable'})


class ResendHTTPError(EmailBlocked):
    def __init__(self,code,category='unknown'):
        super().__init__('ResendHTTP'+str(code))
        self.category=category


class Resend:
    def __init__(self,key):self.key=key
    def call(self,path,payload=None,idempotency=None):
        headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json',
                 'Accept':'application/json','User-Agent':'Irina-CONTROLUCE/1.0'}
        if idempotency:headers['Idempotency-Key']=idempotency
        req=urllib.request.Request('https://api.resend.com'+path,
            data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as r:
                data=r.read(2_000_001)
            if len(data)>2_000_000:raise EmailBlocked('ResendResponseTooLarge')
            return json.loads(data)
        except urllib.error.HTTPError as e:
            category='unknown'
            try:
                body=e.read(8193)
                if len(body)<=8192:
                    value=json.loads(body)
                    name=value.get('name') if isinstance(value,dict) else None
                    if isinstance(name,str) and name in ERROR_CATEGORIES:category=name
            except Exception:pass
            raise ResendHTTPError(e.code,category) from None


def free_quota(usage):
    try:
        periods=usage['emails'];daily=periods['daily'];monthly=periods['monthly']
        # Resend documents numeric daily caps only for the free sending tier.
        # Paid plans/null limits, exhausted or ambiguous quota require review.
        if (type(daily['limit']) is not int or not 0<daily['limit']<=100
                or type(monthly['limit']) is not int or not 0<monthly['limit']<=3000):
            raise ValueError()
        for value in [daily,monthly]:
            if type(value['used']) is not int or not 0<=value['used']<value['limit']:raise ValueError()
        return True
    except Exception:raise EmailBlocked('ResendZeroCostQuotaNotVerified') from None


def address(value):
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',value) is not None


def sender_parts(value):
    if address(value):return '',value
    if not isinstance(value,str):raise EmailBlocked('InvalidDigestEmailSender')
    match=re.fullmatch(r'([^<>]+) <([^<>]+)>',value)
    if not match:raise EmailBlocked('InvalidDigestEmailSender')
    name,mailbox=match.groups()
    if (name!=name.strip() or not 1<=len(name)<=100 or any(ord(c)<32 or ord(c)==127 for c in name)
            or '"' in name or chr(92) in name or not address(mailbox)):
        raise EmailBlocked('InvalidDigestEmailSender')
    return name,mailbox


class DigestEmail:
    def __init__(self,inbox,reader,writer,now=time.time,env=None,provider=None):
        self.inbox,self.reader,self.writer,self.now=inbox,reader,writer,now
        self.env=os.environ if env is None else env
        self.provider=provider or Resend(self.env.get('IRINA_RESEND_API_KEY',''))
        self.next_preflight=0;self.last_status=None
        with inbox.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS digest_emails (
                edition TEXT PRIMARY KEY,payload TEXT NOT NULL,state TEXT NOT NULL,
                provider_id TEXT,error TEXT,last_event TEXT,next_check REAL DEFAULT 0);''')
            db.execute("UPDATE digest_emails SET state='uncertain',error='InterruptedEmailSend' WHERE state='sending'")

    def preflight(self):
        if not self.env.get('IRINA_RESEND_API_KEY'):raise EmailBlocked('ExistingResendKeyMissing')
        domains=self.provider.call('/domains?limit=100')
        if domains.get('has_more'):raise EmailBlocked('ResendDomainListIncomplete')
        verified=[d['name'] for d in domains.get('data',[]) if d.get('status')=='verified'
                  and d.get('capabilities',{}).get('sending')=='enabled']
        configured_sender=self.env.get('IRINA_DIGEST_EMAIL_FROM','')
        sender_name,sender=sender_parts(configured_sender)
        recipient=self.env.get('IRINA_DIGEST_EMAIL_TO','')
        quota=False;quota_error=None;quota_category=None
        try:quota=free_quota(self.provider.call('/usage'))
        except EmailBlocked as e:quota_error=str(e);quota_category=getattr(e,'category','unknown')
        sender_verified=address(sender) and sender.split('@')[1].lower() in [d.lower() for d in verified]
        result={'enabled':self.env.get('IRINA_DIGEST_EMAIL_ENABLED')=='true',
                'sender_configured':bool(configured_sender),'sender_verified':sender_verified,
                'recipient_configured':address(recipient),'zero_cost_quota_verified':quota,
                'quota_error':quota_error,'quota_error_category':quota_category}
        if sender_verified:
            result['sending_address']=sender
            result['sending_name']=sender_name
            result['sending_from']=formataddr((sender_name,sender))
        return result

    def status(self):
        if self.now()<self.next_preflight:return
        self.next_preflight=self.now()+3600
        try:
            if self.env.get('IRINA_DIGEST_EMAIL_PREFLIGHT')!='true':
                raise EmailBlocked('EmailSetupAwaitingSafeConfiguration')
            result=self.preflight()
        except Exception as e:
            result={'enabled':False,'blocked':str(e) if isinstance(e,EmailBlocked) else type(e).__name__}
            if isinstance(e,ResendHTTPError):result['error_category']=e.category
        body=json.dumps(result,sort_keys=True).encode()
        if body!=self.last_status:
            import dropbox
            self.writer.files_upload(body,BASE+'/Esiti/Email-stato.json',mode=dropbox.files.WriteMode.overwrite,mute=True)
            self.last_status=body

    def report(self,edition):
        with self.inbox.db() as db:
            row=dict(db.execute('SELECT * FROM digest_emails WHERE edition=?',(edition,)).fetchone())
        row.pop('payload');row.pop('next_check')
        import dropbox
        self.writer.files_upload(json.dumps(row).encode(),BASE+'/Esiti/email-'+edition+'.json',
                                 mode=dropbox.files.WriteMode.overwrite,mute=True)

    def process(self,job):
        edition,due=validate(job)
        # Coordinator owns the immediate 001 email. This route never backfills it.
        if date.fromisoformat(edition)<MIN_EDITION:return
        if self.env.get('IRINA_DIGEST_EMAIL_ENABLED')!='true' or self.now()<due:return
        recipient=self.env.get('IRINA_DIGEST_EMAIL_TO','')
        sender_name,sender=sender_parts(self.env.get('IRINA_DIGEST_EMAIL_FROM',''))
        if not address(recipient) or not address(sender):raise EmailBlocked('DigestEmailAddressesNotConfigured')
        frozen=json.dumps({'manifest':job,'recipient':recipient,'sender':sender},sort_keys=True)
        with self.inbox.db() as db:
            db.execute("INSERT OR IGNORE INTO digest_emails(edition,payload,state) VALUES (?,?,'queued')",(edition,frozen))
            row=dict(db.execute('SELECT * FROM digest_emails WHERE edition=?',(edition,)).fetchone())
        if row['payload']!=frozen:raise EmailBlocked('EmailEditionConflict')
        if row['state'] not in {'queued','blocked'}:
            self.receipt(row,recipient);self.report(edition);return
        if datetime.fromtimestamp(self.now(),ZoneInfo('Europe/Rome')).date()!=date.fromisoformat(edition):
            with self.inbox.db() as db:db.execute("UPDATE digest_emails SET state='expired' WHERE edition=?",(edition,))
            self.report(edition);return
        try:
            status=self.preflight()
            if not status['sender_verified']:raise EmailBlocked('VerifiedSendingAddressRequired')
            if not status['zero_cost_quota_verified']:raise EmailBlocked('ResendZeroCostQuotaNotVerified')
            meta=self.reader.files_get_metadata(job['pdf_path'])
            if not 0<meta.size<=MAX_BYTES:raise EmailBlocked('InvalidPDFSize')
            _,response=self.reader.files_download(job['pdf_path'])
            data=validate_pdf(response.content,job['pdf_sha256'])
        except Exception as e:
            with self.inbox.db() as db:db.execute("UPDATE digest_emails SET state='blocked',error=? WHERE edition=?",
                (str(e) if isinstance(e,EmailBlocked) else type(e).__name__,edition))
            self.report(edition);return
        key='controluce-'+edition+'-'+hashlib.sha256(frozen.encode()).hexdigest()
        payload={'from':formataddr((sender_name,sender)),'to':[recipient],'subject':'CONTROLUCE | '+str(job['edition_number']).zfill(3)+' | '+edition,
                 'text':'In allegato CONTROLUCE, il digest economico settimanale di due pagine.\nIrina',
                 'attachments':[{'filename':'CONTROLUCE-'+edition+'.pdf','content':base64.b64encode(data).decode()}]}
        with self.inbox.db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed=db.execute("UPDATE digest_emails SET state='sending',error=NULL WHERE edition=? AND state IN ('queued','blocked')",(edition,)).rowcount
        if not changed:return
        state,provider_id,error='uncertain',None,None
        try:
            result=self.provider.call('/emails',payload,idempotency=key)
            provider_id=result.get('id')
            if not isinstance(provider_id,str) or not re.fullmatch('[a-fA-F0-9-]{36}',provider_id):raise ValueError('MissingProviderID')
            state='accepted'
        except Exception as e:
            error=str(e) if isinstance(e,EmailBlocked) else type(e).__name__
            if isinstance(e,EmailBlocked) and re.fullmatch('ResendHTTP4[0-9]{2}',str(e)):state='rejected'
        with self.inbox.db() as db:db.execute('UPDATE digest_emails SET state=?,provider_id=?,error=? WHERE edition=?',
                                              (state,provider_id,error,edition))
        self.report(edition)

    def pending(self):
        if self.env.get('IRINA_DIGEST_EMAIL_ENABLED')!='true':return
        with self.inbox.db() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM digest_emails WHERE state='accepted'")]
        for row in rows:
            recipient=json.loads(row['payload'])['recipient']
            self.receipt(row,recipient);self.report(row['edition'])

    def receipt(self,row,recipient):
        if row['state']!='accepted' or self.now()<row['next_check']:return
        with self.inbox.db() as db:db.execute('UPDATE digest_emails SET next_check=? WHERE edition=?',(self.now()+60,row['edition']))
        result=self.provider.call('/emails/'+row['provider_id'])
        if result.get('to')!=[recipient]:raise EmailBlocked('EmailReceiptRecipientMismatch')
        event=result.get('last_event')
        state='delivered' if event=='delivered' else ('failed' if event in {'bounced','complained','failed'} else 'accepted')
        with self.inbox.db() as db:db.execute('UPDATE digest_emails SET state=?,last_event=? WHERE edition=?',(state,event,row['edition']))
