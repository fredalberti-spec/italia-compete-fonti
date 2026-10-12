"""Prepared offline-access adapter. No environment reads, secret writes or automatic consent.
Real use requires future explicit persistent-access authorization and human OAuth PKCE.
"""
import json,math,threading,time,urllib.parse
from live_adapters import HTTP,RemoteError,Dropbox
SCOPES=frozenset(('files.content.read','files.content.write','files.metadata.read'))
TOKEN_URL='https://api.dropboxapi.com/oauth2/token'

class RefreshHTTP:
    def __init__(self,client_id,refresh_token,*,persistent_access_approved=False,http=None,clock=time.monotonic):
        if persistent_access_approved is not True:raise ValueError('PersistentAccessApprovalRequired')
        if not isinstance(client_id,str) or not client_id or not isinstance(refresh_token,str) or not refresh_token:
            raise ValueError('InvalidRefreshConfiguration')
        self._client=client_id;self._refresh=refresh_token;self._access=None;self._expires=0
        self._http=http or HTTP();self._clock=clock;self._lock=threading.Lock();self.cancelled=None
    def _token(self):
        with self._lock:
            if not self._refresh or (self.cancelled is not None and self.cancelled.is_set()):raise RemoteError()
            if self._access and self._clock()<self._expires:return self._access
            body=urllib.parse.urlencode({'grant_type':'refresh_token','refresh_token':self._refresh,
                'client_id':self._client,'scope':' '.join(sorted(SCOPES))}).encode()
            # No app secret, authorization code, refresh rotation or retries of uncertain exchange.
            raw=self._http.request(TOKEN_URL,body=body,headers={'Content-Type':'application/x-www-form-urlencoded'},limit=16384)
            try:
                r=json.loads(raw);ttl=r['expires_in'];token=r['access_token']
                if (not isinstance(token,str) or not token or type(ttl) not in (int,float)
                    or not math.isfinite(ttl) or not 120<=ttl<=86400
                    or r.get('token_type','').lower()!='bearer' or r.get('refresh_token')
                    or ('scope' in r and frozenset(r['scope'].split())!=SCOPES)):raise ValueError()
            except Exception:
                self._access=None;self._expires=0;raise RemoteError(200,uncertain=True) from None
            self._access=token;self._expires=self._clock()+ttl-60;return token
    def request(self,url,*,headers=None,**kwargs):
        # Refuse untrusted destinations BEFORE renewing or forwarding a credential.
        if self.cancelled is not None and self.cancelled.is_set():raise RemoteError()
        p=urllib.parse.urlsplit(url)
        if (p.scheme!='https' or p.hostname not in ('api.dropboxapi.com','content.dropboxapi.com')
            or p.username or p.password or p.port not in (None,443) or not p.path.startswith('/2/files/')):
            raise ValueError('UnexpectedDropboxDestination')
        h=dict(headers or {});h['Authorization']='Bearer '+self._token()
        try:return self._http.request(url,headers=h,**kwargs)
        except RemoteError as e:
            if e.status==401:
                with self._lock:self._access=None;self._expires=0
            # No replay of original operation: caller sees sanitized failure.
            raise
    def json(self,url,*,payload=None,headers=None,retry=False):
        raw=self.request(url,body=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type':'application/json',**(headers or {})},retry=retry,limit=1024*1024)
        try:return json.loads(raw)
        except Exception:raise RemoteError(uncertain=not retry) from None
    def close(self):
        with self._lock:self._access=None;self._refresh=None;self._expires=0
    def __repr__(self):return '<RefreshHTTP secrets=redacted>'

def prepared_dropbox(client_id,refresh_token,*,persistent_access_approved=False,http=None,clock=time.monotonic):
    transport=RefreshHTTP(client_id,refresh_token,persistent_access_approved=persistent_access_approved,http=http,clock=clock)
    return Dropbox('MEMORY_PROVIDER_PLACEHOLDER',base='/',http=transport)
