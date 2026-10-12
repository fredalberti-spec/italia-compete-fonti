"""Local receipt for the explicitly scoped transfer; no token or network calls."""
import hashlib,json
from pathlib import Path

def pending(root,filename):
    root=Path(root);ledger=json.loads((root/'transfer-ledger.json').read_text())
    item=ledger['files'].get(filename)
    if not item:return True
    digest=hashlib.sha256((root/filename).read_bytes()).hexdigest()
    if digest!=item['sha256']:raise ValueError('TransferredOriginalChanged')
    return item['state']!='uploaded'

def acknowledge_read(root,batch_id,*,source_thread_id,confirmed_note_id):
    """Trusted parent confirmation for one scoped batch, never global SQLite cursor."""
    from datetime import datetime,timezone
    from irina_telegram_trial import write
    root=Path(root);p=root/'transfer-ledger.json';ledger=json.loads(p.read_text())
    if batch_id!=ledger['batch_id'] or confirmed_note_id!=ledger['note_id'] or not source_thread_id:
        raise ValueError('ScopedAckMismatch')
    if ledger.get('parent_read_ack'):return False
    for name in ledger['files']:
        if pending(root,name):raise ValueError('UploadNotComplete')
    ledger['parent_read_ack']=True;ledger['acknowledged_at']=datetime.now(timezone.utc).isoformat()
    ledger['ack_source_thread_id']=source_thread_id
    write(p,json.dumps(ledger,indent=2).encode())
    ack={'action':'acknowledge_read','batch_id':batch_id,'note_id':confirmed_note_id,
         'scope':'single_archived_test','source_thread_id':source_thread_id,
         'acknowledged_at':ledger['acknowledged_at'],'general_cursor_advanced':False,
         'external_actions_authorized':False}
    write(root/('ack-'+batch_id+'.json'),json.dumps(ack,indent=2).encode())
    return True
