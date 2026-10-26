import os
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
import kis_live_v4_5_1 as subject

class SessionTests(unittest.TestCase):
    def test_one_token_three_checks(self):
        calls=[]
        def fake_run(cmd,env,check):
            self.assertEqual(env['KIS_SESSION_ACCESS_TOKEN'],'MEMORY_TOKEN')
            calls.append(cmd)
            return Mock(returncode=0)
        self.assertTrue(subject.run_checks('MEMORY_TOKEN',run=fake_run))
        self.assertEqual(len(calls),3)
        self.assertNotIn('/oauth2/tokenP',' '.join(' '.join(x) for x in calls))
    def test_fail_closed(self):
        with self.assertRaises(subject.SessionBlocked):
            subject.run_checks('TOKEN',run=lambda *args,**kw:Mock(returncode=1))
    def test_403_sanitized(self):
        with patch.object(subject,'credentials',return_value=('x','y','12345678','01')):
            with patch.object(subject,'request',side_effect=HTTPError('https://private',403,'SECRET',{},None)):
                with self.assertRaisesRegex(subject.SessionBlocked,'AUTH_HTTP_403'):
                    subject.acquire_token()
    def test_invalid_token_rejected(self):
        with patch.object(subject,'credentials',return_value=('x','y','12345678','01')):
            with patch.object(subject,'request',return_value={'access_token':'not valid'}):
                with self.assertRaises(subject.SessionBlocked):subject.acquire_token()
if __name__=='__main__':unittest.main()
