"""Cross-branch offline regressions, never actual PostgreSQL or authenticated KIS.

Reuse the explicit non-durable core and fake HTTP client from component tests.
Well-shaped injected captures remain diagnostics and must not preserve or restore
the control coordinator's positive synthetic policy admission path.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for folder in ('policy_v2', 'execution_v2'):
    sys.path.insert(0, str(ROOT/'tests'/folder))
import test_policy_flow as control_fixtures
import test_kis_pipeline as pipeline_fixtures
import test_kis_transport as transport_fixtures
import test_kis_https as https_fixtures
import test_runtime_policy_flow as runtime_fixtures
from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_transport import KisGetOnlyTransport, ReadRateLimiter
from oneil_policy_v2.kis_pipeline import POLICY_BLOCK


class ComponentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.socket_guard = patch('socket.socket', side_effect=AssertionError('NETWORK FORBIDDEN'))
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)
        self.harness = control_fixtures.PolicyFlowTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    def confirmed(self):
        h = self.harness
        status, preview = h.preview()
        self.assertEqual(status, 201)
        command = h.command(preview)
        status, result = h.post(command, key='component-confirmed-0001')
        self.assertEqual(status, 202)
        return result, command

    def captured_inputs(self, *, concrete_https=False):
        inputs = pipeline_fixtures.supplied_inputs()
        clock = transport_fixtures.Clock()
        limiter = ReadRateLimiter(monotonic=clock)
        calls = []

        def capture(read):
            if concrete_https:
                # Production client code, but every DNS/socket/TLS operation is
                # replaced by the HTTPS suite's explicit in-memory doubles.
                fixture = https_fixtures.KisHttpsTests()
                fixture.setUp()
                try:
                    body = json.dumps(read.response.body, ensure_ascii=True).encode('ascii')
                    headers = {'Content-Type': 'application/json',
                               'Content-Length': str(len(body)), **dict(read.response.headers)}
                    fixture.tls.chunks = [('HTTP/1.1 200 OK\r\n' + ''.join(
                        f'{key}: {value}\r\n' for key, value in headers.items()) + '\r\n').encode('ascii') + body]
                    client = https_fixtures.https.KisHttpsGetClient(inputs.binding, monotonic=clock)
                    transport = KisGetOnlyTransport(client, inputs.binding, limiter=limiter,
                        monotonic=clock, utc_now=lambda: pipeline_fixtures.NOW)
                    result = transport.capture(read.recipe)
                    self.assertEqual(len(fixture.tls.sent), 1)
                    self.assertTrue(fixture.tls.sent[0].startswith(b'GET '))
                    fixture.dns.assert_called_once()
                    self.assertEqual(fixture.tls.closed, 1)
                    calls.append(SimpleNamespace(method='GET'))
                    clock.value += 1
                    return result
                finally:
                    fixture.doCleanups()
            response = transport_fixtures.Response(
                json.dumps(read.response.body, ensure_ascii=True).encode('ascii'),
                status=read.response.status_code,
                headers={'Content-Type': 'application/json', **dict(read.response.headers)})
            client = transport_fixtures.Client(response)
            transport = KisGetOnlyTransport(client, inputs.binding, limiter=limiter,
                monotonic=clock, utc_now=lambda: pipeline_fixtures.NOW)
            result = transport.capture(read.recipe)
            clock.value += 1
            calls.extend(client.calls)
            return result

        result = replace(inputs,
            balance_reads=tuple(capture(r) for r in inputs.balance_reads),
            buying_power_read=capture(inputs.buying_power_read),
            sellable_read=capture(inputs.sellable_read), price_read=capture(inputs.price_read),
            session_read=capture(inputs.session_read),
            holiday_reads=tuple(capture(r) for r in inputs.holiday_reads),
            history_reads=tuple(capture(r) for r in inputs.history_reads))
        self.assertTrue(calls)
        self.assertTrue(all(call.method == 'GET' for call in calls))
        return result

    def test_concrete_https_mock_capture_cannot_authorize_runtime_confirmed_order(self):
        runtime = runtime_fixtures.RuntimePolicyFlowTests()
        runtime.setUp()
        self.addCleanup(runtime.doCleanups)
        receipt, _ = runtime.confirm()
        h = runtime.h
        report = h.worker.policy_source.register_kis_offline(receipt['request_id'],
            self.captured_inputs(concrete_https=True), now=pipeline_fixtures.NOW, max_age_seconds=10)
        self.assertTrue(report.shape_complete)
        self.assertFalse(report.policy_ready)
        self.assertFalse(report.live_capable)
        self.assertEqual(runtime.runtime.step(runtime.sample()), 'BLOCKED')
        self.assertEqual(h.store.rows[0]['attempts'], 0)
        self.assertEqual(h.f.broker.calls, [])

    def test_control_confirmation_then_transport_pipeline_cannot_admit(self):
        h = self.harness
        receipt, command = self.confirmed()
        report = h.worker.policy_source.register_kis_offline(receipt['request_id'],
            self.captured_inputs(), now=pipeline_fixtures.NOW, max_age_seconds=10)
        self.assertTrue(report.shape_complete)
        self.assertFalse(report.policy_ready)
        self.assertFalse(report.live_capable)
        self.assertEqual(h.worker.tick(control_fixtures.ACCOUNT), 'BLOCKED')
        self.assertEqual(h.store.rows[0]['attempts'], 0)
        self.assertEqual(h.f.broker.calls, [])
        # A replayed queue receipt is not authority to reinstall old evidence.
        self.assertEqual(h.post(command, key='component-confirmed-0001'), (202, receipt))
        with self.assertRaisesRegex(Blocked, POLICY_BLOCK):
            h.worker.policy_source.read(receipt['request_id'])

    def test_malformed_kis_refresh_removes_confirmed_fixture_fallback(self):
        h = self.harness
        receipt, _ = self.confirmed()
        prior = h.worker.policy_source.read(receipt['request_id'])
        with self.assertRaises(Blocked):
            h.worker.policy_source.register_kis_offline(receipt['request_id'], None,
                now=pipeline_fixtures.NOW, max_age_seconds=10)
        with self.assertRaisesRegex(Blocked, POLICY_BLOCK):
            h.worker.policy_source.register(receipt['request_id'], prior)
        self.assertEqual(h.worker.tick(control_fixtures.ACCOUNT), 'BLOCKED')
        self.assertEqual(h.store.rows[0]['attempts'], 0)
        self.assertEqual(h.f.broker.calls, [])

    def test_kis_block_during_registration_preserves_unknown_command_tombstone(self):
        h = self.harness
        proposal_id = h.propose()
        _, preview = h.preview(proposal_id)
        command = h.command(preview)
        register = h.worker.policy_source.register

        def blocked_register(request_id, admission):
            h.worker.policy_source.register_kis_offline(request_id,
                pipeline_fixtures.supplied_inputs(), now=pipeline_fixtures.NOW, max_age_seconds=10)
            register(request_id, admission)

        with patch.object(h.worker.policy_source, 'register', side_effect=blocked_register):
            self.assertEqual(h.post(command, key='component-unknown-0001'),
                             (503, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        self.assertEqual(h.post(command, key='component-unknown-0001'),
                         (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        _, second = h.preview(proposal_id)
        self.assertEqual(h.post(h.command(second)),
                         (409, {'error': 'POLICY_PROPOSAL_ALREADY_DISPATCHED'}))
        self.assertEqual(h.f.broker.calls, [])


if __name__ == '__main__':
    unittest.main()
