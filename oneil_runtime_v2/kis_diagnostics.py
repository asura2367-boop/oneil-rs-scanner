"""Explicit finite read-to-parser composition. Never policy or live authority.

No credential/network factory, background thread, automatic start, persistence,
account balance projection, or order admission is provided. The caller owns the
separately authorized source. Only sanitized parser summaries leave this object.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
import threading
import time

from oneil_execution_v2.domain import Blocked, aware, require
from oneil_execution_v2.kis_evidence import OfflineRead
from oneil_execution_v2.kis_polling import KisReadPoller, PollLimits
from oneil_policy_v2.kis_pipeline import KisOfflineInputSet, inspect_kis_inputs


def unavailable_diagnostics(state='not_attached'):
    return {'mode': 'UNAUTHENTICATED_KIS_DIAGNOSTIC_ONLY', 'state': state,
            'current': False, 'completed_cycles': 0, 'report': None,
            'valid_for_seconds': 0,
            'provenance_authenticated': False, 'policy_ready': False,
            'live_capable': False}


class KisDiagnosticRun:
    """Single-use polling plus complete-cycle normalization and expiring status.

    Attach to a simulation host for observation only. Attachment never runs it.
    An authorized embedding caller must explicitly invoke run(), on its own
    thread if concurrent observation is needed. stop() is cooperative and never
    closes the source while a read is still in progress.
    """
    def __init__(self, source, binding, day, ticker, *, venue='KRX',
                 limits=PollLimits(), max_age_seconds=30, max_pages=20,
                 max_rows=2000, utc_now=lambda: datetime.now(timezone.utc),
                 monotonic=time.monotonic):
        require(venue == 'KRX', 'KIS_DIAGNOSTIC_HISTORY_VENUE_UNVERIFIED')
        require(type(max_age_seconds) is int and 1 <= max_age_seconds <= 300
                and type(max_pages) is int and 1 <= max_pages <= 100
                and type(max_rows) is int and 1 <= max_rows <= 10000
                and callable(utc_now) and callable(monotonic),
                'KIS_DIAGNOSTIC_CONFIG_INVALID')
        self._lock = threading.RLock()
        self._binding = binding; self._day = day; self._ticker = ticker
        self._age = max_age_seconds; self._pages = max_pages; self._rows = max_rows
        self._utc = utc_now; self._mono = monotonic
        self._previous = None; self._cutoff = None; self._oldest = None
        self._state = 'idle'; self._report = None; self._cycles = 0
        self._started = False; self._stopped = False; self._clock_failed = False
        self._reads = {}; self._holiday = None
        self._collector = _Collector(self, source)
        self._poller = KisReadPoller(self._collector, binding, day, ticker,
            venue=venue, limits=limits, monotonic=monotonic,
            on_request=self._request, on_response=self._response)

    def __repr__(self):
        return 'KisDiagnosticRun(diagnostic_only=True)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_DIAGNOSTIC_SERIALIZATION_BLOCKED')

    def _clock(self):
        try:
            wall = aware(self._utc()); mono = self._mono()
            require(type(mono) in (int, float) and math.isfinite(mono) and mono >= 0,
                    'KIS_DIAGNOSTIC_CLOCK_INVALID')
            require(self._previous is None or
                    (wall >= self._previous[0] and mono >= self._previous[1]),
                    'KIS_DIAGNOSTIC_CLOCK_INVALID')
            self._previous = (wall, mono)
            return wall, mono
        except Exception:
            self._clock_failed = True
            self._state = 'clock_invalid'; self._report = None
            self._poller.stop()
            raise Blocked('KIS_DIAGNOSTIC_CLOCK_INVALID') from None

    def _request(self, stream):
        with self._lock:
            self._clock()
            if stream == 'history':
                # Invalidate before any dependency work. Never reuse a prior
                # successful cycle during a partial or failed refresh.
                self._reads.clear(); self._report = None
                self._state = 'collecting'

    def _response(self, stream):
        with self._lock:
            wall, mono = self._clock()
            final = 'holiday' if self._cycles == 0 else 'book'
            if stream != final or self._stopped:
                return
            require(set(self._reads) == {'history', 'balance', 'buying_power',
                'sellable', 'price', 'book', *(['holiday'] if self._cycles == 0 else [])},
                'KIS_DIAGNOSTIC_CYCLE_INCOMPLETE')
            if self._cycles == 0:
                self._holiday = self._reads['holiday']
            inputs = KisOfflineInputSet(self._binding, self._day, self._ticker, 'KRX',
                balance_reads=self._reads['balance'],
                buying_power_read=self._reads['buying_power'][0],
                sellable_read=self._reads['sellable'][0],
                price_read=self._reads['price'][0],
                session_read=self._reads['book'][0], holiday_reads=self._holiday,
                history_reads=self._reads['history'])
            report = inspect_kis_inputs(inputs, now=wall, max_age_seconds=self._age,
                                        max_pages=self._pages, max_rows=self._rows)
            all_reads = tuple(read for group in self._reads.values() for read in group) + self._holiday
            oldest = min(aware(read.started_at) for read in all_reads)
            # The original calendar timestamps are retained; later cycles do
            # not renew old holiday freshness or make an extra quota reservation.
            remaining = self._age - (wall - oldest).total_seconds()
            self._oldest = oldest; self._cutoff = mono + max(0, remaining)
            self._report = report.summary()  # Never retain typed account values.
            self._state = ('incomplete' if not report.shape_complete else
                           'inconsistent' if report.inconsistencies else 'parsed')
            self._cycles += 1
            self._reads.clear()

    def snapshot(self):
        """Thread-safe, fixed-code projection. No raw input can be injected here."""
        with self._lock:
            try:
                wall, mono = self._clock()
            except Exception:
                wall = mono = None
            status = self._poller.status
            if self._clock_failed:
                self._state = 'clock_invalid'; self._report = None
            elif self._stopped or status.state == 'cancelled':
                self._state = 'cancelled'; self._report = None
            elif status.state in ('failed', 'expired', 'clock_invalid'):
                self._state = status.state; self._report = None
            elif self._report is not None and (mono >= self._cutoff or
                    (wall - self._oldest).total_seconds() >= self._age):
                self._state = 'stale'; self._report = None
            value = unavailable_diagnostics(self._state)
            # The response callback precedes the poller's deadline/cancellation
            # recheck and cycle commit. Do not expose an uncommitted report.
            committed = self._report is not None and status.completed_cycles >= self._cycles
            value.update(completed_cycles=min(self._cycles, status.completed_cycles),
                         current=committed,
                         valid_for_seconds=(max(0, min(self._cutoff - mono,
                             self._age - (wall - self._oldest).total_seconds()))
                             if committed else 0),
                         report=deepcopy(self._report) if committed else None)
            if self._report is not None and not committed:
                value['state'] = 'collecting'
            return value

    def run(self):
        with self._lock:
            require(not self._started, 'KIS_DIAGNOSTIC_ALREADY_STARTED')
            self._started = True
        try:
            self._poller.run()
        except BaseException:
            # Process/embedding interruption must not leave a previously parsed
            # result visible, including interruption during source cleanup.
            self.stop()
            raise
        finally:
            with self._lock:
                self._reads.clear(); self._holiday = None
        return self.snapshot()

    def stop(self):
        with self._lock:
            self._stopped = True; self._state = 'cancelled'; self._report = None
            self._poller.stop()

    def submit(self, *args, **kwargs):
        raise Blocked('KIS_ORDER_SUBMISSION_BLOCKED')

    def policy_evidence(self, *args, **kwargs):
        raise Blocked('KIS_LIVE_EVIDENCE_NOT_ESTABLISHED')


class _Collector:
    """Private read-only adapter. Raw captures never reach the host or browser."""
    def __init__(self, owner, source):
        self.owner = owner; self.source = source

    def _collect(self, stream, result):
        reads = result if stream in ('history', 'balance') else (result,)
        require(type(reads) is tuple and 1 <= len(reads) <= self.owner._pages
                and all(type(read) is OfflineRead for read in reads),
                'KIS_DIAGNOSTIC_CAPTURE_INVALID')
        with self.owner._lock:
            if not self.owner._stopped:
                require(stream not in self.owner._reads, 'KIS_DIAGNOSTIC_DUPLICATE_STREAM')
                self.owner._reads[stream] = reads
        return result

    def history(self, query, **kwargs):
        return self._collect('history', self.source.history(query, **kwargs))

    def balance(self, query, **kwargs):
        return self._collect('balance', self.source.balance(query, **kwargs))

    def capture(self, recipe, **kwargs):
        stream = self.owner._poller.status.stream
        require(stream in ('buying_power', 'sellable', 'price', 'book', 'holiday'),
                'KIS_DIAGNOSTIC_STREAM_INVALID')
        return self._collect(stream, self.source.capture(recipe, **kwargs))

    def close(self):
        self.source.close()
