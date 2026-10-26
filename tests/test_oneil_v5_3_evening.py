import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_v5_3_evening import evaluate, exchange_session
Z=ZoneInfo('Asia/Seoul')
class TestV53(unittest.TestCase):
    def test_window(self):
        self.assertEqual(exchange_session(dt.datetime(2026,10,26,15,45,tzinfo=Z)),['NXT'])
        self.assertEqual(exchange_session(dt.datetime(2026,10,26,17,0,tzinfo=Z)),['NXT','KRX'])
        self.assertEqual(exchange_session(dt.datetime(2026,10,26,20,0,tzinfo=Z)),[])
    def test_off(self):
        r=evaluate(dt.datetime(2026,10,26,17,tzinfo=Z),False,['052460'],lambda a,b:{'ask_krw':5000})
        self.assertEqual(r['status'],'SKIPPED_OFF')
    def test_all_non_authorizing(self):
        r=evaluate(dt.datetime(2026,10,26,17,tzinfo=Z),True,['052460'],lambda a,b:{'ask_krw':5000})
        self.assertEqual(len(r['candidates']),2)
        self.assertFalse(any(x['trade_authorized'] for x in r['candidates']))
        self.assertEqual(r['orders_sent'],0)
    def test_api_failure_not_eligible(self):
        def fail(a,b):raise RuntimeError('PRIVATE')
        r=evaluate(dt.datetime(2026,10,26,17,tzinfo=Z),True,['052460'],fail)
        self.assertEqual(r['candidates'][0]['reason'],'QUOTE_UNAVAILABLE_OR_INVALID')
        self.assertNotIn('PRIVATE',str(r))
