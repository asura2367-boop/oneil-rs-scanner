"""Finite polling tests: synthetic data and forbidden network only."""
from dataclasses import asdict, replace
import unittest
import threading
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import prepare_history_query
from oneil_execution_v2.kis_evidence import prepare_balance_query
from oneil_execution_v2.kis_polling import KisReadPoller, PollLimits, STREAMS, _Stop
from kis_evidence_fixtures import BINDING, DAY, read


class Source:
    def __init__(self):
        self.calls = []; self.hook = None; self.value = None; self.closed = False

    def close(self):
        self.closed = True

    def capture(self, recipe, *, cancellation, deadline_monotonic=None, deadline_clock=None):
        self.calls.append((recipe, cancellation))
        if self.hook:
            self.hook()
        return self.value if self.value is not None else read(recipe, {})

    def history(self, query, **kw):
        return (self.capture(prepare_history_query(query), **kw),)

    def balance(self, query, **kw):
        return (self.capture(prepare_balance_query(query), **kw),)


class PollTests(unittest.TestCase):
    def setUp(self):
        self.socket = patch('socket.socket', side_effect=AssertionError('NETWORK_FORBIDDEN'))
        self.dns = patch('socket.getaddrinfo', side_effect=AssertionError('DNS_FORBIDDEN'))
        self.socket.start(); self.dns.start()
        self.addCleanup(self.socket.stop); self.addCleanup(self.dns.stop)
        self.source = Source(); self.now = 100.0; self.waits = []
        self.wait_patch = patch('oneil_execution_v2.kis_authenticated.Cancellation.wait', self.wait)
        self.wait_patch.start(); self.addCleanup(self.wait_patch.stop)

    def wait(self, seconds):
        self.waits.append(seconds); self.now += seconds

    def poller(self, **kw):
        return KisReadPoller(self.source, BINDING, DAY, '005930', monotonic=lambda: self.now, **kw)

    def test_no_implicit_start(self):
        poll = self.poller()
        self.assertEqual(poll.status.state, 'idle'); self.assertEqual(self.source.calls, [])

    def test_seven_streams_paced_and_no_executable_output(self):
        streams = []
        poll = self.poller(on_request=streams.append)
        status = poll.run()
        self.assertEqual(streams, list(STREAMS))
        self.assertEqual((status.state, status.completed_reads, status.completed_cycles), ('completed', 7, 1))
        self.assertEqual(self.waits, [1.] * 6)
        self.assertFalse(status.live_eligible); self.assertFalse(status.provenance_authenticated)
        self.assertTrue(all(call[0].method == 'GET' for call in self.source.calls))
        self.assertEqual(len(set(call[0].path for call in self.source.calls)), 7)

    def test_holiday_once_across_cycles(self):
        status = self.poller(limits=PollLimits(cycles=3)).run()
        self.assertEqual((status.completed_reads, status.completed_cycles), (19, 3))
        self.assertEqual(len(self.waits), 18)

    def test_stop_before_start(self):
        poll = self.poller(); poll.stop()
        self.assertEqual(poll.run().state, 'cancelled'); self.assertFalse(self.source.calls)

    def test_stop_during_request_callback(self):
        poll = self.poller(on_request=lambda _: poll.stop())
        self.assertEqual(poll.run().state, 'cancelled'); self.assertFalse(self.source.calls)

    def test_stop_inside_source(self):
        poll = self.poller(); self.source.hook = poll.stop
        self.assertEqual(poll.run().state, 'cancelled')
        self.assertEqual(poll.status.completed_reads, 0)
        self.assertTrue(self.source.calls[0][1].cancelled)

    def test_stop_during_response_does_not_publish_success(self):
        poll = self.poller(on_response=lambda _: poll.stop())
        self.assertEqual(poll.run().state, 'cancelled')
        self.assertEqual(poll.status.completed_reads, 0)
        self.assertEqual(len(self.source.calls), 1)

    def test_cleanup_exception_cannot_report_completed(self):
        poll = self.poller()
        with patch.object(self.source, 'close', side_effect=RuntimeError('SYNTHETIC_SECRET')) as close:
            status = poll.run()
        close.assert_called_once_with()
        self.assertEqual((status.state, status.completed_reads, status.completed_cycles), ('failed', 7, 1))
        self.assertIsNone(status.stream)
        self.assertNotIn('SYNTHETIC_SECRET', repr(asdict(status)))
        self.assertFalse(status.policy_ready); self.assertFalse(status.live_capable)

    def test_cross_thread_stop_before_response_publication(self):
        for target, prior_reads in (('history', 0), ('holiday', 6)):
            with self.subTest(target=target):
                self.source = Source(); self.now = 100.; self.waits = []
                entered = threading.Event(); release = threading.Event()
                timed_out = threading.Event(); results = []
                def callback(stream):
                    if stream == target:
                        entered.set()
                        if not release.wait(5):
                            timed_out.set()
                poll = self.poller(on_response=callback)
                runner = threading.Thread(target=lambda: results.append(poll.run()), daemon=True)
                runner.start()
                try:
                    self.assertTrue(entered.wait(5), 'response callback not reached')
                    self.assertEqual(poll.status.completed_reads, prior_reads)
                    poll.stop()  # Main thread cancels while run thread is in callback.
                    stopped = poll.status
                    self.assertEqual(stopped.state, 'cancelled')
                    self.assertFalse(self.source.closed)  # No premature concurrent close.
                finally:
                    release.set()
                    runner.join(5)
                self.assertFalse(runner.is_alive(), 'run did not return after cancellation')
                self.assertFalse(timed_out.is_set(), 'callback release timed out')
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0], stopped)
                self.assertEqual((poll.status.completed_reads, poll.status.completed_cycles), (prior_reads, 0))
                self.assertEqual(len(self.source.calls), prior_reads + 1)
                self.assertTrue(self.source.closed)

    def test_stop_during_wait(self):
        poll = self.poller()
        with patch('oneil_execution_v2.kis_authenticated.Cancellation.wait', lambda *_: poll.stop()):
            self.assertEqual(poll.run().state, 'cancelled')
        self.assertEqual(len(self.source.calls), 1)

    def test_deadline_during_wait(self):
        status = self.poller(limits=PollLimits(duration_seconds=1)).run()
        self.assertEqual(status.state, 'expired'); self.assertEqual(len(self.source.calls), 1)

    def test_late_source_result_not_published(self):
        poll = self.poller(limits=PollLimits(duration_seconds=1))
        self.source.hook = lambda: setattr(self, 'now', 102.)
        self.assertEqual(poll.run().state, 'expired'); self.assertEqual(poll.status.completed_reads, 0)

    def test_late_response_callback_not_published(self):
        poll = self.poller(on_response=lambda _: setattr(self, 'now', 1000.))
        self.assertEqual(poll.run().state, 'expired'); self.assertEqual(poll.status.completed_reads, 0)

    def test_repeated_and_reentrant_run_blocked(self):
        poll = self.poller()
        def hook(_):
            with self.assertRaisesRegex(Blocked, 'KIS_POLL_ALREADY_STARTED'):
                poll.run()
        poll._on_request = hook
        self.assertEqual(poll.run().state, 'completed')
        with self.assertRaisesRegex(Blocked, 'KIS_POLL_ALREADY_STARTED'):
            poll.run()

    def test_error_and_private_exception_redaction(self):
        for error in (RuntimeError('SYNTHETIC_SECRET'), _Stop('SYNTHETIC_SECRET')):
            with self.subTest(error=type(error).__name__):
                def fail(_):
                    raise error
                poll = self.poller(on_response=fail)
                self.assertEqual(poll.run().state, 'failed')
                self.assertNotIn('SYNTHETIC_SECRET', repr(asdict(poll.status)))
                self.assertNotIn(BINDING.cano, repr(poll))

    def test_wrong_result_fails_closed(self):
        self.source.value = object()
        poll = self.poller()
        self.assertEqual(poll.run().state, 'failed'); self.assertEqual(poll.status.completed_reads, 0)

    def test_invalid_clock_and_rollback(self):
        for value in (float('nan'), float('inf'), -1., True, 99.):
            with self.subTest(value=value):
                self.now = 100.
                self.source.hook = lambda: setattr(self, 'now', value)
                poll = self.poller()
                self.assertEqual(poll.run().state, 'clock_invalid')
                self.assertEqual(poll.status.completed_reads, 0)

    def test_config_bounds_before_source(self):
        for limits in (PollLimits(cycles=True), PollLimits(cycles=0), PollLimits(cycles=101),
                       PollLimits(interval_seconds=0), PollLimits(interval_seconds=61),
                       PollLimits(duration_seconds=float('nan')), PollLimits(duration_seconds=3601)):
            with self.subTest(limits=limits), self.assertRaisesRegex(Blocked, 'KIS_POLL_CONFIG_INVALID'):
                self.poller(limits=limits)
        self.assertFalse(self.source.calls)

    def test_demo_not_silently_substituted(self):
        with self.assertRaisesRegex(Blocked, 'KIS_POLL_CONFIG_INVALID'):
            KisReadPoller(self.source, replace(BINDING, environment='demo'), DAY, '005930')
        self.assertFalse(self.source.calls)

    def test_submission_and_evidence_blocked(self):
        poll = self.poller()
        for operation in (poll.submit, poll.policy_evidence):
            with self.assertRaises(Blocked):
                operation()
        self.assertFalse(self.source.calls)


