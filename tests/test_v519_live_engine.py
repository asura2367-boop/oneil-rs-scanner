import tempfile, pathlib, unittest
from oneil_v519_live_engine import *
class Tests(unittest.TestCase):
 def setUp(self):
  self.a=VerifiedAccount(5000000,5000000,0,0,False,False,True,True,True)
  self.p=CertifiedPrice("144960","KRX",10000,True,True,True,True)
 def test_cash_based_five_million(self):
  r=budget(self.a,self.p)
  self.assertEqual(r["trading_cap_krw"],3000000)
  self.assertEqual(r["per_stock_cap_krw"],900000)
  self.assertEqual(r["qty"],90)
 def test_cash_based_two_million(self):
  a=VerifiedAccount(2000000,2000000,0,0,False,False,True,True,True)
  self.assertEqual(budget(a,self.p)["qty"],36)
 def test_broker_cash_cap(self):
  a=VerifiedAccount(5000000,90000,0,0,False,False,True,True,True)
  self.assertEqual(budget(a,self.p)["qty"],9)
 def test_invested_exposure(self):
  a=VerifiedAccount(5000000,5000000,2900000,0,False,False,True,True,True)
  self.assertEqual(budget(a,self.p)["qty"],10)
 def test_existing_ticker(self):
  a=VerifiedAccount(5000000,5000000,0,0,True,False,True,True,True)
  with self.assertRaises(TradingBlocked): budget(a,self.p)
 def test_no_certified_quote(self):
  p=CertifiedPrice("144960","NXT",10000,True,True,False,True)
  with self.assertRaises(TradingBlocked): budget(self.a,p)
 def test_no_verified_cash(self):
  a=VerifiedAccount(5000000,5000000,0,0,False,False,True,True,False)
  with self.assertRaises(TradingBlocked): budget(a,self.p)
 def test_loss_halt(self):
  self.assertTrue(daily_loss(1000000,975000,0,baseline_authenticated=True,current_authenticated=True,cashflows_reconciled=True)["halt"])
 def test_deposit_not_profit(self):
  r=daily_loss(1000000,1090000,100000,baseline_authenticated=True,current_authenticated=True,cashflows_reconciled=True)
  self.assertEqual(r["pnl_krw"],-10000)
 def test_missing_pnl_provenance(self):
  with self.assertRaises(TradingBlocked):daily_loss(1000000,980000,0)
 def test_ledger_rejects_duplicate(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"orders.sqlite")
   x.reserve("same","144960","BUY","KRX",10)
   with self.assertRaises(TradingBlocked):x.reserve("same","144960","BUY","KRX",10)
   x.close()
 def test_unknown_submission_no_retry(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   x.reserve("one","144960","BUY","KRX",10)
   x.close()
   y=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   with self.assertRaises(TradingBlocked):y.reserve("one","144960","BUY","KRX",10)
   y.close()
 def test_partial_fill(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   x.reserve("one","144960","BUY","KRX",10)
   y=x.reconcile("one",broker_id="abc",status="PART_FILLED",filled=4)
   self.assertEqual(y["pending_qty"],6)
   self.assertFalse(y["auto_resubmit"])
   x.close()
 def test_overfill(self):
  with tempfile.TemporaryDirectory() as d:
   x=DurableOrderLedger(pathlib.Path(d)/"db.sqlite")
   x.reserve("one","144960","BUY","KRX",10)
   with self.assertRaises(TradingBlocked):x.reconcile("one",broker_id="abc",status="FILLED",filled=11)
   x.close()
 def test_order_adapter_map(self):
  a=KisCashOrderAdapter(None)
  r=a.prepare(side="BUY",ticker="144960",qty=2,limit_price_krw=10000,cano="12345678",product="01")
  self.assertEqual(r["tr_id"],"TTTC0012U")
 def test_order_submit_never_live(self):
  with self.assertRaises(TradingBlocked):KisCashOrderAdapter(None).submit()
 def test_gate_deny(self):
  self.assertEqual(readiness()["gate"],"DENY")
if __name__=="__main__":unittest.main()
