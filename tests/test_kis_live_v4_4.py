import os
import unittest
from unittest.mock import patch
from kis_live_v4_4 import reconcile, api, Blocked, get_secrets

STATE={'schema_version':1,'mode':'NO_ORDER_STATE_TRACKER','intents':{
 'a':{'ticker':'052460','state':'PREVIEW_ONLY','broker_order_id':None,'filled_qty':0}}}
ROW={'odno':'12345','pdno':'052460','ord_qty':'10','rmn_qty':'7'}

class ReconciliationTests(unittest.TestCase):
    def test_preview_stays_unlinked(self):
        a=reconcile(STATE,[ROW]); self.assertEqual(a['matched_order_ids'],0)
        self.assertEqual(a['unmatched_broker_order_ids'],1)
        self.assertEqual(STATE['intents']['a']['state'],'PREVIEW_ONLY')
    def test_no_orders(self):
        a=reconcile(STATE,[]); self.assertFalse(a['requires_manual_review'])
        self.assertEqual(a['preview_intents_not_linked'],1)
    def test_invalid_qty_blocks(self):
        with self.assertRaises(Blocked): reconcile(STATE,[dict(ROW,rmn_qty='11')])
    def test_preview_id_conflict(self):
        state={'schema_version':1,'mode':'NO_ORDER_STATE_TRACKER','intents':{'a':dict(STATE['intents']['a'],broker_order_id='12')}}
        with self.assertRaises(Blocked): reconcile(state,[])
    def test_only_approved_read_endpoints(self):
        with self.assertRaises(Blocked): api('POST','/uapi/domestic-stock/v1/trading/order-cash',payload={})
    def test_live_guard(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
            with self.assertRaises(Blocked): get_secrets()
    def test_explicit_verified_link_only(self):
        state={'schema_version':1,'mode':'NO_ORDER_STATE_TRACKER','intents':{'a':{'ticker':'052460','state':'ACKNOWLEDGED','broker_order_id':'12345','filled_qty':0}}}
        result=reconcile(state,[ROW]); self.assertEqual(result['matched_order_ids'],1)
    def test_duplicate_order_number(self):
        state={'schema_version':1,'mode':'NO_ORDER_STATE_TRACKER','intents':{
            'a':{'ticker':'052460','state':'ACKNOWLEDGED','broker_order_id':'12345','filled_qty':0},
            'b':{'ticker':'048910','state':'ACKNOWLEDGED','broker_order_id':'12345','filled_qty':0}}}
        with self.assertRaises(Blocked): reconcile(state,[])

if __name__=='__main__': unittest.main()
