import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from oneil_v519_operations import AttestedCash,OperationalProof,assess,Blocked
NOW=dt.datetime(2026,10,28,18,0,tzinfo=ZoneInfo('Asia/Seoul'))
ALL=OperationalProof(*([True]*8))
class OperationsTests(unittest.TestCase):
 def cash(self,**kwargs):
  d=dict(available_cash_krw=5000000,orderable_krw=5000000,existing_commitments_krw=0,ticker_commitments_krw=0,complete=True,as_of=NOW)
  d.update(kwargs)
  return AttestedCash(**d)
 def verify(self,cash=None,proof=ALL,price=10000):
  return assess(cash if cash is not None else self.cash(),proof,now=NOW,price_krw=price,env={})
 def test_five_million_90_shares(self):self.assertEqual(self.verify()['proposed_qty'],90)
 def test_two_million_not_fixed(self):self.assertEqual(self.verify(self.cash(available_cash_krw=2000000,orderable_krw=2000000))['proposed_qty'],36)
 def test_commitment_deducted(self):self.assertEqual(self.verify(self.cash(ticker_commitments_krw=800000))['proposed_qty'],10)
 def test_broker_limit(self):self.assertEqual(self.verify(self.cash(orderable_krw=50000))['proposed_qty'],5)
 def test_stale_balance_blocks(self):self.assertIsNone(self.verify(self.cash(as_of=NOW-dt.timedelta(minutes=2)))['proposed_qty'])
 def test_missing_persistence_blocks(self):self.assertIsNone(self.verify(proof=OperationalProof(*([True]*5+[False]+[True]*2)))['proposed_qty'])
 def test_missing_history_blocks(self):self.assertIsNone(self.verify(proof=OperationalProof(*([True]*4+[False]+[True]*3)))['proposed_qty'])
 def test_price_missing_blocks(self):self.assertIsNone(self.verify(price=None)['proposed_qty'])
 def test_no_live_flag(self):
  with self.assertRaises(Blocked):assess(self.cash(),ALL,now=NOW,price_krw=10000,env={'LIVE_ENABLED':'true'})
 def test_hard_deny_even_with_proofs(self):
  r=self.verify();self.assertEqual((r['gate'],r['orders_sent']),('DENY',0))
 def test_unverified_cash_blocks(self):self.assertIsNone(self.verify(self.cash(complete=False))['proposed_qty'])
 def test_no_record_export_on_missing(self):
  r=assess(None,OperationalProof(),now=NOW,env={});self.assertNotIn('available_cash_krw',r)
