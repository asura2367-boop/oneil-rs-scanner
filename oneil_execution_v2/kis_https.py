"""Concrete, credential-free HTTPS GET client. Never activated or called live here.

Only exact KIS read recipes are accepted. No auth/token management, HTTP POST,
redirects, proxies, retries, connection reuse or runtime registration exists.
Tests must mock DNS, TCP and TLS; importing/constructing this class performs no I/O.
"""
from dataclasses import dataclass
import ipaddress
import math
import os
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from .domain import Blocked
from .kis_adapter import AccountBinding, RequestRecipe
from .kis_transport import (DEMO_ORIGIN, REAL_ORIGIN, OutboundGet, ReadLimits,
                            _validated_recipe)

_CODES = frozenset(('KIS_HTTPS_REQUEST_BLOCKED', 'KIS_HTTPS_CONFIG_BLOCKED',
                    'KIS_HTTPS_DEADLINE_EXCEEDED', 'KIS_HTTPS_DNS_UNAVAILABLE',
                    'KIS_HTTPS_ADDRESS_BLOCKED', 'KIS_HTTPS_TLS_FAILED',
                    'KIS_HTTPS_HEADERS_INVALID', 'KIS_HTTPS_REDIRECT_BLOCKED',
                    'KIS_HTTPS_FRAMING_UNSUPPORTED', 'KIS_HTTPS_BODY_INVALID',
                    'KIS_HTTPS_IO_FAILED', 'KIS_HTTPS_RESPONSE_CLOSED'))
_DNS_SLOT = threading.BoundedSemaphore(1)


class _Failure(Exception):
    pass


def _check(condition, code):
    if not condition:
        raise _Failure(code)


def _safe_error(exc):
    if type(exc) is _Failure and len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in _CODES:
        return exc.args[0]
    return 'KIS_HTTPS_IO_FAILED'


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _safe_close(stream):
    if stream is not None:
        try:
            # No TLS unwrap/shutdown handshake or further HTTP exchange.
            stream.close()
        except Exception:
            pass


class _Deadline:
    def __init__(self, request, monotonic):
        self._clock = monotonic
        now = self._clock()
        _check(_number(now) and now >= 0 and _number(request.deadline_monotonic) and
               0 < request.deadline_monotonic - now <= request.limits.total_seconds,
               'KIS_HTTPS_DEADLINE_EXCEEDED')
        self._previous = now
        self._end = request.deadline_monotonic
        self.connect_end = min(self._end, now + request.limits.connect_seconds)

    def remaining(self, phase_seconds, *, phase_end=None):
        now = self._clock()
        end = min(self._end, self._end if phase_end is None else phase_end)
        _check(_number(now) and self._previous <= now < end, 'KIS_HTTPS_DEADLINE_EXCEEDED')
        self._previous = now
        return min(phase_seconds, end - now)

    def check(self):
        self.remaining(30)


