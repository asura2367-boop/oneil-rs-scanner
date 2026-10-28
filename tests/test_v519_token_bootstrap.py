import unittest
from oneil_v519_token_bootstrap import run

CREDS = {'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false','KIS_APP_KEY':'a',
         'KIS_APP_SECRET':'b','KIS_ACCOUNT_NO':'12345678','KIS_ACCOUNT_PRODUCT':'01',
         'KIS_TOKEN_CACHE_KEY':'stub'}

class BootstrapTests(unittest.TestCase):
    def test_offline_never_uses_network(self):
        self.assertEqual(run(enabled=False,env={}), 'NOT_REQUESTED')
    def test_missing_credentials(self):
        self.assertEqual(run(enabled=True,env={}), 'CREDENTIALS_OR_CACHE_KEY_MISSING')
    def test_live_flags_denied(self):
        self.assertEqual(run(enabled=True,env={**CREDS,'LIVE_ENABLED':'true'}),'BLOCKED_LIVE_FLAGS')
    def test_order_flag_denied(self):
        self.assertEqual(run(enabled=True,env={**CREDS,'KIS_ALLOW_ORDER':'true'}),'BLOCKED_LIVE_FLAGS')
    def test_refreshed(self):
        self.assertEqual(run(enabled=True,env=CREDS,key_loader=lambda:b'x',acquirer=lambda k,n:('secret-token',True)),'READY_THIS_RUN_REFRESHED')
    def test_reused(self):
        self.assertEqual(run(enabled=True,env=CREDS,key_loader=lambda:b'x',acquirer=lambda k,n:('secret-token',False)),'READY_THIS_RUN_REUSED')
    def test_invalid_token(self):
        self.assertEqual(run(enabled=True,env=CREDS,key_loader=lambda:b'x',acquirer=lambda k,n:('',False)),'TOKEN_RESPONSE_INVALID')
    def test_api_failure_sanitized(self):
        def fail(k,n): raise RuntimeError('fake secret should never be logged')
        self.assertEqual(run(enabled=True,env=CREDS,key_loader=lambda:b'x',acquirer=fail),'TOKEN_BOOTSTRAP_BLOCKED')
    def test_key_failure_sanitized(self):
        def fail(): raise RuntimeError('fake secret should never be logged')
        self.assertEqual(run(enabled=True,env=CREDS,key_loader=fail,acquirer=lambda k,n:('x',True)),'TOKEN_BOOTSTRAP_BLOCKED')
    def test_bad_refreshed_type(self):
        self.assertEqual(run(enabled=True,env=CREDS,key_loader=lambda:b'x',acquirer=lambda k,n:('x',1)),'TOKEN_RESPONSE_INVALID')

if __name__ == '__main__': unittest.main()
