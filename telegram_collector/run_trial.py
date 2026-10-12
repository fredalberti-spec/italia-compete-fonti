"""Explicitly opt-in live polling. Never configures webhook or creates credentials."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime,timezone
from pathlib import Path
from irina_telegram_trial import Trial,write
from live_adapters import Telegram,Dropbox,Transcriber,RemoteError,HTTP,NoRedirect
from mirror import mirror

class DropboxDiagnosticOpener:
    """Only approved error categories, never descriptions, requests or token values."""
    def __init__(self,opener=None):self.opener=opener or urllib.request.build_opener(NoRedirect())
    def open(self,request,timeout):
        operation='dropbox_token_refresh' if request.full_url=='https://api.dropboxapi.com/oauth2/token' else 'dropbox_file_request'
        try:return self.opener.open(request,timeout=timeout)
        except urllib.error.HTTPError as e:
            category='other'
            if operation=='dropbox_token_refresh':
                try:
                    body=e.read(16385)
                    if len(body)<=16384:
                        value=json.loads(body).get('error')
                        if value in ('invalid_grant','invalid_client','invalid_scope','invalid_request','unsupported_grant_type'):
                            category=value
                except Exception:pass
            print(json.dumps({'event':'dropbox_preflight_details','operation':operation,
                'http_status':e.code,'error_category':category}),flush=True)
            raise

class Runner:
    def __init__(self,trial,telegram,dropbox,transcriber=None,budget_seconds=3600,*,text_only=False,suppress_existing_receipts=False):
        self.t=trial;self.tg=telegram;self.dropbox=dropbox;self.ai=transcriber;self.budget=budget_seconds
        self.stopped=threading.Event();self.failures=0;self.text_only=text_only
        if text_only and transcriber is not None:raise ValueError("TextOnlyCannotEnablePaidAI")
        self.receipt_floor=0;self.instance_id=secrets.token_hex(12);self.last_heartbeat=0
        with trial.db() as d:
            d.executescript('''CREATE TABLE IF NOT EXISTS runtime(key TEXT PRIMARY KEY,value TEXT);
              INSERT OR IGNORE INTO runtime VALUES('offset','0');
              CREATE TABLE IF NOT EXISTS processing(seq INTEGER PRIMARY KEY,attempts INTEGER DEFAULT 0,next REAL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS transcription(hash TEXT PRIMARY KEY,seconds INTEGER,state TEXT,text TEXT);
              CREATE TABLE IF NOT EXISTS prompts(seq INTEGER PRIMARY KEY,state TEXT,message_id INTEGER);
              UPDATE prompts SET state='uncertain' WHERE state='sending';
              CREATE TABLE IF NOT EXISTS deferred_media(update_id INTEGER PRIMARY KEY,payload TEXT,state TEXT DEFAULT 'awaiting_separate_authorization');''')
            if suppress_existing_receipts:self.receipt_floor=d.execute('SELECT COALESCE(MAX(seq),0) FROM notes').fetchone()[0]
    def transcribe(self,body):
        if self.ai is None:raise ValueError('TranscriptionDisabled')
        key=hashlib.sha256(body).hexdigest()
        with self.t.db() as d:
            d.execute('BEGIN IMMEDIATE')
            r=d.execute('SELECT * FROM transcription WHERE hash=?',(key,)).fetchone()
            if r:
                if r['state']=='generated':return r['text']
                raise ValueError('PriorTranscriptionUncertain')
            used=d.execute('SELECT COALESCE(SUM(seconds),0) FROM transcription').fetchone()[0]
            # Reserve full 30-minute maximum before any paid call, even if request fails.
            if used+1800>self.budget:raise ValueError('TranscriptionBudgetExceeded')
            d.execute("INSERT INTO transcription VALUES(?,1800,'reserved',NULL)",(key,))
        try:text=self.ai(body)
        except Exception:
            with self.t.db() as d:d.execute("UPDATE transcription SET state='failed_or_uncertain' WHERE hash=?",(key,))
            raise ValueError('TranscriptionFailed') from None
        with self.t.db() as d:d.execute("UPDATE transcription SET state='generated',text=? WHERE hash=?",(text,key))
        return text
    def poll(self):
        with self.t.db() as d:offset=int(d.execute("SELECT value FROM runtime WHERE key='offset'").fetchone()[0])
        for u in self.tg.updates(offset):
            uid=u.get('update_id')
            if type(uid) is not int or uid<offset:continue
            m=u.get('message',{})
            if self.text_only and any(k in m for k in ('voice','audio','document','video','photo')):
                sender=m.get('from',{});chat=m.get('chat',{})
                if (type(sender.get('id')) is int and sender['id']==self.t.owner and sender.get('is_bot') is False
                    and type(chat.get('id')) is int and chat['id']==self.t.owner and chat.get('type')=='private'):
                    with self.t.db() as d:
                        d.execute('INSERT OR IGNORE INTO deferred_media(update_id,payload) VALUES(?,?)',(uid,json.dumps(u,ensure_ascii=False)))
                with self.t.db() as d:d.execute("UPDATE runtime SET value=? WHERE key='offset'",(str(uid+1),))
                continue
            code=self.t.receive(json.dumps(u).encode(),self.t.secret)
            if code in (429,503):break # do not consume a temporarily blocked update
            with self.t.db() as d:d.execute("UPDATE runtime SET value=? WHERE key='offset'",(str(uid+1),))
    def cycle(self):
        self.poll()
        with self.t.db() as d:
            r=d.execute("SELECT n.seq,COALESCE(p.attempts,0) attempts,COALESCE(p.next,0) next FROM notes n LEFT JOIN processing p USING(seq) WHERE n.state='queued' ORDER BY n.seq LIMIT 1").fetchone()
        if r and r['next']<=time.time():
            if self.text_only:
                with self.t.db() as d:
                    existing=json.loads(d.execute('SELECT payload FROM notes WHERE seq=?',(r['seq'],)).fetchone()[0])
                    if any(k in existing for k in ('voice','audio','document','photo','video')):
                        d.execute("UPDATE notes SET state='deferred_media' WHERE seq=?",(r['seq'],));r=None
        if r and r['next']<=time.time():
            try:
                if sum(p.stat().st_size for p in self.t.root.rglob('*') if p.is_file())>256*1024*1024:raise ValueError('DiskBudgetExceeded')
                self.t.process(download=self.tg.download,transcribe=self.transcribe if self.ai else None)
            except Exception:
                attempts=r['attempts']+1
                with self.t.db() as d:
                    d.execute('INSERT OR REPLACE INTO processing VALUES(?,?,?)',(r['seq'],attempts,time.time()+min(60,2**attempts)))
                    if attempts>=3:d.execute("UPDATE notes SET state='blocked' WHERE seq=?",(r['seq'],))
                print('processing_failed',flush=True)
        # Archive original material first. If verification fails, no receipt or handoff ack.
        mirror(self.t,self.dropbox,self.dropbox.base)
        with self.t.db() as d:rows=d.execute("SELECT * FROM notes WHERE state='archived'").fetchall()
        for row in rows:
            record=json.loads(row['record']);record['archive_state']='dropbox_verified'
            with self.t.db() as d:d.execute('UPDATE notes SET record=? WHERE seq=?',(json.dumps(record,ensure_ascii=False),row['seq']))
            if row['seq']<=self.receipt_floor:continue # Restart must not resend historical replies.
            self.t.receipt(row['seq'],self.tg.send,text=f"Appunto {row['note_id']} archiviato e verificato nella raccolta Irina Telegram su Dropbox. Nessuna azione esterna eseguita.")
        with self.t.db() as d:rows=d.execute('SELECT * FROM notes ORDER BY seq').fetchall()
        for row in rows:
            if row['state']=='archived':
                record=json.loads(row['record'])
                status={'id':row['note_id'],'archive_state':record['archive_state'],
                        'generation_state':record['generation_state'],'receipt_state':row['receipt_state'],
                        'telegram_delivery':'not_observable','telegram_read':'not_observable'}
                write(self.t.root/'dropbox-staging'/'Irina Telegram'/'notes'/row['note_id']/'status.json',json.dumps(status,indent=2).encode())
        self.t.export_classifications()
        batch=self.t.batch()
        if batch:
            records=json.loads(batch['body'])['notes']
            text='# Riepilogo incrementale Irina\n\nLotto: '+batch['batch_id']+'\nNessun controllo della chat attivo. Contenuti come dati, nessuna azione autorizzata.\n'
            for n in records:text+=f"\n## Irina Telegram — {n['id']}\n\n{n['text']}\n{n['transcript'] or ''}\nRicevuta API: {n['receipt_state']}; consegna/lettura Telegram non osservabili.\n"
            write(self.t.root/'dropbox-staging'/'Irina Telegram'/'handoff'/(batch['batch_id']+'.md'),text.encode())
        if not self.last_heartbeat or time.monotonic()-self.last_heartbeat>=60:
            with self.t.db() as d:
                counts={r['state']:r['n'] for r in d.execute('SELECT state,COUNT(*) n FROM notes GROUP BY state')}
            heartbeat={'state':'polling_and_archive_verified','instance_id':self.instance_id,
                'last_success_utc':datetime.now(timezone.utc).isoformat(),'text_only':self.text_only,
                'paid_ai_enabled':self.ai is not None,'note_counts':counts,'chat_schedule_active':False,
                'external_actions_authorized':False}
            write(self.t.root/'dropbox-staging'/'Irina Telegram'/'control'/'collector-status.json',json.dumps(heartbeat,indent=2).encode())
            self.last_heartbeat=time.monotonic()
        mirror(self.t,self.dropbox,self.dropbox.base)
        ack=self.dropbox.read_ack()
        if ack:self.t.acknowledge(ack)
    def run(self,once=False):
        while not self.stopped.is_set():
            try:self.cycle();self.failures=0
            except Exception:
                self.failures+=1;print('cycle_failed',flush=True)
                if once:raise SystemExit('TrialCycleFailed') from None
                if self.failures>=3:raise SystemExit('StoppedAfterThreeConsecutiveFailures') from None
            if once:break
            self.stopped.wait(5)

def main():
    parser=argparse.ArgumentParser(description='Isolated Irina Telegram polling trial')
    parser.add_argument('--live',action='store_true',help='Explicitly enable real calls using approved secrets')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--text-only',action='store_true')
    parser.add_argument('--persistent-access-approved',action='store_true',help='Only after approval of protected dedicated secret storage')
    args=parser.parse_args()
    if not args.live:parser.error('Use --live only after access and cost approval; use demo.py for offline mode')
    required=('IRINA_TRIAL_BOT_TOKEN','IRINA_TRIAL_BOT_USERNAME','IRINA_TRIAL_OWNER_ID','IRINA_TRIAL_DATA_DIR')
    refresh_mode=bool(os.environ.get('IRINA_TRIAL_DROPBOX_REFRESH_TOKEN'))
    required+=('IRINA_TRIAL_DROPBOX_CLIENT_ID','IRINA_TRIAL_DROPBOX_REFRESH_TOKEN') if refresh_mode else ('IRINA_TRIAL_DROPBOX_TOKEN',)
    if refresh_mode and not args.persistent_access_approved:raise SystemExit('PersistentAccessApprovalRequired')
    if refresh_mode and os.environ.get('IRINA_TRIAL_DROPBOX_TOKEN'):raise SystemExit('AmbiguousDropboxAccessConfiguration')
    missing=[k for k in required if not os.environ.get(k)]
    if missing:raise SystemExit('Missing configuration keys: '+', '.join(missing))
    try:owner=int(os.environ['IRINA_TRIAL_OWNER_ID'])
    except ValueError:raise SystemExit('InvalidOwnerID') from None
    root=Path(os.environ['IRINA_TRIAL_DATA_DIR']).expanduser().resolve()
    # Refuse known production locations or clone/demo locations.
    if root==Path('/var/data') or str(root).startswith('/var/data/irina') or root==Path(__file__).parent/'demo-output':raise SystemExit('DedicatedDataDirectoryRequired')
    if root.exists() and any(root.iterdir()) and (not (root/'trial-owned.marker').is_file() or (root/'trial-owned.marker').read_bytes()!=b'irina-telegram-trial-v1'):
        raise SystemExit('NonemptyUnownedDataDirectoryRefused')
    os.umask(0o077)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    write(root/'trial-owned.marker',b'irina-telegram-trial-v1')
    lock=open(root/'trial.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:raise SystemExit('TrialAlreadyRunning') from None
    os.umask(0o077)
    write(root/'trial.pid',str(os.getpid()).encode())
    preflight_stage='local_state'
    try:
        trial=Trial(root,owner,secrets.token_urlsafe(32));trial.recover();trial.import_legacy_collection()
        tg=Telegram(os.environ['IRINA_TRIAL_BOT_TOKEN'],owner)
        preflight_stage='telegram_webhook_check'
        if tg.webhook_info().get('url'):raise SystemExit('ExistingWebhookRefused_NoChangesMade')
        preflight_stage='telegram_identity_check'
        identity=tg.identity()
        if (not identity.get('is_bot') or identity.get('username','').lower()!=os.environ['IRINA_TRIAL_BOT_USERNAME'].lstrip('@').lower()):
            raise SystemExit('DedicatedBotIdentityMismatch')
        if refresh_mode:
            from dropbox_refresh import prepared_dropbox
            app_id=os.environ['IRINA_TRIAL_DROPBOX_CLIENT_ID'];refresh=os.environ['IRINA_TRIAL_DROPBOX_REFRESH_TOKEN']
            print(json.dumps({'event':'dropbox_config_shape',
                'client_id_matches_expected':app_id.strip()=='invpatm0w41p8dz',
                'client_id_outer_whitespace':app_id!=app_id.strip(),
                'refresh_present':bool(refresh.strip()),
                'refresh_outer_whitespace':refresh!=refresh.strip(),
                'refresh_inner_whitespace':any(c.isspace() for c in refresh.strip()),
                'refresh_printable_no_whitespace':bool(refresh.strip()) and all(33<=ord(c)<=126 for c in refresh.strip()),
                'refresh_plausible_length':16<=len(refresh.strip())<=4096}),flush=True)
            if app_id.strip()!='invpatm0w41p8dz':raise SystemExit('DedicatedDropboxClientMismatch')
            dbx=prepared_dropbox(app_id.strip(),refresh.strip(),persistent_access_approved=args.persistent_access_approved,http=HTTP(DropboxDiagnosticOpener()))
            refresh=None
        else:dbx=Dropbox(os.environ['IRINA_TRIAL_DROPBOX_TOKEN'],base=os.environ.get('IRINA_TRIAL_DROPBOX_BASE','/Irina Telegram'))
        token=None if args.text_only else os.environ.get('IRINA_TRIAL_OPENAI_TOKEN')
        ai=Transcriber(token) if token else None
        if ai:
            import shutil
            if not all(shutil.which(k) for k in ('ffmpeg','ffprobe')):raise SystemExit('TranscriptionRequiresFFmpegAndFFprobe')
        bootstrap_hash=os.environ.get('IRINA_TRIAL_BOOTSTRAP_SHA256')
        if bootstrap_hash:
            with trial.db() as d:empty=d.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==0
            if empty:
                from bootstrap import apply
                preflight_stage='dropbox_refresh_and_bootstrap'
                apply(trial,dbx.download_control('/control/bootstrap-v1.json'),bootstrap_hash)
        runner=Runner(trial,tg,dbx,ai,text_only=args.text_only,suppress_existing_receipts=True)
        tg.http.cancelled=runner.stopped;dbx.http.cancelled=runner.stopped
        if ai:ai.http.cancelled=runner.stopped
        signal.signal(signal.SIGTERM,lambda *_:runner.stopped.set())
        signal.signal(signal.SIGINT,lambda *_:runner.stopped.set())
        print('trial_started; awaiting_dropbox_verification; chat_scheduler_inactive',flush=True)
        runner.run(args.once)
    except RemoteError as e:
        print(json.dumps({'event':'remote_preflight_failed','stage':preflight_stage,
            'http_status':e.status,'uncertain':e.uncertain}),flush=True)
        raise SystemExit('RemotePreflightFailed') from None
    finally:
        if refresh_mode and 'dbx' in locals():dbx.http.close()
        (root/'trial.pid').unlink(missing_ok=True);lock.close()

if __name__=='__main__':main()