def _request_target(request, binding):
    _check(type(request) is OutboundGet and type(request.limits) is ReadLimits,
           'KIS_HTTPS_REQUEST_BLOCKED')
    request.limits.validate()
    _check(type(request.method) is str and request.method == 'GET' and request.verify_tls is True and request.follow_redirects is False and
           request.use_environment is False and type(request.automatic_retries) is int and request.automatic_retries == 0 and
           type(request.chunk_bytes) is int and request.chunk_bytes == 16384 and
           type(request.max_header_bytes) is int and request.max_header_bytes == 16384 and
           type(request.max_header_count) is int and request.max_header_count == 64 and
           type(request.environment) is str and request.environment == binding.environment and
           type(request.endpoint) is str and type(request.url) is str and len(request.url) <= 8192 and
           type(request.headers) is tuple and len(request.headers) == 5, 'KIS_HTTPS_REQUEST_BLOCKED')
    headers = {}
    for pair in request.headers:
        _check(type(pair) is tuple and len(pair) == 2 and all(type(v) is str for v in pair) and
               pair[0] not in headers, 'KIS_HTTPS_REQUEST_BLOCKED')
        headers[pair[0]] = pair[1]
    expected_headers = (('accept', 'application/json'), ('accept-encoding', 'identity'),
                        ('tr_id', headers.get('tr_id')), ('tr_cont', headers.get('tr_cont')), ('custtype', 'P'))
    _check(request.headers == expected_headers, 'KIS_HTTPS_REQUEST_BLOCKED')
    origin = REAL_ORIGIN if binding.environment == 'real' else DEMO_ORIGIN
    parsed = urlsplit(request.url)
    pairs = tuple(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                           encoding='ascii', errors='strict', max_num_fields=32))
    recipe = RequestRecipe('GET', request.endpoint, headers.get('tr_id'), pairs, headers.get('tr_cont'))
    _validated_recipe(recipe, binding)
    _check(request.url == origin + request.endpoint + '?' + urlencode(pairs) and
           parsed.scheme == 'https' and parsed.path == request.endpoint and not parsed.fragment and
           parsed.username is None and parsed.password is None,
           'KIS_HTTPS_REQUEST_BLOCKED')
    host = 'openapi.koreainvestment.com' if binding.environment == 'real' else 'openapivts.koreainvestment.com'
    port = 9443 if binding.environment == 'real' else 29443
    _check(parsed.netloc == f'{host}:{port}', 'KIS_HTTPS_REQUEST_BLOCKED')
    target = parsed.path + '?' + parsed.query
    # Fresh direct connection. No generic request API, request body or caller Host.
    lines = [f'GET {target} HTTP/1.1', f'Host: {host}:{port}', 'Connection: close']
    lines.extend(f'{key}: {value}' for key, value in request.headers)
    return host, port, ('\r\n'.join(lines) + '\r\n\r\n').encode('ascii')


def _tls_context():
    # SSLContext avoids create_default_context's SSLKEYLOGFILE auto-enable path.
    # Reject ambient trust-store replacement rather than mutating process env.
    _check(not any(key in os.environ for key in ('SSL_CERT_FILE', 'SSL_CERT_DIR')),
           'KIS_HTTPS_CONFIG_BLOCKED')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.keylog_filename = None
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_default_certs(ssl.Purpose.SERVER_AUTH)
    context.set_alpn_protocols(['http/1.1'])
    return context


def _resolve(host, port, deadline, connect_seconds):
    """Bound caller DNS wait; a timed-out DNS thread never opens a connection.

    OS resolver cancellation is unavailable. A single daemon slot remains occupied
    until that resolver exits, preventing unbounded abandoned lookup threads.
    """
    slot = _DNS_SLOT
    _check(slot.acquire(blocking=False), 'KIS_HTTPS_DNS_UNAVAILABLE')
    done = threading.Event(); cancelled = threading.Event(); outcome = []

    def lookup():
        try:
            if not cancelled.is_set():
                outcome.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP))
        except Exception:
            pass  # Never retain/propagate resolver exception messages.
        finally:
            slot.release()
            done.set()

    try:
        timeout = deadline.remaining(connect_seconds, phase_end=deadline.connect_end)
        thread = threading.Thread(target=lookup, name='kis-readonly-dns', daemon=True)
        thread.start()
    except Exception:
        slot.release()
        raise
    if not done.wait(timeout):
        cancelled.set()
        raise _Failure('KIS_HTTPS_DNS_UNAVAILABLE')
    deadline.remaining(connect_seconds, phase_end=deadline.connect_end)
    _check(len(outcome) == 1 and type(outcome[0]) is list and 1 <= len(outcome[0]) <= 64,
           'KIS_HTTPS_DNS_UNAVAILABLE')
    # One address, one connect attempt. No hidden multi-address retries.
    entry = outcome[0][0]
    _check(type(entry) is tuple and len(entry) == 5, 'KIS_HTTPS_ADDRESS_BLOCKED')
    family, kind, protocol, _, address = entry
    _check(family in (socket.AF_INET, socket.AF_INET6) and kind == socket.SOCK_STREAM and
           protocol == socket.IPPROTO_TCP and type(address) is tuple and
           len(address) == (2 if family == socket.AF_INET else 4) and
           type(address[0]) is str and type(address[1]) is int and address[1] == port,
           'KIS_HTTPS_ADDRESS_BLOCKED')
    ip = ipaddress.ip_address(address[0])
    _check(ip.is_global and not ip.is_multicast and not ip.is_unspecified and
           ip.version == (4 if family == socket.AF_INET else 6) and
           (family != socket.AF_INET6 or address[2:] == (0, 0)), 'KIS_HTTPS_ADDRESS_BLOCKED')
    return family, address


