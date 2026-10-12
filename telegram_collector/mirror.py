"""Dropbox-shaped injected adapter; no SDK/client/credential loading.
A production adapter must implement upload(path, bytes), metadata(path).
"""
import json
from pathlib import PurePosixPath
from irina_telegram_trial import content_hash,write

def mirror(trial, store, base='/Irina Telegram'):
    if not base.startswith('/') or '..' in PurePosixPath(base).parts:
        raise ValueError('InvalidDestination')
    status=trial.root/'mirror-status.json'
    if status.exists():status.unlink()
    staging=trial.root/'dropbox-staging'/'Irina Telegram'
    count=0
    # Publish note bodies and attachments first, then the handoff files.
    files=sorted((p for p in staging.rglob('*') if p.is_file()),key=lambda p:('handoff' in p.parts,str(p)))
    for p in files:
        body=p.read_bytes();remote=base.rstrip('/')+'/'+p.relative_to(staging).as_posix()
        store.upload(remote,body) # overwrite deterministic path, no autorename
        meta=store.metadata(remote)
        if meta['size']!=len(body) or meta['content_hash']!=content_hash(body):
            raise ValueError('DropboxVerificationFailed')
        count+=1
    # This state attests archive transfer only, never delivery to Elio.
    report={'state':'verified_by_adapter','files':count,'destination':base,
            'delivery_to_elio':'not_acknowledged'}
    write(trial.root/'mirror-status.json',json.dumps(report,indent=2).encode())
    return report
