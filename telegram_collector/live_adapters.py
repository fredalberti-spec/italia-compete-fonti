"""Live HTTP adapters. Construction makes no network calls; secrets never logged."""
import json
import secrets
import tempfile
import subprocess
import math
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

class RemoteError(Exception):
    def __init__(self, status=0, uncertain=False):
        self.status=status; self.uncertain=uncertain
        super().__init__('RemoteRequestFailed')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):raise RemoteError()

class HTTP:
    def __init__(self, opener=None, sleep=time.sleep):
        self.opener=opener or urllib.request.build_opener(NoRedirect());self.sleep=sleep
    def request(self,url,*,body=None,headers=None,retry=False,limit=21*1024*1024):
        for attempt in range(3 if retry else 1):
            if getattr(self,'cancelled',None) is not None and self.cancelled.is_set():raise RemoteError()
            try:
                req=urllib.request.Request(url,data=body,headers=headers or {})
                with self.opener.open(req,timeout=30) as r:
                    data=r.read(limit+1)
                    if len(data)>limit:raise RemoteError()
                    return data
            except urllib.error.HTTPError as e:
                status=e.code
                if retry and status in (429,500,502,503,504) and attempt<2:
                    delay=e.headers.get('Retry-After','1') if e.headers else '1'
                    try:delay=min(5,max(0,float(delay)))
                    except ValueError:delay=1
                    self.sleep(delay);continue
                raise RemoteError(status,uncertain=status>=500) from None
            except RemoteError:raise
            except Exception:
                if retry and attempt<2:self.sleep(attempt+1);continue
                raise RemoteError(uncertain=True) from None
    def json(self,url,*,payload=None,headers=None,retry=False):
        h={'Content-Type':'application/json',**(headers or {})}
        raw=self.request(url,body=json.dumps(payload).encode() if payload is not None else None,headers=h,retry=retry,limit=1024*1024)
        try:return json.loads(raw)
        except Exception:raise RemoteError(uncertain=not retry) from None

class Telegram:
    def __init__(self,token,owner,http=None):
        if not token or not token.strip() or type(owner) is not int:raise ValueError('InvalidTelegramConfig')
        self.token=token;self.owner=owner;self.http=http or HTTP()
    def call(self,method,payload,retry=False):
        r=self.http.json('https://api.telegram.org/bot'+self.token+'/'+method,payload=payload,retry=retry)
        if not isinstance(r,dict) or r.get('ok') is not True:raise RemoteError()
        return r.get('result')
    def identity(self):return self.call('getMe',{},True)
    def webhook_info(self):return self.call('getWebhookInfo',{},True)
    def updates(self,offset):
        r=self.call('getUpdates',{'offset':offset,'timeout':0,'limit':20,'allowed_updates':['message']},True)
        if not isinstance(r,list):raise RemoteError()
        return r
    def download(self,file_id,max_bytes=20*1024*1024):
        r=self.call('getFile',{'file_id':file_id},True)
        path=r.get('file_path','');size=r.get('file_size')
        # Reject URLs, traversal, encoded path segments and empty paths.
        if (not path or path.startswith('/') or '..' in PurePosixPath(path).parts
            or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-' for c in path)
            or (size is not None and (type(size) is not int or not 0<size<=max_bytes))):raise RemoteError()
        body=self.http.request('https://api.telegram.org/file/bot'+self.token+'/'+path,retry=True,limit=max_bytes)
        if not body or (size is not None and len(body)!=size):raise RemoteError()
        return body
    def send(self,recipient,text):
        if recipient!=self.owner:raise ValueError('RecipientNotOwner')
        # sendMessage has no idempotency key: never automatically repeat an uncertain POST.
        r=self.call('sendMessage',{'chat_id':recipient,'text':text[:4000],'link_preview_options':{'is_disabled':True}})
        mid=r.get('message_id') if isinstance(r,dict) else None
        if type(mid) is not int or mid<=0:raise RemoteError(uncertain=True)
        return mid

