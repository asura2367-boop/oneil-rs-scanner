"""Inactive, local-only Windows Credential Manager boundary. No runtime wiring.

Construction/import perform no I/O. Explicit operation-specific authorization is
required even for reads; these records represent trusted caller consent, not an
OS access-control boundary. Only app credentials are stored, never bearer tokens.
No environment/file/account-discovery fallback exists. Targets hash the exact
account fingerprint, CANO, product and environment. Windows generic credentials
are protected for the logged-in Windows user, not from other same-user processes.

Native calls run single-flight on a daemon worker: callers can cancel/time out,
but Win32 Credential APIs cannot cancel an in-flight syscall. A timed-out write
has UNKNOWN outcome and must not be blindly retried. Late read buffers are wiped.
Native and mutable buffers are scrubbed; Python strings/allocator copies cannot
be reliably zeroized. Never log, serialize, inspect locals or crash-dump secrets.
A trusted token issuer must separately enforce its authorization, deadlines and
cancellation. This module is not permission to create persistent access or trade.
"""
import ctypes
import hashlib
import math
import re
import sys
import threading
import time

from .domain import Blocked
from .kis_adapter import AccountBinding
from .kis_authenticated import Cancellation, CredentialLease
from .kis_local_authorization import LocalPermission


_PUBLIC_CODES = frozenset((
    'KIS_CREDENTIALS_INVALID', 'KIS_CREDENTIALS_WINDOWS_REQUIRED',
    'KIS_CREDENTIALS_AUTHORIZATION_REQUIRED', 'KIS_CREDENTIALS_CANCELLED',
    'KIS_CREDENTIALS_DEADLINE_EXCEEDED', 'KIS_CREDENTIALS_BUSY',
    'KIS_CREDENTIALS_OPERATION_FAILED', 'KIS_CREDENTIALS_READ_FAILED',
    'KIS_CREDENTIALS_WRITE_FAILED', 'KIS_CREDENTIALS_WRITE_OUTCOME_UNKNOWN',
))


class _Failure(Exception):
    pass


def _require(ok, code='KIS_CREDENTIALS_INVALID'):
    if not ok:
        raise _Failure(code)


def _binding(binding):
    _require(type(binding) is AccountBinding)
    _require(all(type(v) is str for v in (binding.account, binding.environment,
                                         binding.cano, binding.product_code)))
    binding.validate()


def credential_target(binding):
    """Deterministic exact-scope identifier; no secret or plain account number."""
    try:
        _binding(binding)
        digest = hashlib.sha256('\0'.join((binding.account, binding.environment,
                  binding.cano, binding.product_code)).encode('ascii')).hexdigest()
        return 'oneil-execution-v2/kis/app/v1/' + digest
    except Exception:
        raise Blocked('KIS_CREDENTIALS_INVALID') from None


class AppCredentials:
    """Ephemeral, redacted pair; no __dict__ or dataclass serialization surface."""
    __slots__ = ('app_key', 'app_secret')

    def __init__(self, app_key, app_secret):
        self.app_key = app_key
        self.app_secret = app_secret

    def __repr__(self):
        return 'AppCredentials(redacted=True)'

    def __eq__(self, other):
        return (type(other) is AppCredentials and self.app_key == other.app_key
                and self.app_secret == other.app_secret)

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_CREDENTIALS_SERIALIZATION_BLOCKED')


def _credentials(value):
    _require(type(value) is AppCredentials)
    for secret in (value.app_key, value.app_secret):
        _require(type(secret) is str and re.fullmatch(r'[A-Za-z0-9._~+/=-]{8,1024}', secret))


def _wipe(value):
    if isinstance(value, bytearray):
        value[:] = b'\0' * len(value)


def _encode(value):
    _credentials(value)
    key = value.app_key.encode('ascii')
    return bytearray(b'ONK1' + len(key).to_bytes(2, 'big') + key + value.app_secret.encode('ascii'))


def _decode(blob):
    _require(type(blob) is bytearray and 22 <= len(blob) <= 2054 and blob[:4] == b'ONK1')
    size = int.from_bytes(blob[4:6], 'big')
    _require(8 <= size <= 1024 and 8 <= len(blob) - 6 - size <= 1024)
    result = AppCredentials(blob[6:6+size].decode('ascii'), blob[6+size:].decode('ascii'))
    _credentials(result)
    return result


class _FileTime(ctypes.Structure):
    _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]


