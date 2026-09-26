"""Run ONLY interactively in the user's private Render Shell, with ingestion disabled."""
import asyncio
import fcntl
import getpass
import json
import os
from worker import ROOT

async def telegram():
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    client = TelegramClient(str(ROOT / 'telegram'), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH'])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            phone = getpass.getpass('Numero Telegram con prefisso (nascosto): ')
            await client.send_code_request(phone)
            try:
                await client.sign_in(phone, getpass.getpass('Codice ricevuto in Telegram (nascosto): '))
            except SessionPasswordNeededError:
                await client.sign_in(password=getpass.getpass('Password 2FA (nascosta): '))
        print('Telegram autorizzato. La sessione resta sul disco del servizio.')
    finally:
        await client.disconnect()


def dropbox_auth():
    from dropbox import DropboxOAuth2FlowNoRedirect
    flow = DropboxOAuth2FlowNoRedirect(os.environ['DROPBOX_APP_KEY'], use_pkce=True, token_access_type='offline',
                                      scope=['files.metadata.read', 'files.content.write'])
    print('Apri personalmente questo URL di autorizzazione Dropbox:')
    print(flow.start())
    result = flow.finish(getpass.getpass('Codice Dropbox (nascosto): ').strip())
    if not result.refresh_token:
        raise RuntimeError('MissingRefreshToken')
    (ROOT / 'dropbox.json').write_text(json.dumps({'refresh_token': result.refresh_token}))
    print('Dropbox autorizzato. Credenziale salvata solo sul disco del servizio.')

if __name__ == '__main__':
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    if os.environ.get('INGESTION_ENABLED') == 'true':
        raise SystemExit('Disabilitare prima INGESTION_ENABLED e attendere il riavvio.')
    with (ROOT / 'session.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            asyncio.run(telegram())
            dropbox_auth()
        except Exception as exc:
            raise SystemExit('Autorizzazione incompleta: ' + type(exc).__name__)
