"""Injected-client, synthetic, socket-forbidden tests. No credentials are used."""
import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import traceback
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import (AccountBinding, HistoryQuery, KisReadOnlyAdapter,
                                          RequestRecipe, prepare_history_query)
from oneil_execution_v2.kis_evidence import (BalanceQuery, BuyingPowerQuery, HolidayQuery,
    KisOfflineEvidenceAdapter, MarketQuery, SellableQuery, prepare_balance_query,
    prepare_buying_power_query, prepare_holiday_query, prepare_market_query, prepare_sellable_query)
from oneil_execution_v2.kis_transport import (DEMO_ORIGIN, REAL_ORIGIN, KisGetOnlyTransport,
                                           ReadLimits, ReadRateLimiter)

NOW = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)
BINDING = AccountBinding('a' * 64, 'real', '12345678', '01')
DAY = '2026-11-05'
SECRET = 'FAKE_SECRET_DO_NOT_LOG_12345678'


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class Response:
    def __init__(self, body=b'{"rt_cd":"0","output1":[]}', *, status=200, headers=None):
        self.status_code = status
        self.headers = {'Content-Type': 'application/json', 'tr_cont': 'D'} if headers is None else headers
        self.body = body
        self.request_method = 'GET'; self.tls_verified = True; self.redirect_count = 0
        self.url = None; self.closed = 0; self.body_started = False
        self.chunks = None; self.read_hook = None; self.close_hook = None

    def iter_bytes(self, size):
        self.body_started = True
        if self.read_hook:
            self.read_hook()
        if self.chunks is not None:
            yield from self.chunks
        else:
            for start in range(0, len(self.body), size):
                yield self.body[start:start + size]

    def close(self):
        self.closed += 1
        if self.close_hook:
            self.close_hook()


class Client:
    def __init__(self, response=None):
        self.response = Response() if response is None else response
        self.calls = []
        self.open_hook = None

    def open_get(self, request):
        self.calls.append(request)
        if self.open_hook:
            self.open_hook()
        if self.response.url is None:
            self.response.url = request.url
        return self.response


