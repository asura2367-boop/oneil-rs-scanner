"""Inactive finite GET observation polling. No runtime/network/credential factory."""
from dataclasses import dataclass
import math
import threading
import time

from .domain import Blocked
from .kis_adapter import AccountBinding, HistoryQuery, prepare_history_query
from .kis_authenticated import Cancellation
from .kis_evidence import (BalanceQuery, BuyingPowerQuery, HolidayQuery, MarketQuery,
    OfflineRead, SellableQuery, prepare_balance_query, prepare_buying_power_query,
    prepare_holiday_query, prepare_market_query, prepare_sellable_query)
from .kis_transport import _validated_recipe

STREAMS = ('history', 'balance', 'buying_power', 'sellable', 'price', 'book', 'holiday')


@dataclass(frozen=True, slots=True)
class PollLimits:
    cycles: int = 1
    interval_seconds: float = 1.0
    duration_seconds: float = 120.0

    def validate(self):
        if (type(self.cycles) is not int or not 1 <= self.cycles <= 100 or
                any(type(v) not in (int, float) or not math.isfinite(v)
                    for v in (self.interval_seconds, self.duration_seconds)) or
                not 1 <= self.interval_seconds <= 60 or
                not 1 <= self.duration_seconds <= 3600):
            raise Blocked('KIS_POLL_CONFIG_INVALID')


@dataclass(frozen=True, slots=True)
class PollStatus:
    """Safe projection only: never account, ticker, body, exception or credentials."""
    state: str = 'idle'
    completed_reads: int = 0
    completed_cycles: int = 0
    stream: str | None = None
    live_eligible: bool = False
    provenance_authenticated: bool = False
    policy_ready: bool = False
    live_capable: bool = False


class KisReadPoller:
    """Caller-started, single-use, cooperative poller over a trusted read source.

    The source must enforce its own bounded calls, shared limiter and read-only
    scope. Callbacks are trusted synchronous local functions, not preemptible.
    No raw captures escape this component or enter its public status.
    """
    def __init__(self, source, binding, day, ticker, *, venue='KRX',
                 limits=PollLimits(), monotonic=time.monotonic,
                 on_request=None, on_response=None):
        try:
            if type(limits) is not PollLimits or type(binding) is not AccountBinding:
                raise ValueError()
            limits.validate()
            history = HistoryQuery(binding, day)
            balance = BalanceQuery(binding, day)
            market = MarketQuery(binding.environment, day, ticker, venue)
            recipes = (prepare_history_query(history), prepare_balance_query(balance),
                prepare_buying_power_query(BuyingPowerQuery(binding, day, ticker)),
                prepare_sellable_query(SellableQuery(binding, day, ticker)),
                prepare_market_query(market), prepare_market_query(market, book=True),
                prepare_holiday_query(HolidayQuery(day)))
            for recipe in recipes:
                _validated_recipe(recipe, binding)
            if not callable(monotonic) or any(h is not None and not callable(h)
                                             for h in (on_request, on_response)):
                raise ValueError()
        except Exception:
            raise Blocked('KIS_POLL_CONFIG_INVALID') from None
        self._source = source
        self._history = history; self._balance = balance; self._recipes = recipes
        self._limits = limits; self._clock = monotonic
        self._on_request = on_request; self._on_response = on_response
        self._cancellation = Cancellation()
        self._lock = threading.RLock()
        self._status = PollStatus()
        self._started = False; self._previous = None; self._deadline = None

    def __repr__(self):
        return 'KisReadPoller(inactive_read_only=True)'

    @property
    def status(self):
        with self._lock:
            return self._status

    def _set(self, state, *, reads=None, cycles=None, stream=None):
        old = self._status
        self._status = PollStatus(state, old.completed_reads if reads is None else reads,
                                  old.completed_cycles if cycles is None else cycles, stream)

    def stop(self):
        with self._lock:
            self._cancellation.cancel()
            self._set('cancelled')

    def _check(self):
        if self._cancellation.cancelled:
            raise _Stop('cancelled')
        now = self._clock()
        if (type(now) not in (int, float) or not math.isfinite(now) or now < 0 or
                (self._previous is not None and now < self._previous)):
            raise _Stop('clock_invalid')
        self._previous = now
        if self._deadline is not None and now >= self._deadline:
            raise _Stop('expired')
        return now

    def _callback(self, callback, stream):
        self._check()
        if callback is not None:
            callback(stream)
        self._check()

    def run(self):
        """Explicit finite run; repeated/concurrent run calls fail without resetting."""
        with self._lock:
            if self._started:
                raise Blocked('KIS_POLL_ALREADY_STARTED')
            self._started = True
        try:
            self._deadline = self._check() + self._limits.duration_seconds
            for cycle in range(self._limits.cycles):
                for index, stream in enumerate(STREAMS):
                    if cycle and stream == 'holiday':
                        continue  # daily reservation is still owned by source limiter
                    self._check()
                    if self.status.completed_reads:
                        remaining = self._deadline - self._check()
                        self._cancellation.wait(min(self._limits.interval_seconds, remaining))
                        self._check()
                    with self._lock:
                        self._check(); self._set('running', stream=stream)
                    self._callback(self._on_request, stream)
                    self._check()
                    if stream == 'history':
                        result = self._source.history(self._history, cancellation=self._cancellation,
                            deadline_monotonic=self._deadline, deadline_clock=self._clock)
                    elif stream == 'balance':
                        result = self._source.balance(self._balance, cancellation=self._cancellation,
                            deadline_monotonic=self._deadline, deadline_clock=self._clock)
                    else:
                        result = self._source.capture(self._recipes[index], cancellation=self._cancellation,
                            deadline_monotonic=self._deadline, deadline_clock=self._clock)
                    self._check()
                    reads = result if stream in ('history', 'balance') else (result,)
                    if (type(reads) is not tuple or not reads or
                            any(type(read) is not OfflineRead for read in reads) or
                            reads[0].recipe != self._recipes[index]):
                        raise _Stop('failed')
                    del result, reads
                    self._callback(self._on_response, stream)
                    with self._lock:
                        self._check()
                        self._set('running', reads=self._status.completed_reads + 1, stream=stream)
                with self._lock:
                    self._check(); self._set('running', cycles=cycle + 1)
            with self._lock:
                self._check(); self._set('completed')
        except Exception as exc:
            with self._lock:
                state = (exc.args[0] if type(exc) is _Stop and len(exc.args) == 1
                         and type(exc.args[0]) is str and exc.args[0] in
                         ('cancelled', 'clock_invalid', 'expired', 'failed') else 'failed')
                self._set('cancelled' if self._cancellation.cancelled else state)
        finally:
            # Only the run owner closes, after all source work has returned.
            try:
                self._source.close()
            except Exception:
                with self._lock:
                    self._set('cancelled' if self._cancellation.cancelled else 'failed')
        return self.status

    def submit(self, *args, **kwargs):
        raise Blocked('KIS_ORDER_SUBMISSION_BLOCKED')

    def policy_evidence(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_EVIDENCE_NOT_ESTABLISHED')


class _Stop(Exception):
    pass
