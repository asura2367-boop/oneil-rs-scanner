import pathlib
import os,unittest,tempfile,sqlite3
from pathlib import Path
from unittest.mock import patch
from oneil_v519_live_runner import strict_amount,open_persistent_journal,Blocked,live_submit
from oneil_v519_order_recovery import OrderJournal,ReconcileBlocked
class LiveTests(unittest.TestCase):
 def test_integer_cash(self):self.assertEqual(strict_amount("5000000"),5000000)
 def test_bad_cash(self):
  with self.assertRaises(Blocked):strict_amount(None)
 def test_negative_cash(self):
  with self.assertRaises(Blocked):strict_amount("-10")
 def test_duplicate_order(self):
  with tempfile.TemporaryDirectory() as d:
   db=OrderJournal(Path(d)/"orders.sqlite")
   db.prepare("1234567890abcdef","144960","BUY","KRX",3)
   with self.assertRaises(ReconcileBlocked):db.prepare("1234567890abcdef","144960","BUY","KRX",3)
   db.close()
 def test_state_survives_restart(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/"orders.sqlite"
   cx=OrderJournal(path);cx.prepare("1234567890abcdef","144960","BUY","KRX",1);cx.close()
   cx=OrderJournal(path)
   self.assertEqual(cx.pending()[0][2],"SUBMISSION_UNKNOWN")
   cx.close()
 def test_submit_requires_approval(self):
  with patch.dict(os.environ,{"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"},clear=True):
   with self.assertRaises(Blocked):live_submit("tok","k","s","12345678","01","144960",1,10000,"BUY")
 def test_partial_unlock_is_blocked(self):
  with patch.dict(os.environ,{"LIVE_ENABLED":"true","KIS_ALLOW_ORDER":"false","I_APPROVE_LIVE_KIS_ORDERS":"YES"},clear=True):
   with self.assertRaises(Blocked):live_submit("tok","k","s","12345678","01","144960",1,10000,"BUY")

class RecoveryIntegrationTests(unittest.TestCase):
 def test_pending_orders_block_any_new_run(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)
   db=OrderJournal(p/"orders.sqlite")
   db.prepare("1234567890abcdef","144960","BUY","KRX",2)
   db.close()
   with self.assertRaises(Blocked):open_persistent_journal(p)
 def test_empty_journal_can_open(self):
  with tempfile.TemporaryDirectory() as d:
   db=open_persistent_journal(pathlib.Path(d))
   self.assertEqual(db.pending(),[])
   db.close()
