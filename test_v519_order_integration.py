import sys, types
ledger = types.ModuleType('v519_pg_ledger')
class LedgerBlocked(RuntimeError): pass
ledger.LedgerBlocked = LedgerBlocked
ledger.connect = lambda: None
sys.modules['v519_pg_ledger'] = ledger
submit = types.ModuleType('v519_durable_submit')
submit.make_intent_id = lambda *a: None
submit.submit_once = lambda *a, **k: None
sys.modules['v519_durable_submit'] = submit
import unittest
from v519_order_integration import make_execution_review, execute_live
from v519_pg_ledger import LedgerBlocked

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.plan={'status':'ORDER_PLAN_READ_ONLY','orders_sent':0,'orders':[
            {'ticker':'222800','reference_price':129100,'preview_quantity':1},
            {'ticker':'234690','reference_price':11350,'preview_quantity':15}]}
        self.snapshot={'held':set(),'pending':set(),'invested':0,
                       'complete_daily_pnl':False,'complete_history':False,
                       'executable_quote_verified':False}
        self.ledger={'halted':True,'unresolved':0}
    def test_readonly_plan(self):
        r=make_execution_review(self.plan,self.snapshot,self.ledger)
        self.assertEqual([o['quantity'] for o in r['proposals']],[1,15])
        self.assertEqual(r['orders_sent'],0)
    def test_existing_holding(self):
        self.snapshot['held']={'222800'}
        self.assertEqual(make_execution_review(self.plan,self.snapshot,self.ledger)['proposals'][0]['quantity'],0)
    def test_pending_blocks(self):
        self.snapshot['pending']={'222800'}
        with self.assertRaises(LedgerBlocked):make_execution_review(self.plan,self.snapshot,self.ledger)
    def test_live_denied(self):
        with self.assertRaises(LedgerBlocked):execute_live()
if __name__=='__main__':unittest.main()
