"""Restricted injected-client GET boundary, with NO concrete network client.

No credentials, authentication headers, token refresh, orders, database, runtime
registration or sockets are provided. A conforming client is a trusted dependency:
it must enforce the immutable request controls before contacting a server and
while streaming. Offline mocks cannot establish live transport provenance.
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import re
import threading
import time
from typing import Protocol
from urllib.parse import urlencode

from .domain import Blocked
from .kis_adapter import (AccountBinding, HistoryQuery, KisResponse, RequestRecipe,
                          HISTORY_PATH, prepare_history_query)
from .kis_evidence import (BALANCE_PATH, BOOK_PATH, BUYING_POWER_PATH, HOLIDAY_PATH,
                           PRICE_PATH, SELLABLE_PATH, BalanceQuery, BuyingPowerQuery,
                           HolidayQuery, MarketQuery, OfflineRead, SellableQuery,
                           prepare_balance_query, prepare_buying_power_query,
                           prepare_holiday_query, prepare_market_query,
                           prepare_sellable_query)

# Exact origins from the official KIS configuration, not caller-provided URLs.
REAL_ORIGIN = 'https://openapi.koreainvestment.com:9443'
DEMO_ORIGIN = 'https://openapivts.koreainvestment.com:29443'
_READ_PATHS = frozenset((HISTORY_PATH, BALANCE_PATH, BUYING_POWER_PATH, SELLABLE_PATH,
                         PRICE_PATH, BOOK_PATH, HOLIDAY_PATH))


# Untrusted injected dependencies can raise our private exception too. Never
# echo its message unless it is one of these exact, non-sensitive codes.
_ERROR_CODES = frozenset({
    'KIS_TRANSPORT_BINDING_INVALID',
    'KIS_TRANSPORT_BODY_LENGTH_INVALID',
    'KIS_TRANSPORT_BODY_LIMIT',
    'KIS_TRANSPORT_CAPTURE_CLOCK_INVALID',
    'KIS_TRANSPORT_CAPTURE_FAILED',
    'KIS_TRANSPORT_CLIENT_INVALID',
    'KIS_TRANSPORT_CLOCK_INVALID',
    'KIS_TRANSPORT_CONFIG_INVALID',
    'KIS_TRANSPORT_CONTENT_TYPE_INVALID',
    'KIS_TRANSPORT_CONTINUATION_UNSUPPORTED',
    'KIS_TRANSPORT_DEADLINE_EXCEEDED',
    'KIS_TRANSPORT_ENCODING_INVALID',
    'KIS_TRANSPORT_ENDPOINT_BLOCKED',
    'KIS_TRANSPORT_HEADERS_INVALID',
    'KIS_TRANSPORT_HOLIDAY_RATE_LIMITED',
    'KIS_TRANSPORT_HTTP_ERROR',
    'KIS_TRANSPORT_INVALID_BODY_LIMIT',
    'KIS_TRANSPORT_INVALID_RATE_LIMIT',
    'KIS_TRANSPORT_INVALID_TIMEOUT',
    'KIS_TRANSPORT_JSON_INVALID',
    'KIS_TRANSPORT_LIMITS_INVALID',
    'KIS_TRANSPORT_ORIGIN_BLOCKED',
    'KIS_TRANSPORT_PROVENANCE_INVALID',
    'KIS_TRANSPORT_RATE_LIMITED',
    'KIS_TRANSPORT_READ_FAILED',
    'KIS_TRANSPORT_RECIPE_INVALID',
    'KIS_TRANSPORT_REDIRECT_BLOCKED',
    'KIS_TRANSPORT_REMOTE_RATE_LIMITED',
    'KIS_TRANSPORT_STATUS_INVALID'
})

def _safe_failure(exc):
    if type(exc) is _Failure and len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in _ERROR_CODES:
        return exc.args[0]
    return 'KIS_TRANSPORT_READ_FAILED'


class _Failure(Exception):
    """Internal errors contain only fixed, non-sensitive codes."""


def _check(condition, code):
    if not condition:
        raise _Failure(code)


def _finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


@dataclass(frozen=True)
class ReadLimits:
    connect_seconds: float = 3.0
    read_seconds: float = 3.0
    total_seconds: float = 10.0
    max_body_bytes: int = 262_144

    def validate(self):
        _check(_finite(self.connect_seconds, 0.1, 10) and
               _finite(self.read_seconds, 0.1, 10) and
               _finite(self.total_seconds, 0.1, 30) and
               max(self.connect_seconds, self.read_seconds) <= self.total_seconds,
               'KIS_TRANSPORT_INVALID_TIMEOUT')
        _check(type(self.max_body_bytes) is int and 128 <= self.max_body_bytes <= 1_048_576,
               'KIS_TRANSPORT_INVALID_BODY_LIMIT')


@dataclass(frozen=True)
class OutboundGet:
    """Trusted-client contract; repr is safe, serialization is NOT redaction.

    The URL contains account query parameters. Never log it. Client must disable
    its own URL/header logging, ambient proxies/auth/cookies, redirects and retries.
    Both the open operation and each body read must obey the remaining total
    deadline, in addition to connect/read timeouts and maximum body/header sizes.
    """
    environment: str
    endpoint: str
    url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    limits: ReadLimits
    deadline_monotonic: float = field(repr=False)
    method: str = field(default='GET', init=False)
    verify_tls: bool = field(default=True, init=False)
    follow_redirects: bool = field(default=False, init=False)
    use_environment: bool = field(default=False, init=False)
    automatic_retries: int = field(default=0, init=False)
    chunk_bytes: int = field(default=16_384, init=False)
    max_header_bytes: int = field(default=16_384, init=False)
    max_header_count: int = field(default=64, init=False)


class GetResponse(Protocol):
    """Metadata must describe the actual request, not merely echo its inputs.

    No built-in client implements this contract in this development module.
    iter_bytes must yield raw, undecoded wire bytes within the requested bounds.
    close must be bounded and must not send another HTTP request.
    """
    status_code: int
    url: str
    request_method: str
    tls_verified: bool
    redirect_count: int
    headers: dict[str, str]

    def iter_bytes(self, chunk_bytes: int): ...
    def close(self): ...


class GetOnlyClient(Protocol):
    def open_get(self, request: OutboundGet) -> GetResponse: ...


class ReadRateLimiter:
    """Fail-fast, process-local shared limit; failures consume their reservation.

    One in-flight request per limiter and at most one start per second by default.
    This is a conservative local cap, not an assertion of KIS account quotas.
    Reuse one limiter for all clients sharing a broker budget. No sleeping/retries.
    """
    def __init__(self, *, interval_seconds=1.0, max_requests=1, monotonic=time.monotonic):
        if not (_finite(interval_seconds, 1, 3600) and type(max_requests) is int
                and 1 <= max_requests <= 2 and callable(monotonic)):
            raise Blocked('KIS_TRANSPORT_INVALID_RATE_LIMIT')
        self._interval = interval_seconds
        self._maximum = max_requests
        self._clock = monotonic
        self._starts = deque()
        self._lock = threading.Lock()
        self._active = False
        self._last = None
        self._blocked_until = 0.0
        self._holiday_started = None

    def _now(self):
        value = self._clock()
        _check(_finite(value, 0, 1e15) and (self._last is None or value >= self._last),
               'KIS_TRANSPORT_CLOCK_INVALID')
        self._last = value
        return value

    def acquire(self, *, holiday_first_page=False):
        with self._lock:
            now = self._now()
            while self._starts and now - self._starts[0] >= self._interval:
                self._starts.popleft()
            _check(not self._active and now >= self._blocked_until and
                   len(self._starts) < self._maximum, 'KIS_TRANSPORT_RATE_LIMITED')
            _check(not holiday_first_page or self._holiday_started is None or
                   now - self._holiday_started >= 86400, 'KIS_TRANSPORT_HOLIDAY_RATE_LIMITED')
            self._starts.append(now)
            if holiday_first_page:
                self._holiday_started = now
            self._active = True

    def release(self):
        with self._lock:
            self._active = False

    def throttle(self, seconds):
        with self._lock:
            # Unknown/overlarge server cooldowns stay closed for this limiter.
            # There is deliberately no automatic reset or retry API.
            until = math.inf if seconds is None else self._now() + seconds
            self._blocked_until = max(self._blocked_until, until)


_DEFAULT_LIMITER = ReadRateLimiter()


def _retry_after_seconds(value, utc_now):
    """Never shorten a server cooldown; unsupported values latch the limiter."""
    try:
        if type(value) is not str:
            return None
        if re.fullmatch(r'[0-9]+', value):
            if len(value) > 7:
                return None
            seconds = int(value)
        else:
            target = parsedate_to_datetime(value)
            current = utc_now()
            if (type(current) is not datetime or current.utcoffset() is None or
                    target.utcoffset() is None):
                return None
            seconds = math.ceil((target - current).total_seconds())
        # No unbounded integer conversion, timer or long sleep. A longer request
        # permanently closes this in-memory limiter pending separate review.
        return max(60, seconds) if seconds <= 604800 else None
    except Exception:
        return None


def _wire_day(value):
    _check(type(value) is str and re.fullmatch(r'[0-9]{8}', value),
           'KIS_TRANSPORT_RECIPE_INVALID')
    return f'{value[:4]}-{value[4:6]}-{value[6:]}'


def _validated_recipe(recipe, binding):
    """Rebuild reviewed recipes, rejecting extra parameters or altered fixed modes."""
    _check(type(recipe) is RequestRecipe and all(type(value) is str for value in
           (recipe.method, recipe.path, recipe.tr_id, recipe.tr_cont)) and
           recipe.method == 'GET' and recipe.path in _READ_PATHS, 'KIS_TRANSPORT_ENDPOINT_BLOCKED')
    _check(type(recipe.parameters) is tuple and len(recipe.parameters) <= 32,
           'KIS_TRANSPORT_RECIPE_INVALID')
    params = {}
    for pair in recipe.parameters:
        _check(type(pair) is tuple and len(pair) == 2, 'KIS_TRANSPORT_RECIPE_INVALID')
        key, value = pair
        _check(type(key) is str and re.fullmatch(r'[A-Z0-9_]{1,40}', key) and
               key not in params and type(value) is str and
               re.fullmatch(r'[ -~]{0,100}', value), 'KIS_TRANSPORT_RECIPE_INVALID')
        params[key] = value
    # Day contexts absent from the wire are not fabricated as broker facts. They
    # are only placeholders to reuse the existing exact recipe constructors.
    day = '2000-01-01'
    cursor = (params.get('CTX_AREA_FK100'), params.get('CTX_AREA_NK100'))
    if recipe.path == HISTORY_PATH:
        expected = prepare_history_query(HistoryQuery(binding, _wire_day(params.get('INQR_STRT_DT'))),
                                         cursor=cursor)
    elif recipe.path == BALANCE_PATH:
        expected = prepare_balance_query(BalanceQuery(binding, day), cursor=cursor)
    elif recipe.path == BUYING_POWER_PATH:
        _check(params.get('ORD_UNPR') == '', 'KIS_TRANSPORT_RECIPE_INVALID')
        expected = prepare_buying_power_query(BuyingPowerQuery(binding, day, params.get('PDNO')))
    elif recipe.path == SELLABLE_PATH:
        expected = prepare_sellable_query(SellableQuery(binding, day, params.get('PDNO')))
    elif recipe.path in (PRICE_PATH, BOOK_PATH):
        venue = {'J': 'KRX', 'NX': 'NXT'}.get(params.get('FID_COND_MRKT_DIV_CODE'))
        expected = prepare_market_query(MarketQuery(binding.environment, day, params.get('FID_INPUT_ISCD'), venue),
                                        book=recipe.path == BOOK_PATH)
    else:
        # Portal is real-only and nonpaginated; never follow sample recursion.
        _check(binding.environment == 'real', 'KIS_TRANSPORT_ENDPOINT_BLOCKED')
        _check(params.get('CTX_AREA_FK') == '' and params.get('CTX_AREA_NK') == '',
               'KIS_TRANSPORT_RECIPE_INVALID')
        expected = prepare_holiday_query(HolidayQuery(_wire_day(params.get('BASS_DT'))))
    _check(recipe == expected, 'KIS_TRANSPORT_RECIPE_INVALID')
    return recipe


def _headers(raw, request):
    _check(type(raw) is dict and len(raw) <= request.max_header_count, 'KIS_TRANSPORT_HEADERS_INVALID')
    result = {}; size = 0
    for key, value in raw.items():
        _check(type(key) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key) and
               key.lower() not in result and type(value) is str and
               re.fullmatch(r'[\t -~]{0,8192}', value), 'KIS_TRANSPORT_HEADERS_INVALID')
        size += len(key) + len(value) + 4
        _check(size <= request.max_header_bytes, 'KIS_TRANSPORT_HEADERS_INVALID')
        result[key.lower()] = value
    return result


def _object(pairs):
    result = {}; seen = set()
    for key, value in pairs:
        _check(key.lower() not in seen, 'KIS_TRANSPORT_JSON_INVALID')
        seen.add(key.lower()); result[key] = value
    return result


def _json_integer(value):
    _check(len(value) <= 32, 'KIS_TRANSPORT_JSON_INVALID')
    return int(value)


def _forbid_number(value):
    raise _Failure('KIS_TRANSPORT_JSON_INVALID')


def _parse_body(body):
    text = body.decode('utf-8', errors='strict')
    # Limit nesting before the JSON parser can recurse. Escaped quotes/brackets
    # inside strings do not change the depth. json.loads handles full grammar.
    depth = 0; quoted = False; escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            _check(depth <= 32, 'KIS_TRANSPORT_JSON_INVALID')
        elif char in ']}':
            depth -= 1
    result = json.loads(text, object_pairs_hook=_object, parse_int=_json_integer,
                        parse_float=_forbid_number, parse_constant=_forbid_number)
    _check(type(result) is dict, 'KIS_TRANSPORT_JSON_INVALID')
    return result


class KisGetOnlyTransport:
    """Restricted transport with injected client, never a live-read authorization.

    Binding is server-owned. This class does not load credentials or supply auth.
    No runtime factory imports it. Its output is raw observation data, not policy
    evidence, account provenance, complete history, exchange time or permission.
    """
    def __init__(self, client: GetOnlyClient, binding: AccountBinding, *, origin=None,
                 limits=ReadLimits(), limiter=None, monotonic=time.monotonic,
                 utc_now=lambda: datetime.now(timezone.utc)):
        try:
            _check(type(binding) is AccountBinding and all(type(value) is str for value in
                   (binding.account, binding.environment, binding.cano, binding.product_code)),
                   'KIS_TRANSPORT_BINDING_INVALID')
            binding.validate()
            _check(type(limits) is ReadLimits, 'KIS_TRANSPORT_LIMITS_INVALID')
            limits.validate()
            expected = REAL_ORIGIN if binding.environment == 'real' else DEMO_ORIGIN
            _check(origin is None or type(origin) is str and origin == expected,
                   'KIS_TRANSPORT_ORIGIN_BLOCKED')
            _check(callable(getattr(client, 'open_get', None)) and callable(monotonic) and callable(utc_now),
                   'KIS_TRANSPORT_CLIENT_INVALID')
            _check(limiter is None or type(limiter) is ReadRateLimiter, 'KIS_TRANSPORT_INVALID_RATE_LIMIT')
        except _Failure as exc:
            raise Blocked(_safe_failure(exc)) from None
        except Exception:
            raise Blocked('KIS_TRANSPORT_CONFIG_INVALID') from None
        self._client = client; self._binding = binding; self._origin = expected
        self._limits = limits; self._limiter = _DEFAULT_LIMITER if limiter is None else limiter
        self._clock = monotonic; self._utc_now = utc_now

    def __repr__(self):
        return 'KisGetOnlyTransport(GET_only=True, live_eligible=False)'

    def read(self, recipe: RequestRecipe) -> KisResponse:
        response = None; acquired = False
        try:
            _validated_recipe(recipe, self._binding)
            start = self._clock()
            _check(_finite(start, 0, 1e15), 'KIS_TRANSPORT_CLOCK_INVALID')
            deadline = start + self._limits.total_seconds
            previous = start

            def check_deadline():
                nonlocal previous
                current = self._clock()
                _check(_finite(current, previous, deadline), 'KIS_TRANSPORT_DEADLINE_EXCEEDED')
                previous = current

            request = OutboundGet(self._binding.environment, recipe.path,
                                  self._origin + recipe.path + '?' + urlencode(recipe.parameters),
                                  (('accept', 'application/json'), ('accept-encoding', 'identity'),
                                   ('tr_id', recipe.tr_id), ('tr_cont', recipe.tr_cont), ('custtype', 'P')),
                                  self._limits, deadline)
            self._limiter.acquire(holiday_first_page=recipe.path == HOLIDAY_PATH and recipe.tr_cont == '')
            acquired = True
            check_deadline()
            response = self._client.open_get(request)
            check_deadline()
            _check(response.request_method == 'GET' and response.tls_verified is True and
                   type(response.redirect_count) is int and response.redirect_count == 0 and
                   type(response.url) is str and response.url == request.url,
                   'KIS_TRANSPORT_PROVENANCE_INVALID')
            status = response.status_code
            _check(type(status) is int and 100 <= status <= 599, 'KIS_TRANSPORT_STATUS_INVALID')
            headers = _headers(response.headers, request)
            _check(not 300 <= status <= 399 and 'location' not in headers, 'KIS_TRANSPORT_REDIRECT_BLOCKED')
            if status == 429:
                self._limiter.throttle(_retry_after_seconds(headers.get('retry-after'), self._utc_now))
                raise _Failure('KIS_TRANSPORT_REMOTE_RATE_LIMITED')
            _check(status == 200, 'KIS_TRANSPORT_HTTP_ERROR')
            _check(headers.get('content-encoding', 'identity').lower() == 'identity',
                   'KIS_TRANSPORT_ENCODING_INVALID')
            _check(re.fullmatch(r'application/json(?:\s*;\s*charset=utf-8)?',
                               headers.get('content-type', '').lower()) is not None,
                   'KIS_TRANSPORT_CONTENT_TYPE_INVALID')
            _check('tr_id' not in headers or headers['tr_id'] == recipe.tr_id,
                   'KIS_TRANSPORT_PROVENANCE_INVALID')
            if recipe.path in (BUYING_POWER_PATH, SELLABLE_PATH, HOLIDAY_PATH):
                _check(headers.get('tr_cont', '') == '', 'KIS_TRANSPORT_CONTINUATION_UNSUPPORTED')
            length = headers.get('content-length')
            if length is not None:
                _check(re.fullmatch(r'[0-9]{1,10}', length) is not None and
                       int(length) <= self._limits.max_body_bytes and 'transfer-encoding' not in headers,
                       'KIS_TRANSPORT_BODY_LIMIT')
            body = bytearray(); chunks = 0
            for chunk in response.iter_bytes(request.chunk_bytes):
                check_deadline(); chunks += 1
                _check(chunks <= self._limits.max_body_bytes and type(chunk) is bytes and
                       0 < len(chunk) <= request.chunk_bytes and
                       len(body) + len(chunk) <= self._limits.max_body_bytes, 'KIS_TRANSPORT_BODY_LIMIT')
                body.extend(chunk)
            check_deadline()
            _check(length is None or len(body) == int(length), 'KIS_TRANSPORT_BODY_LENGTH_INVALID')
            parsed = _parse_body(body)
            check_deadline()
            # Never expose cookies, Location, diagnostic headers or raw error text.
            safe_headers = {key: headers[key] for key in ('tr_id', 'tr_cont') if key in headers}
            closing = response
            response = None
            closing.close()
            check_deadline()
            return KisResponse(200, parsed, safe_headers)
        except _Failure as exc:
            raise Blocked(_safe_failure(exc)) from None
        except Exception:
            # Includes injected client/iterator errors, JSON failures, and parser
            # Blocked messages. Never propagate their URL/body/credential details.
            raise Blocked('KIS_TRANSPORT_READ_FAILED') from None
        finally:
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
            finally:
                if acquired:
                    self._limiter.release()

    def read_history(self, recipe: RequestRecipe) -> KisResponse:
        if type(recipe) is not RequestRecipe or recipe.path != HISTORY_PATH:
            raise Blocked('KIS_TRANSPORT_ENDPOINT_BLOCKED')
        return self.read(recipe)

    def capture(self, recipe: RequestRecipe) -> OfflineRead:
        # Even a successful injected-client capture is NOT a provenance attestation.
        # Use the same non-executable transcript type; the offline parser checks
        # aware/order/future/day/freshness constraints independently.
        try:
            started = self._utc_now()
            _check(type(started) is datetime and started.utcoffset() is not None,
                   'KIS_TRANSPORT_CAPTURE_CLOCK_INVALID')
            response = self.read(recipe)
            received = self._utc_now()
            if (type(started) is not datetime or type(received) is not datetime or
                    started.utcoffset() is None or received.utcoffset() is None or received < started):
                raise _Failure('KIS_TRANSPORT_CAPTURE_CLOCK_INVALID')
            return OfflineRead(recipe, response, started, received)
        except _Failure as exc:
            raise Blocked(_safe_failure(exc)) from None
        except Exception:
            raise Blocked('KIS_TRANSPORT_CAPTURE_FAILED') from None

    def submit(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED')

    def policy_evidence(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_EVIDENCE_NOT_ESTABLISHED')