class _Credential(ctypes.Structure):
    _fields_ = [('Flags', ctypes.c_uint32), ('Type', ctypes.c_uint32),
                ('TargetName', ctypes.c_wchar_p), ('Comment', ctypes.c_wchar_p),
                ('LastWritten', _FileTime), ('CredentialBlobSize', ctypes.c_uint32),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                ('Persist', ctypes.c_uint32), ('AttributeCount', ctypes.c_uint32),
                ('Attributes', ctypes.c_void_p), ('TargetAlias', ctypes.c_wchar_p),
                ('UserName', ctypes.c_wchar_p)]


class _Win32Backend:
    """Lazy native boundary. Tests inject synthetic API functions, never real DLLs."""
    def _api(self):
        _require(sys.platform == 'win32', 'KIS_CREDENTIALS_WINDOWS_REQUIRED')
        # Absolute system DLL search flag prevents current-directory DLL loading.
        api = ctypes.WinDLL('advapi32.dll', use_last_error=True, winmode=0x800)
        api.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.POINTER(_Credential))]
        api.CredReadW.restype = ctypes.c_int
        api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), ctypes.c_uint32]
        api.CredWriteW.restype = ctypes.c_int
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api

    def read(self, target):
        api = self._api()
        pointer = ctypes.POINTER(_Credential)()
        try:
            _require(api.CredReadW(target, 1, 0, ctypes.byref(pointer)), 'KIS_CREDENTIALS_READ_FAILED')
            _require(bool(pointer))
            item = pointer.contents
            _require(item.Type == 1 and item.TargetName == target and item.Persist == 2
                     and item.AttributeCount == 0 and item.UserName == 'oneil-kis-app-v1')
            _require(22 <= item.CredentialBlobSize <= 2054 and bool(item.CredentialBlob))
            result = bytearray(item.CredentialBlobSize)
            ctypes.memmove((ctypes.c_ubyte * len(result)).from_buffer(result),
                           item.CredentialBlob, len(result))
            return result
        finally:
            if pointer:
                item = pointer.contents
                # Trust Win32 allocation metadata only within its documented bound.
                if item.CredentialBlob and 0 < item.CredentialBlobSize <= 2560:
                    ctypes.memset(item.CredentialBlob, 0, item.CredentialBlobSize)
                api.CredFree(pointer)

    def write(self, target, blob):
        api = self._api()
        native = (ctypes.c_ubyte * len(blob)).from_buffer(blob)
        item = _Credential(Type=1, TargetName=target, CredentialBlobSize=len(blob),
                           CredentialBlob=native, Persist=2, UserName='oneil-kis-app-v1')
        try:
            _require(api.CredWriteW(ctypes.byref(item), 0), 'KIS_CREDENTIALS_WRITE_FAILED')
        finally:
            ctypes.memset(native, 0, len(blob))


