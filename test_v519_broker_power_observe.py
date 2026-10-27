import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_v519_broker_power_observe import observe
KST=ZoneInfo('Asia/Seoul')
DATA={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,'intents':[{'ticker':'085910','status':'REVIEW_ONLY_UNPRICED','quantity':None,'price_krw':None}]}
class Fake:
    def account(self):return set(),set()
    def indicative_price(self,t,now):return 5000
    def buying_power(self,t,p):return 100000,20
class Test(unittest.TestCase):
    def test_off(self):
        r=observe(DATA,dt.datetime(2026,10,28,10,tzinfo=KST),env={})
        self.assertEqual(r['status'],'NOT_REQUESTED')
    def test_closed(self):
        r=observe(DATA,dt.datetime(2026,10,28,18,tzinfo=KST),enabled=True,env={})
        self.assertEqual(r['status'],'SESSION_BLOCKED')
    def test_indicative_only(self):
        r=observe(DATA,dt.datetime(2026,10,28,10,tzinfo=KST),enabled=True,provider=Fake(),env={})
        self.assertEqual(r['checks'][0]['status'],'INDICATIVE_POWER_OBSERVED_UNATTESTED')
        self.assertFalse(r['broker_buying_power_verified']);self.assertEqual(r['orders_sent'],0)
        self.assertNotIn('100000',str(r));self.assertNotIn('5000',str(r))
    def test_held(self):
        class Held(Fake):
            def account(self):return {'085910'},set()
        r=observe(DATA,dt.datetime(2026,10,28,10,tzinfo=KST),enabled=True,provider=Held(),env={})
        self.assertEqual(r['checks'][0]['status'],'ALREADY_HELD')
    def test_fail_closed(self):
        class F(Fake):
            def buying_power(self,t,p):raise RuntimeError('sensitive response')
        r=observe(DATA,dt.datetime(2026,10,28,10,tzinfo=KST),enabled=True,provider=F(),env={})
        self.assertEqual(r['checks'][0]['status'],'INDICATIVE_LOOKUP_BLOCKED');self.assertNotIn('sensitive',str(r))
    def test_flag(self):
        with self.assertRaises(RuntimeError):observe(DATA,dt.datetime(2026,10,28,10,tzinfo=KST),env={'LIVE_ENABLED':'true'})
if __name__=='__main__':unittest.main()
