"""Inactive, explicitly granted authenticated GET transport. No runtime wiring.

No import/construction I/O. OS DNS cannot be forcibly cancelled: only one daemon
lookup may remain outstanding; it never opens sockets. Network waits poll at
100 ms. OS close and trust-store loading are local operations, not hard realtime
cancellable. Python secret strings/buffers cannot be reliably zeroized. A grant
is a caller attestation, not proof of human consent or a sandbox boundary.
"""
import errno
import ipaddress
import re
import select
import socket
import ssl
import sys
import threading
import time

from .domain import Blocked
from .kis_adapter import AccountBinding
from .kis_authenticated import AuthenticatedGet, Cancellation
from .kis_local_authorization import LocalPermission
from . import kis_https as base

_POLL = 0.1
_DNS_SLOT = threading.BoundedSemaphore(1)
_CODES = frozenset(('KIS_AUTH_HTTPS_GRANT_INVALID', 'KIS_AUTH_HTTPS_CANCELLED',
                    'KIS_AUTH_HTTPS_REQUEST_BLOCKED', 'KIS_AUTH_HTTPS_IO_FAILED',
                    'KIS_AUTH_HTTPS_WINDOWS_REQUIRED', 'KIS_AUTH_HTTPS_LINUX_REQUIRED'))


class _Failure(Exception):
    pass


def _check(value, code):
    if not value:
        raise _Failure(code)


def _safe_error(exc):
    if type(exc) is _Failure and len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in _CODES:
        return exc.args[0]
    if type(exc) is base._Failure:
        return base._safe_error(exc)
    return 'KIS_AUTH_HTTPS_IO_FAILED'


class _Deadline(base._Deadline):
    def __init__(self, request, monotonic, cancellation, grant_check):
        self.cancellation = cancellation
        self.grant_check = grant_check
        super().__init__(request, monotonic)
        self.check()

    def remaining(self, phase_seconds, *, phase_end=None):
        _check(not self.cancellation.cancelled, 'KIS_AUTH_HTTPS_CANCELLED')
        self.grant_check()
        return super().remaining(phase_seconds, phase_end=phase_end)


def _resolve(host, port, deadline, seconds):
    slot = _DNS_SLOT
    base._check(slot.acquire(blocking=False), 'KIS_HTTPS_DNS_UNAVAILABLE')
    done = threading.Event(); abandoned = threading.Event(); outcome = []

    def lookup():
        try:
            if not abandoned.is_set():
                outcome.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM,
                                                  proto=socket.IPPROTO_TCP))
        except Exception:
            pass
        finally:
            slot.release(); done.set()

    started = False
    try:
        deadline.check()
        thread = threading.Thread(target=lookup, daemon=True, name='kis-auth-dns')
        thread.start(); started = True
        while not done.is_set():
            done.wait(min(_POLL, deadline.remaining(seconds, phase_end=deadline.connect_end)))
        deadline.remaining(seconds, phase_end=deadline.connect_end)
    finally:
        abandoned.set()
        if not started:
            slot.release()
    base._check(len(outcome) == 1 and type(outcome[0]) is list and 1 <= len(outcome[0]) <= 64,
                'KIS_HTTPS_DNS_UNAVAILABLE')
    entry = outcome[0][0]
    base._check(type(entry) is tuple and len(entry) == 5, 'KIS_HTTPS_ADDRESS_BLOCKED')
    family, kind, protocol, _, address = entry
    base._check(family in (socket.AF_INET, socket.AF_INET6) and kind == socket.SOCK_STREAM and
                protocol == socket.IPPROTO_TCP and type(address) is tuple and
                len(address) == (2 if family == socket.AF_INET else 4) and
                type(address[0]) is str and type(address[1]) is int and address[1] == port,
                'KIS_HTTPS_ADDRESS_BLOCKED')
    ip = ipaddress.ip_address(address[0])
    base._check(ip.is_global and not ip.is_multicast and not ip.is_unspecified and
                ip.version == (4 if family == socket.AF_INET else 6) and
                (family != socket.AF_INET6 or address[2:] == (0, 0)), 'KIS_HTTPS_ADDRESS_BLOCKED')
    return family, address


