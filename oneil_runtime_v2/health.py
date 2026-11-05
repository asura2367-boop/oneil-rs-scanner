"""Network-free health evaluation. Signals cannot authorize or resume trading.

This evaluator consumes trusted host/adapter observations. It is not wired into
Worker.reserve_attempt; an OK result is only an operational observation.
"""
from dataclasses import dataclass
import math


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class ClockSample:
    utc: float
    elapsed: float
    awake: float | None = None

    def valid(self):
        return number(self.utc) and number(self.elapsed) and (self.awake is None or number(self.awake))


@dataclass(frozen=True)
class Evidence:
    ok: bool
    observed_at: float | None


@dataclass(frozen=True)
class HealthPolicy:
    max_loop_gap_seconds: float = 15
    max_heartbeat_age_seconds: float = 15
    max_dependency_age_seconds: float = 15
    max_clock_disagreement_seconds: float = 2
    min_free_bytes: int = 1_073_741_824

    def __post_init__(self):
        for value in (self.max_loop_gap_seconds, self.max_heartbeat_age_seconds,
                      self.max_dependency_age_seconds, self.max_clock_disagreement_seconds):
            if not number(value) or value <= 0:
                raise ValueError('INVALID_HEALTH_POLICY')
        if type(self.min_free_bytes) is not int or self.min_free_bytes < 0:
            raise ValueError('INVALID_DISK_POLICY')


@dataclass(frozen=True)
class HealthSample:
    clock: ClockSample
    worker_alive: bool
    heartbeat_at: float | None
    database: Evidence
    broker_read: Evidence
    free_bytes: int
    unresolved_orders: bool


@dataclass(frozen=True)
class HealthResult:
    reasons: tuple[str, ...]
    operationally_healthy: bool
    live_capable: bool = False
    admission_enforced: bool = False
    ready_to_trade: bool = False


def evaluate(sample, previous=None, policy=None):
    if type(sample) is not HealthSample or type(sample.clock) is not ClockSample:
        raise ValueError('INVALID_SAMPLE_TYPE')
    policy = policy or HealthPolicy()
    reasons = []
    clock = sample.clock
    if not clock.valid():
        return HealthResult(('CLOCK_INVALID',), False)
    if previous is None:
        reasons.append('STARTUP_RECONCILIATION_REQUIRED')
    elif type(previous) is not ClockSample or not previous.valid():
        reasons.append('PREVIOUS_CLOCK_INVALID')
    else:
        wall = clock.utc - previous.utc
        elapsed = clock.elapsed - previous.elapsed
        if wall < 0 or elapsed < 0:
            reasons.append('CLOCK_REVERSED_OR_HOST_RESTARTED')
        elif abs(wall - elapsed) > policy.max_clock_disagreement_seconds:
            reasons.append('CLOCK_DISCONTINUITY')
        if wall > policy.max_loop_gap_seconds or elapsed > policy.max_loop_gap_seconds:
            reasons.append('LOOP_GAP_REQUIRES_RECONCILIATION')
        if previous.awake is None or clock.awake is None:
            reasons.append('SUSPEND_DETECTION_UNAVAILABLE')
        else:
            awake = clock.awake - previous.awake
            if awake < 0 or awake > elapsed + policy.max_clock_disagreement_seconds:
                reasons.append('AWAKE_CLOCK_INVALID')
            elif elapsed - awake > policy.max_clock_disagreement_seconds:
                reasons.append('SLEEP_OR_HIBERNATION_DETECTED')
    if sample.worker_alive is not True:
        reasons.append('WORKER_NOT_ALIVE')

    def fresh(value, age):
        # Future data is not made fresh by clamping the age to zero.
        return number(value) and 0 <= clock.utc - value <= age

    if not fresh(sample.heartbeat_at, policy.max_heartbeat_age_seconds):
        reasons.append('HEARTBEAT_MISSING_STALE_OR_FUTURE')
    for prefix, evidence in (('DATABASE', sample.database), ('BROKER_READ', sample.broker_read)):
        if type(evidence) is not Evidence or evidence.ok is not True:
            reasons.append(prefix + '_UNAVAILABLE')
        elif not fresh(evidence.observed_at, policy.max_dependency_age_seconds):
            reasons.append(prefix + '_STALE_OR_FUTURE')
    if type(sample.free_bytes) is not int or sample.free_bytes < policy.min_free_bytes:
        reasons.append('DISK_SPACE_LOW_OR_UNKNOWN')
    if sample.unresolved_orders is not False:
        reasons.append('UNRESOLVED_ORDER_OR_UNKNOWN_STATE')
    return HealthResult(tuple(reasons), not reasons)