class KisTransportTests(unittest.TestCase):
    def setUp(self):
        self.socket_guard = patch('socket.socket', side_effect=AssertionError('NETWORK FORBIDDEN'))
        self.socket_guard.start(); self.addCleanup(self.socket_guard.stop)
        self.clock = Clock(); self.client = Client()
        self.limiter = ReadRateLimiter(monotonic=self.clock)
        self.transport = self.make()
        self.history = prepare_history_query(HistoryQuery(BINDING, DAY))

    def make(self, client=None, binding=BINDING, **kwargs):
        return KisGetOnlyTransport(self.client if client is None else client, binding,
                                   limiter=kwargs.pop('limiter', self.limiter), monotonic=self.clock,
                                   utc_now=lambda: NOW, **kwargs)

    def blocked(self, recipe=None, code=None, transport=None):
        with self.assertRaises(Blocked) as result:
            (transport or self.transport).read(self.history if recipe is None else recipe)
        if code:
            self.assertEqual(str(result.exception), code)
        return result.exception

    def mutate_params(self, **updates):
        params = self.history.wire_parameters(); params.update(updates)
        return replace(self.history, parameters=tuple(params.items()))

    def test_all_seven_read_recipes_use_fixed_origin_and_only_get(self):
        recipes = [self.history, prepare_balance_query(BalanceQuery(BINDING, DAY)),
                   prepare_buying_power_query(BuyingPowerQuery(BINDING, DAY, '005930')),
                   prepare_sellable_query(SellableQuery(BINDING, DAY, '005930')),
                   prepare_market_query(MarketQuery('real', DAY, '005930', 'KRX')),
                   prepare_market_query(MarketQuery('real', DAY, '005930', 'NXT'), book=True),
                   prepare_holiday_query(HolidayQuery(DAY))]
        for recipe in recipes:
            with self.subTest(path=recipe.path):
                client = Client(); transport = self.make(client)
                if recipe.path.endswith(('inquire-psbl-order', 'inquire-psbl-sell', 'chk-holiday')):
                    client.response.headers['tr_cont'] = ''
                response = transport.read(recipe)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(client.calls[0].url.startswith(REAL_ORIGIN + recipe.path + '?'))
                self.assertEqual(client.calls[0].method, 'GET')
                self.clock.value += 1

    def test_demo_origin_and_environment_tr_id(self):
        binding = replace(BINDING, environment='demo'); transport = self.make(binding=binding)
        transport.read(prepare_history_query(HistoryQuery(binding, DAY)))
        self.assertTrue(self.client.calls[0].url.startswith(DEMO_ORIGIN + '/'))
        self.assertEqual(dict(self.client.calls[0].headers)['tr_id'], 'VTTC0081R')

    def test_origin_cannot_change_scheme_host_port_or_path(self):
        for origin in ['http://openapi.koreainvestment.com:9443', REAL_ORIGIN + '/', REAL_ORIGIN + '/x',
                       'https://openapi.koreainvestment.com', 'https://OPENAPI.koreainvestment.com:9443',
                       REAL_ORIGIN + '.evil.example', REAL_ORIGIN + '@evil.example',
                       REAL_ORIGIN + '?x=1', REAL_ORIGIN + '#x', DEMO_ORIGIN, 'https://127.0.0.1:9443']:
            with self.subTest(origin=origin), self.assertRaises(Blocked):
                self.make(origin=origin)
        self.assertEqual(self.client.calls, [])

    def test_explicit_exact_origin_allowed(self):
        self.make(origin=REAL_ORIGIN).read(self.history)
        self.assertEqual(len(self.client.calls), 1)

    def test_all_methods_except_exact_get_blocked_before_client(self):
        for method in ['POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS', 'get', 'GET\n', None]:
            with self.subTest(method=method):
                self.blocked(replace(self.history, method=method))
        self.assertEqual(self.client.calls, [])

    def test_token_order_cancel_and_url_variants_blocked(self):
        for path in ['/oauth2/tokenP', '/oauth2/Approval', '/uapi/domestic-stock/v1/trading/order-cash',
                     '/uapi/domestic-stock/v1/trading/order-rvsecncl', self.history.path + '/',
                     self.history.path + '?x=1', self.history.path + '#x', self.history.path + '/../order-cash',
                     self.history.path.replace('inquire', '%69nquire'), REAL_ORIGIN + self.history.path,
                     '//evil.example/path', self.history.path + '\r\nHost: evil.example']:
            with self.subTest(path=path):
                self.blocked(replace(self.history, path=path))
        self.assertEqual(self.client.calls, [])

    def test_account_product_environment_tr_and_continuation_tampering_blocked(self):
        variants = [self.mutate_params(CANO='87654321'), self.mutate_params(ACNT_PRDT_CD='02'),
                    replace(self.history, tr_id='VTTC0081R'), replace(self.history, tr_id='TTTC0012U'),
                    replace(self.history, tr_cont='N'), replace(self.history, tr_cont='D'),
                    replace(self.history, tr_cont='\r\nAuthorization: secret')]
        for recipe in variants:
            with self.subTest(recipe=repr(recipe)):
                self.blocked(recipe)
        self.assertEqual(self.client.calls, [])

    def test_extra_duplicate_missing_or_case_changed_parameters_blocked(self):
        variants = [self.mutate_params(authorization='secret'), self.mutate_params(ORD_QTY='2'),
                    replace(self.history, parameters=self.history.parameters + (('CANO', BINDING.cano),)),
                    replace(self.history, parameters=self.history.parameters[1:]),
                    replace(self.history, parameters=(('cano', BINDING.cano),) + self.history.parameters[1:])]
        for recipe in variants:
            self.blocked(recipe)
        self.assertEqual(self.client.calls, [])

    def test_history_fixed_filters_and_mismatched_dates_blocked(self):
        for update in [dict(INQR_END_DT='20261104'), dict(INQR_STRT_DT='20260231'),
                       dict(EXCG_ID_DVSN_CD='NXT'), dict(INQR_DVSN='02'), dict(PDNO='005930'),
                       dict(SLL_BUY_DVSN_CD='02'), dict(CCLD_DVSN='01')]:
            self.blocked(self.mutate_params(**update))
        self.assertEqual(self.client.calls, [])

    def test_balance_fixed_flags_cannot_change(self):
        recipe = prepare_balance_query(BalanceQuery(BINDING, DAY))
        for key, value in [('AFHR_FLPR_YN', 'Y'), ('PRCS_DVSN', '01'), ('FUND_STTL_ICLD_YN', 'Y')]:
            params = recipe.wire_parameters(); params[key] = value
            self.blocked(replace(recipe, parameters=tuple(params.items())))
        self.assertEqual(self.client.calls, [])

    def test_market_selector_not_rerouted(self):
        recipe = prepare_market_query(MarketQuery('real', DAY, '005930', 'KRX'))
        for value in ['UN', 'J\r\n', 'NX ', 'KRX']:
            params = recipe.wire_parameters(); params['FID_COND_MRKT_DIV_CODE'] = value
            self.blocked(replace(recipe, parameters=tuple(params.items())))
        self.assertEqual(self.client.calls, [])

    def test_buying_power_mode_price_and_inclusion_flags_preserved(self):
        recipe = prepare_buying_power_query(BuyingPowerQuery(BINDING, DAY, '005930'))
        for key, value in [('ORD_DVSN', '00'), ('ORD_UNPR', '0'), ('ORD_UNPR', '010000'),
                           ('ORD_UNPR', '1e4'), ('CMA_EVLU_AMT_ICLD_YN', 'Y')]:
            params = recipe.wire_parameters(); params[key] = value
            self.blocked(replace(recipe, parameters=tuple(params.items())))
        self.assertEqual(self.client.calls, [])

    def test_cursor_parameters_encoded_without_query_injection(self):
        recipe = prepare_history_query(HistoryQuery(BINDING, DAY), cursor=('opaque&x=1', '+/?#'))
        self.transport.read(recipe)
        url = self.client.calls[0].url
        self.assertIn('CTX_AREA_FK100=opaque%26x%3D1', url)
        self.assertIn('CTX_AREA_NK100=%2B%2F%3F%23', url)
        self.assertEqual(dict(self.client.calls[0].headers)['tr_cont'], 'N')

    def test_header_injection_and_invalid_cursor_types_rejected(self):
        for cursor in ['x\r\nHost:evil', None, 1, 'x' * 101, '\x00']:
            self.blocked(self.mutate_params(CTX_AREA_FK100=cursor))
        self.assertEqual(self.client.calls, [])

    def test_client_receives_immutable_security_and_resource_controls(self):
        self.transport.read(self.history)
        request = self.client.calls[0]
        self.assertTrue(request.verify_tls)
        self.assertFalse(request.follow_redirects)
        self.assertFalse(request.use_environment)
        self.assertEqual(request.automatic_retries, 0)
        self.assertLessEqual(request.chunk_bytes, request.limits.max_body_bytes)
        self.assertEqual(request.deadline_monotonic, 110)
        self.assertEqual(dict(request.headers)['accept-encoding'], 'identity')
        self.assertNotIn('authorization', dict(request.headers))
        for key, value in [('method', 'POST'), ('verify_tls', False), ('follow_redirects', True)]:
            with self.assertRaises(AttributeError):
                setattr(request, key, value)

    def test_no_credentials_environment_or_implicit_client(self):
        with self.assertRaises(Blocked):
            self.make(client=object())
        with patch.dict('os.environ', {'HTTP_PROXY': 'http://bad', 'KIS_APP_KEY': SECRET}):
            self.transport.read(self.history)
        self.assertNotIn(SECRET, repr(self.client.calls[0]))
        self.assertNotIn(SECRET, self.client.calls[0].url)
        self.assertNotIn(SECRET, str(self.client.calls[0].headers))

    def test_invalid_limits_rejected(self):
        for update in [dict(connect_seconds=0), dict(read_seconds=float('nan')),
                       dict(total_seconds=31), dict(total_seconds=1), dict(connect_seconds=True),
                       dict(max_body_bytes=True), dict(max_body_bytes=127), dict(max_body_bytes=1_048_577)]:
            with self.subTest(update=update), self.assertRaises(Blocked):
                self.make(limits=replace(ReadLimits(), **update))
        self.assertEqual(self.client.calls, [])

    def test_redirects_refused_without_reading_body(self):
        for status in [301, 302, 303, 307, 308]:
            client = Client(Response(status=status, headers={'Location': 'https://evil.example/' + SECRET}))
            self.blocked(transport=self.make(client), code='KIS_TRANSPORT_REDIRECT_BLOCKED')
            self.assertFalse(client.response.body_started)
            self.assertEqual(len(client.calls), 1); self.assertEqual(client.response.closed, 1)
            self.clock.value += 1

    def test_location_on_success_blocked(self):
        self.client.response.headers['Location'] = 'https://evil.example'
        self.blocked(code='KIS_TRANSPORT_REDIRECT_BLOCKED')
        self.assertFalse(self.client.response.body_started)

    def test_actual_response_metadata_must_match_request(self):
        for key, value in [('tls_verified', False), ('tls_verified', 1), ('redirect_count', 1),
                           ('redirect_count', False), ('request_method', 'POST'),
                           ('url', 'https://evil.example'), ('url', REAL_ORIGIN + '/wrong')]:
            client = Client(); setattr(client.response, key, value)
            self.blocked(transport=self.make(client), code='KIS_TRANSPORT_PROVENANCE_INVALID')
            self.assertFalse(client.response.body_started); self.assertEqual(client.response.closed, 1)
            self.clock.value += 1

    def test_http_and_auth_errors_do_not_retry_or_parse_error_body(self):
        for status in [400, 401, 403, 404, 408, 500, 503]:
            client = Client(Response(body=SECRET.encode(), status=status))
            error = self.blocked(transport=self.make(client), code='KIS_TRANSPORT_HTTP_ERROR')
            self.assertNotIn(SECRET, str(error))
            self.assertFalse(client.response.body_started); self.assertEqual(len(client.calls), 1)
            self.clock.value += 1

    def test_status_type_validation(self):
        for status in [True, '200', 0, 600]:
            client = Client(Response(status=status))
            self.blocked(transport=self.make(client), code='KIS_TRANSPORT_STATUS_INVALID')
            self.clock.value += 1

    def test_header_collisions_size_controls_and_nonstrings_blocked(self):
        variants = [{'Content-Type': 'application/json', 'content-type': 'text/html'},
                    {'x': 'y' * 8193}, {'x': 1}, {'\r\nx': 'y'},
                    {f'header_{i}': 'v' for i in range(65)},
                    {f'header_{i}': 'v' * 4000 for i in range(5)}]
        for headers in variants:
            client = Client(Response(headers=headers))
            self.blocked(transport=self.make(client), code='KIS_TRANSPORT_HEADERS_INVALID')
            self.clock.value += 1

    def test_content_type_and_encoding_restricted(self):
        for key, value in [('Content-Type', 'text/html'), ('Content-Type', 'application/json;charset=latin1'),
                           ('Content-Encoding', 'gzip'), ('Content-Encoding', 'br')]:
            client = Client(); client.response.headers[key] = value
            self.blocked(transport=self.make(client))
            self.assertFalse(client.response.body_started)
            self.clock.value += 1

    def test_utf8_json_content_type_case_insensitive(self):
        self.client.response.headers['Content-Type'] = 'Application/JSON; Charset=UTF-8'
        self.assertEqual(self.transport.read(self.history).status_code, 200)

    def test_response_tr_id_mismatch_blocked(self):
        self.client.response.headers['tr_id'] = 'VTTC0081R'
        self.blocked(code='KIS_TRANSPORT_PROVENANCE_INVALID')

    def test_response_headers_are_filtered_and_raw_body_repr_hidden(self):
        self.client.response.body = json.dumps({'private': SECRET}).encode()
        self.client.response.headers.update({'Set-Cookie': SECRET, 'X-Diagnostic': SECRET})
        response = self.transport.read(self.history)
        self.assertEqual(response.headers, {'tr_cont': 'D'})
        self.assertNotIn(SECRET, repr(response))
        self.assertEqual(response.body['private'], SECRET)  # not a redacted data serializer

    def test_declared_body_limit_blocks_before_read(self):
        self.client.response.headers['Content-Length'] = '1048577'
        self.blocked(code='KIS_TRANSPORT_BODY_LIMIT')
        self.assertFalse(self.client.response.body_started)

    def test_content_length_malformed_or_transfer_encoding_conflict_blocked(self):
        for value in ['-1', '1.0', '99999999999', ' 1', 'abc']:
            client = Client(); client.response.headers['Content-Length'] = value
            self.blocked(transport=self.make(client), code='KIS_TRANSPORT_BODY_LIMIT')
            self.clock.value += 1
        self.client.response.headers.update({'Content-Length': '25', 'Transfer-Encoding': 'chunked'})
        self.blocked(code='KIS_TRANSPORT_BODY_LIMIT')

    def test_content_length_mismatch_rejected(self):
        self.client.response.headers['Content-Length'] = '1'
        self.blocked(code='KIS_TRANSPORT_BODY_LENGTH_INVALID')

    def test_streamed_body_bounded_even_without_content_length(self):
        self.client.response.chunks = [b'a' * 100, b'b' * 100]
        self.blocked(transport=self.make(limits=ReadLimits(max_body_bytes=128)), code='KIS_TRANSPORT_BODY_LIMIT')
        self.assertEqual(self.client.response.closed, 1)

    def test_oversized_empty_or_nonbytes_chunks_rejected(self):
        for chunk in [b'x' * 16385, b'', 'text', bytearray(b'x'), None]:
            client = Client(); client.response.chunks = [chunk]
            self.blocked(transport=self.make(client), code='KIS_TRANSPORT_BODY_LIMIT')
            self.clock.value += 1

    def test_valid_body_exact_limit_and_one_byte_chunks(self):
        body = b'{"value":"' + b'x' * 116 + b'"}'
        self.assertEqual(len(body), 128)
        self.client.response.chunks = [body[i:i+1] for i in range(len(body))]
        self.assertEqual(len(self.make(limits=ReadLimits(max_body_bytes=128)).read(self.history).body['value']), 116)

    def test_json_duplicate_case_collisions_and_unsupported_numbers_rejected(self):
        for body in [b'{"x":1,"x":2}', b'{"X":1,"x":2}', b'{"n":{"X":1,"x":2}}',
                     b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1.5}', b'{"x":1e10}',
                     b'{"x":' + b'9' * 33 + b'}', b'[]', b'null', b'{"x":"\xff"}',
                     b'{bad', b'{"x":1} trailing']:
            client = Client(Response(body=body))
            self.blocked(transport=self.make(client))
            self.clock.value += 1

    def test_deep_json_rejected_before_unbounded_recursion(self):
        self.client.response.body = b'{"x":' + b'[' * 33 + b'0' + b']' * 33 + b'}'
        self.blocked(code='KIS_TRANSPORT_JSON_INVALID')

    def test_json_depth_ignores_escaped_quotes_and_brackets(self):
        value = '[{\\"' * 200
        self.client.response.body = json.dumps({'value': value}).encode()
        self.assertEqual(self.transport.read(self.history).body['value'], value)

    def test_connect_read_and_close_deadlines_checked(self):
        for stage in ['open', 'read', 'close']:
            client = Client()
            def advance(): self.clock.value += 11
            if stage == 'open': client.open_hook = advance
            elif stage == 'read': client.response.read_hook = advance
            else: client.response.close_hook = advance
            self.blocked(transport=self.make(client), code='KIS_TRANSPORT_DEADLINE_EXCEEDED')
            self.assertGreaterEqual(client.response.closed, 1)
            self.clock.value += 1

    def test_trickle_stream_exceeding_total_deadline_rejected(self):
        def trickle():
            for chunk in [b'{', b'"x":', b'1', b'}']:
                self.clock.value += 3
                yield chunk
        self.client.response.chunks = trickle()
        self.blocked(code='KIS_TRANSPORT_DEADLINE_EXCEEDED')

    def test_invalid_or_backwards_clock_fail_closed(self):
        for value in [float('nan'), -1, True]:
            self.clock.value = value
            self.blocked()
        self.assertEqual(self.client.calls, [])
        self.clock.value = 100
        self.client.open_hook = lambda: setattr(self.clock, 'value', 99)
        self.blocked(code='KIS_TRANSPORT_DEADLINE_EXCEEDED')

    def test_client_exception_traceback_redacted_and_no_retry(self):
        def fail(): raise RuntimeError(SECRET)
        self.client.open_hook = fail
        try:
            self.transport.read(self.history)
        except Blocked as exc:
            rendered = ''.join(traceback.format_exception(exc))
        self.assertNotIn(SECRET, rendered)
        self.assertIn('KIS_TRANSPORT_READ_FAILED', rendered)
        self.assertEqual(len(self.client.calls), 1)

    def test_injected_private_exception_cannot_spoof_safe_error(self):
        from oneil_execution_v2.kis_transport import _Failure
        def fail(): raise _Failure(SECRET)
        for stage in ['open', 'read', 'close']:
            client = Client()
            if stage == 'open': client.open_hook = fail
            else: setattr(client.response, stage + '_hook', fail)
            error = self.blocked(transport=self.make(client), code='KIS_TRANSPORT_READ_FAILED')
            self.assertNotIn(SECRET, str(error))
            self.clock.value += 1
        self.transport._utc_now = fail
        with self.assertRaises(Blocked) as outcome:
            self.transport.capture(self.history)
        self.assertNotIn(SECRET, str(outcome.exception))
        class BadClient:
            @property
            def open_get(self): raise _Failure(SECRET)
        with self.assertRaises(Blocked) as outcome:
            self.make(BadClient())
        self.assertNotIn(SECRET, str(outcome.exception))

    def test_stream_and_close_exceptions_redacted(self):
        def fail(): raise RuntimeError(SECRET)
        for stage in ['read', 'close']:
            client = Client(); setattr(client.response, stage + '_hook', fail)
            error = self.blocked(transport=self.make(client), code='KIS_TRANSPORT_READ_FAILED')
            self.assertNotIn(SECRET, str(error)); self.assertGreaterEqual(client.response.closed, 1)
            self.clock.value += 1

    def test_sensitive_url_binding_and_client_hidden_in_repr(self):
        self.transport.read(self.history)
        for value in [self.transport, self.client.calls[0]]:
            self.assertNotIn(BINDING.cano, repr(value))
            self.assertNotIn('CANO', repr(value))
        self.assertIn(BINDING.cano, self.client.calls[0].url)

    def test_local_rate_limit_fails_fast_and_reopens_after_interval(self):
        self.transport.read(self.history)
        self.blocked(code='KIS_TRANSPORT_RATE_LIMITED')
        self.clock.value += 1
        self.transport.read(self.history)
        self.assertEqual(len(self.client.calls), 2)

    def test_limits_are_shared_across_instances_and_failed_reads(self):
        self.client.response.status_code = 500
        self.blocked(code='KIS_TRANSPORT_HTTP_ERROR')
        other = Client()
        self.blocked(transport=self.make(other), code='KIS_TRANSPORT_RATE_LIMITED')
        self.assertEqual(other.calls, [])

    def test_default_limiter_is_process_shared(self):
        first = KisGetOnlyTransport(Client(), BINDING)
        second = KisGetOnlyTransport(Client(), replace(BINDING, environment='demo'))
        self.assertIs(first._limiter, second._limiter)

    def test_concurrent_inflight_read_rejected_even_after_interval(self):
        other = Client()
        def attempt():
            self.clock.value += 2
            self.blocked(transport=self.make(other), code='KIS_TRANSPORT_RATE_LIMITED')
        self.client.open_hook = attempt
        self.transport.read(self.history)
        self.assertEqual(other.calls, [])

    def test_remote_429_cooldown_without_sleep_or_retry(self):
        self.client.response.status_code = 429
        self.client.response.headers['Retry-After'] = '120'
        self.blocked(code='KIS_TRANSPORT_REMOTE_RATE_LIMITED')
        self.assertFalse(self.client.response.body_started)
        self.clock.value += 119
        self.blocked(code='KIS_TRANSPORT_RATE_LIMITED')
        self.clock.value += 1
        self.client.response.status_code = 200
        self.transport.read(self.history)
        self.assertEqual(len(self.client.calls), 2)

    def test_remote_cooldown_never_shortens_numeric_or_date_retry_after(self):
        for value in ['7200', 'Thu, 05 Nov 2026 03:00:00 GMT']:
            limiter = ReadRateLimiter(monotonic=self.clock)
            client = Client(Response(status=429)); client.response.headers['Retry-After'] = value
            transport = self.make(client, limiter=limiter)
            self.blocked(transport=transport, code='KIS_TRANSPORT_REMOTE_RATE_LIMITED')
            self.clock.value += 7199
            self.blocked(transport=transport, code='KIS_TRANSPORT_RATE_LIMITED')
            self.clock.value += 1; client.response.status_code = 200
            transport.read(self.history)
            self.assertEqual(len(client.calls), 2)

    def test_missing_malformed_or_overlarge_retry_after_latches_limiter(self):
        for value in [None, '-1', 'bogus', '604801', '9' * 100]:
            limiter = ReadRateLimiter(monotonic=self.clock)
            client = Client(Response(status=429))
            if value is not None: client.response.headers['Retry-After'] = value
            transport = self.make(client, limiter=limiter)
            self.blocked(transport=transport, code='KIS_TRANSPORT_REMOTE_RATE_LIMITED')
            self.clock.value += 1_000_000
            self.blocked(transport=transport, code='KIS_TRANSPORT_RATE_LIMITED')
            self.assertEqual(len(client.calls), 1)

    def test_small_or_past_retry_after_has_conservative_minimum(self):
        for value in ['0', '1', 'Wed, 21 Oct 2015 07:28:00 GMT']:
            limiter = ReadRateLimiter(monotonic=self.clock)
            client = Client(Response(status=429)); client.response.headers['Retry-After'] = value
            transport = self.make(client, limiter=limiter)
            self.blocked(transport=transport, code='KIS_TRANSPORT_REMOTE_RATE_LIMITED')
            self.clock.value += 59
            self.blocked(transport=transport, code='KIS_TRANSPORT_RATE_LIMITED')
            self.clock.value += 1; client.response.status_code = 200
            transport.read(self.history)

    def test_invalid_utc_clock_for_server_date_latches_without_leak(self):
        client = Client(Response(status=429))
        client.response.headers['Retry-After'] = 'Thu, 05 Nov 2026 03:00:00 GMT'
        transport = self.make(client)
        def fail(): raise ValueError(SECRET)
        transport._utc_now = fail
        self.blocked(transport=transport, code='KIS_TRANSPORT_REMOTE_RATE_LIMITED')
        self.clock.value += 1_000_000
        self.blocked(transport=transport, code='KIS_TRANSPORT_RATE_LIMITED')
        self.assertEqual(len(client.calls), 1)

    def test_holiday_once_per_day_and_no_continuation_exception(self):
        holiday = prepare_holiday_query(HolidayQuery(DAY))
        self.client.response.headers['tr_cont'] = ''
        self.transport.read(holiday)
        self.clock.value += 1
        self.blocked(holiday, code='KIS_TRANSPORT_HOLIDAY_RATE_LIMITED')
        params = holiday.wire_parameters(); params.update(CTX_AREA_FK='next', CTX_AREA_NK='page')
        continuation = replace(holiday, parameters=tuple(params.items()), tr_cont='N')
        self.blocked(continuation, code='KIS_TRANSPORT_RECIPE_INVALID')
        self.assertEqual(len(self.client.calls), 1)
        self.clock.value += 86399
        self.client.response.url = None
        self.transport.read(holiday)
        self.assertEqual(len(self.client.calls), 2)

    def test_portal_nonpaginated_endpoints_reject_continuation_before_body(self):
        recipes = (prepare_buying_power_query(BuyingPowerQuery(BINDING, DAY, '005930')),
                   prepare_sellable_query(SellableQuery(BINDING, DAY, '005930')),
                   prepare_holiday_query(HolidayQuery(DAY)))
        for recipe in recipes:
            for marker in ('M', 'F', 'D', 'E', 'UNKNOWN'):
                with self.subTest(path=recipe.path, marker=marker):
                    client = Client(); client.response.headers['tr_cont'] = marker
                    transport = self.make(client, limiter=ReadRateLimiter(monotonic=self.clock))
                    self.blocked(recipe, code='KIS_TRANSPORT_CONTINUATION_UNSUPPORTED', transport=transport)
                    self.assertFalse(client.response.body_started)
                    self.assertEqual(len(client.calls), 1)

    def test_blank_buying_power_price_is_serialized_without_invented_price(self):
        recipe = prepare_buying_power_query(BuyingPowerQuery(BINDING, DAY, '005930'))
        self.client.response.headers['tr_cont'] = ''
        self.transport.read(recipe)
        self.assertIn('ORD_UNPR=&', self.client.calls[0].url)
        self.assertNotIn('10000', self.client.calls[0].url)

    def test_demo_holiday_and_sellable_not_invented(self):
        transport = self.make(binding=replace(BINDING, environment='demo'))
        self.blocked(prepare_holiday_query(HolidayQuery(DAY)), transport=transport)
        self.blocked(prepare_sellable_query(SellableQuery(BINDING, DAY, '005930')), transport=transport)
        self.assertEqual(self.client.calls, [])

    def test_rate_limit_config_rejected(self):
        for kwargs in [dict(interval_seconds=0), dict(max_requests=3), dict(max_requests=True),
                       dict(interval_seconds=float('inf')), dict(monotonic=None)]:
            with self.assertRaises(Blocked):
                ReadRateLimiter(**kwargs)

    def test_read_history_rejects_other_allowed_endpoint(self):
        with self.assertRaises(Blocked):
            self.transport.read_history(prepare_balance_query(BalanceQuery(BINDING, DAY)))
        self.assertEqual(self.client.calls, [])

    def test_history_parser_works_and_does_not_promote_absence_to_order_result(self):
        history = KisReadOnlyAdapter(self.transport, lambda: NOW).history(HistoryQuery(BINDING, DAY))
        self.assertTrue(history.complete)
        self.assertEqual(history.orders, ())

    def test_fast_multipage_history_fails_closed_at_default_rate_limit(self):
        # The parser requests continuation immediately; transport does not sleep
        # or retry. A caller-paced collector is a separate, unimplemented layer.
        self.client.response.body = json.dumps({'rt_cd': '0', 'output1': [],
            'ctx_area_fk100': 'a', 'ctx_area_nk100': 'b'}).encode('ascii')
        self.client.response.headers['tr_cont'] = 'M'
        history = KisReadOnlyAdapter(self.transport, lambda: NOW).history(HistoryQuery(BINDING, DAY))
        self.assertFalse(history.complete)
        self.assertEqual(history.reason, 'KIS_HISTORY_TRANSPORT_OR_PARSE_ERROR')
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.response.closed, 1)

    def test_capture_keeps_offline_transcript_and_non_executable_observation(self):
        recipe = prepare_market_query(MarketQuery('real', DAY, '005930', 'KRX'))
        self.client.response.body = json.dumps({'rt_cd': '0', 'output': {
            'stck_shrn_iscd': '005930', 'stck_prpr': '10000', 'temp_stop_yn': 'N', 'iscd_stat_cls_code': '00'}}).encode()
        capture = self.transport.capture(recipe)
        result = KisOfflineEvidenceAdapter(NOW, max_age_seconds=10).price(MarketQuery('real', DAY, '005930', 'KRX'), capture)
        self.assertTrue(result.valid, result.reason)
        self.assertFalse(result.value.live_eligible); self.assertFalse(result.value.provenance_authenticated)
        self.assertFalse(result.value.executable)

    def test_capture_rejects_invalid_initial_clock_before_client(self):
        self.transport._utc_now = lambda: NOW.replace(tzinfo=None)
        with self.assertRaisesRegex(Blocked, 'KIS_TRANSPORT_CAPTURE_CLOCK_INVALID'):
            self.transport.capture(self.history)
        self.assertEqual(self.client.calls, [])

    def test_capture_rejects_reverse_clock_without_exposing_payload(self):
        times = iter([NOW, NOW - timedelta(seconds=1)])
        self.transport._utc_now = lambda: next(times)
        with self.assertRaisesRegex(Blocked, 'KIS_TRANSPORT_CAPTURE_CLOCK_INVALID'):
            self.transport.capture(self.history)

    def test_submit_and_policy_evidence_always_block_without_client(self):
        for method in ['submit', 'policy_evidence']:
            with self.assertRaises(Blocked):
                getattr(self.transport, method)()
        self.assertEqual(self.client.calls, [])

    def test_source_has_no_network_credential_database_or_production_wiring(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / 'oneil_execution_v2/kis_transport.py').read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom): imports.add(node.module)
        self.assertEqual(imports, {'collections', 'dataclasses', 'datetime', 'json', 'math', 're',
                                   'threading', 'time', 'typing', 'urllib.parse', 'email.utils', 'domain',
                                   'kis_adapter', 'kis_evidence'})
        for directory in ['oneil_runtime_v2', 'oneil_control_v2', 'oneil_policy_v2']:
            for file in (root / directory).glob('*.py'):
                self.assertNotIn('kis_transport', file.read_text())


if __name__ == '__main__':
    unittest.main()
