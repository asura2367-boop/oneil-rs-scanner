import os
import unittest
from unittest.mock import patch
from kis_live_v4 import prepare, protect, SafetyBlock

S = {'last_processed':'2026-10-23', 'pending':[{'signal_date':'2026-10-23','ticker':'052460','side':'BUY'}]}
R = {'last_processed':'2026-10-23', 'pending':[{'ticker':'052460','reference_close':5980,'indicative_price':5985.98,'estimated_qty':334}]}

class TestV4(unittest.TestCase):
    def test_safe_plan(self):
        x=prepare(S,R)
        self.assertEqual(x['orders_sent'],0)
        self.assertEqual(x['intents'][0]['order_qty'],0)
        self.assertEqual(x['intents'][0]['status'],'BLOCKED_AWAITING_LIVE_PREFLIGHT')
    def test_duplicate(self):
        with self.assertRaises(SafetyBlock):
            prepare({**S,'pending':S['pending']*2},R)
    def test_unmatched_date(self):
        with self.assertRaises(SafetyBlock):
            prepare(S,{**R,'last_processed':'2026-10-22'})
    def test_enabled_denied(self):
        with patch.dict(os.environ, {'LIVE_ENABLED':'true'}):
            with self.assertRaises(SafetyBlock): protect()
    def test_order_gate_denied(self):
        with patch.dict(os.environ, {'KIS_ALLOW_ORDER':'true','LIVE_ENABLED':'false'}):
            with self.assertRaises(SafetyBlock): protect()
if __name__=='__main__': unittest.main()
