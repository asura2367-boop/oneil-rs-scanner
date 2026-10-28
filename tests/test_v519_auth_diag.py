import os
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from oneil_v519_auth_diag import diagnose

VALID = {'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false','KIS_APP_KEY':'test',
         'KIS_APP_SECRET':'test','KIS_ACCOUNT_NO':'12345678',
         'KIS_ACCOUNT_PRODUCT':'01','KIS_TOKEN_CACHE_KEY':'test'}

class AuthTests(unittest.TestCase):
    def test_live_switch_rejected(self):
        self.assertEqual(diagnose({**VALID,'LIVE_ENABLED':'true'}),'BLOCKED_LIVE_FLAGS')
    def test_order_switch_rejected(self):
        self.assertEqual(diagnose({**VALID,'KIS_ALLOW_ORDER':'true'}),'BLOCKED_LIVE_FLAGS')
    def test_missing_credentials(self):
        self.assertEqual(diagnose({**VALID,'KIS_APP_SECRET':''}),'BROKER_CREDENTIALS_MISSING')
    def test_missing_key(self):
        self.assertEqual(diagnose({**VALID,'KIS_TOKEN_CACHE_KEY':''}),'CACHE_KEY_MISSING')
    def test_missing_cache(self):
        with TemporaryDirectory() as d:
            self.assertEqual(diagnose(VALID,Path(d)/'missing'),'ENCRYPTED_CACHE_MISSING')
    def test_valid_cache_stub(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'cache';p.write_text('dummy')
            self.assertEqual(diagnose(VALID,p,reader=lambda: ('redacted',object())),'CACHE_PRESENT_AND_VALID')
    def test_expired_cache_stub(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'cache';p.touch()
            self.assertEqual(diagnose(VALID,p,reader=lambda: None),'CACHE_EXPIRED_OR_UNAVAILABLE')
    def test_reader_exception_sanitized(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'cache';p.touch()
            def bad():raise RuntimeError('SECRET_SENSITIVE_VALUE')
            self.assertEqual(diagnose(VALID,p,reader=bad),'CACHE_INVALID_OR_UNREADABLE')

if __name__=='__main__':unittest.main()
