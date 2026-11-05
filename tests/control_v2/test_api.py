"""Network-free boundary tests with a scripted, non-durable core test double."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import UUID

# Versioned projector is bundled in this checkout; no sibling path dependency.
from oneil_monitor_v2 import project_status
from oneil_control_v2 import ControlAPI, ControlState, SessionRecord, Settings
from oneil_control_v2.app import COOKIE
from oneil_control_v2.state import APIError, canonical, digest
from oneil_execution_v2.domain import Blocked, require

CLOCK = int(datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc).timestamp())
TOKEN, CSRF = 't' * 43, 'c' * 43  # Public test fixtures, not usable credentials.
ACCOUNT = 'a' * 64


class ScriptedBackend:
    def __init__(self):
        self.row = {'mode': 'SIMULATION', 'revision': 7, 'halted': False, 'policy': {'version': 'test-v1'}}
        self.calls, self.unknown, self.raise_after_effect = [], False, False
        self.lock = threading.Lock()

    def status(self, account):
        self.calls.append(('read', account))
        now = datetime.fromtimestamp(CLOCK, timezone.utc)
        return {'mode': 'SIMULATION', 'live_capable': False, 'halted': self.row['halted'],
                'control_revision': self.row['revision'], 'as_of': now, 'requests': [],
                'health': {'heartbeat_at': now - timedelta(seconds=5), 'status': 'SIMULATION'},
                'broker_reconciled_at': now - timedelta(seconds=8)}

    def control(self, account):
        return deepcopy(self.row)

    def unresolved(self, account):
        return self.unknown

    def _effect(self, kind, principal, account, revision, extra=None):
        with self.lock:
            require(self.row['revision'] == revision, 'CONTROL_REVISION_CONFLICT')
            if kind == 'resume':
                require(not self.unknown, 'UNRESOLVED_ORDER')
            self.calls.append((kind, principal, account, revision, extra))
            if kind in ('halt', 'resume'):
                self.row['revision'] += 1
                self.row['halted'] = kind == 'halt'
            if self.raise_after_effect:
                raise RuntimeError('sensitive broker diagnostic must not escape')
            return self.row['revision']

    def confirm_order(self, principal, account, order, expected, key):
        self._effect('order', principal, account, expected, (order, key))
        return {'request_id': str(UUID(int=1)), 'state': 'QUEUED', 'mode': 'SIMULATION', 'live_capable': False}

    def halt(self, principal, account, expected):
        return self._effect('halt', principal, account, expected)

    def resume_simulation(self, principal, account, expected):
        return self._effect('resume', principal, account, expected)

    def cancel_pending(self, principal, account, request_id, expected):
        self._effect('cancel', principal, account, expected, request_id)
        return {'request_id': request_id, 'state': 'CANCELLED'}


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ControlState(Path(self.tmp.name) / 'control.sqlite3')
        self.state.initialize()
        self.record = SessionRecord(digest(TOKEN), digest(CSRF), 'test-owner', ACCOUNT,
                                    frozenset({'monitor.read', 'simulation.command'}), CLOCK + 300, CLOCK - 60)
        self.state.install_session_record(self.record)
        self.backend, self.now, self.counter = ScriptedBackend(), CLOCK, 0
        self.app = ControlAPI(state=self.state, backend=self.backend, projector=project_status,
                              settings=Settings('https://monitor.invalid', 10, True), clock=lambda: self.now)

    def env(self, method='GET', path='/api/v2/status', body=None, key=None, nonce=None, **overrides):
        self.counter += 1
        raw = canonical(body).encode() if type(body) is dict else (body or b'')
        result = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '',
                  'wsgi.url_scheme': 'https', 'HTTP_HOST': 'monitor.invalid',
                  'HTTP_ORIGIN': 'https://monitor.invalid', 'HTTP_SEC_FETCH_SITE': 'same-origin',
                  'HTTP_COOKIE': f'{COOKIE}={TOKEN}', 'HTTP_X_CSRF_TOKEN': CSRF,
                  'HTTP_X_REQUEST_NONCE': nonce or f'nonce{self.counter:020d}',
                  'HTTP_X_REQUEST_TIMESTAMP': str(self.now),
                  'HTTP_IDEMPOTENCY_KEY': key or f'key{self.counter:020d}',
                  'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': BytesIO(raw)}
        result.update(overrides)
        return result

    def call(self, env):
        saved = []
        raw = b''.join(self.app(env, lambda status, headers: saved.append((int(status.split()[0]), dict(headers)))))
        return saved[0][0], json.loads(raw), saved[0][1]

    def post(self, body, path='/api/v2/commands', **kwargs):
        return self.call(self.env('POST', path, body, **kwargs))

    def preview(self, action='order', **kwargs):
        data = {'action': action, 'expected_revision': 7}
        if action == 'order':
            data['order'] = {'ticker': '005930', 'side': 'BUY', 'venue': 'KRX', 'quantity': 2,
                             'limit_price': 10000, 'expires_at': datetime.fromtimestamp(CLOCK + 100, timezone.utc).isoformat()}
        data.update(kwargs)
        return self.post(data, '/api/v2/previews')

    def confirmed(self, preview, action='order', **kwargs):
        return self.post({'action': action, 'confirmation_id': preview['confirmation_id'],
                          'content_digest': preview['content_digest']}, **kwargs)

    def assertError(self, result, status, code):
        self.assertEqual(result[:2], (status, {'error': code}))

    def test_authenticated_read_has_no_commands_or_false_freshness(self):
        status, body, headers = self.call(self.env())
        self.assertEqual(status, 200)
        self.assertEqual(body['source'], 'AUTHENTICATED_SIMULATION_CORE')
        self.assertTrue(body['read_only'])
        self.assertTrue(all(not v['enabled'] for v in body['controls'].values()))
        self.assertEqual(body['broker_sync']['age_seconds'], 8)
        self.assertEqual(self.backend.calls, [('read', ACCOUNT)])
        self.assertNotIn(ACCOUNT, canonical(body))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        with self.state.transaction() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM commands').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM nonces').fetchone()[0], 0)

    def test_read_preserves_staleness(self):
        self.now += 20
        _, body, _ = self.call(self.env())
        self.assertEqual(body['broker_sync']['status'], 'stale')
        self.assertEqual(body['broker_sync']['age_seconds'], 28)

    def test_missing_token(self):
        self.assertError(self.call(self.env(HTTP_COOKIE='')), 401, 'AUTHENTICATION_REQUIRED')
        self.assertEqual(self.backend.calls, [])

    def test_unknown_token(self):
        self.assertError(self.call(self.env(HTTP_COOKIE=f'{COOKIE}=' + 'u' * 43)), 401, 'AUTHENTICATION_REQUIRED')

    def test_duplicate_cookie(self):
        self.assertError(self.call(self.env(HTTP_COOKIE=f'{COOKIE}={TOKEN}; {COOKIE}={TOKEN}')), 401, 'AUTHENTICATION_REQUIRED')

    def test_expired_session(self):
        self.now = self.record.expires_at
        self.assertError(self.call(self.env()), 401, 'AUTHENTICATION_REQUIRED')

    def test_revoked_session_persists_reopen(self):
        self.state.revoke_session(self.record.token_digest)
        self.app.state = ControlState(self.state.path)
        self.assertError(self.call(self.env()), 401, 'AUTHENTICATION_REQUIRED')

    def test_session_cannot_be_reinstalled_or_revived(self):
        self.state.revoke_session(self.record.token_digest)
        with self.assertRaises(sqlite3.IntegrityError):
            self.state.install_session_record(self.record)

    def test_non_https_and_forwarded_headers_do_not_override(self):
        self.assertError(self.call(self.env(**{'wsgi.url_scheme': 'http', 'HTTP_X_FORWARDED_PROTO': 'https'})), 400, 'HTTPS_REQUIRED')

    def test_host_and_origin_validation(self):
        self.assertError(self.call(self.env(HTTP_HOST='evil.invalid')), 400, 'INVALID_HOST')
        self.assertError(self.call(self.env(HTTP_ORIGIN='https://evil.invalid')), 403, 'CROSS_ORIGIN_DENIED')
        self.assertError(self.call(self.env(HTTP_SEC_FETCH_SITE='cross-site')), 403, 'CROSS_ORIGIN_DENIED')

    def test_query_and_bearer_rejected(self):
        self.assertError(self.call(self.env(QUERY_STRING='account=x')), 400, 'QUERY_NOT_ALLOWED')
        self.assertError(self.call(self.env(HTTP_AUTHORIZATION='Bearer anything')), 400, 'COOKIE_SESSION_REQUIRED')

    def test_commands_off_by_default(self):
        self.app.settings = Settings('https://monitor.invalid', 10)
        self.assertError(self.post({'action': 'halt', 'expected_revision': 7}), 403, 'SIMULATION_COMMANDS_DISABLED')

    def test_read_scope_cannot_command(self):
        record = SessionRecord(digest('r' * 43), digest(CSRF), 'reader', ACCOUNT,
                               frozenset({'monitor.read'}), CLOCK + 300, CLOCK - 60)
        self.state.install_session_record(record)
        self.assertError(self.post({'action': 'halt', 'expected_revision': 7}, HTTP_COOKIE=f'{COOKIE}=' + 'r' * 43), 403, 'COMMAND_SCOPE_REQUIRED')

    def test_command_scope_does_not_imply_read(self):
        record = SessionRecord(digest('x' * 43), digest(CSRF), 'operator', ACCOUNT,
                               frozenset({'simulation.command'}), CLOCK + 300, CLOCK - 60)
        self.state.install_session_record(record)
        self.assertError(self.call(self.env(HTTP_COOKIE=f'{COOKIE}=' + 'x' * 43)), 403, 'READ_SCOPE_REQUIRED')

    def test_csrf_and_origin_required_on_post(self):
        body = {'action': 'halt', 'expected_revision': 7}
        self.assertError(self.post(body, HTTP_X_CSRF_TOKEN='x' * 43), 403, 'CSRF_INVALID')
        self.assertError(self.post(body, HTTP_ORIGIN=None), 403, 'SAME_ORIGIN_REQUIRED')
        self.assertError(self.post(body, HTTP_SEC_FETCH_SITE='none'), 403, 'SAME_ORIGIN_REQUIRED')

    def test_timestamp_past_and_future_fail(self):
        for stamp in (CLOCK - 31, CLOCK + 31):
            self.assertError(self.post({'action': 'halt', 'expected_revision': 7}, HTTP_X_REQUEST_TIMESTAMP=str(stamp)), 409, 'REQUEST_EXPIRED')

    def test_nonce_and_key_required(self):
        body = {'action': 'halt', 'expected_revision': 7}
        self.assertError(self.post(body, HTTP_X_REQUEST_NONCE=''), 400, 'REQUEST_NONCE_REQUIRED')
        self.assertError(self.post(body, HTTP_IDEMPOTENCY_KEY=''), 400, 'IDEMPOTENCY_KEY_REQUIRED')

    def test_duplicate_json_boolean_float_and_unknown_fields_fail(self):
        for raw, code in [(b'{"action":"halt","action":"halt","expected_revision":7}', 'DUPLICATE_JSON_FIELD'),
                          (b'{"action":"halt","expected_revision":7.0}', 'INTEGER_JSON_REQUIRED'),
                          (b'{"action":"halt","expected_revision":true}', 'INVALID_REVISION'),
                          (b'{"action":"halt","expected_revision":7,"approved":true}', 'INVALID_FIELDS'),
                          (b'[]', 'OBJECT_REQUIRED')]:
            self.assertError(self.post(raw), 400, code)
        self.assertEqual(self.backend.calls, [])

    def test_content_type_size_length_and_encoding_fail(self):
        body = {'action': 'halt', 'expected_revision': 7}
        self.assertError(self.post(body, CONTENT_TYPE='text/plain'), 415, 'JSON_REQUIRED')
        self.assertError(self.post(body, CONTENT_LENGTH='8193'), 413, 'BODY_TOO_LARGE')
        self.assertError(self.post(body, CONTENT_LENGTH='1000'), 400, 'TRUNCATED_BODY')
        self.assertError(self.post(body, HTTP_TRANSFER_ENCODING='chunked'), 400, 'ENCODING_NOT_ALLOWED')
        self.assertError(self.post(body, HTTP_CONTENT_ENCODING='gzip'), 400, 'ENCODING_NOT_ALLOWED')

    def test_no_login_live_activation_or_get_effect(self):
        self.assertError(self.call(self.env(path='/api/v2/commands')), 404, 'NOT_FOUND')
        self.assertError(self.post({'action': 'activate_live', 'expected_revision': 7}), 400, 'UNSUPPORTED_ACTION')
        self.assertError(self.post({}, '/api/v2/login'), 404, 'NOT_FOUND')
        self.assertError(self.call(self.env(method='DELETE')), 405, 'METHOD_NOT_ALLOWED')
        self.assertEqual(self.backend.calls, [])

    def test_preview_is_not_an_order_and_hides_account(self):
        status, preview, _ = self.preview()
        self.assertEqual(status, 201)
        self.assertEqual(preview['display']['notional_krw'], '20000')
        self.assertIn('SIMULATION ONLY', preview['warning'])
        self.assertNotIn(ACCOUNT, canonical(preview))
        self.assertEqual(self.backend.calls, [])

    def test_order_requires_exact_confirmation_not_browser_approval(self):
        self.assertError(self.post({'action': 'order', 'approved': True}), 400, 'INVALID_FIELDS')
        _, preview, _ = self.preview()
        result = self.confirmed(preview)
        self.assertEqual(result[0], 202)
        self.assertEqual(result[1]['state'], 'QUEUED')
        call = self.backend.calls[0]
        self.assertEqual(call[0:4], ('order', 'test-owner', ACCOUNT, 7))
        self.assertEqual(call[4][0].policy_version, 'test-v1')
        self.assertEqual(call[4][0].quantity, 2)
        self.assertEqual(call[4][1], preview['confirmation_id'])

    def test_browser_cannot_supply_account_or_policy(self):
        for extra in ({'account': 'b' * 64}, {'policy_version': 'unsafe'}):
            order = {'ticker': '005930', 'side': 'BUY', 'venue': 'KRX', 'quantity': 2,
                     'limit_price': 10000, 'expires_at': datetime.fromtimestamp(CLOCK + 100, timezone.utc).isoformat(), **extra}
            self.assertError(self.preview(order=order), 400, 'INVALID_FIELDS')

    def test_order_bad_numbers_and_expiry(self):
        original = {'ticker': '005930', 'side': 'BUY', 'venue': 'KRX', 'quantity': 2,
                    'limit_price': 10000, 'expires_at': datetime.fromtimestamp(CLOCK + 100, timezone.utc).isoformat()}
        for change in ({'quantity': True}, {'limit_price': 0}, {'side': 'SELL_ALL'}, {'venue': 'AUTO'},
                       {'expires_at': '2026-11-05T10:00:00'}):
            self.assertError(self.preview(order={**original, **change}), 400, 'INVALID_ORDER')
        self.assertError(self.preview(order={**original, 'expires_at': datetime.fromtimestamp(CLOCK + 121, timezone.utc).isoformat()}), 400, 'INVALID_ORDER_EXPIRY')

    def test_tampered_confirmation_digest(self):
        _, preview, _ = self.preview()
        preview['content_digest'] = '0' * 64
        self.assertError(self.confirmed(preview), 409, 'CONFIRMATION_CONTENT_CHANGED')
        self.assertEqual(self.backend.calls, [])

    def test_confirmation_is_single_use(self):
        _, preview, _ = self.preview()
        self.assertEqual(self.confirmed(preview)[0], 202)
        self.assertError(self.confirmed(preview), 409, 'CONFIRMATION_ALREADY_USED')
        self.assertEqual(len(self.backend.calls), 1)

    def test_confirmation_expires(self):
        _, preview, _ = self.preview()
        self.now += 60
        self.assertError(self.confirmed(preview), 409, 'CONFIRMATION_EXPIRED')

    def test_confirmation_cannot_cross_session(self):
        _, preview, _ = self.preview()
        record = SessionRecord(digest('s' * 43), digest(CSRF), 'test-owner', ACCOUNT,
                               self.record.scopes, CLOCK + 300, CLOCK - 60)
        self.state.install_session_record(record)
        self.assertError(self.confirmed(preview, HTTP_COOKIE=f'{COOKIE}=' + 's' * 43), 403, 'CONFIRMATION_INVALID')

    def test_confirmation_action_mismatch_is_terminal(self):
        _, preview, _ = self.preview()
        self.assertError(self.confirmed(preview, action='resume_simulation'), 409, 'CONFIRMATION_ACTION_MISMATCH')
        self.assertError(self.confirmed(preview), 409, 'CONFIRMATION_ALREADY_USED')

    def test_revision_change_after_preview_blocks_order(self):
        _, preview, _ = self.preview()
        self.backend.row['revision'] += 1
        self.assertError(self.confirmed(preview), 409, 'CONTROL_REVISION_CONFLICT')
        self.assertEqual(self.backend.calls, [])

    def test_exact_idempotent_retry_returns_same_receipt_after_expiry(self):
        _, preview, _ = self.preview()
        first = self.confirmed(preview, key='stable_key_123456789')
        self.now += 70
        second = self.confirmed(preview, key='stable_key_123456789')
        self.assertEqual(first[:2], second[:2])
        self.assertEqual(len(self.backend.calls), 1)

    def test_changed_content_with_same_key_conflicts(self):
        self.post({'action': 'halt', 'expected_revision': 7}, key='stable_key_123456789')
        self.assertError(self.post({'action': 'halt', 'expected_revision': 8}, key='stable_key_123456789'), 409, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(len(self.backend.calls), 1)

    def test_identical_content_with_different_wire_bytes_conflicts(self):
        raw = b'{"action":"halt","expected_revision":7}'
        self.post(raw, key='stable_key_123456789')
        self.assertError(self.post(raw.replace(b':7', b': 7'), key='stable_key_123456789'), 409, 'IDEMPOTENCY_CONFLICT')

    def test_nonce_replay_rejected_after_restart(self):
        body = {'action': 'halt', 'expected_revision': 7}
        self.post(body, key='stable_key_123456789', nonce='same_nonce_12345678')
        self.app.state = ControlState(self.state.path)
        self.assertError(self.post(body, key='stable_key_123456789', nonce='same_nonce_12345678'), 409, 'REPLAY_DETECTED')

    def test_rejected_confirmation_nonce_is_consumed(self):
        body = {'action': 'order', 'confirmation_id': 'missing_token_12345678', 'content_digest': '0' * 64}
        self.assertError(self.post(body, nonce='same_nonce_12345678'), 403, 'CONFIRMATION_INVALID')
        self.assertError(self.post(body, nonce='same_nonce_12345678'), 409, 'REPLAY_DETECTED')

    def test_crash_after_core_effect_never_reexecutes(self):
        self.backend.raise_after_effect = True
        body = {'action': 'halt', 'expected_revision': 7}
        self.assertError(self.post(body, key='stable_key_123456789'), 503, 'COMMAND_OUTCOME_UNKNOWN')
        self.backend.raise_after_effect = False
        self.app.state = ControlState(self.state.path)
        self.assertError(self.post(body, key='stable_key_123456789'), 409, 'COMMAND_OUTCOME_UNKNOWN')
        self.assertEqual(len(self.backend.calls), 1)

    def test_completion_commit_failure_never_reexecutes(self):
        body = {'action': 'halt', 'expected_revision': 7}
        with patch.object(self.state, 'complete', side_effect=sqlite3.OperationalError('disk full')):
            self.assertError(self.post(body, key='stable_key_123456789'), 503, 'COMMAND_OUTCOME_UNKNOWN')
        self.assertError(self.post(body, key='stable_key_123456789'), 409, 'COMMAND_OUTCOME_UNKNOWN')
        self.assertEqual(len(self.backend.calls), 1)

    def test_reservation_failure_prevents_effect(self):
        with patch.object(self.state, 'reserve', side_effect=sqlite3.OperationalError('locked')):
            self.assertError(self.post({'action': 'halt', 'expected_revision': 7}), 503, 'SERVICE_UNAVAILABLE')
        self.assertEqual(self.backend.calls, [])

    def test_simulation_resume_requires_preview_and_checks_unresolved_twice(self):
        self.assertError(self.post({'action': 'resume_simulation', 'expected_revision': 7}), 400, 'INVALID_FIELDS')
        self.backend.row['halted'] = True
        _, preview, _ = self.preview('resume_simulation')
        self.backend.unknown = True
        self.assertError(self.confirmed(preview, action='resume_simulation'), 409, 'UNRESOLVED_ORDER')
        self.assertTrue(self.backend.row['halted'])
        self.assertEqual(self.backend.calls, [])

    def test_owner_confirmed_simulation_resume(self):
        self.backend.row['halted'] = True
        _, preview, _ = self.preview('resume_simulation')
        self.assertEqual(self.confirmed(preview, action='resume_simulation')[0], 200)
        self.assertFalse(self.backend.row['halted'])
        self.assertEqual(self.backend.row['revision'], 8)

    def test_pending_cancel_is_scoped(self):
        result = self.post({'action': 'cancel_pending', 'expected_revision': 7, 'request_id': str(UUID(int=2))})
        self.assertEqual(result[0], 200)
        self.assertEqual(self.backend.calls[0][0:4], ('cancel', 'test-owner', ACCOUNT, 7))

    def test_rate_limit_includes_rejected_confirmations(self):
        body = {'action': 'order', 'confirmation_id': 'missing_token_12345678', 'content_digest': '0' * 64}
        for _ in range(60):
            self.assertEqual(self.post(body)[0], 403)
        self.assertError(self.post(body), 429, 'RATE_LIMITED')

    def test_concurrent_double_click_only_one_effect(self):
        _, preview, _ = self.preview()
        body = {'action': 'order', 'confirmation_id': preview['confirmation_id'], 'content_digest': preview['content_digest']}
        envs = [self.env('POST', '/api/v2/commands', body, key='concurrent_key_123456789') for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.call, envs))
        self.assertEqual(len(self.backend.calls), 1)
        self.assertTrue(all(result[0] in (202, 409) for result in results))
        self.assertTrue(any(result[0] == 202 for result in results))
        self.assertEqual(self.post(body, key='concurrent_key_123456789')[0], 202)

    def test_concurrent_different_keys_cannot_reuse_confirmation(self):
        _, preview, _ = self.preview()
        body = {'action': 'order', 'confirmation_id': preview['confirmation_id'], 'content_digest': preview['content_digest']}
        envs = [self.env('POST', '/api/v2/commands', body) for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.call, envs))
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(sum(result[0] == 202 for result in results), 1)

    def test_audit_is_append_only_and_excludes_tokens_content(self):
        self.post({'action': 'halt', 'expected_revision': 7})
        with self.state.transaction() as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM audit')]
        self.assertEqual([row['event'] for row in rows], ['COMMAND_RESERVED', 'COMMAND_COMPLETED'])
        self.assertNotIn(TOKEN, canonical(rows))
        self.assertNotIn(CSRF, canonical(rows))
        with self.assertRaises(sqlite3.IntegrityError), self.state.transaction() as conn:
            conn.execute('DELETE FROM audit')

    def test_raw_sessions_not_stored(self):
        with self.state.transaction() as conn:
            rows = [dict(row) for row in conn.execute('SELECT * FROM sessions')]
        self.assertNotIn(TOKEN, canonical(rows))
        self.assertNotIn(CSRF, canonical(rows))

    def test_missing_journal_does_not_create_fallback_authentication(self):
        self.app.state = ControlState(Path(self.tmp.name) / 'missing.sqlite3')
        self.assertError(self.call(self.env()), 503, 'SERVICE_UNAVAILABLE')
        self.assertEqual(self.backend.calls, [])

    def test_revocation_between_authentication_and_admission_blocks_effect(self):
        original = self.state._consume_attempt
        def revoke_after_attempt(*args, **kwargs):
            original(*args, **kwargs)
            self.state.revoke_session(self.record.token_digest)
        with patch.object(self.state, '_consume_attempt', side_effect=revoke_after_attempt):
            self.assertError(self.post({'action': 'halt', 'expected_revision': 7}), 401, 'AUTHENTICATION_REQUIRED')
        self.assertEqual(self.backend.calls, [])

    def test_money_product_remains_lossless_above_javascript_integer_limit(self):
        order = {'ticker': '005930', 'side': 'BUY', 'venue': 'KRX', 'quantity': 1000000000,
                 'limit_price': 1000000000000,
                 'expires_at': datetime.fromtimestamp(CLOCK + 100, timezone.utc).isoformat()}
        status, preview, _ = self.preview(order=order)
        self.assertEqual(status, 201)
        self.assertEqual(preview['display']['notional_krw'], '1000000000000000000000')

    def test_nonprivate_journal_is_rejected(self):
        import os
        os.chmod(self.state.path, 0o644)
        self.assertError(self.call(self.env()), 503, 'PRIVATE_JOURNAL_FILE_REQUIRED')
        self.assertEqual(self.backend.calls, [])

    def test_unknown_internal_error_does_not_leak_diagnostic(self):
        with patch.object(self.backend, 'status', side_effect=RuntimeError('secret=test-internal')):
            result = self.call(self.env())
        self.assertError(result, 503, 'SERVICE_UNAVAILABLE')
        self.assertNotIn('secret', canonical(result[1]))

    def test_delayed_body_cannot_use_expired_session(self):
        original = self.app._body
        def delayed(env):
            result = original(env)
            self.now += 301
            return result
        with patch.object(self.app, '_body', side_effect=delayed):
            self.assertError(self.post({'action': 'halt', 'expected_revision': 7}), 401, 'AUTHENTICATION_REQUIRED')
        self.assertEqual(self.backend.calls, [])

    def test_delayed_body_cannot_use_expired_wire_timestamp(self):
        original = self.app._body
        def delayed(env):
            result = original(env)
            self.now += 31
            return result
        with patch.object(self.app, '_body', side_effect=delayed):
            self.assertError(self.post({'action': 'halt', 'expected_revision': 7}), 409, 'REQUEST_EXPIRED')
        self.assertEqual(self.backend.calls, [])

    def test_delayed_body_cannot_use_expired_preview(self):
        _, preview, _ = self.preview('resume_simulation')
        self.now += 55
        original = self.app._body
        def delayed(env):
            result = original(env)
            self.now += 10
            return result
        with patch.object(self.app, '_body', side_effect=delayed):
            self.assertError(self.confirmed(preview, action='resume_simulation'), 409, 'CONFIRMATION_EXPIRED')
        self.assertEqual(self.backend.calls, [])

    def test_actual_journal_lock_wait_cannot_preserve_expired_session(self):
        holder = self.state._connection()
        self.addCleanup(holder.close)
        holder.execute('BEGIN IMMEDIATE')
        waiting = threading.Event()
        original = self.state.transaction
        @contextmanager
        def queued_transaction():
            waiting.set()
            with original() as conn:
                yield conn
        env = self.env('POST', '/api/v2/commands', {'action': 'halt', 'expected_revision': 7})
        try:
            with patch.object(self.state, 'transaction', queued_transaction), ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(self.call, env)
                self.assertTrue(waiting.wait(1))
                self.now += 301
                holder.execute('COMMIT')
                self.assertError(result.result(timeout=3), 401, 'AUTHENTICATION_REQUIRED')
        finally:
            if holder.in_transaction:
                holder.execute('ROLLBACK')
        self.assertEqual(self.backend.calls, [])

    def test_second_admission_lock_refreshes_wire_timestamp(self):
        original = self.state._consume_attempt
        def delayed(*args, **kwargs):
            original(*args, **kwargs)
            self.now += 31
        with patch.object(self.state, '_consume_attempt', side_effect=delayed):
            self.assertError(self.post({'action': 'halt', 'expected_revision': 7}), 409, 'REQUEST_EXPIRED')
        self.assertEqual(self.backend.calls, [])

    def test_second_admission_lock_refreshes_preview_expiry(self):
        _, preview, _ = self.preview('resume_simulation')
        self.now += 55
        original = self.state._consume_attempt
        def delayed(*args, **kwargs):
            original(*args, **kwargs)
            self.now += 6
        with patch.object(self.state, '_consume_attempt', side_effect=delayed):
            self.assertError(self.confirmed(preview, action='resume_simulation'), 409, 'CONFIRMATION_EXPIRED')
        self.assertEqual(self.backend.calls, [])

    def test_completed_receipt_retry_rechecks_session_after_wait(self):
        body = {'action': 'halt', 'expected_revision': 7}
        self.assertEqual(self.post(body, key='stable_key_123456789')[0], 200)
        original = self.state._consume_attempt
        def delayed(*args, **kwargs):
            original(*args, **kwargs)
            self.now += 301
        with patch.object(self.state, '_consume_attempt', side_effect=delayed):
            self.assertError(self.post(body, key='stable_key_123456789'), 401, 'AUTHENTICATION_REQUIRED')
        self.assertEqual(len(self.backend.calls), 1)

    def test_slow_status_read_uses_completion_freshness(self):
        original = self.backend.status
        def delayed(account):
            raw = original(account)
            self.now += 60
            return raw
        with patch.object(self.backend, 'status', side_effect=delayed):
            status, body, _ = self.call(self.env())
        self.assertEqual(status, 200)
        self.assertEqual(body['broker_sync']['age_seconds'], 68)
        self.assertEqual(body['broker_sync']['status'], 'stale')

    def test_slow_status_read_rechecks_session_before_disclosure(self):
        original = self.backend.status
        def delayed(account):
            raw = original(account)
            self.now += 301
            return raw
        with patch.object(self.backend, 'status', side_effect=delayed):
            self.assertError(self.call(self.env()), 401, 'AUTHENTICATION_REQUIRED')

    def test_read_projection_preserves_subsecond_completion_clock(self):
        original = self.backend.status
        def delayed(account):
            raw = original(account)
            raw['as_of'] += timedelta(microseconds=100000)
            self.now += 0.2
            return raw
        with patch.object(self.backend, 'status', side_effect=delayed):
            status, body, _ = self.call(self.env())
        self.assertEqual(status, 200)
        self.assertEqual(body['snapshot']['status'], 'fresh')
        self.assertAlmostEqual(body['snapshot']['age_seconds'], 0.1)

    def test_corrupt_session_scope_returns_sanitized_service_response(self):
        with self.state.transaction() as conn:
            conn.execute('UPDATE sessions SET scopes=?', ('["unknown"]',))
        self.assertError(self.call(self.env()), 503, 'INVALID_SESSION_CONFIG')
        self.assertEqual(self.backend.calls, [])

    def test_bad_config_fails_closed(self):
        with self.assertRaises(APIError):
            Settings('http://monitor.invalid', 10).validate()
        with self.assertRaises(APIError):
            Settings('https://monitor.invalid/', 10).validate()
        with self.assertRaises(APIError):
            ControlState(':memory:')


if __name__ == '__main__':
    unittest.main()
