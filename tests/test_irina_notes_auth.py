import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs, urlencode
import irina_notes_auth as auth


class NotesAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, IRINA_DATA_DIR=str(self.root), DROPBOX_APP_KEY='test-key')
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
    def call(self, path=auth.BASE, cookie='', query='', method='GET'):
        result = {}
        def start(status, headers):
            result.update(status=status, headers=dict(headers))
        result['body'] = b''.join(auth.application({'PATH_INFO':path, 'REQUEST_METHOD':method,
            'HTTP_COOKIE':cookie,'QUERY_STRING':query},start)).decode()
        return result
    def begin(self):
        r=self.call()
        self.assertEqual(r['status'],'302 Found')
        q=parse_qs(urlparse(r['headers']['Location']).query)
        self.assertEqual(q['code_challenge_method'],['S256'])
        self.assertEqual(set(q['scope'][0].split()),set(auth.SCOPES))
        self.assertIn('HttpOnly',r['headers']['Set-Cookie'])
        return r['headers']['Set-Cookie'].split(';')[0],q['state'][0]
    def finish(self,cookie,state,namespace=auth.NAMESPACE,scope=None):
        import dropbox.oauth
        result=NS(refresh_token='private-refresh',access_token='private-access',scope=scope or ' '.join(auth.SCOPES))
        with patch.object(dropbox.oauth.DropboxOAuth2Flow,'_finish',return_value=result) as finish, \
             patch.object(dropbox.oauth.OAuth2FlowResult,'from_no_redirect_result',return_value=result), \
             patch('dropbox.Dropbox') as client:
            client.return_value.users_get_current_account.return_value=NS(root_info=NS(root_namespace_id=namespace))
            r=self.call(auth.BASE+'/callback',cookie,urlencode({'state':state,'code':'private-code'}))
            return r,finish
    def test_success_and_single_use(self):
        cookie,state=self.begin()
        r,f=self.finish(cookie,state)
        self.assertTrue(r['headers']['Location'].endswith('status=ok'))
        self.assertEqual(json.loads((self.root/'notes_dropbox.json').read_text())['refresh_token'],'private-refresh')
        self.assertEqual((self.root/'notes_dropbox.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(self.call()['status'],'409 Conflict')
        self.assertNotIn('private-',r['body']+str(r['headers']))
    def test_wrong_state_rejected_before_exchange(self):
        cookie,state=self.begin()
        r,f=self.finish(cookie,'wrong-state')
        f.assert_not_called()
        self.assertFalse((self.root/'notes_dropbox.json').exists())
        r,f=self.finish(cookie,state)
        f.assert_not_called()
    def test_wrong_account_and_missing_scope(self):
        for ns,scope in [('other',None),(auth.NAMESPACE,'files.metadata.read')]:
            cookie,state=self.begin()
            r,f=self.finish(cookie,state,ns,scope)
            self.assertTrue(r['headers']['Location'].endswith('status=failed'))
            self.assertFalse((self.root/'notes_dropbox.json').exists())
    def test_no_cookie_expired_and_methods(self):
        cookie,state=self.begin()
        self.assertTrue(self.call(auth.BASE+'/callback',query=urlencode({'state':state,'code':'x'}))['headers']['Location'].endswith('failed'))
        import sqlite3
        with sqlite3.connect(self.root/'notes_oauth.sqlite3') as db:
            db.execute('UPDATE flows SET created=0')
        r,f=self.finish(cookie,state)
        f.assert_not_called()
        self.assertEqual(self.call(method='POST')['status'],'405 Method Not Allowed')
