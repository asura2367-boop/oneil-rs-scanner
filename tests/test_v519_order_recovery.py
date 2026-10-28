import tempfile, unittest
from pathlib import Path
from oneil_v519_order_recovery import OrderJournal, ReconcileBlocked
class TestRecovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.sqlite';self.j=OrderJournal(self.path)
  self.j.prepare('x'*20,'144960','BUY','KRX',10)
 def tearDown(self):self.j.close();self.tmp.cleanup()
 def test_duplicate(self):
  with self.assertRaises(ReconcileBlocked):self.j.prepare('x'*20,'144960','BUY','KRX',10)
 def test_persisted_restart(self):
  self.j.close();self.j=OrderJournal(self.path);self.assertEqual(len(self.j.pending()),1)
 def test_unknown_requires_reconciliation(self):
  with self.assertRaises(ReconcileBlocked):self.j.reconcile('x'*20,{'broker_id':'1','status':'FILLED','filled_qty':10})
 def test_partial_fill(self):
  x=self.j.reconcile('x'*20,{'broker_id':'1','status':'PART_FILLED','filled_qty':3},full_history_verified=True)
  self.assertEqual(x['unfilled_qty'],7);self.assertFalse(x['automatic_resubmission_allowed'])
 def test_complete(self):
  self.j.reconcile('x'*20,{'broker_id':'1','status':'ACKNOWLEDGED','filled_qty':0},full_history_verified=True)
  self.j.reconcile('x'*20,{'broker_id':'1','status':'FILLED','filled_qty':10},full_history_verified=True)
  self.assertEqual(self.j.pending(),[])
 def test_overfill(self):
  with self.assertRaises(ReconcileBlocked):self.j.reconcile('x'*20,{'broker_id':'1','status':'FILLED','filled_qty':11},full_history_verified=True)
 def test_regress(self):
  self.j.reconcile('x'*20,{'broker_id':'1','status':'PART_FILLED','filled_qty':5},full_history_verified=True)
  with self.assertRaises(ReconcileBlocked):self.j.reconcile('x'*20,{'broker_id':'1','status':'PART_FILLED','filled_qty':4},full_history_verified=True)
 def test_id_conflict(self):
  self.j.reconcile('x'*20,{'broker_id':'1','status':'ACKNOWLEDGED','filled_qty':0},full_history_verified=True)
  with self.assertRaises(ReconcileBlocked):self.j.reconcile('x'*20,{'broker_id':'2','status':'FILLED','filled_qty':10},full_history_verified=True)
 def test_terminal_no_replay(self):
  self.j.reconcile('x'*20,{'broker_id':'1','status':'FILLED','filled_qty':10},full_history_verified=True)
  with self.assertRaises(ReconcileBlocked):self.j.reconcile('x'*20,{'broker_id':'1','status':'FILLED','filled_qty':10},full_history_verified=True)
 def test_no_network_import(self):
  import oneil_v519_order_recovery as m
  self.assertFalse(hasattr(m,'submit'))
if __name__=='__main__':unittest.main()
