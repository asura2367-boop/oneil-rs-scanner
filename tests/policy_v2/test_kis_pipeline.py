"""Synthetic offline integration tests. These are NOT authenticated KIS recordings.

The only positive execution fixture remains the independent SimulatedBroker
fixture. KIS transcripts, even perfectly shaped, never enable a worker attempt.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'execution_v2'))
from kis_evidence_fixtures import (
    BINDING, DAY, NOW, POSITION, TOTALS, balance_read, cases, read,
)
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import HistoryQuery, KisResponse, prepare_history_query
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import PolicyAdmission, PolicyContext, SimulationPolicySource, history_digest
from oneil_policy_v2.kis_pipeline import (
    KisOfflineInputSet, POLICY_BLOCK, inspect_kis_inputs,
)
from oneil_policy_v2.planner import plan_automatic
from policy_support import ACCOUNT, account, candidates, controls, market, policy, sessions
from test_policy_admission import PolicyStore


def supplied_inputs():
    fixture = {case.name: case.reads for case in cases()}
    history = read(prepare_history_query(HistoryQuery(BINDING, DAY)), {'output1': []}, marker='D')
    return KisOfflineInputSet(BINDING, DAY, '005930', 'KRX', None,
        fixture['balance'], fixture['buying_power'][0], fixture['sellable'][0],
        fixture['price'][0], fixture['session'][0], fixture['holidays'], (history,))


def inspect(inputs=None, **kwargs):
    return inspect_kis_inputs(inputs or supplied_inputs(),
                              **{'now': NOW, 'max_age_seconds': 10, **kwargs})


def step(report, endpoint):
    return next(s for s in report.steps if s.endpoint == endpoint)


class KisPipelineTests(unittest.TestCase):
    def test_all_seven_parsed_streams_still_refuse_policy_context(self):
        report = inspect()
        self.assertTrue(report.shape_complete)
        self.assertEqual(report.inconsistencies, ())
        self.assertFalse(report.policy_ready)
        self.assertFalse(report.live_capable)
        self.assertEqual(len(report.steps), 7)
        for item in report.steps:
            self.assertFalse(item.outcome.value.provenance_authenticated)
            self.assertFalse(item.outcome.value.live_eligible)
        with self.assertRaisesRegex(Blocked, POLICY_BLOCK):
            report.policy_context()

    def test_all_independent_unknowns_remain_explicit(self):
        report = inspect()
        for fragment in ('PROVENANCE', 'CAUSALITY', 'REQUEST_IDENTITY', 'FILLS',
                         'DAILY_REALIZED', 'COMMITMENTS', 'MANAGED', 'SELLABLE',
                         'BUYING_POWER', 'QUOTE', 'CALENDAR', 'SESSION', 'ELIGIBILITY', 'CONTROLS'):
            self.assertTrue(any(fragment in fact for fact in report.missing_facts), fragment)

    def test_missing_inputs_do_not_become_zero_or_empty_history(self):
        report = inspect(KisOfflineInputSet(BINDING, DAY, '005930', 'KRX', None))
        self.assertFalse(report.shape_complete)
        for item in report.steps:
            self.assertFalse(item.parsed)
            self.assertIsNone(item.outcome.value)

    def test_wrong_input_types_rejected(self):
        for inputs in ({}, None, True, replace(supplied_inputs(), binding={})):
            with self.subTest(inputs=type(inputs)), self.assertRaisesRegex(Blocked, 'KIS_PIPELINE_INPUT_REQUIRED'):
                inspect_kis_inputs(inputs, now=NOW, max_age_seconds=10)

    def test_inquiry_price_cannot_be_invented_for_mode_01(self):
        parsed = step(inspect(), 'buying_power').outcome.value
        self.assertIsNone(parsed.inquiry_price_krw)
        report = inspect(replace(supplied_inputs(), inquiry_price_krw=10001))
        self.assertFalse(step(report, 'buying_power').parsed)
        self.assertEqual(step(report, 'buying_power').outcome.reason, 'KIS_MARKET_INQUIRY_PRICE_MUST_BE_ABSENT')

    def test_mixed_account_rejects_account_endpoints(self):
        inputs = replace(supplied_inputs(), binding=replace(BINDING, cano='99999999'))
        report = inspect(inputs)
        for endpoint in ('balance', 'buying_power', 'sellable', 'history'):
            self.assertFalse(step(report, endpoint).parsed, endpoint)

    def test_mixed_environment_rejects_account_recipes(self):
        report = inspect(replace(supplied_inputs(), binding=replace(BINDING, environment='demo')))
        for endpoint in ('balance', 'buying_power', 'sellable', 'history'):
            self.assertFalse(step(report, endpoint).parsed, endpoint)

    def test_no_nxt_to_krx_fallback(self):
        report = inspect(replace(supplied_inputs(), venue='NXT'))
        for endpoint in ('price', 'session', 'history'):
            self.assertFalse(step(report, endpoint).parsed, endpoint)

    def test_wrong_ticker_rejects_symbol_endpoints(self):
        report = inspect(replace(supplied_inputs(), ticker='000660'))
        for endpoint in ('buying_power', 'sellable', 'price', 'session'):
            self.assertFalse(step(report, endpoint).parsed, endpoint)

    def test_future_stale_and_naive_capture_refused(self):
        inputs = supplied_inputs()
        for stamp in (NOW+timedelta(seconds=1), NOW-timedelta(seconds=11), NOW.replace(tzinfo=None)):
            for attr in ('balance_reads', 'history_reads'):
                bad = tuple(replace(r, started_at=stamp) for r in getattr(inputs, attr))
                result = inspect(replace(inputs, **{attr: bad}))
                self.assertFalse(step(result, attr.removesuffix('_reads')).parsed)

    def test_partial_history_is_discarded(self):
        inputs = supplied_inputs()
        first = inputs.history_reads[0]
        body = {'rt_cd': '0', 'output1': [], 'ctx_area_fk100': 'a', 'ctx_area_nk100': 'b'}
        first = replace(first, response=KisResponse(200, body, {'tr_cont': 'M'}))
        report = inspect(replace(inputs, history_reads=(first,)))
        self.assertIsNone(step(report, 'history').outcome.value)

    def test_complete_multipage_history_stays_unauthenticated(self):
        inputs = supplied_inputs()
        query = HistoryQuery(BINDING, DAY)
        first = read(prepare_history_query(query), {
            'output1': [], 'ctx_area_fk100': 'a ', 'ctx_area_nk100': ' b'}, marker='M',
            started_at=NOW-timedelta(seconds=4), received_at=NOW-timedelta(seconds=3))
        second = read(prepare_history_query(query, cursor=('a ', ' b')), {'output1': []}, marker='D')
        report = inspect(replace(inputs, history_reads=(first, second)))
        history = step(report, 'history').outcome.value
        self.assertTrue(report.shape_complete)
        self.assertTrue(history.history.complete)
        self.assertEqual(history.window.started_at, first.started_at)
        self.assertFalse(history.provenance_authenticated)
        self.assertFalse(report.policy_ready)

    def test_no_asserted_verified_override_is_accepted(self):
        with self.assertRaises(TypeError):
            inspect_kis_inputs(supplied_inputs(), now=NOW, max_age_seconds=10, verified=True)
        with self.assertRaises(TypeError):
            replace(inspect(), policy_ready=True)

    def test_extra_history_after_terminal_rejected(self):
        inputs = supplied_inputs()
        first = replace(inputs.history_reads[0], started_at=NOW-timedelta(seconds=4),
                        received_at=NOW-timedelta(seconds=3))
        report = inspect(replace(inputs, history_reads=(first, inputs.history_reads[0])))
        self.assertFalse(step(report, 'history').parsed)

    def test_history_recipe_content_is_not_ignored(self):
        inputs = supplied_inputs()
        original = inputs.history_reads[0]
        for recipe in (replace(original.recipe, method='POST'), replace(original.recipe, path='/other'),
                       replace(original.recipe, parameters=original.recipe.parameters+(('EXTRA', 'Y'),))):
            with self.subTest(recipe=recipe):
                report = inspect(replace(inputs, history_reads=(replace(original, recipe=recipe),)))
                self.assertFalse(step(report, 'history').parsed)

    def test_replayed_capture_order_is_rejected(self):
        inputs = supplied_inputs()
        one = inputs.history_reads[0]
        report = inspect(replace(inputs, history_reads=(one, one)))
        self.assertFalse(step(report, 'history').parsed)

    def test_history_bounds_apply_without_transport(self):
        inputs = supplied_inputs()
        report = inspect(replace(inputs, history_reads=inputs.history_reads * 2), max_pages=1)
        self.assertFalse(step(report, 'history').parsed)

    def test_holdings_disagreement_is_reported_not_repaired(self):
        inputs = supplied_inputs()
        body = deepcopy(inputs.sellable_read.response.body)
        body['output1']['cblc_qty'] = '11'
        changed = replace(inputs.sellable_read, response=replace(inputs.sellable_read.response, body=body))
        report = inspect(replace(inputs, sellable_read=changed))
        self.assertTrue(report.shape_complete)
        self.assertIn('KIS_CROSS_ENDPOINT_HOLDING_MISMATCH', report.inconsistencies)
        self.assertEqual(step(report, 'balance').outcome.value.positions[0].held_quantity, 10)

    def test_balance_totals_disagreement_is_reported(self):
        report = inspect(replace(supplied_inputs(), balance_reads=(balance_read(totals=[{
            **TOTALS, 'pchs_amt_smtl_amt': '100001', 'evlu_amt_smtl_amt': '101001'}]),)))
        self.assertEqual(report.inconsistencies, ('KIS_REPORTED_PURCHASE_TOTAL_MISMATCH',
                                                  'KIS_REPORTED_VALUATION_TOTAL_MISMATCH'))

    def test_reported_holiday_never_becomes_session_permission(self):
        inputs = supplied_inputs()
        raw = deepcopy(inputs.holiday_reads[0].response.body)
        raw['output']['opnd_yn'] = 'N'
        changed = replace(inputs.holiday_reads[0], response=replace(inputs.holiday_reads[0].response, body=raw))
        report = inspect(replace(inputs, holiday_reads=(changed,)))
        self.assertIn('KIS_CALENDAR_REPORTED_CLOSED', report.inconsistencies)
        self.assertFalse(report.policy_ready)

    def test_reported_stop_and_zero_price_flagged(self):
        inputs = supplied_inputs()
        raw = deepcopy(inputs.price_read.response.body)
        raw['output'].update(temp_stop_yn='Y', stck_prpr='0')
        changed = replace(inputs.price_read, response=replace(inputs.price_read.response, body=raw))
        report = inspect(replace(inputs, price_read=changed))
        self.assertIn('KIS_REPORTED_PRICE_NONPOSITIVE', report.inconsistencies)
        self.assertIn('KIS_REPORTED_INSTRUMENT_STOPPED', report.inconsistencies)

    def test_valuation_pnl_does_not_become_daily_loss(self):
        inputs = replace(supplied_inputs(), balance_reads=(balance_read(totals=[{
            **TOTALS, 'evlu_pfls_smtl_amt': '-12345'}]),))
        report = inspect(inputs)
        self.assertIn('WHOLE_ACCOUNT_DAILY_REALIZED_AND_UNREALIZED_LOSS', report.missing_facts)
        self.assertFalse(hasattr(report, 'daily_loss_krw'))

    def test_no_socket_or_external_transport_is_used(self):
        with patch('socket.socket', side_effect=AssertionError('socket forbidden')):
            self.assertTrue(inspect().shape_complete)

    def test_report_does_not_include_raw_account_payload_or_economics(self):
        inputs = supplied_inputs()
        summary = json.dumps(inspect(inputs).summary())
        for private in ('00000000', '700000', '900000', '1001000', 'CANO', 'ACNT_PRDT_CD'):
            self.assertNotIn(private, summary)
            self.assertNotIn(private, repr(inputs))

    def test_hostile_payload_error_is_sanitized(self):
        class Poison(dict):
            def items(self):
                raise Blocked('private-account-secret')
        inputs = supplied_inputs()
        history = replace(inputs.history_reads[0], response=KisResponse(200, Poison()))
        report = inspect(replace(inputs, history_reads=(history,)))
        self.assertFalse(step(report, 'history').parsed)
        self.assertNotIn('private-account-secret', json.dumps(report.summary()))


class KisWorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        p = policy(); a = account(equity_krw=1_000_000)
        self.store = PolicyStore(p)
        self.broker = SimulatedBroker(self.store.now, cash_krw=1_000_000)
        c = PolicyContext(p, a, controls(p), 7, history_digest(self.broker.history(ACCOUNT)), {},
                          candidates(), {('005930', 'BUY'): market()}, sessions(), {})
        proposal = plan_automatic(p, a, c.controls, c.candidates, c.markets, c.sessions,
                                  now=NOW, technical={})[0]
        self.row = self.store.enqueue(**proposal.order.__dict__)
        self.row.update(control_revision=7, idempotency_key='automatic-fixture', digest=proposal.order.digest)
        self.admission = PolicyAdmission(proposal, c, 'AUTOMATIC', None, c)
        self.source = SimulationPolicySource()
        self.broker.install_policy_context(c)
        self.worker = Worker(self.store, self.broker, policy_source=self.source)

    def inspect_for_request(self, inputs=None, **kwargs):
        return self.source.register_kis_offline(self.row['request_id'],
            supplied_inputs() if inputs is None else inputs, now=NOW, max_age_seconds=10, **kwargs)

    def blocked(self):
        self.assertEqual(self.worker.tick(ACCOUNT), 'BLOCKED')
        self.assertEqual(self.row['reason'], POLICY_BLOCK)
        self.assertEqual(self.row['attempts'], 0)
        self.assertEqual(self.broker.calls, [])
        self.assertNotIn('ATTEMPT_COMMITTED', self.store.events)

    def test_parsed_kis_data_reaches_worker_as_block_not_policy_permission(self):
        self.assertTrue(self.inspect_for_request().shape_complete)
        self.blocked()

    def test_failed_refresh_invalidates_prior_synthetic_registration(self):
        self.source.register(self.row['request_id'], self.admission)
        self.inspect_for_request(replace(supplied_inputs(), balance_reads=()))
        self.blocked()

    def test_malformed_refresh_cannot_leave_prior_registration_executable(self):
        self.source.register(self.row['request_id'], self.admission)
        with self.assertRaises(Blocked):
            self.source.register_kis_offline(self.row['request_id'], {}, now=NOW, max_age_seconds=10)
        self.blocked()

    def test_fixture_reregistration_cannot_clear_kis_failure(self):
        self.inspect_for_request()
        with self.assertRaisesRegex(Blocked, POLICY_BLOCK):
            self.source.register(self.row['request_id'], self.admission)
        self.blocked()

    def test_final_atomic_gate_rechecks_source_after_initial_read(self):
        self.source.register(self.row['request_id'], self.admission)
        self.store.before_admission = lambda: self.inspect_for_request()
        self.blocked()

    def test_block_is_request_scoped(self):
        self.source.register(self.row['request_id'], self.admission)
        self.source.register_kis_offline('other-request', supplied_inputs(), now=NOW, max_age_seconds=10)
        # This is independently initialized simulation evidence, NOT KIS data.
        self.assertEqual(self.worker.tick(ACCOUNT), 'ACKNOWLEDGED')
        self.assertEqual(len(self.broker.calls), 1)

    def test_invalid_request_key_does_not_mutate_other_fixture(self):
        self.source.register(self.row['request_id'], self.admission)
        with self.assertRaisesRegex(Blocked, 'POLICY_REQUEST_ID_INVALID'):
            self.source.register_kis_offline('', supplied_inputs(), now=NOW, max_age_seconds=10)
        self.assertEqual(self.source.read(self.row['request_id']), self.admission)

    def test_report_is_not_accepted_as_admission_or_broker(self):
        report = self.inspect_for_request()
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            SimulationPolicySource().register('request', report)
        with self.assertRaisesRegex(Blocked, 'LIVE_BROKER_NOT_IMPLEMENTED'):
            Worker(self.store, report, policy_source=self.source)

    def test_repeated_inspection_never_attempts_or_creates_history(self):
        for _ in range(3):
            self.inspect_for_request()
        self.blocked()
        self.assertEqual(self.worker.tick(ACCOUNT), 'IDLE')
        self.assertEqual(self.broker.history(ACCOUNT).orders, ())
