import unittest,datetime as dt
from zoneinfo import ZoneInfo
from oneil_v519_risk_audit import inspect
N=dt.datetime(2026,10,28,10,tzinfo=ZoneInfo('Asia/Seoul'))
C={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,'intents':[{'ticker':'085910'}]}
A={'gate':'DENY','orders_sent':0,'broker_status':'READ_ONLY_OBSERVED'}
Q={'version':'5.18','quote_price_approved_count':0,'quote_schema_fully_attested':False,'exchange_business_day_attested':False,'exchange_session_phase_attested':False}
R={'daily_loss_verified':False,'baseline_verified':False,'external_cashflows_reconciled':False,'orders_sent':0}
class Tests(unittest.TestCase):
 def test_baseline_denies(self):
  x=inspect(C,A,Q,R,N);self.assertEqual(x['gate'],'DENY');self.assertEqual(x['orders_sent'],0)
 def test_no_account_denies(self):self.assertIn('ACCOUNT_READ_NOT_VERIFIED',inspect(C,None,Q,R,N)['block_codes'])
 def test_quote_unattested_denies(self):self.assertIn('EXECUTABLE_QUOTE_NOT_ATTESTED',inspect(C,A,Q,R,N)['block_codes'])
 def test_loss_unverified_denies(self):self.assertIn('DAILY_LOSS_NOT_VERIFIED',inspect(C,A,Q,R,N)['block_codes'])
 def test_duplicate_invalid(self):
  c={**C,'intents':[{'ticker':'085910'},{'ticker':'085910'}]};self.assertIn('CANDIDATES_NOT_VALID',inspect(c,A,Q,R,N)['block_codes'])
 def test_invalid_ticker(self):
  c={**C,'intents':[{'ticker':'ABC'}]};self.assertEqual(inspect(c,A,Q,R,N)['candidate_count'],0)
 def test_no_broker_amounts_leak(self):
  a={**A,'orderable_cash_krw':987654321};self.assertNotIn('987654321',str(inspect(C,a,Q,R,N)))
 def test_apparent_verified_all_still_denies(self):
  q={**Q,'quote_price_approved_count':1,'quote_schema_fully_attested':True,'exchange_business_day_attested':True,'exchange_session_phase_attested':True}
  r={**R,'daily_loss_verified':True,'baseline_verified':True,'external_cashflows_reconciled':True}
  x=inspect(C,A,q,r,N);self.assertEqual(x['gate'],'DENY');self.assertIn('ORDER_SUBMISSION_DISABLED',x['block_codes'])
 def test_naive_clock_rejected(self):
  with self.assertRaises(ValueError):inspect(C,A,Q,R,dt.datetime(2026,10,28,10))
if __name__=='__main__':unittest.main()
