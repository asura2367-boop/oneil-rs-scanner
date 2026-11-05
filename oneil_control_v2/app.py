"""Socket-free WSGI boundary: authenticated reads and explicit simulation commands.

No broker transport, API key, login issuer, live mode, migration, or auto-start.
A deployment must provide HTTPS and a reviewed external identity/session issuer.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID

from oneil_execution_v2.domain import Order, Blocked
from .state import APIError, check, canonical, digest

COOKIE = '__Host-oneil_session'
KEY = re.compile('[A-Za-z0-9_-]{16,128}')
HEX = re.compile('[0-9a-f]{64}')
STATUS = {200: 'OK', 201: 'Created', 202: 'Accepted', 400: 'Bad Request',
          401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
          405: 'Method Not Allowed', 409: 'Conflict', 413: 'Content Too Large',
          415: 'Unsupported Media Type', 429: 'Too Many Requests',
          500: 'Internal Server Error', 503: 'Service Unavailable'}
# Domain messages must never disclose arbitrary database, identity or broker text.
SAFE_ERRORS = {'CONTROL_REVISION_CONFLICT', 'UNRESOLVED_ORDER', 'HALTED', 'POLICY_CHANGED',
               'IDEMPOTENCY_CONFLICT', 'REQUEST_EXPIRED', 'APPROVAL_EXPIRED', 'REQUEST_NOT_FOUND',
               'ALREADY_ADMITTED_BROKER_CANCEL_REQUIRED', 'ACCOUNT_NOT_SIMULATION'}


@dataclass(frozen=True)
class Settings:
    origin: str
    broker_max_age_seconds: int
    simulation_commands_enabled: bool = False

    def validate(self):
        origin = urlsplit(self.origin)
        check(origin.scheme == 'https' and bool(origin.hostname) and not origin.username and
              not origin.password and not origin.path and not origin.query and not origin.fragment,
              503, 'HTTPS_ORIGIN_REQUIRED')
        check(type(self.broker_max_age_seconds) is int and 0 < self.broker_max_age_seconds <= 3600,
              503, 'EXPLICIT_FRESHNESS_POLICY_REQUIRED')
        check(type(self.simulation_commands_enabled) is bool, 503, 'INVALID_SETTINGS')
        return self


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, 400, 'DUPLICATE_JSON_FIELD')
        result[key] = value
    return result


def _no_float(value):
    raise APIError(400, 'INTEGER_JSON_REQUIRED')


def exact_fields(value, fields):
    check(type(value) is dict and set(value) == set(fields), 400, 'INVALID_FIELDS')


def revision(value):
    check(type(value) is int and 1 <= value <= 2**53 - 1, 400, 'INVALID_REVISION')
    return value


class ControlAPI:
    def __init__(self, *, state, backend, projector, settings, clock=time.time):
        self.state, self.backend, self.projector = state, backend, projector
        self.settings, self.clock = settings.validate(), clock

    def _now(self):
        return int(self.clock())

    def _session(self, env, now):
        check(env.get('wsgi.url_scheme') == 'https', 400, 'HTTPS_REQUIRED')
        check(env.get('HTTP_HOST') == urlsplit(self.settings.origin).netloc, 400, 'INVALID_HOST')
        check(env.get('QUERY_STRING', '') == '', 400, 'QUERY_NOT_ALLOWED')
        check(env.get('HTTP_SEC_FETCH_SITE', 'same-origin') in ('same-origin', 'none'), 403, 'CROSS_ORIGIN_DENIED')
        if env.get('HTTP_ORIGIN') is not None:
            check(env['HTTP_ORIGIN'] == self.settings.origin, 403, 'CROSS_ORIGIN_DENIED')
        check(not env.get('HTTP_AUTHORIZATION'), 400, 'COOKIE_SESSION_REQUIRED')
        raw = env.get('HTTP_COOKIE', '')
        check(isinstance(raw, str) and len(raw) <= 4096, 401, 'AUTHENTICATION_REQUIRED')
        cookies = {}
        for entry in raw.split(';'):
            if not entry.strip():
                continue
            key, separator, value = entry.strip().partition('=')
            check(separator and key not in cookies, 401, 'AUTHENTICATION_REQUIRED')
            cookies[key] = value
        return self.state.authenticate(cookies.get(COOKIE), now)

    def _body(self, env):
        check(env.get('CONTENT_TYPE') == 'application/json', 415, 'JSON_REQUIRED')
        check(not env.get('HTTP_TRANSFER_ENCODING') and not env.get('HTTP_CONTENT_ENCODING') and
              not env.get('CONTENT_ENCODING'), 400, 'ENCODING_NOT_ALLOWED')
        length = env.get('CONTENT_LENGTH', '')
        check(isinstance(length, str) and re.fullmatch('[1-9][0-9]{0,5}', length), 400, 'CONTENT_LENGTH_REQUIRED')
        check(int(length) <= 8192, 413, 'BODY_TOO_LARGE')
        raw = env['wsgi.input'].read(int(length))
        check(type(raw) is bytes and len(raw) == int(length), 400, 'TRUNCATED_BODY')
        try:
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                              parse_constant=_no_float, parse_float=_no_float)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise APIError(400, 'INVALID_JSON') from error
        check(type(data) is dict, 400, 'OBJECT_REQUIRED')
        return data, raw

    def __call__(self, env, start_response):
        try:
            status, payload = self.handle(env)
        except APIError as error:
            status, payload = error.status, {'error': error.code}
        except Exception:
            status, payload = 503, {'error': 'SERVICE_UNAVAILABLE'}
        body = canonical(payload).encode('utf-8')
        headers = [('Content-Type', 'application/json; charset=utf-8'), ('Cache-Control', 'no-store'),
                   ('X-Content-Type-Options', 'nosniff'), ('Content-Length', str(len(body))),
                   ('Referrer-Policy', 'no-referrer'), ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'"),
                   ('Strict-Transport-Security', 'max-age=31536000'), ('Vary', 'Cookie')]
        start_response(f'{status} {STATUS[status]}', headers)
        return [body]

    def handle(self, env):
        now = self._now()
        session = self._session(env, now)
        method, path = env.get('REQUEST_METHOD'), env.get('PATH_INFO')
        if method == 'GET':
            check('monitor.read' in session.scopes, 403, 'READ_SCOPE_REQUIRED')
            check(path == '/api/v2/status', 404, 'NOT_FOUND')
            raw = self.backend.status(session.account)
            # A backend read can wait. Recheck expiry/revocation before disclosure
            # and measure freshness at read completion, preserving subsecond time.
            read_time = self.clock()
            self._session(env, int(read_time))
            result = self.projector(raw, now=datetime.fromtimestamp(read_time, timezone.utc),
                                    broker_max_age_seconds=self.settings.broker_max_age_seconds)
            # Preserve monitor.v1 conservative disabled controls. Never make a
            # read poll an authorization, queue mutation, freshness rewrite or resume.
            result['source'] = 'AUTHENTICATED_SIMULATION_CORE'
            result['owner_authorization'] = {'state': 'AUTHENTICATED_READ_ONLY'}
            return 200, result
        check(method == 'POST', 405, 'METHOD_NOT_ALLOWED')
        check(path in ('/api/v2/previews', '/api/v2/commands'), 404, 'NOT_FOUND')
        check(self.settings.simulation_commands_enabled, 403, 'SIMULATION_COMMANDS_DISABLED')
        check('simulation.command' in session.scopes, 403, 'COMMAND_SCOPE_REQUIRED')
        check(env.get('HTTP_ORIGIN') == self.settings.origin and env.get('HTTP_SEC_FETCH_SITE') == 'same-origin',
              403, 'SAME_ORIGIN_REQUIRED')
        csrf = env.get('HTTP_X_CSRF_TOKEN', '')
        check(isinstance(csrf, str) and re.fullmatch('[A-Za-z0-9_-]{43,128}', csrf) and
              hmac.compare_digest(digest(csrf), session.csrf_digest), 403, 'CSRF_INVALID')
        key, nonce = env.get('HTTP_IDEMPOTENCY_KEY', ''), env.get('HTTP_X_REQUEST_NONCE', '')
        check(isinstance(key, str) and KEY.fullmatch(key), 400, 'IDEMPOTENCY_KEY_REQUIRED')
        check(isinstance(nonce, str) and KEY.fullmatch(nonce), 400, 'REQUEST_NONCE_REQUIRED')
        stamp = env.get('HTTP_X_REQUEST_TIMESTAMP', '')
        check(isinstance(stamp, str) and re.fullmatch('[0-9]{10,12}', stamp) and abs(now - int(stamp)) <= 30,
              409, 'REQUEST_EXPIRED')
        data, raw = self._body(env)
        # Hash exact wire body plus method/path. A retry must preserve body bytes.
        request_digest = hashlib.sha256(method.encode()+b'\n'+path.encode()+b'\n'+raw).hexdigest()
        confirmation = self._validate_command(data, path)
        cached, approved = self.state.reserve(session, key=key, request_digest=request_digest,
                                               nonce=nonce, request_timestamp=int(stamp),
                                               clock=self.clock, confirmation=confirmation)
        if cached is not None:
            return cached
        preview = None
        try:
            if path == '/api/v2/previews':
                payload, preview = self._preview(session, data, self._now())
                status = 201
            else:
                status, payload = self._execute(session, data, approved)
        except APIError as error:
            status, payload = error.status, {'error': error.code}
        except Blocked as error:
            code = str(error)
            status, payload = (404 if code == 'REQUEST_NOT_FOUND' else 409), {'error': code if code in SAFE_ERRORS else 'SIMULATION_COMMAND_BLOCKED'}
        except Exception as error:
            # The core may have committed before throwing. Do NOT complete or
            # release this key/challenge. The durable STARTED record blocks retry.
            raise APIError(503, 'COMMAND_OUTCOME_UNKNOWN') from error
        try:
            self.state.complete(session, key, status, payload, self._now(), preview=preview)
        except Exception as error:
            raise APIError(503, 'COMMAND_OUTCOME_UNKNOWN') from error
        return status, payload

    def _validate_command(self, data, path):
        action = data.get('action')
        if path.endswith('/previews'):
            check(action in ('order', 'resume_simulation'), 400, 'UNSUPPORTED_ACTION')
            exact_fields(data, ('action', 'expected_revision', 'order') if action == 'order' else ('action', 'expected_revision'))
            revision(data['expected_revision'])
            if action == 'order':
                exact_fields(data['order'], ('ticker', 'side', 'venue', 'quantity', 'limit_price', 'expires_at'))
            return None
        check(action in ('order', 'resume_simulation', 'halt', 'cancel_pending'), 400, 'UNSUPPORTED_ACTION')
        if action in ('order', 'resume_simulation'):
            exact_fields(data, ('action', 'confirmation_id', 'content_digest'))
            check(isinstance(data['confirmation_id'], str) and KEY.fullmatch(data['confirmation_id']), 400, 'INVALID_CONFIRMATION')
            check(isinstance(data['content_digest'], str) and HEX.fullmatch(data['content_digest']), 400, 'INVALID_CONFIRMATION')
            return data['confirmation_id'], data['content_digest']
        exact_fields(data, ('action', 'expected_revision', 'request_id') if action == 'cancel_pending' else ('action', 'expected_revision'))
        revision(data['expected_revision'])
        if action == 'cancel_pending':
            try:
                check(type(data['request_id']) is str and str(UUID(data['request_id'])) == data['request_id'], 400, 'INVALID_REQUEST_ID')
            except (ValueError, AttributeError):
                raise APIError(400, 'INVALID_REQUEST_ID')
        return None

    def _preview(self, session, data, now):
        control = self.backend.control(session.account)
        check(control['mode'] == 'SIMULATION', 409, 'SIMULATION_ONLY')
        check(control['revision'] == data['expected_revision'], 409, 'CONTROL_REVISION_CONFLICT')
        content = {'action': data['action'], 'expected_revision': data['expected_revision'], 'mode': 'SIMULATION'}
        expires = min(now + 60, session.expires_at)
        if data['action'] == 'order':
            check(not control['halted'], 409, 'HALTED')
            try:
                order = Order.from_payload({**data['order'], 'account': session.account,
                                            'policy_version': control['policy']['version']})
                order.validate()
                expiry = order.expires_at.timestamp()
                check(now < expiry <= now + 120, 400, 'INVALID_ORDER_EXPIRY')
            except (ValueError, TypeError, AttributeError, OverflowError) as error:
                if isinstance(error, APIError):
                    raise
                raise APIError(400, 'INVALID_ORDER') from error
            expires = min(expires, int(expiry))
            content['order'] = order.payload()
            display = {k: v for k, v in order.payload().items() if k != 'account'}
            display['notional_krw'] = str(order.notional)  # Lossless even above JS MAX_SAFE_INTEGER.
            warning = 'SIMULATION ONLY. Confirm this exact order. Acceptance is not a fill.'
        else:
            check(not self.backend.unresolved(session.account), 409, 'UNRESOLVED_ORDER')
            display = {'action': 'resume_simulation', 'expected_revision': data['expected_revision']}
            warning = 'SIMULATION ONLY. Resume admission and invalidate older queued approvals.'
        token = secrets.token_urlsafe(32)  # Single-use confirmation reference, not a login credential.
        payload = {'confirmation_id': token, 'content_digest': digest(canonical(content)),
                   'expires_at': datetime.fromtimestamp(expires, timezone.utc).isoformat(),
                   'display': display, 'warning': warning, 'mode': 'SIMULATION', 'live_capable': False}
        return payload, (token, content, expires)

    def _execute(self, session, data, approved):
        action = data['action']
        if approved is not None:
            check(approved['mode'] == 'SIMULATION' and approved['action'] == action, 409, 'CONFIRMATION_ACTION_MISMATCH')
            expected = approved['expected_revision']
        else:
            expected = data['expected_revision']
        if action == 'order':
            result = self.backend.confirm_order(session.principal, session.account,
                                                Order.from_payload(approved['order']), expected,
                                                data['confirmation_id'])
            return 202, result
        if action == 'resume_simulation':
            result = self.backend.resume_simulation(session.principal, session.account, expected)
        elif action == 'halt':
            result = self.backend.halt(session.principal, session.account, expected)
        else:
            result = self.backend.cancel_pending(session.principal, session.account, data['request_id'], expected)
        return 200, {'result': result, 'mode': 'SIMULATION', 'live_capable': False}
