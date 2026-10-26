import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_v5_0 import budget,session,reconcile,report

class TestV5(unittest.TestCase):
 def test_budget(self):
  b=budget(1000000)
  self.assertEqual((b['reserve_krw'],b['trading_budget_krw'],b['stock_cap_krw'],b['evening_buy_cap_krw']),(400000,600000,180000,180000))
 def test_reject_missing_evidence(self):
  self.assertFalse(reconcile({'source':'SYNTHETIC_EXAMPLE'})['daily_pnl_verified'])
 def test_reconcile_verified(self):
  d={'source':'BROKER_VERIFIED','baseline_captured_before_open':True,'all_trades_reconciled':True,'cashflows_reconciled':True,'all_positions_valued':True,'evidence':{'opening_equity':1000000,'closing_equity':984000,'net_external_cashflow':0,'fees_and_taxes':100}}
  self.assertEqual(reconcile(d)['daily_pnl_krw'],-16000)
 def test_evening_ineligible(self):
  now=dt.datetime(2026,10,26,17,tzinfo=ZoneInfo('Asia/Seoul'))
  self.assertEqual(session(now,'KRX'),'BLOCKED_UNVERIFIED_VENUE')
 def test_evening_eligible(self):
  now=dt.datetime(2026,10,26,17,tzinfo=ZoneInfo('Asia/Seoul'))
  self.assertEqual(session(now,'KRX',True,True),'EVENING_PREVIEW')
 def test_after_20(self):
  now=dt.datetime(2026,10,26,20,1,tzinfo=ZoneInfo('Asia/Seoul'))
  self.assertEqual(session(now,'NXT',True,True),'BLOCKED_MARKET_CLOSED')
 def test_preview_gate(self):
  x=report({'source':'SYNTHETIC_EXAMPLE','sample_cash_krw':1000000,'candidates':[]},dt.datetime(2026,10,26,21,tzinfo=ZoneInfo('Asia/Seoul')))
  self.assertEqual(x['gate'],'DENY')
if __name__=='__main__':unittest.main()
