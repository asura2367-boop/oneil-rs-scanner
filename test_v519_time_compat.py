import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_live_v5_19 import fresh, build
KST=ZoneInfo('Asia/Seoul')
class TestCompatibility(unittest.TestCase):
    def setUp(self):self.now=dt.datetime(2026,10,27,18,30,tzinfo=KST)
    def test_as_of(self):self.assertTrue(fresh({'as_of_kst':self.now.isoformat()},self.now))
    def test_generated(self):self.assertTrue(fresh({'generated_at_kst':self.now.isoformat()},self.now))
    def test_old(self):self.assertFalse(fresh({'as_of_kst':(self.now-dt.timedelta(minutes=6)).isoformat()},self.now))
    def test_future(self):self.assertFalse(fresh({'generated_at_kst':(self.now+dt.timedelta(seconds=1)).isoformat()},self.now))
    def test_naive(self):self.assertFalse(fresh({'as_of_kst':'2026-10-27T18:30:00'},self.now))
    def test_missing(self):self.assertFalse(fresh({},self.now))
    def test_always_deny(self):
        v=build({'gate':'DENY','orders_sent':0,'review_intents':[]},{'version':'5.18','gate':'DENY','orders_sent':0,'generated_at_kst':self.now.isoformat()},{'gate':'DENY','orders_sent':0,'as_of_kst':self.now.isoformat()},None,now=self.now)
        self.assertEqual(v['gate'],'DENY');self.assertEqual(v['orders_sent'],0)
        self.assertIn('QUOTE_NOT_CERTIFIED_FOR_TRADING',v['issues'])
        self.assertIn('ACCOUNT_BUYING_POWER_NOT_VERIFIED',v['issues'])
if __name__=='__main__':unittest.main()
