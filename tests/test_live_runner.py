import os,unittest,tempfile,sqlite3
from pathlib import Path
from unittest.mock import patch
from oneil_v519_live_runner import strict_amount,record_unknown,init_db,Blocked,live_submit
class LiveTests(unittest.TestCase):
 def test_integer_cash(self):self.assertEqual(strict_amount("5000000"),5000000)
 def test_bad_cash(self):
  with self.assertRaises(Blocked):strict_amount(None)
 def test_negative_cash(self):
  with self.assertRaises(Blocked):strict_amount("-10")
 def test_duplicate_order(self):
  with tempfile.TemporaryDirectory() as d:
   db=init_db(Path(d)/"orders.sqlite")
   record_unknown(db,"x","144960","BUY",3,10000)
   with self.assertRaises(Blocked):record_unknown(db,"x","144960","BUY",3,10000)
   db.close()
 def test_state_survives_restart(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/"orders.sqlite"
   cx=init_db(path);record_unknown(cx,"x","144960","BUY",1,10000);cx.close()
   cx=init_db(path)
   self.assertEqual(cx.execute("SELECT status FROM orders WHERE intent='x'").fetchone()[0],"SUBMISSION_UNKNOWN")
   cx.close()
 def test_submit_requires_approval(self):
  with patch.dict(os.environ,{"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"},clear=True):
   with self.assertRaises(Blocked):live_submit("tok","k","s","12345678","01","144960",1,10000,"BUY")
 def test_partial_unlock_is_blocked(self):
  with patch.dict(os.environ,{"LIVE_ENABLED":"true","KIS_ALLOW_ORDER":"false","I_APPROVE_LIVE_KIS_ORDERS":"YES"},clear=True):
   with self.assertRaises(Blocked):live_submit("tok","k","s","12345678","01","144960",1,10000,"BUY")
