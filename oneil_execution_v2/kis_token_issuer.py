"""Inactive Windows-local KIS token issuer, never invoked with real material here.

Only the official tokenP POST exists. No arbitrary URL, generic POST, order,
revoke, hashkey, retry, token file/cache or runtime registration is exposed.
Every attempt needs a fresh consumed action-time permission. The caller must
obtain real human consent, not manufacture permission from configuration.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import re
import sys
import threading
import time

from .domain import Blocked
from .kis_adapter import AccountBinding
from .kis_authenticated import Cancellation, CredentialLease
from .kis_local_authorization import LocalPermission
from .kis_windows_credentials import AppCredentials
from .kis_transport import ReadLimits
from . import kis_https as base
from . import kis_authenticated_https as secure

_TOKEN_PATH = '/oauth2/tokenP'
_KST = timezone(timedelta(hours=9))
_CODES = frozenset(('KIS_TOKEN_PERMISSION_REQUIRED', 'KIS_TOKEN_PLATFORM_BLOCKED',
                   'KIS_TOKEN_INPUT_INVALID', 'KIS_TOKEN_RATE_LIMITED',
                   'KIS_TOKEN_RESPONSE_INVALID', 'KIS_TOKEN_EXPIRED',
                   'KIS_TOKEN_HTTP_FAILED', 'KIS_TOKEN_CANCELLED', 'KIS_TOKEN_FAILED'))


class _Failure(Exception):
    pass


def _check(value, code):
    if not value:
        raise _Failure(code)


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


class _IssueLimiter:
    """Process-local conservative single-flight/one-minute guard, not KIS quota."""
    def __init__(self):
        self._lock = threading.Lock(); self._busy = False; self._last = None

    def reserve(self, now):
        with self._lock:
            _check(_number(now, 0, 1e15) and not self._busy and
                   (self._last is None or now - self._last >= 60), 'KIS_TOKEN_RATE_LIMITED')
            self._last = now; self._busy = True

    def release(self):
        with self._lock:
            self._busy = False


_ISSUE_LIMITER = _IssueLimiter()


@dataclass(frozen=True, repr=False)
class _TokenRequest:
    deadline_monotonic: float
    limits: ReadLimits = field(default_factory=lambda: ReadLimits(max_body_bytes=16_384))
    chunk_bytes: int = 4096
    max_header_bytes: int = 8192
    max_header_count: int = 32


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError()


def _lease(body, binding, credentials, started, received, elapsed, minimum):
    value = json.loads(body.decode('utf-8'), object_pairs_hook=_object, parse_constant=_reject_constant)
    _check(type(value) is dict and set(value) == {'access_token', 'token_type', 'expires_in',
           'access_token_token_expired'}, 'KIS_TOKEN_RESPONSE_INVALID')
    token = value['access_token']; kind = value['token_type']; lifetime = value['expires_in']
    absolute = value['access_token_token_expired']
    _check(type(token) is str and re.fullmatch(r'[A-Za-z0-9._~+/=-]{8,4096}', token) and
           type(kind) is str and kind.lower() == 'bearer' and
           type(lifetime) is int and 0 < lifetime <= 86400 and type(absolute) is str and
           re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}', absolute),
           'KIS_TOKEN_RESPONSE_INVALID')
    expiry = datetime.strptime(absolute, '%Y-%m-%d %H:%M:%S').replace(tzinfo=_KST)
    # An old token may be returned again. Never extend the broker's absolute
    # timestamp or grant a fresh lifetime by measuring expires_in after transit.
    expiry = min(expiry, started + timedelta(seconds=lifetime))
    # Wall time may stall during transit. Anchor remaining validity to the more
    # conservative elapsed clock and carry that reduction into the returned UTC
    # expiry so the read source cannot reset it when creating a monotonic bound.
    effective_received = max(received, started + timedelta(seconds=elapsed))
    expiry = min(expiry, received + (expiry - effective_received))
    _check(started <= received < expiry and (expiry - received).total_seconds() > minimum,
           'KIS_TOKEN_EXPIRED')
    # issued_at means local acquisition observation, NOT proven broker issuance.
    return CredentialLease(binding, credentials.app_key, credentials.app_secret,
                           token, started, expiry)


class KisTokenIssuer:
    """No-I/O constructor. Windows issuance requires a one-use local permission.

    This only labels an app-issued token with the trusted local binding. It does
    NOT prove broker-side account/product scope or reduce the bearer to read-only
    broker permissions. The transport's capabilities are restricted locally.
    """
    def __init__(self, *, monotonic=time.monotonic, utc_now=lambda: datetime.now(timezone.utc)):
        if not callable(monotonic) or not callable(utc_now):
            raise Blocked('KIS_TOKEN_INPUT_INVALID')
        self._clock = monotonic; self._utc = utc_now
        self._last_utc = None; self._last_tick = None; self._lock = threading.Lock()

    def __repr__(self):
        return 'KisTokenIssuer(inactive=True, one_permission_per_attempt=True)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_AUTH_SERIALIZATION_BLOCKED')

    def _now(self):
        value = self._utc(); tick = self._clock()
        _check(type(value) is datetime and value.utcoffset() is not None and
               _number(tick, 0, 1e15) and (self._last_utc is None or value >= self._last_utc) and
               (self._last_tick is None or tick >= self._last_tick), 'KIS_TOKEN_INPUT_INVALID')
        self._last_utc = value; self._last_tick = tick
        return value, tick

    def issue(self, binding, credentials, *, permission, minimum_validity_seconds,
              deadline_monotonic, cancellation):
        stream = None; body = None; wire = None; reserved = False; locked = False
        try:
            _check(sys.platform == 'win32', 'KIS_TOKEN_PLATFORM_BLOCKED')
            _check(type(binding) is AccountBinding and type(credentials) is AppCredentials and
                   type(permission) is LocalPermission and type(cancellation) is Cancellation and
                   _number(minimum_validity_seconds, 0, 86400) and
                   _number(deadline_monotonic, 0, 1e15), 'KIS_TOKEN_INPUT_INVALID')
            _check(all(type(value) is str for value in (binding.account, binding.environment,
                   binding.cano, binding.product_code)), 'KIS_TOKEN_INPUT_INVALID')
            binding.validate()
            for secret in (credentials.app_key, credentials.app_secret):
                _check(type(secret) is str and re.fullmatch(r'[A-Za-z0-9._~+/=-]{8,4096}', secret),
                       'KIS_TOKEN_INPUT_INVALID')
            _check(not cancellation.cancelled, 'KIS_TOKEN_CANCELLED')
            permission.check(binding, 'issue_token')
            locked = self._lock.acquire(blocking=False)
            _check(locked, 'KIS_TOKEN_RATE_LIMITED')
            started, tick = self._now()
            _check(deadline_monotonic > tick, 'KIS_TOKEN_INPUT_INVALID')
            remaining = min(10.0, deadline_monotonic - tick)
            _check(remaining >= .1, 'KIS_TOKEN_INPUT_INVALID')
            limits = ReadLimits(connect_seconds=min(3., remaining), read_seconds=min(3., remaining),
                                total_seconds=remaining, max_body_bytes=16_384)
            request = _TokenRequest(tick + remaining, limits)
            _ISSUE_LIMITER.reserve(tick); reserved = True
            operation = permission.consume(binding, 'issue_token')
            deadline = secure._Deadline(request, self._clock, cancellation, operation.check)
            host, port = (('openapi.koreainvestment.com', 9443) if binding.environment == 'real'
                          else ('openapivts.koreainvestment.com', 29443))
            stream = secure._connect(host, port, deadline, limits.connect_seconds, limits.read_seconds)
            deadline.check()
            body = json.dumps({'grant_type': 'client_credentials', 'appkey': credentials.app_key,
                               'appsecret': credentials.app_secret}, separators=(',', ':')).encode('ascii')
            wire = (f'POST {_TOKEN_PATH} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n'
                    'Content-Type: application/json\r\nAccept: application/json\r\n'
                    f'Accept-Encoding: identity\r\nContent-Length: {len(body)}\r\n\r\n').encode('ascii') + body
            stream.send(wire); wire = None; body = None
            status, _, pending, length = base._read_headers(stream, request, deadline)
            _check(status == 200, 'KIS_TOKEN_HTTP_FAILED')
            body = bytearray(pending)
            while len(body) < length:
                chunk = base._recv(stream, min(request.chunk_bytes, length-len(body)), deadline,
                                   limits.read_seconds)
                _check(bool(chunk), 'KIS_TOKEN_RESPONSE_INVALID'); body.extend(chunk)
            deadline.check()
            stream.close(); stream = None
            deadline.check(); received, received_tick = self._now()
            lease = _lease(body, binding, credentials, started, received, received_tick - tick,
                           minimum_validity_seconds)
            deadline.check()
            return lease
        except Exception as exc:
            if type(exc) is _Failure and len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in _CODES:
                code = exc.args[0]
            elif type(exc) is Blocked and str(exc).startswith('KIS_LOCAL_PERMISSION_'):
                code = 'KIS_TOKEN_PERMISSION_REQUIRED'
            else:
                code = 'KIS_TOKEN_FAILED'
            raise Blocked(code) from None
        finally:
            if type(body) is bytearray:
                body[:] = b'\0' * len(body)
            body = None; wire = None
            base._safe_close(stream)
            if reserved: _ISSUE_LIMITER.release()
            if locked: self._lock.release()
