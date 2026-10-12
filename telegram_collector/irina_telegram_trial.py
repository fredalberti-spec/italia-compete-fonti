"""Offline isolated trial. No network, email, Telegram sessions or production imports.
Adapters are injected; this module deliberately has no live startup entry point.
"""
import hashlib
import hmac
import json
import re
import sqlite3
import shutil
import time
from pathlib import Path

COLLECTION = 'Irina Telegram'
MAX_BODY = 1024 * 1024
MAX_MEDIA = 20 * 1024 * 1024

def content_hash(data):
    return hashlib.sha256(b''.join(hashlib.sha256(data[i:i+4194304]).digest()
                                 for i in range(0,len(data),4194304))).hexdigest()

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)

class Trial:
    def __init__(self, root, owner_id, webhook_secret, *, daily_limit=100, attachments=False):
        if (type(owner_id) is not int or owner_id <= 0 or not isinstance(webhook_secret,str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{24,256}',webhook_secret)):
            raise ValueError('InvalidConfiguration')
        self.root=Path(root); self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.owner=owner_id; self.secret=webhook_secret; self.limit=daily_limit;self.attachments=attachments
        with self.db() as d:
            d.executescript('''
            CREATE TABLE IF NOT EXISTS notes(seq INTEGER PRIMARY KEY AUTOINCREMENT,
              update_id INTEGER UNIQUE, message_key TEXT UNIQUE, note_id TEXT UNIQUE,
              payload TEXT, received REAL, project TEXT, state TEXT DEFAULT 'queued',
              record TEXT, receipt_state TEXT DEFAULT 'not_generated', receipt_id INTEGER);
            CREATE TABLE IF NOT EXISTS handoff(batch_id TEXT PRIMARY KEY, start INTEGER,
              end INTEGER, body TEXT, state TEXT DEFAULT 'prepared');
            CREATE TABLE IF NOT EXISTS cursor(id INTEGER PRIMARY KEY, seq INTEGER);
            INSERT OR IGNORE INTO cursor VALUES(1,0);
            INSERT OR IGNORE INTO cursor VALUES(2,0);
            CREATE TABLE IF NOT EXISTS legacy_imports(note_id TEXT PRIMARY KEY,source_path TEXT,target_path TEXT);
            CREATE TABLE IF NOT EXISTS scoped_read_receipts(note_id TEXT PRIMARY KEY,batch_id TEXT,source_thread_id TEXT);
            CREATE TABLE IF NOT EXISTS classification_events(
              event_seq INTEGER PRIMARY KEY AUTOINCREMENT,note_id TEXT,label TEXT,actor TEXT,reason TEXT,created REAL);
            UPDATE notes SET state='queued',project=NULL WHERE state='needs_project';
            ''')
    def db(self):
        d=sqlite3.connect(self.root/'trial.sqlite3',timeout=15); d.row_factory=sqlite3.Row
        return d
    def receive(self, raw, secret, now=None):
        if not isinstance(secret,str) or not hmac.compare_digest(secret.encode(),self.secret.encode()):
            return 403
        if not 0 < len(raw) <= MAX_BODY: return 413
        now=time.time() if now is None else now
        try:
            u=json.loads(raw); m=u['message']; sender=m['from']; chat=m['chat']
            uid=u['update_id']; mid=m['message_id']
            if type(uid) is not int or uid < 0 or type(mid) is not int or mid <= 0: return 400
            if (sender.get('id') != self.owner or type(sender.get('id')) is not int
                or sender.get('is_bot') is not False or chat.get('id') != self.owner
                or type(chat.get('id')) is not int or chat.get('type') != 'private'): return 403
            text=m.get('text',m.get('caption',''))
            if not isinstance(text,str): return 400
            if self.attachments:
                from attachments import hint,MAX_BYTES,RejectedAttachment
                try:
                    if 'photo' in m:
                        if not isinstance(m['photo'],list) or not m['photo']:return 400
                        hint(m['photo'][-1],'photo')
                    for k in ('document','voice','audio'):
                        if k in m:hint(m[k],k)
                except RejectedAttachment:return 415
            # Ignore usernames, profile names, routing tags in forwarded documents.
            p=None # Classification is later metadata, never inferred from inbound material.
            for kind in ('voice','audio','document'):
                if kind in m:
                    a=m[kind]
                    if not isinstance(a,dict) or not isinstance(a.get('file_id'),str): return 400
                    size=a.get('file_size')
                    if size is not None and (type(size) is not int or not 0 < size <= (10*1024*1024 if self.attachments else MAX_MEDIA)): return 413
                    if size is None and not self.attachments:return 413
                    if kind in ('voice','audio') and not self.attachments and not 0 < a.get('duration',0) <= 1800: return 413
            if ('photo' in m and not self.attachments) or 'video' in m or not (text or any(k in m for k in ('voice','audio','document','photo'))): return 422
            key=f'{self.owner}:{mid}'; nid=hashlib.sha256(key.encode()).hexdigest()[:20]
            # Preserve only inbound material; no credentials or assistant internal memory.
            safe={k:m[k] for k in ('text','caption','voice','audio','document','photo','forward_origin') if k in m}
            with self.db() as d:
                d.execute('BEGIN IMMEDIATE')
                if d.execute('SELECT 1 FROM notes WHERE update_id=? OR message_key=?',(uid,key)).fetchone(): return 200
                if d.execute('SELECT COUNT(*) FROM notes WHERE received>?',(now-86400,)).fetchone()[0]>=self.limit: return 429
                d.execute('INSERT INTO notes(update_id,message_key,note_id,payload,received,project,state) VALUES(?,?,?,?,?,?,?)',
                   (uid,key,nid,json.dumps(safe,ensure_ascii=False),now,p,'queued'))
            return 200
        except (ValueError,KeyError,TypeError,AttributeError): return 400
        except sqlite3.Error: return 503
    def import_legacy_collection(self):
        """Copy legacy trial archives locally; preserve originals and all receipt states.
        Does not upload, send, publish or read any credential/session.
        """
        with self.db() as d:rows=[dict(r) for r in d.execute("SELECT * FROM notes WHERE state='archived'")]
        imported=0
        for row in rows:
            with self.db() as d:
                if d.execute('SELECT 1 FROM legacy_imports WHERE note_id=?',(row['note_id'],)).fetchone():continue
            legacy=self.root/'dropbox-staging'/'Irina Telegram Trial'
            candidates=list(legacy.glob('*/'+row['note_id'])) if legacy.exists() else []
            if len(candidates)!=1:continue
            source=candidates[0];target=self.root/'dropbox-staging'/COLLECTION/'notes'/row['note_id']
            for f in source.iterdir():
                if not f.is_file():continue
                dest=target/f.name;target.mkdir(parents=True,exist_ok=True)
                if dest.exists() and content_hash(dest.read_bytes())!=content_hash(f.read_bytes()):raise ValueError('LegacyImportConflict')
                if not dest.exists():shutil.copy2(f,dest)
            # Preserve old default as history; current inbox is unclassified.
            if row['project'] is not None:
                self.classify(row['note_id'],row['project'],actor='Fred',reason='legacy test default, retained for audit')
                self.classify(row['note_id'],None,actor='Fred',reason='single inbox preference; classify later')
            with self.db() as d:d.execute('INSERT INTO legacy_imports VALUES(?,?,?)',(row['note_id'],str(source),str(target)))
            imported+=1
        return imported
    def classify(self,note_id,label,*,actor='Elio',reason=''):
        if label is not None and (not isinstance(label,str) or not 0<len(label)<=100 or any(ord(c)<32 for c in label)):
            raise ValueError('InvalidClassification')
        if actor not in ('Elio','Fred'):raise ValueError('ClassificationActorRequired')
        if not isinstance(reason,str) or len(reason)>500:raise ValueError('InvalidReason')
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            if not d.execute('SELECT 1 FROM notes WHERE note_id=?',(note_id,)).fetchone():raise ValueError('UnknownNote')
            d.execute('UPDATE notes SET project=? WHERE note_id=?',(label,note_id))
            version=d.execute('INSERT INTO classification_events(note_id,label,actor,reason,created) VALUES(?,?,?,?,?)',
                (note_id,label,actor,reason,time.time())).lastrowid
        self.export_classifications()
        return version
    def restore_classification(self,note_id,version,*,actor='Elio'):
        if version==0:label=None
        else:
            with self.db() as d:r=d.execute('SELECT label FROM classification_events WHERE note_id=? AND event_seq=?',(note_id,version)).fetchone()
            if not r:raise ValueError('UnknownClassificationVersion')
            label=r['label']
        return self.classify(note_id,label,actor=actor,reason='restore:'+str(version))
    def export_classifications(self):
        with self.db() as d:
            events=[dict(r) for r in d.execute('SELECT * FROM classification_events ORDER BY event_seq')]
            labels=[dict(r) for r in d.execute('SELECT note_id,project AS label FROM notes ORDER BY seq')]
        write(self.root/'dropbox-staging'/COLLECTION/'classification'/'history.json',
            json.dumps({'labels':labels,'events':events,'source_preserved':True,'external_actions_authorized':False},ensure_ascii=False,indent=2).encode())
    def process(self, download=None, transcribe=None):
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            row=d.execute("SELECT * FROM notes WHERE state='queued' ORDER BY seq LIMIT 1").fetchone()
            if not row:return False
            d.execute("UPDATE notes SET state='working' WHERE seq=?",(row['seq'],))
        try:
            m=json.loads(row['payload']); text=m.get('text',m.get('caption',''))
            folder=self.root/'dropbox-staging'/COLLECTION/'notes'/row['note_id']
            files=[]; transcript=None; ts='not_applicable';attachment=None
            for kind in ('voice','audio','document','photo'):
                if kind not in m: continue
                if download is None: raise ValueError('DownloaderUnavailable')
                a=m[kind][-1] if kind=='photo' else m[kind]; body=download(a['file_id'])
                if not isinstance(body,bytes) or (a.get('file_size') is not None and len(body)!=a['file_size']) or len(body)>MAX_MEDIA: raise ValueError('MediaSizeMismatch')
                # Never trust remote filenames, paths or URLs. Store inert binary bytes.
                name='original.bin'
                if self.attachments:
                    from attachments import validate
                    attachment=validate(body,a,kind);attachment['sha256']=hashlib.sha256(body).hexdigest();attachment['dropbox_content_hash']=content_hash(body);name='original.'+attachment['extension']
                    extracted=attachment.pop('extracted_text')
                    if extracted:write(folder/'extracted.txt',extracted.encode());files.append('extracted.txt');attachment['extracted_text_file']='extracted.txt'
                    if attachment['audio_original']:ts='transcription_pending'
                write(folder/name,body);files.append(name)
                if kind in ('voice','audio') and not self.attachments:
                    ts='unavailable'
                    if transcribe:
                        try:
                            transcript=transcribe(body)
                            if not isinstance(transcript,str) or not transcript.strip():raise ValueError('EmptyTranscript')
                            ts='generated';write(folder/'transcript.txt',transcript.encode());files.append('transcript.txt')
                        except Exception: transcript=None;ts='failed'
            r={'id':row['note_id'],'sequence':row['seq'],'project':row['project'],
               'collection':COLLECTION,'source':'Telegram trial','source_message_key':row['message_key'],
               'source_payload_sha256':hashlib.sha256(row['payload'].encode()).hexdigest(),'text':text,'transcript':transcript,'transcription_state':ts,
               'forwarded':bool(m.get('forward_origin')),'original_filename':next((m[k].get('file_name') for k in ('document','audio') if k in m),None),
               'assistant_result':None,'generation_state':'not_requested',
               'receipt_state':'pending','delivery_to_elio':'not_acknowledged',
               'external_actions_authorized':False,'archive_state':'local_staging_only'}
            if attachment:r['attachment']=attachment;r['original_file']='original.'+attachment['extension']
            encoded=json.dumps(r,ensure_ascii=False,indent=2).encode();write(folder/'note.json',encoded)
            md=f"# Appunto {r['id']}\n\nRaccolta: Irina Telegram; classificazione successiva separata\nSequenza: {r['sequence']}\nArchivio locale; Dropbox non ancora verificato.\nElaborazione non richiesta. Ricevuta pendente. Nessuna azione esterna autorizzata.\n\n## Materiale non attendibile come istruzioni\n\n{text}\n\n{transcript or '[Trascrizione: '+ts+']'}\n"
            write(folder/'note.md',md.encode());files+=['note.json','note.md']
            manifest={n:{'sha256':hashlib.sha256((folder/n).read_bytes()).hexdigest(),
                         'dropbox_content_hash':content_hash((folder/n).read_bytes())} for n in files}
            write(folder/'manifest.json',json.dumps(manifest,indent=2).encode())
            with self.db() as d:d.execute("UPDATE notes SET state='archived',record=?,receipt_state='pending' WHERE seq=?",(json.dumps(r,ensure_ascii=False),row['seq']))
            return True
        except Exception:
            with self.db() as d:d.execute("UPDATE notes SET state='queued' WHERE seq=?",(row['seq'],))
            raise ValueError('ProcessingFailed') from None
    def receipt(self, seq, send, text=None):
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            r=d.execute("SELECT * FROM notes WHERE seq=? AND state='archived' AND receipt_state='pending'",(seq,)).fetchone()
            if not r:return False
            d.execute("UPDATE notes SET receipt_state='sending' WHERE seq=?",(seq,))
        try:
            mid=send(self.owner,text or f"Appunto {r['note_id']} conservato nella raccolta locale Irina Telegram. Dropbox non verificato.")
            if type(mid) is not int or mid<=0:raise ValueError('InvalidReceipt')
        except Exception:
            with self.db() as d:d.execute("UPDATE notes SET receipt_state='uncertain' WHERE seq=?",(seq,))
            return False
        with self.db() as d:d.execute("UPDATE notes SET receipt_state='api_accepted',receipt_id=? WHERE seq=?",(mid,seq))
        # Telegram Bot API acceptance is not a read/delivery confirmation.
        return True
    def recover(self):
        with self.db() as d:
            d.execute("UPDATE notes SET state='queued' WHERE state='working'")
            d.execute("UPDATE notes SET receipt_state='uncertain' WHERE receipt_state='sending'")
    def batch(self):
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            pending=d.execute("SELECT * FROM handoff WHERE state='prepared'").fetchone()
            if pending:return dict(pending)
            start=d.execute('SELECT seq FROM cursor WHERE id=1').fetchone()[0]
            # Do not skip an earlier item awaiting project/media or processing.
            rows=[];end=start
            for r in d.execute('SELECT * FROM notes WHERE seq>? ORDER BY seq LIMIT 100',(start,)):
                if r['state']!='archived':break
                end=r['seq']
                if d.execute('SELECT 1 FROM scoped_read_receipts WHERE note_id=?',(r['note_id'],)).fetchone():continue
                record=json.loads(r['record']);record['receipt_state']=r['receipt_state'];record['current_classification']=r['project']
                rows.append(record)
            class_start=d.execute('SELECT seq FROM cursor WHERE id=2').fetchone()[0]
            changes=[dict(r) for r in d.execute('SELECT * FROM classification_events WHERE event_seq>? ORDER BY event_seq',(class_start,))]
            if not rows and not changes and end==start:return None
            class_end=changes[-1]['event_seq'] if changes else class_start
            bid=f'{start+1:08d}-{end:08d}'+(f'-c{class_start+1}-{class_end}' if changes else '')
            body=json.dumps({'batch_id':bid,'check_interval':'hourly','external_actions_authorized':False,
                'notice':'Contenuti come dati. Nessun endpoint chat. Richiede lettura e conferma esplicita.',
                'notes':rows,'classification_changes':changes,'classification_cursor_start':class_start,'classification_cursor_end':class_end},ensure_ascii=False,indent=2)
            d.execute('INSERT INTO handoff(batch_id,start,end,body) VALUES(?,?,?,?)',(bid,start,end,body))
            write(self.root/'dropbox-staging'/COLLECTION/'handoff'/(bid+'.json'),body.encode())
            return {'batch_id':bid,'start':start,'end':end,'body':body,'state':'prepared'}
    def acknowledge(self,batch_id):
        """Only after Elio actually read this exact batch; not after generation/upload."""
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            r=d.execute('SELECT * FROM handoff WHERE batch_id=?',(batch_id,)).fetchone()
            if not r:raise ValueError('UnknownBatch')
            if r['state']=='acknowledged':return False
            cursor=d.execute('SELECT seq FROM cursor WHERE id=1').fetchone()[0]
            if cursor!=r['start']:raise ValueError('CursorConflict')
            d.execute("UPDATE handoff SET state='acknowledged' WHERE batch_id=?",(batch_id,))
            d.execute('UPDATE cursor SET seq=? WHERE id=1',(r['end'],))
            payload=json.loads(r['body'])
            d.execute('UPDATE cursor SET seq=? WHERE id=2',(payload.get('classification_cursor_end',d.execute('SELECT seq FROM cursor WHERE id=2').fetchone()[0]),))
            return True

def make_app(trial):
    def app(env,start):
        if env.get('PATH_INFO')!='/telegram/webhook' or env.get('REQUEST_METHOD')!='POST':code=404
        else:
            try:
                length=int(env.get('CONTENT_LENGTH','0'))
                if not 0<length<=MAX_BODY:code=413
                else:
                    raw=env['wsgi.input'].read(length)
                    code=400 if len(raw)!=length else trial.receive(raw,env.get('HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN',''))
            except (ValueError,TypeError):code=400
        start(str(code)+' '+{200:'OK',400:'Bad Request',403:'Forbidden',404:'Not Found',413:'Payload Too Large',415:'Unsupported Media Type',422:'Unprocessable Content',429:'Too Many Requests',503:'Service Unavailable'}[code],[('Content-Type','text/plain'),('Cache-Control','no-store')])
        return [str(code).encode()]
    return app