class WindowsCredentialStore:
    """No enumeration/delete/credential creation; writes only user-supplied values.

    backend and monotonic injection are for offline testing/trusted composition.
    Even an injected backend is rejected outside Windows. Never inject a platform
    override in deployed code; tests patch sys.platform explicitly.
    """
    def __init__(self, *, backend=None, monotonic=time.monotonic):
        self._backend = _Win32Backend() if backend is None else backend
        self._clock = monotonic
        self._busy = threading.Lock()

    def _check(self, deadline, cancellation):
        _require(type(cancellation) is Cancellation)
        _require(not cancellation.cancelled, 'KIS_CREDENTIALS_CANCELLED')
        now = self._clock()
        _require(type(now) in (int, float) and math.isfinite(now) and now >= 0)
        _require(type(deadline) in (int, float) and math.isfinite(deadline) and
                 0 < deadline - now <= 120, 'KIS_CREDENTIALS_DEADLINE_EXCEEDED')

    def _run(self, binding, permission, operation, deadline, cancellation, blob=None):
        _require(sys.platform == 'win32', 'KIS_CREDENTIALS_WINDOWS_REQUIRED')
        _binding(binding)
        _require(type(permission) is LocalPermission, 'KIS_CREDENTIALS_AUTHORIZATION_REQUIRED')
        action = 'read_app_credentials' if operation == 'read' else 'store_app_credentials'
        permission.check(binding, action)
        self._check(deadline, cancellation)
        target = credential_target(binding)
        _require(self._busy.acquire(False), 'KIS_CREDENTIALS_BUSY')
        worker_blob = None if blob is None else bytearray(blob)
        lock = threading.Lock(); done = threading.Event()
        state = {'abandoned': False, 'result': None, 'failed': False, 'permission_use': None}

        def check_permission():
            # Consume and receipt publication share this lock, so a poll never
            # mistakes our consumed write for a forbidden second operation.
            with lock:
                use = state['permission_use']
                if use is None:
                    permission.check(binding, action)
                else:
                    use.check()

        def worker():
            result = None
            try:
                self._check(deadline, cancellation)
                permission.check(binding, action)
                if operation == 'read':
                    result = self._backend.read(target)
                else:
                    with lock:
                        _require(not state['abandoned'], 'KIS_CREDENTIALS_CANCELLED')
                        state['permission_use'] = permission.consume(binding, action)
                    check_permission()
                    self._backend.write(target, worker_blob)
            except Exception:
                with lock:
                    state['failed'] = True
            finally:
                _wipe(worker_blob)
                with lock:
                    if state['abandoned']:
                        _wipe(result)
                    else:
                        state['result'] = result
                    done.set()
                self._busy.release()

        thread = threading.Thread(target=worker, daemon=True, name='kis-credential-operation')
        try:
            thread.start()
        except Exception:
            _wipe(worker_blob)
            self._busy.release()
            raise _Failure('KIS_CREDENTIALS_OPERATION_FAILED') from None
        try:
            while not done.wait(0.01):
                self._check(deadline, cancellation)
                check_permission()
            self._check(deadline, cancellation)
            check_permission()
            with lock:
                _require(not state['failed'], 'KIS_CREDENTIALS_OPERATION_FAILED')
                result = state['result']; state['result'] = None
            return result
        except Exception:
            with lock:
                state['abandoned'] = True
                _wipe(state['result']); state['result'] = None
            # Writes may have reached Windows even when cancellation won the race.
            if operation == 'write':
                raise _Failure('KIS_CREDENTIALS_WRITE_OUTCOME_UNKNOWN') from None
            raise

    def read(self, binding, *, permission, deadline_monotonic, cancellation):
        blob = None
        try:
            blob = self._run(binding, permission, 'read', deadline_monotonic, cancellation)
            return _decode(blob)
        except _Failure as exc:
            code = exc.args[0] if (len(exc.args) == 1 and type(exc.args[0]) is str
                                  and exc.args[0] in _PUBLIC_CODES) else 'KIS_CREDENTIALS_OPERATION_FAILED'
            raise Blocked(code) from None
        except Exception:
            raise Blocked('KIS_CREDENTIALS_READ_FAILED') from None
        finally:
            _wipe(blob)

    def write(self, binding, credentials, *, permission, deadline_monotonic, cancellation):
        blob = None
        try:
            blob = _encode(credentials)
            self._run(binding, permission, 'write', deadline_monotonic, cancellation, blob)
        except _Failure as exc:
            code = exc.args[0] if (len(exc.args) == 1 and type(exc.args[0]) is str
                                  and exc.args[0] in _PUBLIC_CODES) else 'KIS_CREDENTIALS_OPERATION_FAILED'
            raise Blocked(code) from None
        except Exception:
            raise Blocked('KIS_CREDENTIALS_WRITE_FAILED') from None
        finally:
            _wipe(blob)


class WindowsCredentialProvider:
    """Inactive composition with an explicitly authorized, trusted token issuer.

    Read permission does NOT authorize the issuer's network or persistent
    access. Issuer must enforce those independently. No bearer cache/persistence.
    """
    def __init__(self, store, issuer, permission):
        self._store = store; self._issuer = issuer; self._permission = permission

    def acquire(self, binding, *, minimum_validity_seconds, deadline_monotonic, cancellation):
        try:
            _require(type(minimum_validity_seconds) in (int, float) and
                     math.isfinite(minimum_validity_seconds) and 0 < minimum_validity_seconds <= 86400)
            _require(type(self._permission) is LocalPermission)
            self._permission.check(binding, 'issue_token')
            credentials = self._store.read(binding, permission=self._permission,
                deadline_monotonic=deadline_monotonic, cancellation=cancellation)
            lease = self._issuer.issue(binding, credentials, permission=self._permission,
                minimum_validity_seconds=minimum_validity_seconds,
                deadline_monotonic=deadline_monotonic, cancellation=cancellation)
            _require(type(lease) is CredentialLease and lease.binding == binding and
                     lease.app_key == credentials.app_key and lease.app_secret == credentials.app_secret)
            self._store._check(deadline_monotonic, cancellation)
            return lease
        except Exception:
            raise Blocked('KIS_CREDENTIALS_PROVIDER_FAILED') from None
