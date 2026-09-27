"""Separate web service. Start neither worker.py nor a Telegram session."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import threading

from irina_inbox import Inbox, owner_phones
from irina_processor import Archive, Intelligence, Processor, WhatsApp


def main():
    required = ('IRINA_DATA_DIR','IRINA_OWNER_PHONES','IRINA_VERIFY_TOKEN','META_APP_SECRET',
                'IRINA_WHATSAPP_TOKEN','OPENAI_API_KEY','IRINA_OPENAI_MODEL',
                'IRINA_DROPBOX_REFRESH_TOKEN','DROPBOX_APP_KEY')
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit('Missing configuration keys: ' + ', '.join(missing))
    root = Path(os.environ['IRINA_DATA_DIR'])
    inbox = Inbox(root)
    lock = open(root / 'processor.lock','a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    inbox.recover()
    owners = owner_phones(os.environ['IRINA_OWNER_PHONES'])
    primary = os.environ.get('IRINA_PRIMARY_PHONE','')
    if primary not in owners:
        raise SystemExit('IRINA_PRIMARY_PHONE must be one of the owner numbers')
    processor = Processor(inbox, WhatsApp(os.environ['IRINA_WHATSAPP_TOKEN'], owners),
        Intelligence(os.environ['OPENAI_API_KEY'], os.environ['IRINA_OPENAI_MODEL']),
        Archive(os.environ['IRINA_DROPBOX_REFRESH_TOKEN'], os.environ['DROPBOX_APP_KEY']), primary=primary)
    # Access logging disabled: Meta verification uses a secret query parameter.
    server = subprocess.Popen(['gunicorn','--bind','0.0.0.0:'+os.environ.get('PORT','10000'),
                               '--workers','1','--threads','4','--timeout','30','irina_inbox:application'])
    from irina_email import run as email_run
    from irina_notes import make_index, run as notes_run
    email_stop = threading.Event()
    processor.notes = make_index(root, os.environ['IRINA_DROPBOX_REFRESH_TOKEN'], os.environ['DROPBOX_APP_KEY'])
    threading.Thread(target=notes_run, args=(processor.notes, email_stop), daemon=True).start()
    threading.Thread(target=email_run, args=(processor, email_stop), daemon=True).start()
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
        email_stop.set()
        server.terminate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({'event':'irina_inbox_ready','owner_allowlist':True,'telegram_client':False,
                      'chatgpt_chat_sync':False,'projects':['italia_compete','off_class','personale']}), flush=True)
    try:
        last_cleanup = 0
        while not stopping and server.poll() is None:
            if time.time()-last_cleanup > 3600:
                processor.prune_mirrored_media()
                last_cleanup=time.time()
            if not processor.step():
                time.sleep(1)
    finally:
        email_stop.set()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == '__main__':
    main()