def _wait(stream, deadline, end, *, writing=False):
    timeout = min(_POLL, deadline.remaining(30, phase_end=end))
    ready = select.select([] if writing else [stream], [stream] if writing else [], [stream], timeout)
    deadline.remaining(30, phase_end=end)
    return any(ready)


def _operation(stream, action, deadline, end, *, writing=False):
    while True:
        deadline.remaining(30, phase_end=end)
        try:
            result = action()
            deadline.remaining(30, phase_end=end)
            return result
        except ssl.SSLWantWriteError:
            _wait(stream, deadline, end, writing=True)
        except ssl.SSLWantReadError:
            _wait(stream, deadline, end)
        except BlockingIOError:
            _wait(stream, deadline, end, writing=writing)


class _Stream:
    def __init__(self, stream, deadline, seconds):
        self._stream = stream; self._deadline = deadline; self._seconds = seconds

    def settimeout(self, seconds):
        # Parser compatibility only: actual socket must remain nonblocking.
        pass

    def recv(self, maximum):
        seconds = self._deadline.remaining(self._seconds)
        end = self._deadline._previous + seconds
        return _operation(self._stream, lambda: self._stream.recv(maximum), self._deadline, end)

    def send(self, wire):
        seconds = self._deadline.remaining(self._seconds)
        end = self._deadline._previous + seconds
        view = memoryview(wire)
        try:
            while view:
                count = _operation(self._stream, lambda: self._stream.send(view), self._deadline, end, writing=True)
                base._check(type(count) is int and 0 < count <= len(view), 'KIS_HTTPS_IO_FAILED')
                view = view[count:]
        finally:
            view.release()

    def close(self):
        stream, self._stream = self._stream, None
        base._safe_close(stream)  # no unwrap, flush, retry, or TLS shutdown


def _connect(host, port, deadline, connect_seconds, read_seconds):
    """Internal socket helper, not an HTTP API. Caller validates official origin."""
    _check(sys.platform == 'win32', 'KIS_AUTH_HTTPS_WINDOWS_REQUIRED')
    return _connect_official(host, port, deadline, connect_seconds, read_seconds)


def _connect_official(host, port, deadline, connect_seconds, read_seconds):
    """Shared private socket mechanics. Platform entrypoints enforce their gates."""
    raw = None; secured = None
    try:
        base._check((host, port) in (('openapi.koreainvestment.com', 9443),
                    ('openapivts.koreainvestment.com', 29443)), 'KIS_HTTPS_REQUEST_BLOCKED')
        deadline.check(); context = base._tls_context(); deadline.check()
        family, address = _resolve(host, port, deadline, connect_seconds)
        raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        raw.setblocking(False)
        deadline.remaining(connect_seconds, phase_end=deadline.connect_end)
        result = raw.connect_ex(address)
        pending = (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                   getattr(errno, 'WSAEWOULDBLOCK', 10035), getattr(errno, 'WSAEINPROGRESS', 10036))
        while result in pending:
            if _wait(raw, deadline, deadline.connect_end, writing=True):
                result = raw.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        base._check(result == 0, 'KIS_HTTPS_IO_FAILED')
        deadline.remaining(connect_seconds, phase_end=deadline.connect_end)
        secured = context.wrap_socket(raw, server_hostname=host, do_handshake_on_connect=False,
                                      suppress_ragged_eofs=False)
        raw = None
        secured.setblocking(False)
        _operation(secured, secured.do_handshake, deadline, deadline.connect_end)
        base._check(secured.version() in ('TLSv1.2', 'TLSv1.3') and
                    secured.selected_alpn_protocol() in (None, 'http/1.1'), 'KIS_HTTPS_TLS_FAILED')
        stream = _Stream(secured, deadline, read_seconds); secured = None
        return stream
    finally:
        base._safe_close(secured); base._safe_close(raw)


