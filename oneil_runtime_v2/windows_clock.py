"""Read Windows clocks without changing power, time or security settings."""
import os
import time
from .health import ClockSample


def sample_windows_clock():
    if os.name != 'nt':
        raise RuntimeError('WINDOWS_CLOCK_UNAVAILABLE')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    ticks = kernel.GetTickCount64
    ticks.argtypes = []
    ticks.restype = ctypes.c_ulonglong
    awake = kernel.QueryUnbiasedInterruptTime
    awake.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    awake.restype = wintypes.BOOL
    value = ctypes.c_ulonglong()
    elapsed = ticks() / 1000.0
    if not awake(ctypes.byref(value)):
        raise RuntimeError('WINDOWS_AWAKE_CLOCK_UNAVAILABLE')
    return ClockSample(time.time(), elapsed, value.value / 10_000_000.0)
