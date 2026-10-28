import unittest,inspect
import oneil_v519_live_runner as r
class CashBudgetTests(unittest.TestCase):
 def test_actual_cash_5m(self):
  self.assertEqual(r.calculate_cash_budget(5000000,5000000,999,10000,existing_exposure_krw=0,pending_orders_verified=True),90)
 def test_not_fixed_one_million(self):
  self.assertEqual(r.calculate_cash_budget(2000000,2000000,999,10000,existing_exposure_krw=0,pending_orders_verified=True),36)
 def test_portfolio_exposure_reduces(self):
  self.assertEqual(r.calculate_cash_budget(5000000,5000000,999,10000,existing_exposure_krw=2800000,pending_orders_verified=True),20)
 def test_full_exposure_blocks(self):
  self.assertEqual(r.calculate_cash_budget(5000000,5000000,999,10000,existing_exposure_krw=3100000,pending_orders_verified=True),0)
 def test_unverified_pending_blocks(self):
  with self.assertRaises(r.Blocked):r.calculate_cash_budget(5000000,5000000,999,10000,existing_exposure_krw=0,pending_orders_verified=False)
 def test_orderable_cash_caps(self):
  self.assertEqual(r.calculate_cash_budget(5000000,40000,999,10000,existing_exposure_krw=0,pending_orders_verified=True),4)
 def test_execute_lock_preserved(self):
  self.assertIn('LIVE_EXECUTION_UNAVAILABLE_UNTIL_EVIDENCE_AND_RECOVERY_AUDITED',inspect.getsource(r.run))
 def test_unfilled_global_block(self):
  self.assertIn('UNFILLED_ORDERS_REQUIRE_COMMITMENT_RECONCILIATION',inspect.getsource(r.run))
