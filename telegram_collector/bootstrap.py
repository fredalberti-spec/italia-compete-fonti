"""Hash-pinned data migration only. Never execute SQL/config from Dropbox contents."""
import hashlib,json,re
from irina_telegram_trial import write,COLLECTION,content_hash

def apply(trial,body,expected_sha256):
    if not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256) or hashlib.sha256(body).hexdigest()!=expected_sha256:
        raise ValueError('BootstrapHashMismatch')
    p=json.loads(body)
    if p.get('version')!=1 or p.get('owner_id')!=trial.owner or not isinstance(p.get('notes'),list) or len(p['notes'])>100:
        raise ValueError('BootstrapIdentityMismatch')
    cols=('seq','update_id','message_key','note_id','payload','received','project','state','record','receipt_state','receipt_id')
    with trial.db() as d:
        d.execute('BEGIN IMMEDIATE')
        if d.execute('SELECT COUNT(*) FROM notes').fetchone()[0]:return False
        for n in p['notes']:
            if set(n)!=set(cols) or n['state'] not in ('queued','archived','blocked','deferred_media') or n['receipt_state'] not in ('not_generated','pending','api_accepted','uncertain'):
                raise ValueError('BootstrapInvalidNote')
            if type(n['seq']) is not int or n['seq']<=0 or n['message_key'].split(':')[0]!=str(trial.owner):raise ValueError('BootstrapInvalidNote')
            expected=hashlib.sha256(n['message_key'].encode()).hexdigest()[:20]
            if n['note_id']!=expected or not isinstance(json.loads(n['payload']),dict):raise ValueError('BootstrapInvalidNote')
            d.execute('INSERT INTO notes('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(n[k] for k in cols))
        for receipt in p.get('scoped_read_receipts',[]):
            if not d.execute("SELECT 1 FROM notes WHERE note_id=? AND state='archived'",(receipt['note_id'],)).fetchone():raise ValueError('BootstrapUnknownReadNote')
            d.execute('INSERT INTO scoped_read_receipts VALUES(?,?,?)',(receipt['note_id'],receipt['batch_id'],receipt['source_thread_id']))
        # Do not copy/advance global cursor. Old pending notes must be processed first.
    for n in p['notes']:
        if n['state']=='archived':
            record=json.loads(n['record']);folder=trial.root/'dropbox-staging'/COLLECTION/'notes'/n['note_id']
            original=json.dumps(record,ensure_ascii=False,indent=2).encode();write(folder/'note.json',original)
            write(folder/'source-message.json',n['payload'].encode())
            manifest={k:{'sha256':hashlib.sha256((folder/k).read_bytes()).hexdigest(),'dropbox_content_hash':content_hash((folder/k).read_bytes())} for k in ('note.json','source-message.json')}
            write(folder/'manifest.json',json.dumps(manifest,indent=2).encode())
    return True