class AuthenticatedDeadlineTests(unittest.TestCase):
    setUp = PollTests.setUp
    wait = PollTests.wait
    # Reuse network-denying setup, not inherited test cases.
    def authenticated(self):
        from test_kis_authenticated import Clock, Provider, Client
        from oneil_execution_v2.kis_authenticated import KisAuthenticatedReadSource
        from oneil_execution_v2.kis_transport import ReadRateLimiter
        clock = Clock(); provider = Provider(clock); client = Client()
        source = KisAuthenticatedReadSource(BINDING, provider, client,
            monotonic=clock, utc_now=clock.utc, limiter=ReadRateLimiter(monotonic=clock))
        return source, clock, provider, client

    def test_all_seven_through_authenticated_source_with_paced_clock(self):
        from test_kis_authenticated import Response, history_response
        from kis_evidence_fixtures import cases
        source, clock, provider, client = self.authenticated()
        responses = [history_response()] + [
            Response(case.reads[0].response.body,
                     marker=case.reads[0].response.headers.get('tr_cont', ''))
            for case in cases()]
        client.values = list(responses)
        waits = []; starts = []; streams = []
        client.hook = lambda: starts.append(clock())
        def advance(token, seconds):
            waits.append(seconds)
            clock.advance(seconds)
        poll = KisReadPoller(source, BINDING, DAY, '005930', monotonic=clock,
                            on_response=streams.append)
        with patch('oneil_execution_v2.kis_authenticated.Cancellation.wait', advance):
            status = poll.run()
        self.assertEqual((status.state, status.completed_reads, status.completed_cycles), ('completed', 7, 1))
        self.assertEqual(streams, list(STREAMS))
        self.assertEqual(waits, [1.] * 6)
        self.assertEqual(starts, [100., 101., 102., 103., 104., 105., 106.])
        self.assertEqual(len(client.calls), 7)
        self.assertEqual(len(set(call.request.url.split('?')[0] for call in client.calls)), 7)
        self.assertTrue(all(call.method == 'GET' for call in client.calls))
        self.assertTrue(all(response.closed == 1 for response in responses))
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(source._closed); self.assertIsNone(source._lease)
        self.assertFalse(status.live_eligible); self.assertFalse(status.provenance_authenticated)
        self.assertFalse(status.policy_ready); self.assertFalse(status.live_capable)

    def test_provider_consumes_poll_budget_before_client(self):
        source, clock, provider, client = self.authenticated()
        provider.hook = lambda: clock.advance(2)
        poll = KisReadPoller(source, BINDING, DAY, '005930',
            limits=PollLimits(duration_seconds=1), monotonic=clock)
        status = poll.run()
        self.assertEqual(status.completed_reads, 0)
        self.assertNotEqual(status.state, 'completed')
        self.assertFalse(client.calls)
        self.assertTrue(source._closed)

    def test_attempt_receives_shared_deadline(self):
        from oneil_execution_v2.kis_adapter import HistoryQuery
        from test_kis_authenticated import history_response
        source, clock, provider, client = self.authenticated()
        client.values = [history_response()]
        source.history(HistoryQuery(BINDING, DAY), deadline_monotonic=101., deadline_clock=clock)
        self.assertEqual(client.calls[0].request.deadline_monotonic, 101.)
        self.assertEqual(provider.calls[0][1]['deadline_monotonic'], 101.)

    def test_mismatched_clock_rejected_before_provider(self):
        from oneil_execution_v2.kis_adapter import HistoryQuery
        source, clock, provider, client = self.authenticated()
        with self.assertRaisesRegex(Blocked, 'KIS_AUTH_CLOCK_INVALID'):
            source.history(HistoryQuery(BINDING, DAY), deadline_monotonic=101.,
                           deadline_clock=lambda: clock())
        self.assertFalse(provider.calls); self.assertFalse(client.calls)

    def test_stop_during_retry_wait(self):
        from test_kis_authenticated import Response
        source, clock, provider, client = self.authenticated()
        client.values = [Response(status=503)]
        poll = KisReadPoller(source, BINDING, DAY, '005930', monotonic=clock)
        with patch('oneil_execution_v2.kis_authenticated.Cancellation.wait', lambda *_: poll.stop()):
            status = poll.run()
        self.assertEqual((status.state, status.completed_reads), ('cancelled', 0))
        self.assertEqual(len(client.calls), 1); self.assertTrue(source._closed)

    def test_stop_during_pagination_rate_wait(self):
        from test_kis_authenticated import history_response
        source, clock, provider, client = self.authenticated()
        client.values = [history_response(marker='M', cursor=('next', 'page'))]
        poll = KisReadPoller(source, BINDING, DAY, '005930', monotonic=clock)
        with patch('oneil_execution_v2.kis_authenticated.Cancellation.wait', lambda *_: poll.stop()):
            status = poll.run()
        self.assertEqual((status.state, status.completed_reads), ('cancelled', 0))
        self.assertEqual(len(client.calls), 1); self.assertTrue(source._closed)

    def test_response_cancellation_closes_source_and_response(self):
        from test_kis_authenticated import history_response
        source, clock, provider, client = self.authenticated()
        response = history_response(); client.values = [response]
        poll = KisReadPoller(source, BINDING, DAY, '005930', monotonic=clock)
        response.read_hook = poll.stop
        status = poll.run()
        self.assertEqual((status.state, status.completed_reads), ('cancelled', 0))
        self.assertEqual(response.closed, 1); self.assertTrue(source._closed)


if __name__ == '__main__':
    unittest.main()
