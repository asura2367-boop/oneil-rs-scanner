import datetime as dt, unittest
from zoneinfo import ZoneInfo
from oneil_v519_stage5_readiness import assess,REQUIRED
NOW=dt.datetime(2026,10,28,17,0,tzinfo=ZoneInfo("Asia/Seoul"))
class Stage5Tests(unittest.TestCase):
 def test_missing_all(self):
  r=assess({},NOW,{"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"})
  self.assertEqual(r["readiness_checks_missing"],10)
  self.assertEqual(r["orders_sent"],0)
 def test_all_claimed_true_still_deny(self):
  docs={k:{"verified":True,v:True,"orders_sent":0,"generated_at_kst":NOW.isoformat()} for k,v in REQUIRED.items()}
  r=assess(docs,NOW,{"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"})
  self.assertEqual(r["gate"],"DENY")
  self.assertFalse(r["user_approved"])
 def test_on_switch_blocked(self):
  with self.assertRaises(ValueError):assess({},NOW,{"LIVE_ENABLED":"true"})
 def test_order_switch_blocked(self):
  with self.assertRaises(ValueError):assess({},NOW,{"KIS_ALLOW_ORDER":"true"})
 def test_stale(self):
  docs={"account":{"verified":True,"broker_account_verified":True,"orders_sent":0,
                   "generated_at_kst":(NOW-dt.timedelta(minutes=10)).isoformat()}}
  self.assertIn("MISSING_OR_UNVERIFIED_ACCOUNT",assess(docs,NOW,{})["blocks"])
 def test_no_order_calls(self):self.assertEqual(assess({},NOW,{})["broker_order_api_calls"],0)
