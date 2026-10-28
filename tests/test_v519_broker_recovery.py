import tempfile,unittest
from pathlib import Path
from oneil_v519_order_recovery import OrderJournal,ReconcileBlocked
from oneil_v519_broker_recovery import read_full_day,reconcile_existing

class Tests(unittest.TestCase):
 def test_empty_page(self):
  self.assertEqual(read_full_day(lambda *x:('D',{'rt_cd':'0','output1':[]}), '20261028'),[])
 def test_missing_header(self):
  with self.assertRaises(ReconcileBlocked):read_full_day(lambda *x:(None,{'rt_cd':'0','output1':[]}), '20261028')
 def test_unterminated_cursor(self):
  with self.assertRaises(ReconcileBlocked):read_full_day(lambda *x:('F',{'rt_cd':'0','output1':[]}), '20261028')
 def test_unknown_broker_id_never_retries(self):
  with tempfile.TemporaryDirectory() as d:
   j=OrderJournal(Path(d)/'orders.sqlite');j.prepare('intent_0000000001','144960','BUY','KRX',5)
   r=reconcile_existing(j,[],trading_day='20261028',full_history_verified=True)
   self.assertEqual(r['unresolved'],1);self.assertEqual(r['orders_sent'],0);j.close()
 def test_incomplete_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   j=OrderJournal(Path(d)/'orders.sqlite')
   with self.assertRaises(ReconcileBlocked):reconcile_existing(j,[],trading_day='20261028')
   j.close()
 def test_no_rows_unauthorized(self):
  with tempfile.TemporaryDirectory() as d:
   j=OrderJournal(Path(d)/'orders.sqlite');r=reconcile_existing(j,[],trading_day='20261028',full_history_verified=True)
   self.assertEqual(r['gate'],'DENY');j.close()