class _Response(base._Response):
    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_AUTH_SERIALIZATION_BLOCKED')

    def iter_bytes(self, chunk_bytes):
        try:
            base._check(not self._closed and not self._started and type(chunk_bytes) is int and
                        1 <= chunk_bytes <= self._request.chunk_bytes, 'KIS_HTTPS_RESPONSE_CLOSED')
            self._started = True
            remaining = self._length
            pending = self._pending; self._pending = b''
            while pending:
                self._deadline.check()
                chunk, pending = pending[:chunk_bytes], pending[chunk_bytes:]
                remaining -= len(chunk)
                yield chunk
            while remaining:
                chunk = base._recv(self._stream, min(chunk_bytes, remaining), self._deadline,
                                   self._request.limits.read_seconds)
                base._check(bool(chunk), 'KIS_HTTPS_BODY_INVALID')
                remaining -= len(chunk)
                yield chunk
            self._deadline.check()
        except Exception as exc:
            raise Blocked(_safe_error(exc)) from None
        finally:
            self.close()


class KisAuthenticatedHttpsGetClient:
    def __init__(self, binding, *, permission, monotonic=time.monotonic):
        try:
            _check(type(binding) is AccountBinding and all(type(v) is str for v in
                    (binding.account, binding.environment, binding.cano, binding.product_code)) and
                    callable(monotonic) and type(permission) is LocalPermission,
                    'KIS_AUTH_HTTPS_GRANT_INVALID')
            binding.validate()
        except Exception:
            raise Blocked('KIS_AUTH_HTTPS_GRANT_INVALID') from None
        self._binding = binding; self._permission = permission; self._clock = monotonic

    def __repr__(self):
        return 'KisAuthenticatedHttpsGetClient(GET_only=True, inactive=True)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_AUTH_SERIALIZATION_BLOCKED')

    def _check_grant(self):
        try:
            self._permission.check(self._binding, 'authenticated_get')
        except Exception:
            raise _Failure('KIS_AUTH_HTTPS_GRANT_INVALID') from None

    def _check_platform(self):
        _check(sys.platform == 'win32', 'KIS_AUTH_HTTPS_WINDOWS_REQUIRED')

    def _connect(self, host, port, deadline, connect_seconds, read_seconds):
        return _connect(host, port, deadline, connect_seconds, read_seconds)

    def open_authenticated_get(self, envelope):
        stream = None; wire = None
        try:
            self._check_platform()
            _check(type(envelope) is AuthenticatedGet and type(envelope.cancellation) is Cancellation,
                    'KIS_AUTH_HTTPS_REQUEST_BLOCKED')
            host, port, public_wire = base._request_target(envelope.request, self._binding)
            self._check_grant()
            for secret in (envelope.app_key, envelope.app_secret, envelope.bearer):
                _check(type(secret) is str and re.fullmatch(r'[A-Za-z0-9._~+/=-]{8,4096}', secret),
                        'KIS_AUTH_HTTPS_REQUEST_BLOCKED')
            deadline = _Deadline(envelope.request, self._clock, envelope.cancellation, self._check_grant)
            stream = self._connect(host, port, deadline, envelope.request.limits.connect_seconds,
                              envelope.request.limits.read_seconds)
            deadline.check()
            wire = public_wire[:-2] + (f'authorization: Bearer {envelope.bearer}\r\n'
                f'appkey: {envelope.app_key}\r\nappsecret: {envelope.app_secret}\r\n\r\n').encode('ascii')
            stream.send(wire); wire = None
            status, headers, pending, length = base._read_headers(stream, envelope.request, deadline)
            deadline.check()
            response = _Response(status, envelope.request.url, headers, stream, pending, length,
                                 envelope.request, deadline)
            stream = None
            if status != 200:
                response.close()
            return response
        except Exception as exc:
            raise Blocked(_safe_error(exc)) from None
        finally:
            wire = None
            base._safe_close(stream)
