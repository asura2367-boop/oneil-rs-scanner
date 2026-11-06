"""Exact tokenP flow and provider/source composition, with all I/O synthetic."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pickle
import traceback
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import AccountBinding, HistoryQuery, prepare_history_query
from oneil_execution_v2.kis_authenticated import Cancellation, KisAuthenticatedReadSource
from oneil_execution_v2.kis_local_authorization import LocalPermission
from oneil_execution_v2.kis_transport import ReadRateLimiter
from oneil_execution_v2.kis_windows_credentials import AppCredentials, WindowsCredentialProvider, WindowsCredentialStore, _encode
from oneil_execution_v2 import kis_token_issuer as issuer
from oneil_execution_v2 import kis_authenticated_https as secure

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
BINDING = AccountBinding('a'*64, 'real', '12345678', '01')
KEY = 'SYNTHETIC_APP_KEY_ONLY'; SECRET = 'SYNTHETIC_APP_SECRET_ONLY'; TOKEN = 'SYNTHETIC_BEARER_ONLY'
CREDS = AppCredentials(KEY, SECRET)


def payload(**values):
    return {'access_token': TOKEN, 'token_type': 'Bearer', 'expires_in': 3600,
            'access_token_token_expired': '2026-11-05 10:00:00', **values}


def response(body=None, *, status='200 OK', headers=''):
    if body is None: body = json.dumps(payload()).encode()
    if type(body) is dict: body = json.dumps(body).encode()
    return (f'HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n'
            + headers + '\r\n').encode() + body


class Stream:
    def __init__(self, wire=None):
        self.wire = response() if wire is None else wire
        self.sent = []; self.closed = 0; self.hook = None; self.close_hook = None
    def send(self, data):
        self.sent.append(data)
        if self.hook: self.hook()
    def settimeout(self, value): pass
    def recv(self, maximum):
        chunk, self.wire = self.wire[:maximum], self.wire[maximum:]
        return chunk
    def close(self):
        self.closed += 1
        if self.close_hook: self.close_hook()


class TokenIssuerTests(unittest.TestCase):
    issuer_type = issuer.KisTokenIssuer
    platform = 'win32'
    blocked_platform = 'linux'
    connector_module = secure
    def setUp(self):
        self.tick = 100.; self.now = NOW; self.token = Cancellation(); self.stream = Stream()
        self.patches = [patch.object(issuer.sys, 'platform', self.platform),
            patch.object(issuer, '_ISSUE_LIMITER', issuer._IssueLimiter()),
            patch.object(self.connector_module, '_connect', return_value=self.stream),
            patch('socket.socket', side_effect=AssertionError('REAL_SOCKET_FORBIDDEN')),
            patch('socket.getaddrinfo', side_effect=AssertionError('REAL_DNS_FORBIDDEN'))]
        _, _, self.connect, *_ = [p.start() for p in self.patches]
        for p in self.patches: self.addCleanup(p.stop)
        self.clock = lambda: self.tick
        self.subject = self.issuer_type(monotonic=self.clock, utc_now=lambda: self.now)
        self.permission = self.grant()

    def grant(self, binding=BINDING, actions=('issue_token', 'read_app_credentials', 'authenticated_get')):
        return LocalPermission(binding, actions=actions, issued_at=NOW,
            expires_at=NOW+timedelta(minutes=5), monotonic=self.clock, utc_now=lambda: self.now)

    def issue(self, **updates):
        args = dict(permission=self.permission, minimum_validity_seconds=60,
                    deadline_monotonic=self.tick+10, cancellation=self.token)
        args.update(updates)
        return self.subject.issue(BINDING, CREDS, **args)

    def test_constructor_is_inactive(self):
        self.issuer_type(); self.connect.assert_not_called()

    def test_only_exact_real_token_post_and_minimum_body(self):
        lease = self.issue()
        self.assertEqual(lease.binding, BINDING); self.assertEqual(lease.bearer, TOKEN)
        self.assertEqual(lease.expires_at, NOW+timedelta(hours=1))
        self.assertEqual(len(self.stream.sent), 1); sent = self.stream.sent[0]
        head, body = sent.split(b'\r\n\r\n', 1)
        self.assertTrue(head.startswith(b'POST /oauth2/tokenP HTTP/1.1\r\n'))
        self.assertIn(b'Host: openapi.koreainvestment.com:9443', head)
        self.assertEqual(json.loads(body), {'grant_type':'client_credentials','appkey':KEY,'appsecret':SECRET})
        self.assertNotIn(BINDING.cano.encode(), sent)
        for forbidden in (b'Authorization:', b'Cookie:', b'order-cash', b'hashkey'):
            self.assertNotIn(forbidden, sent)
        self.assertEqual(self.stream.closed, 1)

    def test_demo_separates_host_and_binding(self):
        binding = replace(BINDING, environment='demo')
        lease = self.subject.issue(binding, CREDS, permission=self.grant(binding),
            minimum_validity_seconds=60, deadline_monotonic=110, cancellation=self.token)
        self.assertEqual(lease.binding, binding)
        self.assertEqual(self.connect.call_args.args[:2], ('openapivts.koreainvestment.com', 29443))
        self.assertIn(b'Host: openapivts.koreainvestment.com:29443', self.stream.sent[0])

    def test_permission_scope_and_no_action_block_before_network(self):
        for permission in (None, self.grant(actions=()), self.grant(replace(BINDING, cano='87654321'))):
            with self.assertRaises(Blocked): self.issue(permission=permission)
        self.connect.assert_not_called()

    def test_non_windows_and_precancel_block_before_network(self):
        with patch.object(issuer.sys, 'platform', self.blocked_platform):
            with self.assertRaisesRegex(Blocked, 'PLATFORM_BLOCKED'): self.issue()
        self.token.cancel()
        with self.assertRaisesRegex(Blocked, 'CANCELLED'): self.issue()
        self.connect.assert_not_called()

    def test_token_permission_is_consumed_even_on_uncertain_network_failure(self):
        self.connect.side_effect = OSError(SECRET)
        with self.assertRaises(Blocked): self.issue()
        self.tick += 61
        with self.assertRaisesRegex(Blocked, 'PERMISSION_REQUIRED'): self.issue()
        self.assertEqual(self.connect.call_count, 1)

    def test_process_rate_guard_rejects_fresh_permission(self):
        self.issue()
        with self.assertRaisesRegex(Blocked, 'RATE_LIMITED'): self.issue(permission=self.grant())
        self.assertEqual(self.connect.call_count, 1)

    def test_fresh_permission_after_cooldown_can_issue_again(self):
        self.issue(); self.tick += 61; self.now += timedelta(seconds=61)
        self.stream.wire = response()
        self.issue(permission=self.grant()); self.assertEqual(self.connect.call_count, 2)

    def test_absolute_expiry_caps_old_reissued_token(self):
        self.stream.wire = response(payload(expires_in=86400, access_token_token_expired='2026-11-05 09:02:00'))
        lease = self.issue(); self.assertEqual(lease.expires_at, NOW+timedelta(minutes=2))

    def test_transit_does_not_start_a_fresh_lifetime(self):
        def advance(): self.tick += 2; self.now += timedelta(seconds=2)
        self.stream.hook = advance
        lease = self.issue(); self.assertEqual(lease.expires_at, NOW+timedelta(hours=1))

    def test_frozen_utc_transit_cannot_overstate_remaining_validity(self):
        self.stream.wire = response(payload(expires_in=60, access_token_token_expired='2026-11-05 09:01:00'))
        self.stream.hook = lambda: setattr(self, 'tick', 109.)
        with self.assertRaisesRegex(Blocked, 'EXPIRED'):
            self.issue(minimum_validity_seconds=59)

    def test_frozen_utc_transit_is_carried_into_returned_expiry(self):
        self.stream.wire = response(payload(expires_in=120, access_token_token_expired='2026-11-05 09:02:00'))
        self.stream.hook = lambda: setattr(self, 'tick', 109.)
        lease = self.issue()
        self.assertEqual(lease.expires_at, NOW+timedelta(seconds=111))

    def test_unsafe_malformed_and_short_tokens_fail_closed(self):
        cases = [payload(access_token='bad\r\ninjection'), payload(expires_in=True),
                 payload(expires_in=86401), payload(expires_in='3600'), payload(token_type='Basic'),
                 payload(access_token_token_expired='2026-11-05 09:00:30'),
                 payload(access_token_token_expired='2026-99-05 09:00:00'),
                 {**payload(), 'unexpected':SECRET}, {'error':SECRET},
                 b'{"access_token":"a","access_token":"b"}', b'{}', b'null']
        for body in cases:
            self.stream.wire = response(body)
            with patch.object(issuer, '_ISSUE_LIMITER', issuer._IssueLimiter()):
                with self.assertRaises(Blocked): self.issue(permission=self.grant())
        self.assertEqual(self.stream.closed, len(cases))

    def test_401_429_500_never_retry_or_refresh(self):
        for status in ('401 Unauthorized', '429 Too Many Requests', '500 Internal Server Error'):
            self.stream.wire = response(b'SYNTHETIC_ERROR_BODY', status=status)
            with patch.object(issuer, '_ISSUE_LIMITER', issuer._IssueLimiter()):
                with self.assertRaisesRegex(Blocked, 'HTTP_FAILED'): self.issue(permission=self.grant())
        self.assertEqual(self.connect.call_count, 3); self.assertEqual(len(self.stream.sent), 3)

    def test_redirect_chunked_and_body_limits_reject(self):
        for wire in (response(headers='Location: https://evil.example/\r\n'),
                     response(headers='Transfer-Encoding: chunked\r\n'),
                     response(b'x'*16385), response(b'', status='302 Found')):
            self.stream.wire = wire
            with patch.object(issuer, '_ISSUE_LIMITER', issuer._IssueLimiter()):
                with self.assertRaises(Blocked): self.issue(permission=self.grant())

    def test_cancel_revoke_deadline_and_wall_rollback_after_send_drop_lease(self):
        for hook in (self.token.cancel, self.permission.revoke,
                     lambda: setattr(self, 'tick', 111.),
                     lambda: setattr(self, 'now', NOW-timedelta(seconds=1))):
            self.token = Cancellation(); self.permission = self.grant()
            # Fresh closures must refer to the current permission/token.
            if hook.__name__ == 'cancel': hook = self.token.cancel
            if hook.__name__ == 'revoke': hook = self.permission.revoke
            self.stream.hook = hook; self.stream.wire = response()
            with patch.object(issuer, '_ISSUE_LIMITER', issuer._IssueLimiter()):
                with self.assertRaises(Blocked): self.issue()
            self.tick = 100.; self.now = NOW
        self.assertEqual(self.stream.closed, 4)

    def test_close_elapsed_is_included_in_token_validity(self):
        self.stream.wire = response(payload(expires_in=60, access_token_token_expired='2026-11-05 09:01:00'))
        self.stream.close_hook = lambda: setattr(self, 'tick', 109.)
        with self.assertRaisesRegex(Blocked, 'EXPIRED'):
            self.issue(minimum_validity_seconds=59)

    def test_cancellation_during_close_drops_success(self):
        self.stream.close_hook = self.token.cancel
        with self.assertRaises(Blocked): self.issue()

    def test_public_errors_never_include_secrets(self):
        self.connect.side_effect = RuntimeError(SECRET)
        try: self.issue()
        except Blocked:
            rendered = traceback.format_exc()
            for value in (KEY, SECRET, TOKEN): self.assertNotIn(value, rendered)
        else: self.fail('expected failure')
        with self.assertRaises(TypeError): pickle.dumps(self.subject)

    def test_complete_provider_issuer_get_source_remains_nonpermitting(self):
        class StoreBackend:
            def read(self, target): return _encode(CREDS)
        store = WindowsCredentialStore(backend=StoreBackend(), monotonic=lambda: self.tick)
        provider = WindowsCredentialProvider(store, self.subject, self.permission)
        client = secure.KisAuthenticatedHttpsGetClient(BINDING, permission=self.permission,
                                                       monotonic=lambda: self.tick)
        source = KisAuthenticatedReadSource(BINDING, provider, client, limiter=ReadRateLimiter(),
                    monotonic=lambda: self.tick, utc_now=lambda: self.now)
        token_stream = self.stream
        get_stream = Stream(response({'rt_cd':'0', 'output1':[]}, headers='tr_cont: D\r\n'))
        self.connect.side_effect = [token_stream, get_stream]
        result = source.capture(prepare_history_query(HistoryQuery(BINDING, '2026-11-05')))
        self.assertEqual(result.response.body['rt_cd'], '0')
        self.assertEqual(len(token_stream.sent), 1); self.assertEqual(len(get_stream.sent), 1)
        self.assertTrue(token_stream.sent[0].startswith(b'POST /oauth2/tokenP '))
        self.assertTrue(get_stream.sent[0].startswith(b'GET /uapi/'))
        self.assertIn(('authorization: Bearer '+TOKEN).encode(), get_stream.sent[0])
        with self.assertRaises(Blocked): source.policy_evidence()
        with self.assertRaises(Blocked): source.submit()
        source.close()


if __name__ == '__main__': unittest.main()
