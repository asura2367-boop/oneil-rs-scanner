import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from oneil_v519_cash_runtime import inspect
NOW=dt.datetime(2026,10,28,13,tzinfo=ZoneInfo('Asia/Seoul'))
SOURCE={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,
        'intents':[{'ticker':'144960','status':'REVIEW_ONLY_UNPRICED','quantity':None,'price_krw':None}]}
class Provider:
 def positions(self):return set(),set()
 def balance(self):return {'rt_cd':'0','output2':[{'dnca_tot_amt':'5000000'}]}
 def reference_price(self,t,now):return 10000
 def power(self,t,price):return {'rt_cd':'0','output':{'nrcvb_buy_amt':'3000000','ord_psbl_cash':'3000000'}}
class TestRuntime(unittest.TestCase):
 def test_default_no_network(self):self.assertEqual(inspect(SOURCE,now=NOW)['status'],'NOT_REQUESTED')
 def test_observation(self):self.assertEqual(inspect(SOURCE,Provider(),enabled=True,now=NOW)['checks'][0]['status'],'INDICATIVE_OBSERVED_NOT_AUTHORIZED')
 def test_no_cash_in_report(self):self.assertNotIn('5000000',str(inspect(SOURCE,Provider(),enabled=True,now=NOW)))
 def test_held(self):
  class Held(Provider):
   def positions(self):return {'144960'},set()
  self.assertEqual(inspect(SOURCE,Held(),enabled=True,now=NOW)['checks'][0]['status'],'HELD_OR_UNFILLED_BLOCKED')
 def test_failure_denied(self):
  class Bad(Provider):
   def balance(self):raise ValueError('SECRET')
  r=inspect(SOURCE,Bad(),enabled=True,now=NOW)
  self.assertEqual(r['status'],'ACCOUNT_READ_BLOCKED');self.assertNotIn('SECRET',str(r))
 def test_live_rejected(self):
  with self.assertRaises(ValueError):inspect(SOURCE,now=NOW,env={'LIVE_ENABLED':'true'})
 def test_duplicate_rejected(self):
  with self.assertRaises(ValueError):inspect(dict(SOURCE,intents=SOURCE['intents']*2),now=NOW)
 def test_never_order(self):
  r=inspect(SOURCE,Provider(),enabled=True,now=NOW)
  self.assertEqual((r['gate'],r['orders_sent'],r['broker_order_api_calls']),('DENY',0,0))
