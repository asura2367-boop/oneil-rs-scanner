"""Synthetic, socket-forbidden auth lifecycle/GET composition tests. No secrets."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import pickle
import traceback
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import HistoryQuery, RequestRecipe, prepare_history_query
from oneil_execution_v2.kis_authenticated import (AuthenticatedGet, Cancellation,
    CredentialLease, KisAuthenticatedReadSource, RetryPolicy, TransientReadFailure)
from oneil_execution_v2.kis_evidence import (BalanceQuery, HolidayQuery, MarketQuery,
    OfflineRead, prepare_balance_query, prepare_holiday_query, prepare_market_query)
from oneil_execution_v2.kis_transport import ReadRateLimiter
from kis_evidence_fixtures import BINDING, DAY, NOW, POSITION, TOTALS, cases

KEY = 'SYNTHETIC_APP_KEY_NOT_REAL'
SECRET = 'SYNTHETIC_SECRET_NOT_REAL'
TOKEN = 'SYNTHETIC_BEARER_NOT_REAL'


class Clock:
    def __init__(self):
        self.value = 100.0; self.offset = 0.0

    def __call__(self):
        return self.value

    def utc(self):
        return NOW + timedelta(seconds=self.value - 100 + self.offset)

    def advance(self, seconds):
        self.value += seconds


class Provider:
    def __init__(self, clock):
        self.clock = clock; self.calls = []; self.values = []; self.hook = None

    def lease(self, token=TOKEN):
        return CredentialLease(BINDING, KEY, SECRET, token, self.clock.utc(),
                               self.clock.utc() + timedelta(hours=1))

    def acquire(self, binding, **kwargs):
        self.calls.append((binding, kwargs))
        if self.hook:
            self.hook()
        value = self.values.pop(0) if self.values else self.lease()
        if isinstance(value, BaseException):
            raise value
        return value


class Response:
    def __init__(self, body=None, status=200, marker='D'):
        self.status_code = status
        self.headers = {'content-type': 'application/json', 'tr_cont': marker}
        self.body = json.dumps({'rt_cd': '0', 'output1': []} if body is None else body).encode()
        self.url = None; self.request_method = 'GET'; self.tls_verified = True
        self.redirect_count = 0; self.closed = 0; self.reads = 0; self.read_hook = None

    def iter_bytes(self, size):
        self.reads += 1
        if self.read_hook:
            self.read_hook()
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset+size]

    def close(self):
        self.closed += 1


class Client:
    def __init__(self):
        self.calls = []; self.values = []; self.hook = None

    def open_authenticated_get(self, request):
        self.calls.append(request)
        if self.hook:
            self.hook()
        value = self.values.pop(0) if self.values else Response()
        if isinstance(value, BaseException):
            raise value
        if value.url is None:
            value.url = request.request.url
        return value


def balance_response(*, rows=None, marker='D', cursor=('', ''), totals=None):
    return Response({'rt_cd': '0', 'output1': [POSITION] if rows is None else rows,
                     'output2': [TOTALS if totals is None else totals],
                     'ctx_area_fk100': cursor[0], 'ctx_area_nk100': cursor[1]}, marker=marker)


def history_response(*, marker='D', cursor=('', '')):
    return Response({'rt_cd': '0', 'output1': [],
                     'ctx_area_fk100': cursor[0], 'ctx_area_nk100': cursor[1]}, marker=marker)


class AuthenticatedReadTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket', side_effect=AssertionError('NETWORK FORBIDDEN'))
        guard.start(); self.addCleanup(guard.stop)
        dns = patch('socket.getaddrinfo', side_effect=AssertionError('DNS FORBIDDEN'))
        dns.start(); self.addCleanup(dns.stop)
        self.clock = Clock(); self.provider = Provider(self.clock); self.client = Client()
        self.limiter = ReadRateLimiter(monotonic=self.clock)
        self.source = self.make(); self.token = Cancellation(); self.waits = []
        self.wait_hook = None

        def wait(token, seconds):
            self.waits.append(seconds)
            self.clock.advance(seconds)
            if self.wait_hook:
                self.wait_hook(token)
            return token.cancelled
        patcher = patch.object(Cancellation, 'wait', autospec=True, side_effect=wait)
        patcher.start(); self.addCleanup(patcher.stop)
        self.recipe = prepare_history_query(HistoryQuery(BINDING, DAY))

    def make(self, **kwargs):
        return KisAuthenticatedReadSource(kwargs.pop('binding', BINDING),
            kwargs.pop('provider', self.provider), kwargs.pop('client', self.client),
            monotonic=self.clock, utc_now=self.clock.utc,
            limiter=kwargs.pop('limiter', self.limiter), **kwargs)

    def capture(self, recipe=None, **kwargs):
        return self.source.capture(self.recipe if recipe is None else recipe,
                                   cancellation=self.token, **kwargs)

    def blocked(self, call=None, code=None):
        with self.assertRaises(Blocked) as caught:
            (self.capture if call is None else call)()
        if code:
            self.assertEqual(str(caught.exception), code)
        return caught.exception

    def test_construction_has_no_provider_client_or_socket_effect(self):
        self.assertEqual(self.provider.calls, []); self.assertEqual(self.client.calls, [])

    def test_only_exact_get_auth_envelope_and_offline_result(self):
        result = self.capture()
        self.assertIs(type(result), OfflineRead)
        request = self.client.calls[0]
        self.assertIs(type(request), AuthenticatedGet)
        self.assertEqual(request.method, 'GET'); self.assertEqual(request.request.method, 'GET')
        self.assertEqual(request.headers[-3:], (('authorization', 'Bearer '+TOKEN),
                                               ('appkey', KEY), ('appsecret', SECRET)))
        self.assertFalse(request.request.follow_redirects)
        self.assertFalse(request.request.use_environment)
        self.assertEqual(request.request.automatic_retries, 0)
        self.assertEqual(self.provider.calls[0][0], BINDING)
        self.assertIs(self.provider.calls[0][1]['cancellation'], self.token)
        self.assertLessEqual(self.provider.calls[0][1]['deadline_monotonic'], request.request.deadline_monotonic)

    def test_all_seven_existing_endpoints_remain_get_only(self):
        for case in cases():
            fixture = case.reads[0]
            self.client.values.append(Response(fixture.response.body,
                                              marker=fixture.response.headers['tr_cont']))
            self.capture(fixture.recipe)
        self.capture()
        self.assertEqual(len(self.client.calls), 7)
        self.assertEqual(len(self.provider.calls), 1)

    def test_methods_paths_and_auth_mutations_block_before_provider(self):
        variants = [replace(self.recipe, method='POST'), replace(self.recipe, path='/oauth2/tokenP'),
                    replace(self.recipe, path='/uapi/domestic-stock/v1/trading/order-cash'),
                    replace(self.recipe, path='/uapi/domestic-stock/v1/trading/order-rvsecncl'),
                    replace(self.recipe, tr_id='TTTC0012U'),
                    replace(self.recipe, parameters=self.recipe.parameters + (('Authorization', TOKEN),))]
        for recipe in variants:
            self.blocked(lambda: self.capture(recipe))
        self.assertEqual(self.provider.calls, []); self.assertEqual(self.client.calls, [])

    def test_other_account_recipe_blocks_before_provider(self):
        binding = replace(BINDING, cano='11111111')
        self.blocked(lambda: self.capture(prepare_history_query(HistoryQuery(binding, DAY))))
        self.assertEqual(self.provider.calls, [])

    def test_invalid_configuration(self):
        for kwargs in [{'policy': RetryPolicy(max_attempts=4)}, {'policy': RetryPolicy(max_attempts=True)},
                       {'policy': RetryPolicy(operation_seconds=121)}, {'provider': object()},
                       {'client': object()}, {'binding': replace(BINDING, environment='unknown')}]:
            self.blocked(lambda: self.make(**kwargs), 'KIS_AUTH_CONFIG_INVALID')

    def test_wrong_lease_scope_blocks_before_client(self):
        self.provider.values.append(replace(self.provider.lease(), binding=replace(BINDING, environment='demo')))
        self.blocked(code='KIS_AUTH_LEASE_INVALID'); self.assertEqual(self.client.calls, [])

    def test_invalid_header_material_blocks_before_client(self):
        for bad in ['', 'abc\r\nHeader: x', 'hello world', '한글문자열12345678', 'x'*4097]:
            self.provider.values.append(replace(self.provider.lease(), app_secret=bad))
            self.blocked(code='KIS_AUTH_LEASE_INVALID')
        self.assertEqual(self.client.calls, [])

    def test_expired_short_future_or_overlong_lease_blocks(self):
        variants = [dict(expires_at=NOW), dict(expires_at=NOW+timedelta(seconds=40)),
                    dict(issued_at=NOW+timedelta(minutes=5)),
                    dict(expires_at=NOW+timedelta(days=2)), dict(expires_at=NOW.replace(tzinfo=None))]
        for update in variants:
            self.provider.values.append(replace(self.provider.lease(), **update))
            self.blocked()
        self.assertEqual(self.client.calls, [])

    def test_valid_lease_is_reused_without_extra_provider_lookup(self):
        self.capture(); self.capture()
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(len(self.client.calls), 2); self.assertEqual(self.waits, [1.0])

    def test_expiring_lease_is_replaced_before_another_read(self):
        self.capture(); self.clock.advance(3560)
        self.provider.values.append(self.provider.lease(TOKEN+'2'))
        self.capture()
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(self.client.calls[-1].bearer, TOKEN+'2')

    def test_wall_clock_rollback_blocks_across_operations(self):
        self.capture(); self.clock.offset = -1
        self.blocked(code='KIS_AUTH_CLOCK_INVALID')
        self.assertEqual(len(self.client.calls), 1)

    def test_monotonic_rollback_blocks_across_operations(self):
        self.capture(); self.clock.value -= 1
        self.blocked(code='KIS_AUTH_CLOCK_INVALID')
        self.assertEqual(len(self.client.calls), 1)

    def test_monotonic_expiry_prevents_wall_clock_freeze_extending_lease(self):
        self.capture(); self.clock.advance(3560); self.clock.offset = -3560
        self.provider.values.append(self.provider.lease(TOKEN+'2'))
        self.capture()
        self.assertEqual(len(self.provider.calls), 2)

    def test_reacquired_old_token_cannot_reset_monotonic_expiry(self):
        original = self.provider.lease()
        self.provider.values.append(original); self.capture()
        self.clock.advance(3601); self.clock.offset = -3601
        self.provider.values.append(original)
        self.blocked(code='KIS_AUTH_LEASE_EXPIRED')
        self.assertEqual(len(self.client.calls), 1)

    def test_reissued_same_token_cannot_change_recorded_expiry_metadata(self):
        original = self.provider.lease()
        self.provider.values.append(original); self.capture(); self.clock.advance(3560)
        self.provider.values.append(replace(original, expires_at=original.expires_at+timedelta(hours=1)))
        self.blocked(code='KIS_AUTH_LEASE_INVALID')
        self.assertEqual(len(self.client.calls), 1)

    def test_rotating_back_to_old_token_keeps_original_expiry_bound(self):
        original = self.provider.lease()
        self.provider.values.append(original); self.capture()
        self.clock.advance(3560); self.clock.offset = -3560
        self.provider.values.append(self.provider.lease(TOKEN+'2')); self.capture()
        self.clock.advance(3560); self.clock.offset = -7120
        self.provider.values.append(original)
        self.blocked(code='KIS_AUTH_LEASE_EXPIRED')
        self.assertEqual(len(self.client.calls), 2)

    def test_unresolved_grant_bound_memory_is_capped(self):
        for number in range(32):
            self.provider.values.append(self.provider.lease(TOKEN+str(number)))
            self.capture(); self.clock.advance(3560); self.clock.offset -= 3560
        self.provider.values.append(self.provider.lease(TOKEN+'overflow'))
        self.blocked(code='KIS_AUTH_LEASE_INVALID')
        self.assertEqual(len(self.source._lease_bounds), 32)
        self.assertEqual(len(self.client.calls), 32)

    def test_grant_record_can_expire_when_utc_also_proves_expiry(self):
        self.capture(); self.clock.advance(3601)
        self.provider.values.append(self.provider.lease(TOKEN+'2')); self.capture()
        self.assertEqual(len(self.source._lease_bounds), 1)

    def test_cancel_after_final_lease_check_still_prevents_client_call(self):
        original = self.source._validate_cached_lease
        def cancel_after_check(*args):
            original(*args)
            self.token.cancel()
        with patch.object(self.source, '_validate_cached_lease', side_effect=cancel_after_check):
            self.blocked(code='KIS_AUTH_CANCELLED')
        self.assertEqual(self.client.calls, [])

    def test_provider_exceeding_attempt_deadline_never_sends(self):
        self.provider.hook = lambda: self.clock.advance(11)
        self.blocked(code='KIS_AUTH_DEADLINE_EXCEEDED')
        self.assertEqual(self.client.calls, [])

    def test_provider_cancellation_never_sends(self):
        self.provider.hook = self.token.cancel
        self.blocked(code='KIS_AUTH_CANCELLED')
        self.assertEqual(self.client.calls, [])

    def test_pre_cancelled_operation_never_consults_provider(self):
        self.token.cancel(); self.blocked(code='KIS_AUTH_CANCELLED')
        self.assertEqual(self.provider.calls, [])

    def test_invalid_cancellation_type_is_rejected(self):
        self.blocked(lambda: self.source.capture(self.recipe, cancellation=object()),
                     'KIS_AUTH_CANCELLATION_INVALID')

    def test_cancellation_in_rate_wait_stops_without_provider_refresh(self):
        self.capture(); self.wait_hook = lambda token: token.cancel()
        self.blocked(code='KIS_AUTH_CANCELLED')
        self.assertEqual(len(self.provider.calls), 1); self.assertEqual(len(self.client.calls), 1)

    def test_cancellation_during_open_closes_response(self):
        response = Response(); self.client.values.append(response); self.client.hook = self.token.cancel
        self.blocked(code='KIS_AUTH_CANCELLED'); self.assertEqual(response.closed, 1)

    def test_cancellation_during_body_closes_response(self):
        response = Response(); response.read_hook = self.token.cancel; self.client.values.append(response)
        self.blocked(code='KIS_AUTH_CANCELLED'); self.assertEqual(response.closed, 1)

    def test_cancellation_during_cleanup_drops_success(self):
        response = Response(); response.close = self.token.cancel; self.client.values.append(response)
        self.blocked(code='KIS_AUTH_CANCELLED')

    def test_502_503_504_retries_bounded_with_shared_rate_budget(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                self.source = self.make()
                before = len(self.client.calls)
                self.client.values.extend([Response(status=status), Response(status=status), Response()])
                self.capture()
                self.assertEqual(len(self.client.calls)-before, 3)
        self.assertTrue(all(wait >= 1 for wait in self.waits))

    def test_exhausted_retry_budget_is_three_attempts(self):
        responses = [Response(status=503) for _ in range(4)]
        self.client.values.extend(responses)
        self.blocked(code='KIS_AUTH_READ_FAILED')
        self.assertEqual(len(self.client.calls), 3)
        self.assertEqual([r.closed for r in responses], [1, 1, 1, 0])

    def test_explicit_transient_read_failure_retries(self):
        self.client.values.extend([TransientReadFailure('CONNECT_TIMEOUT'), Response()])
        self.capture(); self.assertEqual(len(self.client.calls), 2)

    def test_stream_transient_failure_retries_after_cleanup(self):
        response = Response()
        def failure():
            raise TransientReadFailure('PEER_DISCONNECTED')
        response.read_hook = failure
        self.client.values.extend([response, Response()])
        self.capture(); self.assertEqual(response.closed, 1); self.assertEqual(len(self.client.calls), 2)

    def test_generic_tls_exception_and_unknown_transient_are_not_retried(self):
        for exc in (RuntimeError(SECRET), TransientReadFailure('TLS_FAILURE')):
            before = len(self.client.calls); self.client.values.append(exc)
            self.blocked(code='KIS_AUTH_READ_FAILED')
            self.assertEqual(len(self.client.calls)-before, 1)

    def test_retry_wait_is_cancellable(self):
        self.client.values.append(Response(status=503)); self.wait_hook = lambda token: token.cancel()
        self.blocked(code='KIS_AUTH_CANCELLED'); self.assertEqual(len(self.client.calls), 1)

    def test_one_401_renews_once_and_replays_only_exact_get(self):
        self.provider.values.extend([self.provider.lease(), self.provider.lease(TOKEN+'2')])
        self.client.values.extend([Response(status=401), Response()])
        self.capture()
        self.assertEqual(len(self.provider.calls), 2); self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(self.client.calls[0].request.url, self.client.calls[1].request.url)
        self.assertNotEqual(self.client.calls[0].bearer, self.client.calls[1].bearer)

    def test_401_with_same_reissued_token_blocks_without_replay(self):
        self.client.values.append(Response(status=401))
        self.blocked(code='KIS_AUTH_RENEWAL_REPEATED')
        self.assertEqual(len(self.client.calls), 1)

    def test_repeated_401_latches_source_auth_closed(self):
        self.provider.values.extend([self.provider.lease(), self.provider.lease(TOKEN+'2')])
        self.client.values.extend([Response(status=401), Response(status=401)])
        self.blocked(code='KIS_AUTH_UNAUTHORIZED')
        self.blocked(code='KIS_AUTH_UNAUTHORIZED')
        self.assertEqual(len(self.client.calls), 2); self.assertEqual(len(self.provider.calls), 2)

    def test_consecutive_401_across_single_attempt_operations_latches_closed(self):
        self.source = self.make(policy=RetryPolicy(max_attempts=1))
        self.provider.values.extend([self.provider.lease(), self.provider.lease(TOKEN+'2')])
        self.client.values.extend([Response(status=401), Response(status=401)])
        for _ in range(3):
            self.blocked(code='KIS_AUTH_UNAUTHORIZED')
        self.assertEqual(len(self.client.calls), 2); self.assertEqual(len(self.provider.calls), 2)

    def test_successful_renewal_clears_consecutive_401_counter(self):
        self.provider.values.extend([self.provider.lease(), self.provider.lease(TOKEN+'2'),
                                     self.provider.lease(TOKEN+'3')])
        self.client.values.extend([Response(status=401), Response(), Response(status=401), Response()])
        self.capture(); self.capture()
        self.assertEqual(len(self.client.calls), 4); self.assertFalse(self.source._auth_halted)

    def test_403_and_500_never_refresh_or_retry(self):
        for status in (403, 500):
            self.client.values.append(Response(status=status)); self.blocked()
        self.assertEqual(len(self.provider.calls), 1); self.assertEqual(len(self.client.calls), 2)

    def test_200_api_failure_never_refreshes_or_retries(self):
        self.client.values.append(Response({'rt_cd': '1', 'msg1': SECRET}))
        self.blocked(code='KIS_AUTH_API_REJECTED')
        self.assertEqual(len(self.provider.calls), 1); self.assertEqual(len(self.client.calls), 1)

    def test_429_latches_shared_cooldown_without_retry_or_provider_call(self):
        response = Response(status=429); response.headers['Retry-After'] = '60'
        self.client.values.append(response)
        self.blocked(code='KIS_AUTH_REMOTE_RATE_LIMITED')
        self.blocked(code='KIS_AUTH_DEADLINE_EXCEEDED')
        self.assertEqual(len(self.client.calls), 1); self.assertEqual(len(self.provider.calls), 1)

    def test_status_retry_requires_verified_response_metadata(self):
        response = Response(status=503); response.url = 'https://untrusted.example/'
        self.client.values.append(response); self.blocked()
        self.assertEqual(len(self.client.calls), 1); self.assertEqual(response.closed, 1)

    def test_tls_unverified_response_is_not_retried(self):
        response = Response(status=503); response.tls_verified = False
        self.client.values.append(response); self.blocked()
        self.assertEqual(len(self.client.calls), 1)

    def test_raised_status_getter_closes_response_before_outer_ownership(self):
        class Broken:
            url = None
            closed = 0
            @property
            def status_code(self):
                raise RuntimeError(SECRET)
            def close(self):
                self.closed += 1
        response = Broken(); self.client.values.append(response)
        self.blocked(); self.assertEqual(response.closed, 1)

    def test_operation_budget_is_shared_across_retries(self):
        self.source = self.make(policy=RetryPolicy(operation_seconds=2))
        self.client.values.extend([Response(status=503), Response(status=503)])
        self.blocked(code='KIS_AUTH_DEADLINE_EXCEEDED')
        self.assertEqual(len(self.client.calls), 2)

    def test_single_flight_reentrant_call_blocks(self):
        seen = []
        def hook():
            seen.append(str(self.blocked(code='KIS_AUTH_OPERATION_BUSY')))
        self.provider.hook = hook
        self.capture(); self.assertEqual(seen, ['KIS_AUTH_OPERATION_BUSY'])

    def test_close_discards_lease_and_cannot_reopen(self):
        self.capture(); self.source.close()
        self.assertIsNone(self.source._lease)
        self.blocked(code='KIS_AUTH_SOURCE_CLOSED')
        self.assertEqual(len(self.provider.calls), 1)

    def test_repr_and_pickle_do_not_disclose_credentials(self):
        self.capture()
        values = [self.source, self.provider.lease(), self.client.calls[0], self.client.calls[0].request]
        for value in values:
            for secret in (KEY, SECRET, TOKEN, BINDING.cano):
                self.assertNotIn(secret, repr(value))
        for value in values[1:3]:
            with self.assertRaises(TypeError):
                pickle.dumps(value)

    def test_provider_and_client_exception_details_are_redacted(self):
        for target in (self.provider, self.client):
            target.values.append(Blocked(SECRET))
            try:
                self.capture()
            except Blocked:
                trace = traceback.format_exc()
                self.assertNotIn(SECRET, trace); self.assertNotIn(TOKEN, trace)
            else:
                self.fail('expected failure')

    def test_history_paginates_exact_cursors_and_drops_no_partial_result(self):
        self.client.values.extend([history_response(marker='M', cursor=('NEXT', 'PAGE')),
                                   history_response()])
        reads = self.source.history(HistoryQuery(BINDING, DAY), cancellation=self.token)
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[1].recipe.tr_cont, 'N')
        self.assertEqual(reads[1].recipe.wire_parameters()['CTX_AREA_FK100'], 'NEXT')
        self.assertEqual(len(self.provider.calls), 1)

    def test_history_missing_terminal_or_loop_returns_no_partial_result(self):
        self.client.values.extend([history_response(marker='M', cursor=('A', 'B')),
                                   history_response(marker='M', cursor=(' A ', 'B'))])
        self.blocked(lambda: self.source.history(HistoryQuery(BINDING, DAY)),
                     'KIS_AUTH_HISTORY_INCOMPLETE')
        self.assertEqual(len(self.client.calls), 2)

    def test_history_page_limit_and_scope_reject(self):
        self.client.values.append(history_response(marker='M', cursor=('A', 'B')))
        self.blocked(lambda: self.source.history(HistoryQuery(BINDING, DAY), max_pages=1),
                     'KIS_AUTH_HISTORY_INCOMPLETE')
        self.blocked(lambda: self.source.history(HistoryQuery(replace(BINDING, cano='11111111'), DAY)),
                     'KIS_AUTH_SCOPE_INVALID')
        self.assertEqual(len(self.client.calls), 1)

    def test_balance_paginates_and_returns_only_validated_captures(self):
        second = {**POSITION, 'pdno': '000660'}
        self.client.values.extend([balance_response(marker='M', cursor=('A', 'B')),
                                   balance_response(rows=[second])])
        reads = self.source.balance(BalanceQuery(BINDING, DAY))
        self.assertEqual(len(reads), 2); self.assertEqual(reads[1].recipe.tr_cont, 'N')

    def test_balance_invalid_first_page_never_follows_cursor(self):
        for response in [balance_response(rows=[POSITION]*51, marker='M', cursor=('A', 'B')),
                         balance_response(rows=[{'pdno': '005930'}], marker='M', cursor=('A', 'B')),
                         balance_response(rows=[POSITION, POSITION], marker='M', cursor=('A', 'B'))]:
            before = len(self.client.calls); self.client.values.append(response)
            self.blocked(lambda: self.source.balance(BalanceQuery(BINDING, DAY)))
            self.assertEqual(len(self.client.calls)-before, 1)

    def test_balance_changed_totals_duplicate_rows_and_loops_block(self):
        for second in [balance_response(rows=[], totals={**TOTALS, 'dnca_tot_amt':'900001'}),
                       balance_response(),
                       balance_response(rows=[], marker='M', cursor=(' A ', 'B'))]:
            self.client.values.extend([balance_response(marker='M', cursor=('A', 'B')), second])
            self.blocked(lambda: self.source.balance(BalanceQuery(BINDING, DAY)))

    def test_balance_row_and_page_limits_block_without_partial_capture(self):
        self.client.values.append(balance_response(marker='M', cursor=('A', 'B')))
        self.blocked(lambda: self.source.balance(BalanceQuery(BINDING, DAY), max_pages=1),
                     'KIS_AUTH_PAGE_LIMIT')
        self.client.values.append(balance_response(rows=[POSITION, {**POSITION, 'pdno': '000660'}]))
        self.blocked(lambda: self.source.balance(BalanceQuery(BINDING, DAY), max_rows=1),
                     'KIS_AUTH_PAGE_LIMIT')

    def test_holiday_no_retries_and_daily_limit_is_shared(self):
        self.client.values.append(Response(status=503, marker=''))
        recipe = prepare_holiday_query(HolidayQuery(DAY))
        self.blocked(lambda: self.capture(recipe))
        self.blocked(lambda: self.capture(recipe), 'KIS_AUTH_HOLIDAY_RATE_LIMITED')
        self.assertEqual(len(self.client.calls), 1); self.assertEqual(len(self.provider.calls), 1)

    def test_holiday_and_sellable_do_not_gain_pagination(self):
        recipe = prepare_holiday_query(HolidayQuery(DAY))
        self.client.values.append(Response(marker='M'))
        self.blocked(lambda: self.capture(recipe)); self.assertEqual(len(self.client.calls), 1)

    def test_policy_admission_and_submission_are_unconditionally_blocked(self):
        self.blocked(lambda: self.source.policy_evidence(verified=True), 'KIS_LIVE_EVIDENCE_NOT_ESTABLISHED')
        self.blocked(lambda: self.source.submit(approved=True), 'KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED')
        self.assertFalse(hasattr(self.source, 'post'))
        self.assertFalse(hasattr(self.source, 'request'))

    def test_no_runtime_registration_or_network_secret_loader_import(self):
        path = Path(__file__).parents[2]/'oneil_execution_v2/kis_authenticated.py'
        text = path.read_text()
        for forbidden in ('import socket', 'import requests', 'import http', 'import os', 'getenv(',
                          'open(', 'KisHttpsGetClient(', 'tokenP', 'order-cash'):
            self.assertNotIn(forbidden, text)
        root = path.parent.parent
        for directory in ('oneil_runtime_v2', 'oneil_control_v2', 'oneil_policy_v2'):
            for file in (root/directory).glob('*.py'):
                self.assertNotIn('kis_authenticated', file.read_text())


if __name__ == '__main__':
    unittest.main()
