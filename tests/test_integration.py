import inspect, unittest
import oneil_v519_live_runner as runner
class T(unittest.TestCase):
 def test_live_is_blocked_before_network(self):
  text=inspect.getsource(runner.run)
  self.assertLess(text.index('LIVE_EXECUTION_UNAVAILABLE_UNTIL_EVIDENCE_AND_RECOVERY_AUDITED'), text.index('t=token(key,secret)'))
 def test_full_portfolio_read(self):
  code=inspect.getsource(runner.run)
  self.assertIn('verified_balance_snapshot',code)
  self.assertIn('unfilled_codes',code)
 def test_per_stock_cap(self):
  self.assertIn('PER_STOCK_BUDGET_EXCEEDED',inspect.getsource(runner.run))
 def test_recovery_preserved(self):
  self.assertTrue(callable(runner.recover_orders_readonly))
 def test_emergency_halt_preserved(self):
  self.assertTrue(callable(runner.emergency_halt))
