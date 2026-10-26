import unittest
from unittest.mock import patch
import kis_live_v4_8 as v

class TestV48(unittest.TestCase):
    def test_zero_deny(self):
        r=v.report_template(__import__('datetime').datetime(2026,10,26))
        self.assertEqual(r['status'],'DENY')
        self.assertFalse(r['daily_loss_verified'])
        self.assertEqual(r['orders_sent'],0)
    def test_valid_shape(self):
        self.assertTrue(all(v.parse_equity_summary({'rt_cd':'0','output2':[{'tot_evlu_amt':'1000000','scts_evlu_amt':'500000'}]}).values()))
    def test_missing_does_not_pass(self):
        self.assertFalse(all(v.parse_equity_summary({'rt_cd':'0','output2':[{'tot_evlu_amt':'1000000'}]}).values()))
    def test_bad_response(self):
        with self.assertRaises(v.Blocked): v.parse_equity_summary({'rt_cd':'1'})
    def test_order_switch(self):
        with patch.dict('os.environ',{'LIVE_ENABLED':'true'}):
            with self.assertRaises(v.Blocked): v.safety_off()
    def test_unverified_cashflows(self):
        r=v.report_template(__import__('datetime').datetime.now())
        self.assertFalse(r['external_cashflows_verified'])
        self.assertFalse(r['v4_7_input_generated'])
if __name__=='__main__': unittest.main()
