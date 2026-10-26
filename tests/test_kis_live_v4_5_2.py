import base64
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import kis_live_v4_5_2 as m

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.orig=m.CACHE
        m.CACHE=Path(self.temp.name)/'cache.json'
        self.addCleanup(setattr,m,'CACHE',self.orig)
        self.key=os.urandom(32)
        self.now=dt.datetime.now(dt.timezone.utc)

    def test_encrypt_reuse(self):
        token='PRIVATE_TEST_ACCESS_TOKEN'
        m._save(self.key,token,self.now+dt.timedelta(hours=5))
        self.assertNotIn(token,m.CACHE.read_text())
        self.assertEqual(m._read(self.key,self.now)[0],token)
    def test_expired_not_used(self):
        m._save(self.key,'SECRET',self.now+dt.timedelta(minutes=3))
        self.assertIsNone(m._read(self.key,self.now))
    def test_wrong_key_blocks(self):
        m._save(self.key,'SECRET',self.now+dt.timedelta(hours=5))
        with self.assertRaises(m.CacheBlocked):
            m._read(os.urandom(32),self.now)
    def test_tamper_blocks(self):
        m._save(self.key,'SECRET',self.now+dt.timedelta(hours=5))
        obj=json.loads(m.CACHE.read_text())
        obj['ciphertext']=base64.b64encode(b'corrupt').decode()
        m.CACHE.write_text(json.dumps(obj))
        with self.assertRaises(m.CacheBlocked): m._read(self.key,self.now)
    def test_token_does_not_issue_when_cached(self):
        m._save(self.key,'CACHED',self.now+dt.timedelta(hours=5))
        with patch.object(m,'credentials',side_effect=AssertionError('should not request')):
            self.assertEqual(m.acquire_token(self.key,self.now),('CACHED',False))
    def test_bad_key(self):
        with patch.dict(os.environ,{'KIS_TOKEN_CACHE_KEY':'bad'}):
            with self.assertRaises(m.CacheBlocked): m._key()
    def test_missing_cache(self):
        self.assertIsNone(m._read(self.key,self.now))
    def test_expiry(self):
        self.assertEqual(m._expiry({'expires_in':3600},self.now),self.now+dt.timedelta(hours=1))
        self.assertEqual(m._expiry({'access_token_token_expired':'2026-10-26 15:00:00'},self.now).tzinfo,dt.timezone.utc)

if __name__=='__main__': unittest.main()
