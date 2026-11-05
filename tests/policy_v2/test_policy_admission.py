"""Offline worker-policy integration. ScriptedStore is NOT PostgreSQL proof."""
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'execution_v2'))
from test_worker import ScriptedStore
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import Blocked, Order, require
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import (PolicyContext, PolicyAdmission, SimulationPolicySource,
                                      history_digest)
from oneil_policy_v2.planner import plan_automatic, plan_manual_add
from policy_support import (NOW, ACCOUNT, policy, account, controls, candidates, market,
                             sessions, position, manual, evidence)


class PolicyStore(ScriptedStore):
    def __init__(self, p):
        super().__init__()
        self.clock = NOW
        self.policy = p.core_risk
        self.control_row.update(policy=asdict(self.policy), revision=7)
        self.before_admission = None

    def admission_ledger(self, account, day):
        return {'orders': tuple(Order.from_payload(r['payload']) for r in self.rows
                               if r['attempts'] and r.get('trading_day') == day)}

    def reserve_attempt(self, row, day, policy, validate):
        if self.before_admission:
            self.before_admission()
        require(not self.control_row['halted'], 'HALTED')
        require(self.control_row['policy'] == asdict(policy), 'POLICY_CHANGED')
        require(row['control_revision'] == self.control_row['revision'], 'CONTROL_CHANGED')
        daily = sum(o.notional for o in self.admission_ledger(row['account'], day)['orders'] if o.side == 'BUY')
        record = validate(self.now(), daily)
        row.update(state='SUBMISSION_UNKNOWN', attempts=1, attempt_id=uuid.uuid4(),
                   trading_day=day, admission_evidence=record)
        self.events.append('ATTEMPT_COMMITTED')
        return row


class PolicyAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.p = policy()
        self.configure()

    def configure(self, positions=(), *, p=None, markets=None):
        self.p = p or self.p
        self.a = account(positions, equity_krw=1_000_000 + sum(x.market_value_krw for x in positions))
        self.store = PolicyStore(self.p)
        self.broker = SimulatedBroker(self.store.now, cash_krw=self.a.orderable_cash_krw,
            positions={x.ticker: {'quantity': x.quantity, 'market_value_krw': x.market_value_krw} for x in positions})
        self.source = SimulationPolicySource()
        self.context = PolicyContext(self.p, self.a, controls(self.p), 7,
            history_digest(self.broker.history(ACCOUNT)), {}, candidates(),
            markets or {('005930', 'BUY'): market()}, sessions(), {})
        self.worker = Worker(self.store, self.broker, policy_source=self.source)

    def queue(self, *, mode='AUTOMATIC', manual_request=None):
        c = self.context
        if mode == 'AUTOMATIC':
            proposal = plan_automatic(c.policy, c.account, c.controls, c.candidates,
                c.markets, c.sessions, now=NOW, technical=c.technical)[0]
        else:
            proposal = plan_manual_add(c.policy, c.account, c.controls, manual_request,
                c.markets, c.sessions, now=NOW, technical=c.technical)
        order = proposal.order
        row = self.store.enqueue(**{**order.__dict__})
        row.update(control_revision=7, idempotency_key='policy-manual-' + manual_request.request_id
            if manual_request else 'automatic-' + str(row['request_id']), digest=order.digest)
        self.admission = PolicyAdmission(proposal, c, mode, manual_request, c)
        self.source.register(row['request_id'], self.admission)
        self.broker.install_policy_context(c)
        self.row = row
        return row

    def changed(self, **changes):
        self.context = replace(self.context, **changes)
        self.source.register(self.row['request_id'], replace(self.admission, context=self.context))

    def blocked(self, reason):
        self.assertEqual(self.worker.tick(ACCOUNT), 'BLOCKED')
        self.assertEqual(self.row['reason'], reason)
        self.assertEqual(self.broker.calls, [])
        self.assertEqual(self.row['attempts'], 0)

    def test_exact_policy_admitted_and_full_evidence_audited(self):
        self.queue()
        self.broker.submit_hook = lambda: self.assertIn('admission_evidence', self.row)
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')
        audit = self.row['admission_evidence']
        saved = audit['admission']['context']['policy']
        self.assertEqual((saved['reserve_pct'], saved['per_stock_pct'], saved['stop_loss_pct'], saved['take_profit_pct']), (37, 28, 6, 12))
        self.assertEqual(audit['order_digest'], self.admission.proposal.order.digest)
        self.assertEqual(audit['mode'], 'SIMULATION')
        self.assertFalse(audit['live_capable'])
        self.assertEqual(self.broker.specs[str(self.row['request_id'])][1].order_code, '00')
        self.assertEqual(self.row['filled_quantity'], 0)

    def test_default_worker_blocks_without_source(self):
        self.queue(); self.worker = Worker(self.store, self.broker)
        self.blocked('POLICY_ADMISSION_REQUIRED')

    def test_default_worker_blocks_generic_order_bypass(self):
        plain = ScriptedStore(); row = plain.enqueue()
        self.assertEqual(Worker(plain, SimulatedBroker(plain.now)).tick(plain.account), 'BLOCKED')
        self.assertEqual(row['reason'], 'POLICY_ADMISSION_REQUIRED')

    def test_fixture_worker_cannot_accept_policy_namespace(self):
        self.queue(); self.worker = Worker(self.store, self.broker, core_fixture_only=True)
        self.blocked('POLICY_ADMISSION_REQUIRED')

    def test_restart_missing_intent_blocks(self):
        self.queue(); self.worker = Worker(self.store, self.broker, policy_source=SimulationPolicySource())
        self.blocked('POLICY_ADMISSION_REQUIRED')

    def test_uninstalled_simulator_evidence_does_not_use_clock_fallback(self):
        self.queue(); self.broker._policy_contexts.clear()
        self.blocked('SIMULATOR_POLICY_EVIDENCE_REQUIRED')

    def test_exact_order_cannot_shrink(self):
        self.queue(); self.row['payload']['quantity'] -= 1
        self.blocked('POLICY_ORDER_MISMATCH')

    def test_whole_account_external_position_cannot_be_omitted(self):
        self.queue(); self.broker._balance(ACCOUNT)['positions']['000660'] = {'quantity': 5, 'market_value_krw': 5000}
        self.blocked('POLICY_POSITION_MISMATCH')

    def test_missing_managed_provenance_blocks(self):
        pos = position(); self.configure((pos,), markets={('005930', 'SELL'): market(side='SELL', price=1200)})
        self.queue(); bad = replace(pos, managed_reference='')
        self.changed(account=replace(self.a, positions=(bad,)))
        self.blocked('EVIDENCE_REFERENCE_REQUIRED')

    def test_managed_sell_quantity_enforced(self):
        pos = position(managed_quantity=3, sellable_quantity=8)
        self.configure((pos,), markets={('005930', 'SELL'): market(side='SELL', price=1200)})
        self.queue(); self.assertEqual(self.admission.proposal.order.quantity, 3)
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')

    def test_changed_sellable_quantity_rejects_original(self):
        pos = position(); self.configure((pos,), markets={('005930', 'SELL'): market(side='SELL', price=1200)})
        self.queue(); self.changed(account=replace(self.a, positions=(replace(pos, sellable_quantity=4),)))
        self.blocked('POLICY_PROPOSAL_CHANGED')

    def test_missing_calendar_blocks(self):
        self.queue(); self.changed(sessions={})
        self.blocked('SESSION_EVIDENCE_REQUIRED')

    def test_holiday_blocks_even_when_clock_open(self):
        self.queue(); self.changed(sessions=sessions(business_day=False))
        self.blocked('HOLIDAY_OR_UNVERIFIED_CALENDAR')

    def test_calendar_capability_and_original_permit_must_match(self):
        self.queue(); self.changed(sessions=sessions(order_code='01'))
        self.blocked('POLICY_SESSION_MISMATCH')

    def test_candidate_change_rejects_stale_proposal(self):
        from oneil_policy_v2.evidence import Candidate
        self.queue(); self.changed(candidates=candidates(Candidate('005930', 89)))
        self.blocked('POLICY_NO_ELIGIBLE_PROPOSAL')

    def test_control_halt_at_final_boundary_blocks(self):
        self.queue()
        self.store.before_admission = lambda: self.changed(controls=replace(self.context.controls, halted=True))
        self.blocked('HALTED_OR_UNKNOWN')

    def test_control_revision_change_at_final_boundary_blocks(self):
        self.queue(); self.store.before_admission = lambda: self.store.control_row.update(revision=8)
        self.blocked('CONTROL_CHANGED')

    def test_policy_changes_without_new_order_are_blocked(self):
        self.queue(); changed_policy = replace(self.p, reserve_pct=99)
        self.changed(policy=changed_policy, controls=controls(changed_policy))
        self.blocked('POLICY_CHANGED')

    def test_evidence_expires_during_final_admission(self):
        self.queue(); self.store.before_admission = lambda: setattr(self.store, 'clock', NOW + timedelta(seconds=6))
        # Original order expires with its shortest quote evidence, even if newer
        # evidence were later installed. Owner must confirm another exact order.
        self.assertEqual(self.worker.tick(ACCOUNT), 'EXPIRED')
        self.assertEqual(self.row['reason'], 'REQUEST_EXPIRED'); self.assertEqual(self.broker.calls, [])

    def test_history_digest_mismatch_blocks(self):
        self.queue(); self.changed(broker_history_digest='0' * 64)
        self.blocked('POLICY_HISTORY_MISMATCH')

    def test_reflected_history_reference_required(self):
        self.queue(); self.changed(account=replace(self.a, reflected_history_reference='other'))
        self.blocked('ACCOUNT_NOT_CAUSALLY_RECONCILED')

    def test_missing_fill_watermarks_blocks(self):
        self.queue(); self.broker._balance(ACCOUNT)['fills']['earlier'] = 4
        self.blocked('ACCOUNT_NOT_CAUSALLY_RECONCILED')

    def test_duplicate_same_day_buy_from_durable_ledger_blocks(self):
        self.queue()
        committed = Order(ACCOUNT, '005930', 'BUY', 'KRX', 2, 1000, NOW + timedelta(seconds=5), self.p.version)
        with patch.object(self.store, 'admission_ledger', return_value={'orders': (committed,)}):
            self.blocked('POLICY_NO_ELIGIBLE_PROPOSAL')

    def test_fee_buffer_rechecked_against_durable_daily_commitments(self):
        self.configure(p=policy(risk=replace(self.p.risk, max_daily_buy_krw=200_000)))
        self.queue()
        committed = Order(ACCOUNT, '000660', 'BUY', 'KRX', 25, 1000, NOW + timedelta(seconds=5), self.p.version)
        with patch.object(self.store, 'admission_ledger', return_value={'orders': (committed,)}):
            self.blocked('POLICY_PROPOSAL_CHANGED')

    def test_broker_cash_must_cover_enriched_evidence(self):
        self.queue(); self.broker._balance(ACCOUNT)['cash'] -= 1
        self.blocked('POLICY_ACCOUNT_MISMATCH')

    def test_unresolved_enriched_history_blocks(self):
        self.queue(); self.changed(account=replace(self.a, unresolved_orders=('external',)))
        self.blocked('UNRESOLVED_ORDERS')

    def test_quote_from_simulator_must_match_policy_evidence(self):
        self.queue(); altered = market(price=1001)
        self.broker.install_policy_context(replace(self.context, markets={('005930', 'BUY'): altered}))
        self.blocked('POLICY_QUOTE_MISMATCH')

    def test_manual_add_preserves_exact_quantity_and_idempotency(self):
        pos = position()
        self.configure((pos,), markets={('005930', 'BUY'): market(), ('005930', 'SELL'): market(side='SELL')})
        request = manual(self.p)
        self.queue(mode='MANUAL_ADD', manual_request=request)
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')
        self.assertEqual(self.admission.proposal.order.quantity, request.quantity)

    def test_manual_add_alternate_idempotency_key_rejected(self):
        self.configure((position(),), markets={('005930', 'BUY'): market(), ('005930', 'SELL'): market(side='SELL')})
        self.queue(mode='MANUAL_ADD', manual_request=manual(self.p)); self.row['idempotency_key'] = 'another-valid-key'
        self.blocked('MANUAL_IDEMPOTENCY_REQUIRED')

    def test_source_copies_nested_evidence(self):
        self.queue(); self.context.markets.clear()
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')

    def test_source_cannot_rewrite_intent(self):
        self.queue()
        with self.assertRaisesRegex(Blocked, 'POLICY_INTENT_IMMUTABLE'):
            self.source.register(self.row['request_id'], replace(self.admission, mode='MANUAL_ADD'))

    def test_ambiguous_submission_still_not_retried(self):
        self.queue(); self.broker.behavior = 'timeout_after_accept'
        self.assertEqual(self.worker.tick(ACCOUNT), 'SUBMISSION_UNKNOWN')
        self.assertEqual(self.worker.tick(ACCOUNT), 'UNRESOLVED_ORDER')
        self.assertEqual(len(self.broker.calls), 1)

    def test_full_sell_removes_zero_holding_in_simulator(self):
        pos = position()
        self.configure((pos,), markets={('005930', 'SELL'): market(side='SELL', price=1200)})
        self.queue(); self.broker.behavior = 'fill'
        self.assertEqual(self.worker.tick(ACCOUNT), 'FILLED')
        self.assertNotIn('005930', self.broker.account(ACCOUNT).positions)


if __name__ == '__main__': unittest.main()
