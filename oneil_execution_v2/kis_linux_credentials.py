"""Inactive Linux sealed-memory credential input; no writer or runtime wiring.

Only an explicitly supplied descriptor is read, after exact-scope local consent.
No path, environment variable, descriptor discovery, native keyring, persistent
store, token issuer or network implementation is provided. Trusted secret entry
and the separately authorized provisioning of the descriptor remain external.

The descriptor must be an unlinked, current-user-owned regular sealed object.
F_SEAL_WRITE/GROW/SHRINK/SEAL freeze its contents and seal set. Linux memfd is the
intended input, not a plaintext credential file. Sealing is not encryption or a
same-user/admin isolation boundary. Memory pages may be swapped or dumped.
Mutable read buffers are wiped; Python strings cannot be reliably zeroized.
"""
import hashlib
import hmac
import math
import os
import re
import stat
import sys
import threading
import time

from .domain import Blocked
from .kis_adapter import AccountBinding
from .kis_app_credentials import AppCredentials
from .kis_authenticated import Cancellation
from .kis_local_authorization import LocalPermission


_MIN_BYTES = 54
_MAX_BYTES = 2086
# Stable Linux UAPI values from linux/fcntl.h. Some otherwise compatible Python
# builds do not export these names. Unsupported kernels still fail the syscall;
# there is never a no-seals fallback.
_F_GET_SEALS = 1034
_REQUIRED_SEALS = 0x0008 | 0x0004 | 0x0002 | 0x0001
_CODES = frozenset((
    'KIS_LINUX_CREDENTIALS_PLATFORM_REQUIRED', 'KIS_LINUX_CREDENTIALS_INVALID',
    'KIS_LINUX_CREDENTIALS_PERMISSION_REQUIRED', 'KIS_LINUX_CREDENTIALS_CANCELLED',
    'KIS_LINUX_CREDENTIALS_DEADLINE_EXCEEDED', 'KIS_LINUX_CREDENTIALS_CLOCK_INVALID',
    'KIS_LINUX_CREDENTIALS_BUSY', 'KIS_LINUX_CREDENTIALS_INPUT_REJECTED',
    'KIS_LINUX_CREDENTIALS_READ_FAILED',
))


class _Failure(Exception):
    pass


def _require(value, code='KIS_LINUX_CREDENTIALS_INVALID'):
    if not value:
        raise _Failure(code)


def _code(exc):
    if (type(exc) is _Failure and len(exc.args) == 1 and
            type(exc.args[0]) is str and exc.args[0] in _CODES):
        return exc.args[0]
    return 'KIS_LINUX_CREDENTIALS_READ_FAILED'


def _digest(binding):
    _require(type(binding) is AccountBinding and all(type(v) is str for v in
             (binding.account, binding.environment, binding.cano, binding.product_code)))
    binding.validate()
    return hashlib.sha256('\0'.join((binding.account, binding.environment,
                          binding.cano, binding.product_code)).encode('ascii')).digest()


def _wipe(value):
    if type(value) is bytearray:
        value[:] = b'\0' * len(value)


class _Guard:
    def __init__(self, clock, binding, permission, deadline, cancellation):
        self.clock = clock; self.binding = binding; self.permission = permission
        self.deadline = deadline; self.cancellation = cancellation
        self.previous = None; self.lock = threading.Lock(); self.failure = None

    def abort(self, code='KIS_LINUX_CREDENTIALS_READ_FAILED'):
        with self.lock:
            if self.failure is None:
                self.failure = code if code in _CODES else 'KIS_LINUX_CREDENTIALS_READ_FAILED'

    def check(self):
        # Failures are sticky: an abandoned call cannot recover if a clock or
        # other dependency later becomes valid again.
        with self.lock:
            if self.failure is not None:
                raise _Failure(self.failure)
            try:
                self._check()
            except Exception as exc:
                self.failure = _code(exc)
                raise _Failure(self.failure) from None

    def _check(self):
        _require(type(self.cancellation) is Cancellation)
        _require(not self.cancellation.cancelled, 'KIS_LINUX_CREDENTIALS_CANCELLED')
        tick = self.clock()
        _require(type(tick) in (int, float) and math.isfinite(tick) and
                 0 <= tick <= 1e15 and
                 (self.previous is None or tick >= self.previous),
                 'KIS_LINUX_CREDENTIALS_CLOCK_INVALID')
        self.previous = tick
        _require(type(self.deadline) in (int, float) and math.isfinite(self.deadline) and
                 0 < self.deadline - tick <= 120,
                 'KIS_LINUX_CREDENTIALS_DEADLINE_EXCEEDED')
        _require(type(self.permission) is LocalPermission,
                 'KIS_LINUX_CREDENTIALS_PERMISSION_REQUIRED')
        try:
            self.permission.check(self.binding, 'read_app_credentials')
        except Exception:
            raise _Failure('KIS_LINUX_CREDENTIALS_PERMISSION_REQUIRED') from None


