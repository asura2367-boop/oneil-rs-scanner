import os
import unittest
from unittest.mock import patch
from kis_live_v4_3 import initialize, Blocked, guard, simulated_transition
I='a'*20
LEDGER={'schema_version':1,'recorded':{I:{'ticker':'052460','state':'PREVIEW_RECORDED_NO_ORDER'}}}
class Tests(unittest.TestCase):
    def test_intent_import(self):
        state=initialize(LEDGER)
        self.assertEqual(state['intents'][I]['state'],'PREVIEW_ONLY')
        self.assertIsNone(state['intents'][I]['broker_order_id'])
    def test_reexecution_is_idempotent(self):
        a=initialize(LEDGER)
        self.assertEqual(a, initialize(LEDGER,a))
    def test_halt_irreversible(self):
        state=initialize(LEDGER, halted=True)
        self.assertTrue(initialize(LEDGER,state,halted=False)['halted'])
        new={'schema_version':1,'recorded':{**LEDGER['recorded'],'b'*20:{'ticker':'048910','state':'PREVIEW_RECORDED_NO_ORDER'}}}
        self.assertEqual(initialize(new,state)['intents']['b'*20]['state'],'HALTED')
    def test_transition_disabled(self):
        with self.assertRaises(Blocked): simulated_transition({'state':'ACKNOWLEDGED','filled_qty':0}, 'FILLED',filled_qty=1)
    def test_illegal_transition(self):
        with self.assertRaises(Blocked): simulated_transition({'state':'PREVIEW_ONLY','filled_qty':0},'FILLED',filled_qty=1,simulation=True)
    def test_fill_regression(self):
        with self.assertRaises(Blocked): simulated_transition({'state':'PARTIALLY_FILLED','filled_qty':2},'FILLED',filled_qty=1,simulation=True)
    def test_invalid_ledger(self):
        with self.assertRaises(Blocked): initialize({'schema_version':1,'recorded':{I:{'ticker':'052460','state':'EXECUTED'}}})
    def test_enabled_denied(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
            with self.assertRaises(Blocked):guard()
    def test_order_gate_denied(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'true'}):
            with self.assertRaises(Blocked):guard()
    def test_conflict(self):
        state=initialize(LEDGER)
        other={'schema_version':1,'recorded':{I:{'ticker':'048910','state':'PREVIEW_RECORDED_NO_ORDER'}}}
        with self.assertRaises(Blocked):initialize(other,state)
if __name__=='__main__': unittest.main()
