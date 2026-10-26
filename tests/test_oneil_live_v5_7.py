import json, os, unittest
from pathlib import Path
from unittest.mock import patch
from oneil_live_v5_7 import validate
D=json.loads((Path(__file__).resolve().parents[1]/'v5_7_synthetic.json').read_text())
class TestV57(unittest.TestCase):
 def test_budget(self):
  r=validate(D);self.assertEqual(r['synthetic_budget_krw'],600000);self.assertEqual(r['per_stock_cap_krw'],180000);self.assertEqual(r['orders_sent'],0)
 def test_safety(self):
  r=validate(D);self.assertEqual(r['gate'],'DENY');self.assertIn('DAILY_PNL_VERIFIED_MISSING',r['block_codes'])
 def test_duplicate(self):
  d={**D,'candidates':[D['candidates'][0]]*2};self.assertIn('DUPLICATE_OR_BAD_TICKER',validate(d)['block_codes'])
 def test_live_guard(self):
  with patch.dict(os.environ,{'LIVE_ENABLED':'true'}):
   with self.assertRaises(ValueError):validate(D)
 def test_evening(self):
  d={**D,'observed_at_kst':'2026-10-27T18:00:00+09:00'};r=validate(d);self.assertEqual(r['session'],'EVENING_REVIEW');self.assertIn('EXCHANGE_TRADEABILITY_UNVERIFIED',r['block_codes'])
 def test_closed(self):
  d={**D,'observed_at_kst':'2026-10-27T20:10:00+09:00'};self.assertIn('MARKET_CLOSED',validate(d)['block_codes'])
 def test_ratio(self):
  r=validate(D,50,20,20);self.assertEqual(r['synthetic_budget_krw'],500000);self.assertEqual(r['per_stock_cap_krw'],100000)
if __name__=='__main__':unittest.main()