def _duplicate_descriptor(descriptor):
    import fcntl
    return fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)


def _read_sealed_descriptor(descriptor, guard):
    # Lazy import keeps imports inert and usable on non-Linux review machines.
    import fcntl
    buffer = None
    duplicate = descriptor
    try:
        guard.check()
        before = os.fstat(duplicate)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 0 and
                 before.st_uid == os.geteuid() and _MIN_BYTES <= before.st_size <= _MAX_BYTES,
                 'KIS_LINUX_CREDENTIALS_INPUT_REJECTED')
        seals = fcntl.fcntl(duplicate, _F_GET_SEALS)
        _require(seals & _REQUIRED_SEALS == _REQUIRED_SEALS,
                 'KIS_LINUX_CREDENTIALS_INPUT_REJECTED')
        guard.check()
        buffer = bytearray(before.st_size)
        # Position-independent, bounded mutable read: does not consume or alter
        # the caller's offset and avoids an extra immutable bytes read buffer.
        count = os.preadv(duplicate, [buffer], 0)
        guard.check()
        after = os.fstat(duplicate)
        _require(count == before.st_size and
                 (before.st_dev, before.st_ino, before.st_size, before.st_uid, before.st_nlink) ==
                 (after.st_dev, after.st_ino, after.st_size, after.st_uid, after.st_nlink),
                 'KIS_LINUX_CREDENTIALS_INPUT_REJECTED')
        result = buffer; buffer = None
        return result
    finally:
        _wipe(buffer)


def _decode(buffer, digest):
    _require(type(buffer) is bytearray and _MIN_BYTES <= len(buffer) <= _MAX_BYTES and
             buffer[:4] == b'ONL1' and hmac.compare_digest(buffer[4:36], digest),
             'KIS_LINUX_CREDENTIALS_INPUT_REJECTED')
    size = int.from_bytes(buffer[36:38], 'big')
    _require(8 <= size <= 1024 and 8 <= len(buffer) - 38 - size <= 1024,
             'KIS_LINUX_CREDENTIALS_INPUT_REJECTED')
    import codecs
    key = codecs.decode(memoryview(buffer)[38:38+size], 'ascii')
    secret = codecs.decode(memoryview(buffer)[38+size:], 'ascii')
    _require(all(re.fullmatch(r'[A-Za-z0-9._~+/=-]{8,1024}', v) for v in (key, secret)),
             'KIS_LINUX_CREDENTIALS_INPUT_REJECTED')
    return AppCredentials(key, secret)


def linux_credential_elapsed():
    """Suspend-inclusive Linux clock for the default reader's deadline argument.

    No platform fallback: CLOCK_MONOTONIC excludes suspension and cannot enforce
    the same elapsed-time bound. Calling this reads a clock, never credentials.
    """
    try:
        if sys.platform != 'linux' or not hasattr(time, 'CLOCK_BOOTTIME'):
            raise ValueError()
        return time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1e9
    except Exception:
        raise Blocked('KIS_LINUX_CREDENTIALS_CLOCK_INVALID') from None