def _recv(stream, maximum, deadline, read_seconds):
    stream.settimeout(deadline.remaining(read_seconds))
    data = stream.recv(maximum)
    deadline.check()
    _check(type(data) is bytes and len(data) <= maximum, 'KIS_HTTPS_BODY_INVALID')
    return data


def _read_headers(stream, request, deadline):
    buffer = bytearray()
    while True:
        marker = buffer.find(b'\r\n\r\n')
        if marker >= 0:
            _check(marker + 4 <= request.max_header_bytes, 'KIS_HTTPS_HEADERS_INVALID')
            head = bytes(buffer[:marker]); pending = bytes(buffer[marker + 4:])
            break
        _check(len(buffer) < request.max_header_bytes, 'KIS_HTTPS_HEADERS_INVALID')
        chunk = _recv(stream, min(request.chunk_bytes, request.max_header_bytes - len(buffer)),
                      deadline, request.limits.read_seconds)
        _check(bool(chunk), 'KIS_HTTPS_HEADERS_INVALID')
        buffer.extend(chunk)
    lines = head.split(b'\r\n')
    _check(len(lines) <= request.max_header_count + 1 and all(b'\r' not in line and b'\n' not in line for line in lines),
           'KIS_HTTPS_HEADERS_INVALID')
    status_line = re.fullmatch(rb'HTTP/1\.[01] ([0-9]{3}) [ -~]{0,256}', lines[0])
    _check(status_line is not None, 'KIS_HTTPS_HEADERS_INVALID')
    status = int(status_line[1])
    _check(200 <= status <= 599, 'KIS_HTTPS_HEADERS_INVALID')  # no interim-response loop
    headers = {}
    for line in lines[1:]:
        _check(len(line) <= 8192 and b':' in line, 'KIS_HTTPS_HEADERS_INVALID')
        key, value = line.split(b':', 1)
        _check(re.fullmatch(rb'[A-Za-z0-9_-]{1,80}', key) is not None and
               re.fullmatch(rb'[\t -~]*', value) is not None, 'KIS_HTTPS_HEADERS_INVALID')
        name = key.decode('ascii').lower()
        _check(name not in headers, 'KIS_HTTPS_HEADERS_INVALID')
        headers[name] = value.strip(b' \t').decode('ascii')
    _check(not 300 <= status <= 399 and 'location' not in headers, 'KIS_HTTPS_REDIRECT_BLOCKED')
    if status != 200:
        # Preserve 429 status/cooldown even when its ignored body is compressed or
        # chunked. We close now, so those body framing details are not consumed.
        return status, headers, b'', 0
    _check('transfer-encoding' not in headers and headers.get('content-encoding', 'identity').lower() == 'identity',
           'KIS_HTTPS_FRAMING_UNSUPPORTED')
    length = headers.get('content-length')
    _check(type(length) is str and re.fullmatch(r'[0-9]{1,10}', length) is not None,
           'KIS_HTTPS_FRAMING_UNSUPPORTED')
    length = int(length)
    _check(length <= request.limits.max_body_bytes and len(pending) <= length, 'KIS_HTTPS_BODY_INVALID')
    _check(re.fullmatch(r'application/json(?:\s*;\s*charset=utf-8)?', headers.get('content-type', '').lower()) is not None,
           'KIS_HTTPS_FRAMING_UNSUPPORTED')
    return status, headers, pending, length


