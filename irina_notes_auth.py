"""One-time Dropbox OAuth for the Notes reader; credentials stay on the server."""
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from irina_notes import NAMESPACE, ROOT

BASE = '/connect/dropbox-notes'
CALLBACK = 'https://irina-assistant-inbox.onrender.com' + BASE + '/callback'
SCOPES = ['account_info.read', 'files.metadata.read', 'files.content.read']
COOKIE = '__Host-irina_notes_oauth'


def application(env, start):
    root = Path(os.environ['IRINA_DATA_DIR'])
    token_path = root / 'notes_dropbox.json'
    def response(status, text='', headers=()):
        start(status, [('Content-Type','text/plain; charset=utf-8'), ('Cache-Control','no-store'),
                       ('Referrer-Policy','no-referrer'), ('X-Content-Type-Options','nosniff'), *headers])
        return [text.encode('utf-8')]
    def redirect(status):
        return response('303 See Other', headers=[('Location',BASE+'/result?status='+status),
             ('Set-Cookie',COOKIE+'=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0')])
    path = env.get('PATH_INFO', '')
    if env.get('REQUEST_METHOD') != 'GET':
        return response('405 Method Not Allowed')
    if path == BASE+'/result':
        ok = parse_qs(env.get('QUERY_STRING','')).get('status') == ['ok'] and token_path.exists()
        return response('200 OK', 'Dropbox collegato a Irina per la lettura delle note. La prima sincronizzazione è in corso.' if ok else
                        'Collegamento non completato. Nessuna credenziale è stata sostituita.')
    if path not in (BASE, BASE+'/callback'):
        return response('404 Not Found')
    # This onboarding endpoint cannot replace an established credential.
    if token_path.exists():
        return response('409 Conflict', 'Il lettore delle note è già collegato.')
    import dropbox
    from dropbox.oauth import DropboxOAuth2Flow
    app_key = os.environ['DROPBOX_APP_KEY']
    with sqlite3.connect(root / 'notes_oauth.sqlite3', timeout=10) as db:
        os.chmod(root / 'notes_oauth.sqlite3', 0o600)
        db.execute('CREATE TABLE IF NOT EXISTS flows (id TEXT PRIMARY KEY, created REAL, session TEXT, verifier TEXT)')
        db.execute('DELETE FROM flows WHERE created < ?', (time.time()-600,))
        if path == BASE:
            if db.execute('SELECT COUNT(*) FROM flows').fetchone()[0] >= 50:
                return response('429 Too Many Requests', 'Riprova più tardi.')
            session = {}
            flow = DropboxOAuth2Flow(app_key, CALLBACK, session, 'csrf', token_access_type='offline',
                                     scope=SCOPES, use_pkce=True, timeout=15)
            url = flow.start()
            sid = secrets.token_urlsafe(32)
            verifier = flow.code_verifier.decode() if isinstance(flow.code_verifier, bytes) else flow.code_verifier
            db.execute('INSERT INTO flows VALUES (?,?,?,?)', (sid, time.time(), json.dumps(session), verifier))
            return response('302 Found', headers=[('Location',url),
                ('Set-Cookie',COOKIE+'='+sid+'; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600')])
        cookies = SimpleCookie()
        try:
            cookies.load(env.get('HTTP_COOKIE',''))
            sid = cookies[COOKIE].value
        except (KeyError, ValueError):
            return redirect('failed')
        db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
        row = db.execute('SELECT session,verifier FROM flows WHERE id=?', (sid,)).fetchone()
        db.execute('DELETE FROM flows WHERE id=?', (sid,))
        db.commit()
        if not row:
            return redirect('failed')
        try:
            query = parse_qs(env.get('QUERY_STRING',''), strict_parsing=True, max_num_fields=10)
            if any(len(v)!=1 for v in query.values()):
                raise ValueError('DuplicateParameter')
            flow = DropboxOAuth2Flow(app_key, CALLBACK, json.loads(row[0]), 'csrf', token_access_type='offline',
                                     scope=SCOPES, use_pkce=True, timeout=15)
            flow.code_verifier = row[1].encode()
            result = flow.finish({k:v[0] for k,v in query.items()})
            granted = set(result.scope.split()) if isinstance(result.scope,str) else set(result.scope or [])
            if not result.refresh_token or not set(SCOPES).issubset(granted):
                raise ValueError('RequiredScopeMissing')
            client = dropbox.Dropbox(oauth2_access_token=result.access_token, timeout=15)
            account = client.users_get_current_account()
            if account.root_info.root_namespace_id != NAMESPACE:
                raise ValueError('WrongDropboxAccount')
            client.with_path_root(dropbox.common.PathRoot.namespace_id(NAMESPACE)).files_get_metadata(ROOT)
            # O_EXCL also prevents concurrent callbacks overwriting the first success.
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as out:
                json.dump({'refresh_token':result.refresh_token},out)
                out.flush()
                os.fsync(out.fileno())
            print(json.dumps({'event':'irina_notes_authorized','read_only':True,'account_verified':True}), flush=True)
            return redirect('ok')
        except Exception as exc:
            # Do not log query strings, provider payloads, or credentials.
            print(json.dumps({'event':'irina_notes_authorization_failed','error_type':type(exc).__name__}), flush=True)
            return redirect('failed')
