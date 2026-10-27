import unittest
from oneil_live_v5_10 import compile_report
class TestV510(unittest.TestCase):
    def test_five_candidates_still_no_orders(self):
        c={'status':'DENY','source':'V4B_CSV_UNVERIFIED','orders_sent':0,'review_candidates':[{'ticker':f'{i:06d}','flag':'REVIEW_ONLY_NOT_LIVE_QUOTE','rs':90,'flow_category':'FOREIGN_ONLY'} for i in range(5)]}
        b={'broker_read_status':'CHILD_COMPLETED','broker_report_checks':{'preflight':'SNAPSHOT_PRESENT','reconciliation':'READ_ONLY_VALID','gate':'DENY_VALID'}}
        x=compile_report(c,b,broker_requested=True)
        self.assertEqual(x['candidate_count'],5)
        self.assertEqual(x['orders_sent'],0)
        self.assertIsNone(x['actual_order_quantities'])
        self.assertFalse(x['actual_orderable_cash_verified'])
    def test_bad_broker_does_not_authorize(self):
        x=compile_report({}, {'broker_read_status':'CHILD_COMPLETED'},broker_requested=True)
        self.assertFalse(x['candidate_input_valid'])
        self.assertEqual(x['broker_report_checks']['preflight'],'MISSING')
        self.assertEqual(x['gate'],'DENY')
    def test_fake_report_denied(self):
        x=compile_report({'status':'ALLOW','source':'V4B_CSV_UNVERIFIED','orders_sent':0,'review_candidates':[]},{})
        self.assertFalse(x['candidate_input_valid'])
    def test_malformed_row_rejected(self):
        c={'status':'DENY','source':'V4B_CSV_UNVERIFIED','orders_sent':0,'review_candidates':[{'ticker':'INVALID','flag':'REVIEW_ONLY_NOT_LIVE_QUOTE'}]}
        self.assertEqual(compile_report(c,{})['candidate_count'],0)
if __name__=='__main__': unittest.main()
