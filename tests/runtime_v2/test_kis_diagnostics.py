"""Network-denied real parser/poller/SQLite/WSGI composition, synthetic inputs."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import pickle
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'execution_v2'))
from kis_evidence_fixtures import BINDING, DAY, NOW, cases, read
from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import HistoryQuery, prepare_history_query
from oneil_execution_v2.kis_evidence import prepare_balance_query
from oneil_execution_v2.kis_polling import PollLimits
from oneil_runtime_v2.kis_diagnostics import KisDiagnosticRun
import test_simulation_host as host_fixture


class SuppliedSource:
    def __init__(self):
        fixtures = {c.name: c.reads for c in cases()}
        history = read(prepare_history_query(HistoryQuery(BINDING, DAY)), {'output1': []}, marker='D')
        self.reads = {'history': (history,), 'balance': fixtures['balance'],
            'buying_power': fixtures['buying_power'], 'sellable': fixtures['sellable'],
            'price': fixtures['price'], 'book': fixtures['session'], 'holiday': fixtures['holidays']}
        self.paths = {values[0].recipe.path: key for key, values in self.reads.items()}
        self.calls = []; self.closed = False; self.hook = None

    def get(self, recipe, **kwargs):
        stream = self.paths[recipe.path]; self.calls.append(stream)
        if self.hook: self.hook(stream)
        return self.reads[stream]

    def history(self, query, **kwargs):
        return self.get(prepare_history_query(query), **kwargs)

    def balance(self, query, **kwargs):
        return self.get(prepare_balance_query(query), **kwargs)

    def capture(self, recipe, **kwargs):
        return self.get(recipe, **kwargs)[0]

    def close(self):
        self.closed = True


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('NO_NETWORK'))
            guard.start(); self.addCleanup(guard.stop)
        self.source = SuppliedSource(); self.seconds = 0.; self.wall_offset = 0.
        guard = patch('oneil_execution_v2.kis_authenticated.Cancellation.wait',
                      lambda token, seconds: self.advance(seconds))
        guard.start(); self.addCleanup(guard.stop)

    def advance(self, seconds): self.seconds += seconds

    def runner(self, **kwargs):
        return KisDiagnosticRun(self.source, BINDING, DAY, '005930',
            utc_now=lambda: NOW+timedelta(seconds=self.seconds+self.wall_offset),
            monotonic=lambda: 100+self.seconds, **kwargs)

    def test_construct_and_observe_never_start_and_never_authorize(self):
        run = self.runner()
        self.assertEqual(run.snapshot()['state'], 'idle'); self.assertFalse(self.source.calls)
        for operation in (run.submit, run.policy_evidence):
            with self.assertRaises(Blocked): operation()
        with self.assertRaises(TypeError): pickle.dumps(run)
        self.assertNotIn(BINDING.cano, repr(run))

    def test_complete_cycle_reaches_real_parsers_and_erases_raw_buffers(self):
        run = self.runner(); status = run.run()
        self.assertEqual(status['state'], 'parsed'); self.assertTrue(status['current'])
        self.assertEqual(status['completed_cycles'], 1)
        self.assertTrue(status['report']['shape_complete']); self.assertEqual(len(status['report']['steps']), 7)
        self.assertFalse(status['policy_ready']); self.assertFalse(status['live_capable'])
        self.assertFalse(status['provenance_authenticated']); self.assertTrue(self.source.closed)
        self.assertEqual(run._reads, {}); self.assertIsNone(run._holiday)
        payload = json.dumps(status)
        for private in (BINDING.cano, BINDING.account, '005930', '900000', 'SYNTHETIC_UNKNOWN'):
            self.assertNotIn(private, payload)
        status['report']['steps'][0]['reason'] = 'MUTATED'
        self.assertNotIn('MUTATED', json.dumps(run.snapshot()))
        with self.assertRaises(Blocked): run.run()

    def test_malformed_body_is_incomplete_not_success_counter(self):
        original = self.source.reads['price'][0]
        self.source.reads['price'] = (replace(original, response=replace(original.response, body={})),)
        status = self.runner().run()
        self.assertEqual(status['state'], 'incomplete')
        self.assertFalse(status['report']['shape_complete'])
        self.assertFalse(next(x for x in status['report']['steps'] if x['endpoint'] == 'price')['parsed'])

    def test_holdings_disagreement_is_visible_not_repaired(self):
        original = self.source.reads['sellable'][0]; body = deepcopy(original.response.body)
        body['output1']['cblc_qty'] = '11'
        self.source.reads['sellable'] = (replace(original, response=replace(original.response, body=body)),)
        status = self.runner().run()
        self.assertEqual(status['state'], 'inconsistent')
        self.assertIn('KIS_CROSS_ENDPOINT_HOLDING_MISMATCH', status['report']['inconsistencies'])

    def test_partial_cycle_and_dependency_exception_never_preserve_report(self):
        run = self.runner(limits=PollLimits(cycles=2)); seen = []
        def hook(stream):
            if self.source.calls.count('history') == 2:
                seen.append(run.snapshot())
                raise RuntimeError('SYNTHETIC_PRIVATE_ERROR')
        self.source.hook = hook
        result = run.run()
        self.assertEqual(result['state'], 'failed'); self.assertIsNone(result['report'])
        self.assertEqual(seen[0]['state'], 'collecting'); self.assertIsNone(seen[0]['report'])
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR', json.dumps(result))

    def test_holiday_is_not_refetched_or_retimestamped(self):
        run = self.runner(limits=PollLimits(cycles=2), max_age_seconds=10)
        result = run.run()
        self.assertEqual(self.source.calls.count('holiday'), 1)
        self.assertEqual(result['completed_cycles'], 2)
        self.assertEqual(result['state'], 'stale'); self.assertIsNone(result['report'])

    def test_stale_by_monotonic_even_if_utc_is_frozen(self):
        run = self.runner(); self.assertTrue(run.run()['current'])
        self.seconds += 30; self.wall_offset -= 30
        result = run.snapshot()
        self.assertEqual(result['state'], 'stale'); self.assertFalse(result['current'])

    def test_stale_by_utc_even_if_monotonic_is_frozen(self):
        run = self.runner(); run.run(); self.wall_offset += 30
        self.assertEqual(run.snapshot()['state'], 'stale')

    def test_wall_and_monotonic_rollback_latch_closed(self):
        for kind in ('wall', 'mono'):
            self.source = SuppliedSource(); self.seconds = 0.; self.wall_offset = 0.
            run = self.runner(); run.run()
            if kind == 'wall': self.wall_offset -= 1
            else: self.seconds -= 1
            self.assertEqual(run.snapshot()['state'], 'clock_invalid')
            self.seconds += 5; self.wall_offset += 5
            self.assertEqual(run.snapshot()['state'], 'clock_invalid')

    def test_deadline_and_cleanup_failure_clear_report(self):
        run = self.runner(limits=PollLimits(duration_seconds=1))
        self.assertEqual(run.run()['state'], 'expired')
        self.source = SuppliedSource(); run = self.runner()
        with patch.object(self.source, 'close', side_effect=RuntimeError('SYNTHETIC_SECRET')):
            result = run.run()
        self.assertEqual(result['state'], 'failed'); self.assertIsNone(result['report'])

    def test_stop_before_run_never_calls_source(self):
        run = self.runner(); run.stop()
        self.assertEqual(run.run()['state'], 'cancelled'); self.assertFalse(self.source.calls)
        self.assertTrue(self.source.closed)

    def test_interrupted_cleanup_cannot_leave_a_report_visible(self):
        run = self.runner()
        with patch.object(self.source, 'close', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): run.run()
        self.assertEqual(run.snapshot()['state'], 'cancelled')
        self.assertIsNone(run.snapshot()['report'])

    def test_final_callback_expiry_never_commits_diagnostic_counter(self):
        run = self.runner(); original = run._response
        def callback(stream):
            original(stream)
            if stream == 'holiday': self.advance(120)
        run._poller._on_response = callback
        result = run.run()
        self.assertEqual(result['state'], 'expired')
        self.assertEqual(result['completed_cycles'], 0)
        self.assertIsNone(result['report'])

    def test_cross_thread_cancel_does_not_publish_late_parsed_cycle(self):
        run = self.runner(); entered = threading.Event(); release = threading.Event(); result = []
        original = run._response
        def callback(stream):
            original(stream)
            if stream == 'holiday':
                entered.set(); self.assertTrue(release.wait(5))
        run._poller._on_response = callback
        thread = threading.Thread(target=lambda: result.append(run.run()), daemon=True); thread.start()
        try:
            self.assertTrue(entered.wait(5))
            pending = run.snapshot()
            self.assertFalse(pending['current']); self.assertIsNone(pending['report'])
            self.assertEqual(pending['completed_cycles'], 0)
            run.stop(); self.assertFalse(self.source.closed)
        finally:
            release.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertTrue(self.source.closed)
        self.assertEqual(result[0]['state'], 'cancelled'); self.assertIsNone(result[0]['report'])
        self.assertEqual(result[0]['completed_cycles'], 0)

    def test_wrong_recipe_or_truncated_history_cannot_parse(self):
        original = self.source.reads['history'][0]
        self.source.reads['history'] = (replace(original,
            response=replace(original.response, headers={'tr_cont': 'M'})),)
        result = self.runner().run()
        self.assertEqual(result['state'], 'incomplete')
        self.source = SuppliedSource(); original = self.source.reads['price'][0]
        self.source.reads['price'] = (replace(original, recipe=replace(original.recipe, method='POST')),)
        self.assertEqual(self.runner().run()['state'], 'failed')

    def test_configuration_and_page_bounds(self):
        for options in ({'venue': 'NXT'}, {'max_age_seconds': True}, {'max_age_seconds': 301},
                        {'max_pages': 101}, {'max_rows': 10001}):
            with self.assertRaises(Blocked): self.runner(**options)
        self.assertFalse(self.source.calls)
        self.source.reads['history'] *= 2
        self.assertEqual(self.runner(max_pages=1).run()['state'], 'failed')

    def test_actual_authenticated_source_to_real_pipeline_with_synthetic_client(self):
        from test_kis_authenticated import Clock, Provider, Client, Response, history_response
        from oneil_execution_v2.kis_authenticated import KisAuthenticatedReadSource
        from oneil_execution_v2.kis_transport import ReadRateLimiter
        clock = Clock(); provider = Provider(clock); client = Client()
        responses = [history_response()] + [Response(c.reads[0].response.body,
            marker=c.reads[0].response.headers.get('tr_cont', '')) for c in cases()]
        client.values = list(responses)
        source = KisAuthenticatedReadSource(BINDING, provider, client,
            monotonic=clock, utc_now=clock.utc, limiter=ReadRateLimiter(monotonic=clock))
        run = KisDiagnosticRun(source, BINDING, DAY, '005930', monotonic=clock, utc_now=clock.utc)
        with patch('oneil_execution_v2.kis_authenticated.Cancellation.wait', lambda _, sec: clock.advance(sec)):
            result = run.run()
        self.assertEqual(result['state'], 'parsed'); self.assertTrue(result['report']['shape_complete'])
        self.assertFalse(result['provenance_authenticated']); self.assertFalse(result['policy_ready'])
        self.assertEqual(len(client.calls), 7); self.assertEqual(len(provider.calls), 1)
        self.assertTrue(source._closed); self.assertIsNone(source._lease)
        self.assertTrue(all(response.closed == 1 for response in responses))


class DiagnosticHostTests(unittest.TestCase):
    setUp = host_fixture.SimulationHostTests.setUp
    inject = host_fixture.SimulationHostTests.inject
    call = host_fixture.SimulationHostTests.call

    def diagnostic(self):
        source = SuppliedSource()
        run = KisDiagnosticRun(source, BINDING, DAY, '005930', utc_now=lambda: NOW, monotonic=lambda: 100.)
        return run, source

    def test_no_default_source_and_attachment_never_starts(self):
        self.inject()
        self.assertEqual(self.call('/api/v2/host-state')[1]['kis_diagnostics']['state'], 'not_attached')
        run, source = self.diagnostic(); self.host.attach_kis_diagnostics(run)
        self.assertFalse(source.calls)
        self.assertEqual(self.call('/api/v2/host-state')[1]['kis_diagnostics']['state'], 'idle')
        with self.assertRaises(Blocked): self.host.attach_kis_diagnostics(run)
        with self.assertRaises(Blocked): self.host.attach_kis_diagnostics({})

    def test_authentication_scope_and_real_sqlite_projection_no_account_data(self):
        run, source = self.diagnostic(); self.host.attach_kis_diagnostics(run)
        with patch('oneil_execution_v2.kis_authenticated.Cancellation.wait', lambda *_: None): run.run()
        self.assertEqual(self.call('/api/v2/host-state')[0], 401)
        self.inject(scopes=frozenset({'monitor.read'}))
        code, body = self.call('/api/v2/host-state')
        self.assertEqual(code, 200); self.assertEqual(body['kis_diagnostics']['state'], 'parsed')
        self.assertFalse(body['live_capable']); self.assertTrue(body['halted'])
        self.assertFalse(body['proposals']); self.assertFalse(self.host.broker.calls)
        for private in (BINDING.cano, BINDING.account, '005930', '900000'):
            self.assertNotIn(private, json.dumps(body['kis_diagnostics']))
        # GET projection never writes raw captures or diagnostic status to journals.
        for path in self.directory.glob('*.sqlite'):
            self.assertNotIn(b'KIS_CROSS_ENDPOINT_HOLDING_MISMATCH', path.read_bytes())

    def test_stop_and_close_cancel_without_starting_or_concurrent_source_close(self):
        run, source = self.diagnostic(); self.host.attach_kis_diagnostics(run)
        self.supervisor.request_stop(); self.inject()
        self.assertEqual(self.call('/api/v2/host-state')[1]['kis_diagnostics']['state'], 'cancelled')
        self.host.close(); self.assertFalse(source.calls); self.assertFalse(source.closed)
        # Caller still owns invoking run/cleanup. Cancellation prevents reads.
        self.assertEqual(run.run()['state'], 'cancelled'); self.assertTrue(source.closed)

    def test_browser_cannot_attach_run_or_upload_captures(self):
        self.inject()
        for path in ('/api/v2/kis-diagnostics', '/api/v2/kis-start', '/api/v2/kis-captures'):
            self.assertNotEqual(self.call(path, {'verified': True})[0], 200)
        self.assertIsNone(self.host._kis_diagnostics); self.assertFalse(self.host.broker.calls)


if __name__ == '__main__': unittest.main()
