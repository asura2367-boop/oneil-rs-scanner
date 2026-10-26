import datetime as dt
import unittest
from kis_live_v4_10 import regular_session, build_verified_budget, Stop

P=[{'ticker':'052460','status':'PREVIEW_ONLY','reference_close':5900},
   {'ticker':'048910','status':'PREVIEW_ONLY','reference_close':8100}]

class TestV410(unittest.TestCase):
    def test_regular_hours(self):
        self.assertTrue(regular_session(dt.datetime(2026,10,26,10,30)))
        self.assertFalse(regular_session(dt.datetime(2026,10,26,16,30)))
        self.assertFalse(regular_session(dt.datetime(2026,10,25,10,30)))
    def test_allocation(self):
        r=build_verified_budget(P,set(),set(),0,lambda t:6000 if t=='052460' else 8000,
            lambda t,p:(1000000,200),40,30)
        self.assertEqual(r['reserve_pct'],'40')
        self.assertEqual(r['per_stock_max_krw'],180000)
        self.assertEqual(r['gate'],'DENY')
    def test_missing_broker_check_blocks(self):
        with self.assertRaises(Exception):
            build_verified_budget(P,set(),set(),0,lambda t:6000,
                lambda t,p: (_ for _ in ()).throw(RuntimeError('broken')))
    def test_held_blocks(self):
        with self.assertRaises(Stop):
            build_verified_budget(P,{'052460'},set(),0,lambda t:6000,lambda t,p:(1000000,200))
    def test_deduction(self):
        r=build_verified_budget(P,set(),set(),900000,lambda t:5900 if t=='052460' else 8000,lambda t,p:(1000000,200))
        self.assertEqual(r['orderable_cash_krw'],100000)

if __name__=='__main__': unittest.main()
