import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from oneil_v519_final_diagnostic import summarize
KST=ZoneInfo("Asia/Seoul")
class Tests(unittest.TestCase):
 def setUp(self):
  self.now=dt.datetime(2026,10,28,13,0,tzinfo=KST)
  stamp=self.now.isoformat()
  self.c={"generated_at_kst":stamp,"gate":"DENY","mode":"LIVE_INTENTS_PREVIEW_ONLY","orders_sent":0,"intents":[{"ticker":"144960","status":"REVIEW_ONLY_UNPRICED","quantity":None,"price_krw":None}]}
  self.a={"as_of_kst":stamp,"gate":"DENY","orders_sent":0,"broker_status":"READ_ONLY_OBSERVED"}
  self.b={"generated_at_kst":stamp,"gate":"DENY","orders_sent":0,"status":"OBSERVATION_ATTEMPTED"}
  self.q={"generated_at_kst":stamp,"gate":"DENY","orders_sent":0,"version":"5.18"}
  self.l={"generated_at_kst":stamp,"gate":"DENY","orders_sent":0,"daily_loss_evidence_valid":True,"daily_loss_limit_reached":False,"order_history_structure_valid":True}
 def check(self):
  return summarize(self.c,self.a,self.b,self.q,self.l,self.l,self.now,env={"LIVE_ENABLED":"false","KIS_ALLOW_ORDER":"false"})
 def test_full_observation_still_denies(self):
  r=self.check();self.assertEqual(r["gate"],"DENY");self.assertEqual(r["orders_sent"],0)
 def test_no_executable_quote(self):self.assertIn("EXECUTABLE_QUOTE_UNATTESTED",self.check()["block_codes"])
 def test_independent_history_not_approved(self):self.assertIn("BROKER_ORDER_HISTORY_INDEPENDENCE_UNVERIFIED",self.check()["block_codes"])
 def test_no_auto_retry(self):self.assertIn("AUTO_RESUBMISSION_DISABLED",self.check()["block_codes"])
 def test_cash_allocation(self):
  r=self.check();self.assertEqual((r["reserve_pct"],r["trading_pct"],r["stock_pct_of_trading_budget"]),(40,60,30))
 def test_missing_account(self):self.a=None;self.assertFalse(self.check()["account_observed"])
 def test_missing_loss(self):self.l=None;self.assertFalse(self.check()["daily_loss_evidence_valid"])
 def test_stale_quote(self):self.q["generated_at_kst"]=(self.now-dt.timedelta(minutes=6)).isoformat();self.assertFalse(self.check()["quote_observed"])
 def test_duplicate_candidate(self):self.c["intents"]*=2;self.assertEqual(self.check()["candidate_count"],0)
 def test_live_switch_block(self):
  with self.assertRaises(ValueError):summarize(self.c,self.a,self.b,self.q,self.l,self.l,self.now,env={"LIVE_ENABLED":"true"})
 def test_unpriced_only(self):
  self.c["intents"][0]["price_krw"]=12345;self.assertEqual(self.check()["candidate_count"],0)
 def test_future_evidence(self):
  self.a["as_of_kst"]=(self.now+dt.timedelta(seconds=10)).isoformat();self.assertFalse(self.check()["account_observed"])
if __name__=="__main__":unittest.main()
