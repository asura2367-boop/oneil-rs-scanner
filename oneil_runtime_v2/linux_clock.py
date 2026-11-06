"""Read Linux suspend-aware clocks. Never adjusts time or host settings."""
import sys
import time
from .health import ClockSample


def sample_linux_clock():
    if sys.platform != 'linux' or not hasattr(time, 'CLOCK_BOOTTIME'):
        raise RuntimeError('LINUX_SUSPEND_CLOCK_REQUIRED')
    # BOOTTIME includes suspend; MONOTONIC excludes it. Reject a sample spanning
    # a long scheduling pause rather than hiding an uncertain cross-clock skew.
    boot_start = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    awake_start = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    utc = time.time_ns()
    awake_end = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    boot_end = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    if not (0 <= boot_end - boot_start <= 250_000_000 and
            0 <= awake_end - awake_start <= boot_end - boot_start):
        raise RuntimeError('LINUX_CLOCK_SAMPLE_UNCERTAIN')
    sample = ClockSample(utc / 1e9, (boot_start + boot_end) / 2e9,
                         (awake_start + awake_end) / 2e9)
    if not sample.valid():
        raise RuntimeError('LINUX_CLOCK_SAMPLE_INVALID')
    return sample


def elapsed_linux():
    """Suspend-inclusive deadline clock for pairing and each HTTP operation."""
    if sys.platform != 'linux' or not hasattr(time, 'CLOCK_BOOTTIME'):
        raise RuntimeError('LINUX_SUSPEND_CLOCK_REQUIRED')
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1e9
