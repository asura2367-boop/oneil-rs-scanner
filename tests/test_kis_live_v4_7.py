import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from kis_live_v4_7 import assess, verify_snapshot, ValidationError

NOW = dt.datetime(2026,10,26,14,30,tzinfo=ZoneInfo('Asia/Seoul'))
def evidence(**patch):
    base=dict(schema_version=1,market_date='2026-10-26',scope='WHOLE_ACCOUNT',
              evidence_verified=True,baseline_time_kst='2026-10-26T08:59:00+09:00',
              current_time_kst='2026-10-26T14:29:00+09:00',baseline_equity_krw=1_000_000,
              current_equity_krw=985_000,net_external_cashflow_krw=0,
              cashflows_reconciled=True,holdings_reconciled=True,
              fees_included=True,source_complete=True)
    base.update(patch)
    return base
class Tests(unittest.TestCase):
    def test_missing(self): self.assertIn('DAILY_LOSS_NOT_VERIFIED',assess(None,NOW)['block_codes'])
    def test_realized_or_unrealized_not_claimed(self): self.assertEqual(verify_snapshot(evidence(),NOW),-15000)
    def test_limit(self): self.assertIn('DAILY_LOSS_LIMIT_REACHED',assess(evidence(current_equity_krw=980000),NOW)['block_codes'])
    def test_deposit_adjustment(self): self.assertEqual(verify_snapshot(evidence(current_equity_krw=1085000,net_external_cashflow_krw=100000),NOW),-15000)
    def test_unverified(self): self.assertFalse(assess(evidence(evidence_verified=False),NOW)['daily_loss_verified'])
    def test_stale(self): self.assertIn('STALE_EVIDENCE',assess(evidence(current_time_kst='2026-10-26T14:00:00+09:00'),NOW)['block_codes'])
    def test_missing_flow(self): self.assertFalse(assess(evidence(net_external_cashflow_krw=None),NOW)['daily_loss_verified'])
    def test_halt(self): self.assertIn('EMERGENCY_HALT_ACTIVE',assess(evidence(),NOW,True)['block_codes'])
    def test_never_approve(self): self.assertFalse(assess(evidence(),NOW)['approved'])
    def test_preopen(self): self.assertFalse(assess(evidence(baseline_time_kst='2026-10-26T09:05:00+09:00'),NOW)['daily_loss_verified'])
    def test_wrong_scope(self): self.assertFalse(assess(evidence(scope='STRATEGY_ONLY'),NOW)['daily_loss_verified'])
if __name__=='__main__': unittest.main()