class Dropbox:
    """Use an App Folder app to keep server-side access confined to its sandbox."""
    def __init__(self,access_token,base='/Irina Telegram',http=None):
        if not access_token or not base.startswith('/') or '..' in PurePosixPath(base).parts:raise ValueError('InvalidDropboxConfig')
        self.token=access_token;self.base=base.rstrip('/') or '/';self.http=http or HTTP();self.folders=set();self.uploaded={}
    def path(self,path):
        if (not path.startswith(self.base.rstrip('/')+'/') or path.startswith('//')
            or any(p in ('.','..') for p in path.split('/'))):raise ValueError('OutsideTrialFolder')
        if self.base=='/' and (len(PurePosixPath(path).parts)<2 or PurePosixPath(path).parts[1] not in ('notes','handoff','classification','control')):
            raise ValueError('OutsideInboxLayout')
        return path
    def ensure_parents(self,path):
        parts=PurePosixPath(self.path(path)).parent.parts[1:]
        parent=''
        for part in parts:
            parent+='/'+part
            if parent in self.folders:continue
            try:
                self.http.json('https://api.dropboxapi.com/2/files/create_folder_v2',payload={'path':parent,'autorename':False},headers={'Authorization':'Bearer '+self.token},retry=True)
            except RemoteError as e:
                if e.status!=409:raise
                meta=self.http.json('https://api.dropboxapi.com/2/files/get_metadata',payload={'path':parent},headers={'Authorization':'Bearer '+self.token},retry=True)
                if meta.get('.tag')!='folder':raise RemoteError()
            self.folders.add(parent)
    def upload(self,path,body):
        digest=__import__('hashlib').sha256(body).hexdigest()
        if self.uploaded.get(path)==digest:return
        self.ensure_parents(path)
        args={'path':self.path(path),'mode':'overwrite','autorename':False,'mute':True,'strict_conflict':False}
        self.http.request('https://content.dropboxapi.com/2/files/upload',body=body,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/octet-stream','Dropbox-API-Arg':json.dumps(args)},retry=True,limit=1024*1024)
        self.uploaded[path]=digest
    def metadata(self,path):
        return self.http.json('https://api.dropboxapi.com/2/files/get_metadata',payload={'path':self.path(path)},headers={'Authorization':'Bearer '+self.token},retry=True)
    def download_control(self,path):
        if not path.startswith(self.base.rstrip('/')+'/control/'):raise ValueError('OutsideControlFolder')
        return self.http.request('https://content.dropboxapi.com/2/files/download',body=b'',headers={
            'Authorization':'Bearer '+self.token,'Dropbox-API-Arg':json.dumps({'path':self.path(path)})},retry=True,limit=1024*1024)
    def read_ack(self):
        path=self.base.rstrip('/')+'/control/ack.json'
        try:
            raw=self.http.request('https://content.dropboxapi.com/2/files/download',body=b'',headers={
                'Authorization':'Bearer '+self.token,'Dropbox-API-Arg':json.dumps({'path':path})},retry=True,limit=4096)
        except RemoteError as e:
            if e.status==409:return None
            raise
        try:
            r=json.loads(raw)
            if not isinstance(r,dict) or r.get('action')!='acknowledge_read' or not isinstance(r.get('batch_id'),str):raise ValueError()
            return r['batch_id']
        except Exception:raise RemoteError() from None

class Transcriber:
    def __init__(self,token,http=None,encoder=None):
        self.token=token;self.http=http or HTTP();self.encoder=encoder or self.encode
    @staticmethod
    def encode(body):
        with tempfile.TemporaryDirectory(prefix='irina-audio-') as tmp:
            src=Path(tmp)/'input.bin';dest=Path(tmp)/'audio.mp3';src.write_bytes(body)
            try:
                duration=float(subprocess.check_output(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','ogg,mp3,wav,aac,flac,mov,matroska,webm','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(src)],timeout=20,stderr=subprocess.DEVNULL))
                if not math.isfinite(duration) or not 0<duration<=1800:raise ValueError()
                subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-protocol_whitelist','file,pipe','-format_whitelist','ogg,mp3,wav,aac,flac,mov,matroska,webm','-i',str(src),'-t','1800','-vn','-ac','1','-ar','16000','-b:a','48k',str(dest)],check=True,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                result=dest.read_bytes()
                if not 0<len(result)<=20*1024*1024:raise ValueError()
                return result
            except Exception:raise RemoteError() from None
    def __call__(self,body):
        body=self.encoder(body)
        boundary='irina'+secrets.token_hex(12)
        data=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\ngpt-4o-mini-transcribe\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n').encode()+body+f'\r\n--{boundary}--\r\n'.encode()
        # Paid POST: no automatic retry on an ambiguous outcome.
        raw=self.http.request('https://api.openai.com/v1/audio/transcriptions',body=data,headers={
            'Authorization':'Bearer '+self.token,'Content-Type':'multipart/form-data; boundary='+boundary},limit=1024*1024)
        try:
            text=json.loads(raw)['text']
            if not isinstance(text,str) or not text.strip():raise ValueError()
            return text
        except Exception:raise RemoteError(uncertain=True) from None