class LinuxCredentialInput:
    """No-I/O, no-cache reader. Only explicit reads touch the supplied descriptor.

    This implements the app-pair input boundary, not CredentialProvider.acquire:
    it cannot issue a bearer. A local permission record is a misuse guard, not
    proof of human consent. Import/creation never authorizes or starts a read.
    Native calls are single-flight; cancellation abandons and wipes late buffers.
    No hard syscall interruption, memory encryption or zeroization is promised.
    """
    live_capable = False

    def __init__(self, *, monotonic=linux_credential_elapsed):
        self._clock = monotonic
        self._busy = threading.Lock()

    def __repr__(self):
        return 'LinuxCredentialInput(inactive=True, persistent_store=False)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_CREDENTIALS_SERIALIZATION_BLOCKED')

    def read(self, binding, *, descriptor, permission, deadline_monotonic, cancellation):
        result_slot = {'buffer': None}
        try:
            _require(sys.platform == 'linux', 'KIS_LINUX_CREDENTIALS_PLATFORM_REQUIRED')
            digest = _digest(binding)
            _require(type(descriptor) is int and 3 <= descriptor <= 1048575)
            guard = _Guard(self._clock, binding, permission, deadline_monotonic, cancellation)
            guard.check()
            self._run(descriptor, guard, result_slot)
            guard.check()
            result = _decode(result_slot['buffer'], digest)
            guard.check()
            return result
        except Exception as exc:
            raise Blocked(_code(exc)) from None
        finally:
            _wipe(result_slot['buffer'])

    def _run(self, descriptor, guard, result_slot):
        # Allocate coordination state before owning a lock/descriptor. Startup
        # allocation failure must not leak an FD or leave the reader busy.
        lock = threading.Lock(); done = threading.Event()
        state = {'abandoned': False, 'buffer': None, 'error': None,
                 'descriptor': None}

        def finish(buffer, error):
            with lock:
                state['error'] = error
                if state['abandoned'] or error is not None:
                    _wipe(buffer)
                else:
                    state['buffer'] = buffer
                self._busy.release()
                done.set()

        def worker():
            buffer = None; error = None
            with lock:
                owned = state['descriptor']; state['descriptor'] = None
            if owned is None:
                return  # An interrupted start already reclaimed and closed it.
            try:
                guard.check()
                buffer = _read_sealed_descriptor(owned, guard)
                guard.check()
            except BaseException as exc:
                error = _code(exc)
            finally:
                try:
                    os.close(owned)
                except BaseException:
                    error = 'KIS_LINUX_CREDENTIALS_READ_FAILED'
                finish(buffer, error)

        _require(self._busy.acquire(False), 'KIS_LINUX_CREDENTIALS_BUSY')
        duplicate = None
        try:
            guard.check()
            # Pin synchronously before entering the cancellation-return window.
            # After this point only this owned duplicate reaches the worker;
            # the caller may close/reuse its original as soon as read returns.
            duplicate = _duplicate_descriptor(descriptor)
            guard.check()
            state['descriptor'] = duplicate
        except BaseException:
            try:
                if duplicate is not None:
                    os.close(duplicate)
            finally:
                self._busy.release()
            raise
        try:
            threading.Thread(target=worker, name='kis-linux-credential-input', daemon=True).start()
        except BaseException:
            guard.abort()
            with lock:
                state['abandoned'] = True
                _wipe(state['buffer']); state['buffer'] = None
                owned = state['descriptor']; state['descriptor'] = None
            # Startup may be interrupted after the worker has claimed ownership.
            # Only whoever owns the descriptor may close it/release single-flight.
            if owned is not None:
                try:
                    os.close(owned)
                finally:
                    finish(None, 'KIS_LINUX_CREDENTIALS_READ_FAILED')
            raise
        try:
            while not done.wait(0.01):
                guard.check()
            guard.check()
            with lock:
                if state['error'] is not None:
                    raise _Failure(state['error'])
                # Transfer through a slot already owned by read's finally,
                # avoiding an interruption gap at a returned-buffer assignment.
                result_slot['buffer'] = state['buffer']; state['buffer'] = None
            return
        except BaseException as exc:
            # Includes KeyboardInterrupt/SystemExit: clean up but preserve the
            # caller's control-flow exception instead of treating it as a read.
            guard.abort(_code(exc))
            with lock:
                state['abandoned'] = True
                _wipe(state['buffer']); state['buffer'] = None
            raise
