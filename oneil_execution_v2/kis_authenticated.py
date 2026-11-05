"""Inactive authenticated GET-only foundation, tested with synthetic providers only.

No OS credential store, environment, file, token issuer, HTTP implementation or
runtime registration is provided. Dependencies must be reviewed, bounded and
cancellable. An injected provider/client is not authenticated provenance proof.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import math
import re
import threading
import time
from typing import Protocol

from .domain import Blocked
from .kis_adapter import (AccountBinding, HistoryQuery, KisReadOnlyAdapter,
                          RequestRecipe, _fields)
from .kis_evidence import (HOLIDAY_PATH, BalanceQuery,
                          KisOfflineEvidenceAdapter, OfflineRead,
                          prepare_balance_query, _position, _totals)
from .kis_transport import (KisGetOnlyTransport, OutboundGet, ReadLimits,
                            ReadRateLimiter, _DEFAULT_LIMITER, _validated_recipe)


class _Fault(Exception):
    pass


def _check(ok, code):
    if not ok:
        raise _Fault(code)


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


class Cancellation:
    """Cooperative cancellation, including immediate wake from retry/rate waits.

    Providers and clients MUST observe this token during their bounded calls.
    This wrapper cannot interrupt an arbitrary nonconforming dependency.
    """
    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self):
        return self._event.is_set()

    def wait(self, seconds):
        return self._event.wait(seconds)


@dataclass(frozen=True, slots=True)
class CredentialLease:
    """Ephemeral secret material supplied by a future trusted platform provider.

    Never serialize/log this value. repr and pickle are restricted, not a memory
    security boundary. Python strings cannot be reliably zeroized. No real
    provider or secret retrieval is included in this development module.
    """
    binding: AccountBinding = field(repr=False)
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    bearer: str = field(repr=False)
    issued_at: datetime = field(repr=False)
    expires_at: datetime = field(repr=False)

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_AUTH_SERIALIZATION_BLOCKED')


class CredentialProvider(Protocol):
    """Future Windows provider boundary; acquire does not imply token issuance.

    Return an already-authorized in-memory lease from the exact account/product/
    environment. Honor deadline, cancellation and minimum validity. No fallback
    to environment variables, plaintext files, browser state or account discovery.
    Issuing/persisting credentials is outside this interface's implementation.
    """
    def acquire(self, binding: AccountBinding, *, minimum_validity_seconds: float,
                deadline_monotonic: float, cancellation: Cancellation) -> CredentialLease: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedGet:
    """Only a validated GET and three exact auth headers; never log/serialize.

    The future client must independently revalidate the exact seven read recipes,
    actual TLS/origin/method/redirect metadata, and every OutboundGet control.
    It must disable logging, proxies, cookies, redirects and its own retries,
    bound streaming/close, and check cancellation throughout. The existing
    credential-free KisHttpsGetClient deliberately does not accept this type.
    """
    request: OutboundGet = field(repr=False)
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    bearer: str = field(repr=False)
    cancellation: Cancellation = field(repr=False)

    @property
    def method(self):
        return 'GET'

    @property
    def headers(self):
        return self.request.headers + (('authorization', 'Bearer ' + self.bearer),
                                       ('appkey', self.app_key), ('appsecret', self.app_secret))

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_AUTH_SERIALIZATION_BLOCKED')


class AuthenticatedGetClient(Protocol):
    def open_authenticated_get(self, request: AuthenticatedGet): ...


class TransientReadFailure(Exception):
    """Explicit trusted-client classification; no arbitrary exception is retried.

    Only connect/read timeout or peer disconnect may use these fixed kinds.
    Certificate/TLS-policy, redirect, framing, parsing and cancellation failures
    MUST NOT be converted to this exception. Its message is never propagated.
    """
    def __init__(self, kind):
        self.kind = kind
        super().__init__('KIS_AUTH_TRANSIENT_READ')


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    operation_seconds: float = 30.0

    def validate(self):
        _check(type(self.max_attempts) is int and 1 <= self.max_attempts <= 3 and
               _number(self.base_delay_seconds, 1, 5) and
               _number(self.operation_seconds, 1, 120), 'KIS_AUTH_CONFIG_INVALID')


class _Budget:
    def __init__(self, clock, utc_now, seconds, cancellation):
        self._clock = clock; self._utc_now = utc_now; self.cancellation = cancellation
        self.previous = None; self.previous_utc = None
        start = self.now()
        self.deadline = start + seconds
        self.check()

    def now(self):
        value = self._clock()
        _check(_number(value, 0, 1e15) and
               (self.previous is None or value >= self.previous), 'KIS_AUTH_CLOCK_INVALID')
        self.previous = value
        return value

    def check(self):
        _check(not self.cancellation.cancelled, 'KIS_AUTH_CANCELLED')
        value = self.now()
        _check(value < self.deadline, 'KIS_AUTH_DEADLINE_EXCEEDED')
        return self.deadline - value

    def utc(self):
        value = self._utc_now()
        _check(type(value) is datetime and value.utcoffset() is not None and
               (self.previous_utc is None or value >= self.previous_utc), 'KIS_AUTH_CLOCK_INVALID')
        self.previous_utc = value
        return value

    def wait(self, seconds):
        remaining = self.check()
        _check(seconds < remaining, 'KIS_AUTH_DEADLINE_EXCEEDED')
        self.cancellation.wait(seconds)
        self.check()


class _StreamingResponse:
    def __init__(self, response, attempt):
        self._response = response; self._attempt = attempt
        self.status_code = attempt.status

    def __getattr__(self, name):
        return getattr(self._response, name)

    def iter_bytes(self, chunk_bytes):
        try:
            self._attempt.budget.check()
            for chunk in self._response.iter_bytes(chunk_bytes):
                self._attempt.budget.check()
                yield chunk
        except TransientReadFailure as exc:
            self._attempt.mark_transient(exc)
            raise RuntimeError('KIS_AUTH_READ_FAILED') from None

    def close(self):
        # A conforming client's close is bounded local cleanup with no requests.
        self._response.close()


class _Attempt:
    def __init__(self, source, budget):
        self.source = source; self.lease = None; self.budget = budget
        self.sent = False; self.status = None; self.transient = False; self.failure_code = None

    def mark_transient(self, exc):
        self.transient = (type(exc) is TransientReadFailure and
                          type(exc.kind) is str and exc.kind in
                          ('CONNECT_TIMEOUT', 'READ_TIMEOUT', 'PEER_DISCONNECTED'))

    def open_get(self, request):
        try:
            remaining = self.budget.check()
            self.lease = self.source._lease_for(self.budget, request.deadline_monotonic)
            self.source._validate_cached_lease(self.lease, self.budget, remaining + 30)
            self.budget.check()
            _check(self.budget.now() < request.deadline_monotonic, 'KIS_AUTH_DEADLINE_EXCEEDED')
        except _Fault as exc:
            if len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in _CODES:
                self.failure_code = exc.args[0]
            raise RuntimeError('KIS_AUTH_READ_FAILED') from None
        self.sent = True
        envelope = AuthenticatedGet(request, self.lease.app_key, self.lease.app_secret,
                                    self.lease.bearer, self.budget.cancellation)
        try:
            response = self.source._client.open_authenticated_get(envelope)
        except TransientReadFailure as exc:
            self.mark_transient(exc)
            raise RuntimeError('KIS_AUTH_READ_FAILED') from None
        # Return through the existing validator before any status-based retry.
        try:
            self.status = response.status_code
            return _StreamingResponse(response, self)
        except Exception:
            try:
                response.close()
            except Exception:
                pass
            raise RuntimeError('KIS_AUTH_READ_FAILED') from None


class KisAuthenticatedReadSource:
    """Inactive, trusted-dependency composition. No live provider/client supplied.

    Results remain OfflineRead, never authenticated semantic/policy evidence.
    Operations are single-flight per source; the process-shared GET limiter also
    covers pages, renewal retries, failures and all other cooperating sources.
    """
    def __init__(self, binding, provider, client, *, policy=RetryPolicy(), limits=ReadLimits(),
                 limiter=None, monotonic=time.monotonic,
                 utc_now=lambda: datetime.now(timezone.utc)):
        try:
            _check(type(binding) is AccountBinding and all(type(v) is str for v in
                   (binding.account, binding.environment, binding.cano, binding.product_code)),
                   'KIS_AUTH_CONFIG_INVALID')
            binding.validate()
            _check(type(policy) is RetryPolicy and type(limits) is ReadLimits and
                   (limiter is None or type(limiter) is ReadRateLimiter) and
                   callable(getattr(provider, 'acquire', None)) and
                   callable(getattr(client, 'open_authenticated_get', None)) and
                   callable(monotonic) and callable(utc_now), 'KIS_AUTH_CONFIG_INVALID')
            policy.validate(); limits.validate()
        except Exception:
            raise Blocked('KIS_AUTH_CONFIG_INVALID') from None
        self._binding = binding; self._provider = provider; self._client = client
        self._policy = policy; self._limits = limits
        self._limiter = _DEFAULT_LIMITER if limiter is None else limiter
        self._clock = monotonic; self._utc_now = utc_now
        self._last_monotonic = None; self._last_utc = None
        self._lease = None; self._lease_deadline = 0; self._lease_bounds = {}
        self._rejected_bearer = None; self._auth_halted = False
        self._consecutive_unauthorized = 0; self._closed = False
        self._lock = threading.Lock()

    def __repr__(self):
        return 'KisAuthenticatedReadSource(GET_only=True, live_eligible=False)'

    def _monotonic(self):
        value = self._clock()
        _check(_number(value, 0, 1e15) and
               (self._last_monotonic is None or value >= self._last_monotonic),
               'KIS_AUTH_CLOCK_INVALID')
        self._last_monotonic = value
        return value

    def _utc(self):
        value = self._utc_now()
        _check(type(value) is datetime and value.utcoffset() is not None and
               (self._last_utc is None or value >= self._last_utc), 'KIS_AUTH_CLOCK_INVALID')
        self._last_utc = value
        return value

    def _validate_cached_lease(self, lease, budget, required):
        _check((lease.expires_at - budget.utc()).total_seconds() > required and
               self._lease_deadline - budget.now() > required, 'KIS_AUTH_LEASE_EXPIRED')

    def _lease_for(self, budget, attempt_deadline):
        # Require validity through this entire operation plus clock/expiry skew.
        _check(not self._auth_halted, 'KIS_AUTH_UNAUTHORIZED')
        required = budget.check() + 30
        now = budget.utc()
        if (self._lease is not None and (self._lease.expires_at - now).total_seconds() > required
                and self._lease_deadline - budget.now() > required):
            return self._lease
        self._lease = None
        lease = self._provider.acquire(self._binding, minimum_validity_seconds=required,
                                       deadline_monotonic=min(budget.deadline, attempt_deadline),
                                       cancellation=budget.cancellation)
        budget.check(); now = budget.utc()
        _check(type(lease) is CredentialLease and type(lease.binding) is AccountBinding and
               lease.binding == self._binding, 'KIS_AUTH_LEASE_INVALID')
        for value in (lease.app_key, lease.app_secret, lease.bearer):
            _check(type(value) is str and re.fullmatch(r'[A-Za-z0-9._~+/=-]{8,4096}', value),
                   'KIS_AUTH_LEASE_INVALID')
        _check(all(type(v) is datetime and v.utcoffset() is not None for v in
                   (lease.issued_at, lease.expires_at)), 'KIS_AUTH_LEASE_INVALID')
        _check(lease.issued_at <= now < lease.expires_at and
               0 < (lease.expires_at - lease.issued_at).total_seconds() <= 86400 and
               (lease.expires_at - now).total_seconds() > required,
               'KIS_AUTH_LEASE_EXPIRED')
        if lease.bearer == self._rejected_bearer:
            self._auth_halted = True
            raise _Fault('KIS_AUTH_RENEWAL_REPEATED')
        # Reacquiring an old bearer never resets its monotonic expiry, even if
        # the wall clock freezes or the provider returns a cached token again.
        # Forget a bound only after UTC independently proves that grant expired.
        self._lease_bounds = {key: value for key, value in self._lease_bounds.items()
                              if value[1] > now}
        fingerprint = hashlib.sha256(lease.bearer.encode('ascii')).digest()
        previous = self._lease_bounds.get(fingerprint)
        if previous is not None:
            _check(previous[:2] == (lease.issued_at, lease.expires_at), 'KIS_AUTH_LEASE_INVALID')
            deadline = previous[2]
        else:
            _check(len(self._lease_bounds) < 32, 'KIS_AUTH_LEASE_INVALID')
            deadline = budget.now() + (lease.expires_at - now).total_seconds()
        _check(deadline - budget.now() > required, 'KIS_AUTH_LEASE_EXPIRED')
        self._lease_bounds[fingerprint] = (lease.issued_at, lease.expires_at, deadline)
        self._lease_deadline = deadline
        self._lease = lease
        return lease

    def _capture(self, recipe, budget):
        _validated_recipe(recipe, self._binding)  # before consulting provider
        attempts = 0; renewed = False
        while True:
            budget.check()
            remaining = min(self._limits.total_seconds, budget.check())
            _check(remaining >= 0.1, 'KIS_AUTH_DEADLINE_EXCEEDED')
            limits = replace(self._limits, total_seconds=remaining,
                             connect_seconds=min(self._limits.connect_seconds, remaining),
                             read_seconds=min(self._limits.read_seconds, remaining))
            attempt = _Attempt(self, budget)
            transport = KisGetOnlyTransport(attempt, self._binding, limits=limits,
                                           limiter=self._limiter, monotonic=self._monotonic,
                                           utc_now=self._utc)
            started = budget.utc()
            try:
                response = transport.read(recipe)
                budget.check(); received = budget.utc()
                _check(_fields(response.body).get('rt_cd') == '0', 'KIS_AUTH_API_REJECTED')
                self._consecutive_unauthorized = 0
                return OfflineRead(recipe, response, started, received)
            except Blocked as exc:
                budget.check()
                # Only the existing transport's exact validation outcomes qualify.
                # Remote 429, auth/semantic bodies, malformed input and TLS do not.
                code = str(exc)
                if attempt.failure_code is not None:
                    raise _Fault(attempt.failure_code) from None
                if code == 'KIS_TRANSPORT_RATE_LIMITED' and not attempt.sent:
                    budget.wait(1.0)
                    continue
                attempts += int(attempt.sent)
                retryable = (code == 'KIS_TRANSPORT_HTTP_ERROR' and
                             type(attempt.status) is int and attempt.status in (502, 503, 504))
                retryable |= (code == 'KIS_TRANSPORT_READ_FAILED' and attempt.transient)
                unauthorized = code == 'KIS_TRANSPORT_HTTP_ERROR' and attempt.status == 401
                if unauthorized:
                    self._rejected_bearer = attempt.lease.bearer
                    self._lease = None
                    self._consecutive_unauthorized += 1
                    if self._consecutive_unauthorized >= 2:
                        self._auth_halted = True
                    retryable = not renewed
                    renewed = True
                if recipe.path == HOLIDAY_PATH or attempts >= self._policy.max_attempts or not retryable:
                    if unauthorized:
                        raise _Fault('KIS_AUTH_UNAUTHORIZED') from None
                    if code == 'KIS_TRANSPORT_REMOTE_RATE_LIMITED':
                        raise _Fault('KIS_AUTH_REMOTE_RATE_LIMITED') from None
                    if code == 'KIS_TRANSPORT_HOLIDAY_RATE_LIMITED':
                        raise _Fault('KIS_AUTH_HOLIDAY_RATE_LIMITED') from None
                    raise _Fault('KIS_AUTH_READ_FAILED') from None
                budget.wait(min(5.0, self._policy.base_delay_seconds * (2 ** (attempts - 1))))

    def _run(self, operation, cancellation):
        _check(type(cancellation) is Cancellation, 'KIS_AUTH_CANCELLATION_INVALID')
        _check(self._lock.acquire(blocking=False), 'KIS_AUTH_OPERATION_BUSY')
        try:
            _check(not self._closed, 'KIS_AUTH_SOURCE_CLOSED')
            budget = _Budget(self._monotonic, self._utc, self._policy.operation_seconds, cancellation)
            result = operation(budget)
            budget.check()
            return result
        finally:
            self._lock.release()

    def _public(self, operation, cancellation):
        try:
            return self._run(operation, Cancellation() if cancellation is None else cancellation)
        except _Fault as exc:
            # Only internal fixed codes, not exceptions supplied by dependencies.
            if len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in _CODES:
                raise Blocked(exc.args[0]) from None
            raise Blocked('KIS_AUTH_READ_FAILED') from None
        except Exception:
            raise Blocked('KIS_AUTH_READ_FAILED') from None

    def capture(self, recipe: RequestRecipe, *, cancellation=None) -> OfflineRead:
        return self._public(lambda budget: self._capture(recipe, budget), cancellation)

    def history(self, query: HistoryQuery, *, cancellation=None, max_pages=20, max_rows=2000):
        """Return only fully parsed traversal captures; no partial result escapes."""
        def operation(budget):
            _check(type(query) is HistoryQuery and query.binding == self._binding,
                   'KIS_AUTH_SCOPE_INVALID')
            source = self
            reads = []

            class RecordingTransport:
                def read_history(self, recipe):
                    read = source._capture(recipe, budget)
                    reads.append(read)
                    return read.response

            adapter = KisReadOnlyAdapter(RecordingTransport(), budget.utc,
                                         max_pages=max_pages, max_rows=max_rows)
            observation = adapter.history(query)
            budget.check()
            _check(observation.complete is True, 'KIS_AUTH_HISTORY_INCOMPLETE')
            return tuple(reads)
        return self._public(operation, cancellation)

    def balance(self, query: BalanceQuery, *, cancellation=None, max_pages=20, max_rows=2000):
        """Bounded cursor traversal with full existing balance validation at end."""
        def operation(budget):
            _check(type(query) is BalanceQuery and query.binding == self._binding,
                   'KIS_AUTH_SCOPE_INVALID')
            _check(type(max_pages) is int and 1 <= max_pages <= 100 and
                   type(max_rows) is int and 1 <= max_rows <= 10000, 'KIS_AUTH_PAGE_LIMIT')
            reads = []; cursor = ('', ''); seen = {cursor}; count = 0
            seen_tickers = set(); totals = None
            for _ in range(max_pages):
                read = self._capture(prepare_balance_query(query, cursor=cursor), budget)
                reads.append(read)
                body = _fields(read.response.body); headers = _fields(read.response.headers)
                rows = body.get('output1')
                _check(type(rows) is list and
                       len(rows) <= (50 if self._binding.environment == 'real' else 20),
                       'KIS_AUTH_BALANCE_INCOMPLETE')
                page = tuple(_position(row) for row in rows)
                current = _totals(body.get('output2'))
                _check(totals is None or totals == current, 'KIS_AUTH_BALANCE_INCOMPLETE')
                totals = current
                tickers = [row.ticker for row in page]
                _check(len(set(tickers)) == len(tickers) and not set(tickers) & seen_tickers,
                       'KIS_AUTH_BALANCE_INCOMPLETE')
                seen_tickers.update(tickers)
                count += len(rows)
                _check(count <= max_rows, 'KIS_AUTH_PAGE_LIMIT')
                continuation = headers.get('tr_cont')
                _check(continuation in ('M', 'F', 'D', 'E'), 'KIS_AUTH_BALANCE_INCOMPLETE')
                if continuation in ('D', 'E'):
                    parsed = KisOfflineEvidenceAdapter(budget.utc(), max_age_seconds=120,
                        max_pages=max_pages, max_rows=max_rows).balance(query, tuple(reads))
                    _check(parsed.valid, 'KIS_AUTH_BALANCE_INCOMPLETE')
                    return tuple(reads)
                cursor = (body.get('ctx_area_fk100'), body.get('ctx_area_nk100'))
                prepare_balance_query(query, cursor=cursor)
                key = tuple(value.strip() for value in cursor)
                _check(key != ('', '') and key not in seen, 'KIS_AUTH_CURSOR_LOOP')
                seen.add(key)
            raise _Fault('KIS_AUTH_PAGE_LIMIT')
        return self._public(operation, cancellation)

    def close(self):
        if not self._lock.acquire(blocking=False):
            raise Blocked('KIS_AUTH_OPERATION_BUSY')
        try:
            self._lease = None; self._lease_bounds.clear()
            self._rejected_bearer = None; self._closed = True
        finally:
            self._lock.release()

    def policy_evidence(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_EVIDENCE_NOT_ESTABLISHED')

    def submit(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_SUBMISSION_NOT_IMPLEMENTED')


_CODES = frozenset(('KIS_AUTH_CONFIG_INVALID', 'KIS_AUTH_CLOCK_INVALID',
    'KIS_AUTH_CANCELLED', 'KIS_AUTH_DEADLINE_EXCEEDED', 'KIS_AUTH_LEASE_INVALID',
    'KIS_AUTH_LEASE_EXPIRED', 'KIS_AUTH_RENEWAL_REPEATED', 'KIS_AUTH_API_REJECTED',
    'KIS_AUTH_UNAUTHORIZED', 'KIS_AUTH_REMOTE_RATE_LIMITED', 'KIS_AUTH_READ_FAILED',
    'KIS_AUTH_HOLIDAY_RATE_LIMITED', 'KIS_AUTH_CANCELLATION_INVALID',
    'KIS_AUTH_OPERATION_BUSY', 'KIS_AUTH_SOURCE_CLOSED', 'KIS_AUTH_SCOPE_INVALID',
    'KIS_AUTH_HISTORY_INCOMPLETE', 'KIS_AUTH_BALANCE_INCOMPLETE',
    'KIS_AUTH_PAGE_LIMIT', 'KIS_AUTH_CURSOR_LOOP'))
