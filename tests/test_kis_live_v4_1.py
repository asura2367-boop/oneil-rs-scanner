import datetime as dt
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kis_live_v4_1 import evaluate

DAY=dt.datetime(2026,10,26,10,40,tzinfo=dt.timezone(dt.timedelta(hours=9)))
def one(code='052460'):
    return {'ticker':code,'signal_date':'2026-10-23','intent_id':'123','estimated_price_krw':6000}

class PreflightTests(unittest.TestCase):
    def test_allowed_preview_is_never_order(self):
        x=evaluate([one()],DAY,set(),0,set(),lambda t:5900,lambda t,p:(1000000,500))[0]
        self.assertEqual(x['status'],'PREVIEW_ELIGIBLE_NO_ORDER')
        self.assertEqual(x['orders_sent'],0)
        self.assertLessEqual(x['preview_amount_krw'],200000)
    def test_block_held(self):
        x=evaluate([one()],DAY,{'052460'},0,set(),lambda t:5900,lambda t,p:(1000000,500))[0]
        self.assertEqual(x['status'],'BLOCKED_ALREADY_HELD')
    def test_block_unfilled(self):
        x=evaluate([one()],DAY,set(),0,{'052460'},lambda t:5900,lambda t,p:(1000000,500))[0]
        self.assertEqual(x['status'],'BLOCKED_UNFILLED_ORDER')
    def test_block_total_cap(self):
        x=evaluate([one()],DAY,set(),1000000,set(),lambda t:5900,lambda t,p:(1000000,500))[0]
        self.assertEqual(x['status'],'BLOCKED_TOTAL_CAP')
    def test_fail_closed_quote(self):
        def broken(t): raise RuntimeError('any secret-bearing error')
        x=evaluate([one()],DAY,set(),0,set(),broken,lambda t,p:(1000000,500))[0]
        self.assertEqual(x['status'],'BLOCKED_DATA_OR_API_ERROR')
    def test_stale(self):
        x=evaluate([one()],DAY+dt.timedelta(days=2),set(),0,set(),lambda t:5900,lambda t,p:(1000000,500))[0]
        self.assertEqual(x['status'],'BLOCKED_STALE_OR_FUTURE_SIGNAL')
    def test_cap_across_two_stocks(self):
        out=evaluate([one(),one('048910')],DAY,set(),750000,set(),lambda t:5900,lambda t,p:(1000000,500))
        self.assertLessEqual(sum(x['preview_amount_krw'] for x in out),250000)
    def test_no_negative_values(self):
        x=evaluate([one()],DAY,set(),0,set(),lambda t:5900,lambda t,p:(-1,100))[0]
        self.assertEqual(x['status'],'BLOCKED_BUYING_POWER_INVALID')
if __name__=='__main__':unittest.main()
