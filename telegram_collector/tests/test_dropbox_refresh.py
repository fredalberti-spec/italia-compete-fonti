import sys,unittest,json,threading
from pathlib import Path
from urllib.parse import parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dropbox_refresh import RefreshHTTP,SCOPES,TOKEN_URL,prepared_dropbox
from live_adapters import RemoteError
class Fake:
 def __init__(self):self.calls=[];self.response={'access_token':'FAKE_ACCESS','expires_in':3600,'token_type':'bearer','scope':' '.join(SCOPES)};self.error=None
 def request(self,url,**kw):
  self.calls.append((url,kw))
  if url==TOKEN_URL:
   if self.error=='timeout':raise RemoteError(0,True)
   return json.dumps(self.response).encode()
  if self.error=='401':raise RemoteError(401)
  return b'{"size":1}'
class RefreshTests(unittest.TestCase):
 def make(self):
  f=Fake();now=[0];p=RefreshHTTP('PUBLIC_APP','FAKE_REFRESH',persistent_access_approved=True,http=f,clock=lambda:now[0]);return p,f,now
 def test_approval_gate_no_network(self):
  with self.assertRaisesRegex(ValueError,'ApprovalRequired'):RefreshHTTP('PUBLIC_APP','FAKE_REFRESH')
 def test_wire_and_cache_then_expiry(self):
  p,f,t=self.make();url='https://api.dropboxapi.com/2/files/get_metadata'
  p.json(url,payload={'path':'/notes/a'});p.json(url,payload={'path':'/notes/b'})
  self.assertEqual(sum(u==TOKEN_URL for u,k in f.calls),1)
  q=parse_qs(f.calls[0][1]['body'].decode());self.assertEqual(set(q),{'grant_type','refresh_token','client_id','scope'})
  self.assertEqual(q['grant_type'],['refresh_token']);self.assertFalse(f.calls[0][1].get('retry',False))
  self.assertEqual(f.calls[1][1]['headers']['Authorization'],'Bearer FAKE_ACCESS')
  t[0]=3541;p.json(url);self.assertEqual(sum(u==TOKEN_URL for u,k in f.calls),2)
 def test_scope_expansion_and_rotation_refused(self):
  for extra in ({'scope':'account_info.read'},{'refresh_token':'FAKE_ROTATED'},{'expires_in':float('inf')},{'token_type':'basic'}):
   p,f,t=self.make();f.response.update(extra)
   with self.assertRaises(RemoteError):p.json('https://api.dropboxapi.com/2/files/get_metadata')
   self.assertEqual(len(f.calls),1);self.assertIsNone(p._access)
 def test_uncertain_exchange_not_retried(self):
  p,f,t=self.make();f.error='timeout'
  with self.assertRaises(RemoteError):p.json('https://api.dropboxapi.com/2/files/get_metadata',retry=True)
  self.assertEqual(len(f.calls),1)
 def test_401_no_operation_replay(self):
  p,f,t=self.make();f.error='401'
  with self.assertRaises(RemoteError):p.json('https://api.dropboxapi.com/2/files/get_metadata')
  self.assertEqual(len(f.calls),2);self.assertIsNone(p._access)
 def test_unknown_destination_closed_cancelled_no_network(self):
  p,f,t=self.make()
  for url in ['http://api.dropboxapi.com/2/files/get_metadata','https://evil.test/2/files/upload','https://api.dropboxapi.com/oauth2/token']:
   with self.assertRaises(ValueError):p.request(url)
  self.assertEqual(f.calls,[]);p.cancelled=threading.Event();p.cancelled.set()
  with self.assertRaises(RemoteError):p.json('https://api.dropboxapi.com/2/files/get_metadata')
  p.cancelled=None;p.close()
  with self.assertRaises(RemoteError):p.json('https://api.dropboxapi.com/2/files/get_metadata')
  self.assertEqual(f.calls,[]);self.assertNotIn('FAKE_REFRESH',repr(p))
 def test_existing_dropbox_uses_provider(self):
  f=Fake();dbx=prepared_dropbox('PUBLIC_APP','FAKE_REFRESH',persistent_access_approved=True,http=f)
  self.assertEqual(dbx.metadata('/notes/a')['size'],1)
  self.assertEqual(f.calls[-1][1]['headers']['Authorization'],'Bearer FAKE_ACCESS')