@dataclass(repr=False)
class _Response:
    status_code: int
    url: str
    headers: dict
    _stream: object
    _pending: bytes
    _length: int
    _request: OutboundGet
    _deadline: _Deadline
    request_method: str = 'GET'
    tls_verified: bool = True
    redirect_count: int = 0
    _started: bool = False
    _closed: bool = False

    def __repr__(self):
        return f'KisHttpsResponse(status_code={self.status_code}, GET_only=True)'

    def iter_bytes(self, chunk_bytes):
        try:
            _check(not self._closed and not self._started and type(chunk_bytes) is int and
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
                chunk = _recv(self._stream, min(chunk_bytes, remaining), self._deadline,
                              self._request.limits.read_seconds)
                _check(bool(chunk), 'KIS_HTTPS_BODY_INVALID')
                remaining -= len(chunk)
                yield chunk
            self._deadline.check()
        except Exception as exc:
            raise Blocked(_safe_error(exc)) from None
        finally:
            self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            stream, self._stream = self._stream, None
            self._pending = b''
            _safe_close(stream)


class KisHttpsGetClient:
    """Standard-library HTTPS client with no credentials or write capability.

    Construction/import has no DNS/socket/TLS side effects. A caller must supply
    the trusted binding used by KisGetOnlyTransport. No runtime wires this class.
    Testing this implementation with mocked I/O does not prove a live connection,
    authenticated provenance or broker-schema compatibility.
    """
    def __init__(self, binding: AccountBinding, *, monotonic=time.monotonic):
        try:
            _check(type(binding) is AccountBinding and all(type(v) is str for v in
                   (binding.account, binding.environment, binding.cano, binding.product_code)) and callable(monotonic),
                   'KIS_HTTPS_CONFIG_BLOCKED')
            binding.validate()
        except Exception as exc:
            raise Blocked(_safe_error(exc)) from None
        self._binding = binding
        self._clock = monotonic

    def __repr__(self):
        return 'KisHttpsGetClient(GET_only=True, credentials=False)'

    def open_get(self, request: OutboundGet):
        raw = None; secured = None
        try:
            host, port, wire = _request_target(request, self._binding)
            deadline = _Deadline(request, self._clock)
            context = _tls_context()
            family, address = _resolve(host, port, deadline, request.limits.connect_seconds)
            deadline.remaining(request.limits.connect_seconds, phase_end=deadline.connect_end)
            raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            raw.settimeout(deadline.remaining(request.limits.connect_seconds, phase_end=deadline.connect_end))
            raw.connect(address)
            deadline.remaining(request.limits.connect_seconds, phase_end=deadline.connect_end)
            secured = context.wrap_socket(raw, server_hostname=host, do_handshake_on_connect=False,
                                          suppress_ragged_eofs=False)
            raw = None  # SSL socket owns the connected descriptor now.
            secured.settimeout(deadline.remaining(request.limits.connect_seconds, phase_end=deadline.connect_end))
            secured.do_handshake()
            deadline.remaining(request.limits.connect_seconds, phase_end=deadline.connect_end)
            _check(secured.version() in ('TLSv1.2', 'TLSv1.3') and
                   secured.selected_alpn_protocol() in (None, 'http/1.1'), 'KIS_HTTPS_TLS_FAILED')
            secured.settimeout(deadline.remaining(request.limits.read_seconds))
            secured.sendall(wire)
            deadline.check()
            status, headers, pending, length = _read_headers(secured, request, deadline)
            deadline.check()
            response = _Response(status, request.url, headers, secured, pending, length, request, deadline)
            secured = None  # response owns socket and cleanup
            if status != 200:
                response.close()
            return response
        except Exception as exc:
            raise Blocked(_safe_error(exc)) from None
        finally:
            _safe_close(secured)
            _safe_close(raw)
