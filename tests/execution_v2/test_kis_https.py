"""Concrete HTTPS client tests with ALL DNS/TCP/TLS I/O replaced by offline doubles."""
from dataclasses import replace
import json
from pathlib import Path
import socket
import ssl
import threading
import traceback
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import AccountBinding, HistoryQuery, prepare_history_query
from oneil_execution_v2.kis_transport import (DEMO_ORIGIN, REAL_ORIGIN, KisGetOnlyTransport,
                                           OutboundGet, ReadLimits, ReadRateLimiter)
from oneil_execution_v2 import kis_https as https

BINDING = AccountBinding('a' * 64, 'real', '12345678', '01')
SECRET = 'SYNTHETIC_PRIVATE_ERROR_NOT_TO_ECHO'
BODY = b'{"rt_cd":"0","output1":[]}'


class Clock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value


class RawSocket:
    def __init__(self):
        self.timeouts = []; self.addresses = []; self.closed = 0; self.connect_hook = None
    def settimeout(self, value): self.timeouts.append(value)
    def connect(self, address):
        self.addresses.append(address)
        if self.connect_hook: self.connect_hook()
    def close(self): self.closed += 1


class TlsSocket:
    def __init__(self):
        self.timeouts = []; self.sent = []; self.receives = []; self.chunks = []
        self.closed = 0; self.handshakes = 0; self.protocol = 'TLSv1.3'; self.alpn = 'http/1.1'
        self.handshake_hook = None; self.send_hook = None; self.recv_hook = None; self.close_hook = None
    def settimeout(self, value): self.timeouts.append(value)
    def do_handshake(self):
        self.handshakes += 1
        if self.handshake_hook: self.handshake_hook()
    def version(self): return self.protocol
    def selected_alpn_protocol(self): return self.alpn
    def sendall(self, value):
        self.sent.append(value)
        if self.send_hook: self.send_hook()
    def recv(self, maximum):
        self.receives.append(maximum)
        if self.recv_hook: self.recv_hook()
        if not self.chunks: return b''
        data = self.chunks[0][:maximum]; self.chunks[0] = self.chunks[0][maximum:]
        if not self.chunks[0]: self.chunks.pop(0)
        return data
    def close(self):
        self.closed += 1
        if self.close_hook: self.close_hook()


class Context:
    def __init__(self, secured):
        self.secured = secured; self.options = 0; self.wrap_calls = []; self.loads = []
        self.protocols = None; self.wrap_hook = None
    def load_default_certs(self, purpose): self.loads.append(purpose)
    def set_alpn_protocols(self, protocols): self.protocols = protocols
    def wrap_socket(self, raw, **kwargs):
        self.wrap_calls.append((raw, kwargs))
        if self.wrap_hook: self.wrap_hook()
        return self.secured


def wire(body=BODY, *, status='200 OK', fields=None, length=True):
    headers = [('Content-Type', 'application/json'), ('tr_cont', 'D')]
    if length: headers.append(('Content-Length', str(len(body))))
    if fields: headers.extend(fields)
    return (f'HTTP/1.1 {status}\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers) + '\r\n').encode() + body


class KisHttpsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.raw = RawSocket(); self.tls = TlsSocket(); self.context = Context(self.tls)
        self.tls.chunks = [wire()]
        self.address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 9443))
        self.patches = [patch.object(https.socket, 'getaddrinfo', return_value=[self.address]),
                        patch.object(https.socket, 'socket', return_value=self.raw),
                        patch.object(https.ssl, 'SSLContext', return_value=self.context),
                        patch.object(https, '_DNS_SLOT', threading.BoundedSemaphore(1)),
                        patch.dict('os.environ', {}, clear=True)]
        self.dns, self.tcp, self.ssl_context, *_ = [item.start() for item in self.patches]
        for item in self.patches: self.addCleanup(item.stop)
        self.client = https.KisHttpsGetClient(BINDING, monotonic=self.clock)
        self.recipe = prepare_history_query(HistoryQuery(BINDING, '2026-11-05'))
        self.request = self.request_for(self.recipe)

    def request_for(self, recipe, environment='real', limits=ReadLimits()):
        origin = REAL_ORIGIN if environment == 'real' else DEMO_ORIGIN
        return OutboundGet(environment, recipe.path, origin + recipe.path + '?' + urlencode(recipe.parameters),
                           (('accept', 'application/json'), ('accept-encoding', 'identity'),
                            ('tr_id', recipe.tr_id), ('tr_cont', recipe.tr_cont), ('custtype', 'P')),
                           limits, self.clock.value + limits.total_seconds)

    def error(self, request=None, *, code=None, consume=False):
        with self.assertRaises(Blocked) as result:
            response = self.client.open_get(self.request if request is None else request)
            if consume: list(response.iter_bytes(16384))
        if code: self.assertEqual(str(result.exception), code)
        self.assertNotIn(SECRET, str(result.exception))
        return result.exception

    def test_constructor_has_no_dns_socket_or_tls_side_effect(self):
        https.KisHttpsGetClient(BINDING)
        self.dns.assert_not_called(); self.tcp.assert_not_called(); self.ssl_context.assert_not_called()

    def test_real_get_uses_exact_host_numeric_address_and_single_wire_request(self):
        response = self.client.open_get(self.request)
        self.assertEqual(b''.join(response.iter_bytes(16384)), BODY)
        self.dns.assert_called_once_with('openapi.koreainvestment.com', 9443,
                                        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        self.tcp.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self.assertEqual(self.raw.addresses, [('8.8.8.8', 9443)])
        self.assertEqual(len(self.tls.sent), 1)
        sent = self.tls.sent[0]
        self.assertTrue(sent.startswith(b'GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld?'))
        self.assertIn(b'Host: openapi.koreainvestment.com:9443\r\n', sent)
        self.assertIn(b'Connection: close\r\n', sent)
        self.assertTrue(sent.endswith(b'\r\n\r\n'))
        for forbidden in [b'Authorization:', b'appkey:', b'appsecret:', b'Cookie:', b'POST ', b'Content-Length:']:
            self.assertNotIn(forbidden, sent)
        self.assertTrue(response.tls_verified); self.assertEqual(response.url, self.request.url)
        self.assertEqual(response.redirect_count, 0); self.assertEqual(self.tls.closed, 1)

    def test_demo_uses_separate_origin_port_sni_and_tr(self):
        binding = replace(BINDING, environment='demo')
        recipe = prepare_history_query(HistoryQuery(binding, '2026-11-05'))
        self.dns.return_value = [(*self.address[:4], ('8.8.8.8', 29443))]
        client = https.KisHttpsGetClient(binding, monotonic=self.clock)
        response = client.open_get(self.request_for(recipe, environment='demo')); response.close()
        self.assertIn(b'Host: openapivts.koreainvestment.com:29443', self.tls.sent[0])
        self.assertIn(b'tr_id: VTTC0081R', self.tls.sent[0])
        self.assertEqual(self.context.wrap_calls[0][1]['server_hostname'], 'openapivts.koreainvestment.com')

    def test_tls_verification_minimum_sni_alpn_and_no_ragged_eof(self):
        self.client.open_get(self.request).close()
        self.ssl_context.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(self.context.check_hostname)
        self.assertEqual(self.context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertIsNone(self.context.keylog_filename)
        self.assertEqual(self.context.loads, [ssl.Purpose.SERVER_AUTH])
        self.assertEqual(self.context.protocols, ['http/1.1'])
        self.assertTrue(self.context.options & ssl.OP_NO_COMPRESSION)
        self.assertEqual(self.context.wrap_calls[0][1], {
            'server_hostname': 'openapi.koreainvestment.com', 'do_handshake_on_connect': False,
            'suppress_ragged_eofs': False})
        self.assertEqual(self.tls.handshakes, 1)

    def test_ambient_trust_store_overrides_block_before_dns(self):
        for key in ['SSL_CERT_FILE', 'SSL_CERT_DIR']:
            with patch.dict('os.environ', {key: '/untrusted/' + SECRET}):
                self.error(code='KIS_HTTPS_CONFIG_BLOCKED')
        self.dns.assert_not_called(); self.tcp.assert_not_called(); self.ssl_context.assert_not_called()

    def test_proxy_netrc_cookies_keylog_and_credentials_environment_ignored(self):
        with patch.dict('os.environ', {'HTTPS_PROXY': 'https://evil.example/' + SECRET,
                                      'HTTP_PROXY': 'http://evil.example', 'ALL_PROXY': 'socks5://evil.example',
                                      'NETRC': '/untrusted/' + SECRET, 'SSLKEYLOGFILE': '/untrusted/' + SECRET,
                                      'KIS_APP_KEY': SECRET, 'KIS_ACCESS_TOKEN': SECRET}):
            self.client.open_get(self.request).close()
        self.assertIsNone(self.context.keylog_filename)
        self.assertNotIn(SECRET.encode(), self.tls.sent[0])
        self.assertEqual(self.raw.addresses, [('8.8.8.8', 9443)])

    def test_invalid_binding_rejected_without_io(self):
        for binding in [None, replace(BINDING, cano='bad'), replace(BINDING, environment='unknown')]:
            with self.assertRaises(Blocked): https.KisHttpsGetClient(binding)
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_origin_and_endpoint_variants_block_before_dns(self):
        for url in [self.request.url.replace('https:', 'http:'), self.request.url.replace(':9443', ':443'),
                    self.request.url.replace('openapi.', 'evil.'), self.request.url.replace('openapi.', 'OPENAPI.'),
                    self.request.url + '#fragment', self.request.url + '&EXTRA=x',
                    self.request.url.replace('inquire-daily-ccld', 'order-cash'),
                    'https://user@openapi.koreainvestment.com:9443' + self.request.url.split(':9443')[1],
                    self.request.url.replace('inquire-daily-ccld', '%69nquire-daily-ccld')]:
            with self.subTest(url=url): self.error(replace(self.request, url=url))
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_every_security_control_revalidated_even_forged_frozen_object(self):
        for key, value in [('method', 'POST'), ('verify_tls', False), ('follow_redirects', True),
                           ('automatic_retries', 1), ('automatic_retries', False), ('use_environment', True),
                           ('chunk_bytes', 32768), ('max_header_bytes', 65536), ('max_header_count', 100)]:
            request = replace(self.request)
            object.__setattr__(request, key, value)
            self.error(request, code='KIS_HTTPS_REQUEST_BLOCKED')
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_arbitrary_authorization_cookie_host_or_duplicate_headers_block(self):
        for pair in [('authorization', SECRET), ('appkey', SECRET), ('cookie', SECRET),
                     ('Host', 'evil.example'), ('accept', 'application/json')]:
            self.error(replace(self.request, headers=self.request.headers + (pair,)))
        self.dns.assert_not_called()

    def test_account_product_and_tr_mismatch_block_before_dns(self):
        self.error(replace(self.request, url=self.request.url.replace('CANO=12345678', 'CANO=87654321')))
        self.error(replace(self.request, url=self.request.url.replace('ACNT_PRDT_CD=01', 'ACNT_PRDT_CD=02')))
        self.error(replace(self.request, headers=tuple((k, 'VTTC0081R' if k == 'tr_id' else v)
                                                      for k, v in self.request.headers)))
        self.dns.assert_not_called()

    def test_deadline_invalid_before_dns(self):
        for end in [100, 99, 131, float('nan'), True]:
            self.error(replace(self.request, deadline_monotonic=end), code='KIS_HTTPS_DEADLINE_EXCEEDED')
        self.dns.assert_not_called()

    def test_local_tls_context_setup_expiry_stops_before_dns_or_socket(self):
        def context(protocol):
            self.clock.value += 4
            return self.context
        self.ssl_context.side_effect = context
        self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED')
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_resolver_error_is_redacted_without_connect(self):
        self.dns.side_effect = OSError(SECRET)
        self.error(code='KIS_HTTPS_DNS_UNAVAILABLE')
        self.tcp.assert_not_called()

    def test_dns_timeout_keeps_one_slot_and_late_lookup_never_connects(self):
        started = threading.Event(); release = threading.Event(); finished = threading.Event()
        def lookup(*args, **kwargs):
            started.set()
            release.wait(2)
            finished.set()
            return [self.address]
        self.dns.side_effect = lookup
        request = self.request_for(self.recipe, limits=ReadLimits(.1, .1, .1))
        try:
            self.error(request, code='KIS_HTTPS_DNS_UNAVAILABLE')
            self.assertTrue(started.is_set())
            self.error(request, code='KIS_HTTPS_DNS_UNAVAILABLE')
            self.assertEqual(self.dns.call_count, 1)
        finally:
            release.set(); self.assertTrue(finished.wait(1))
        self.tcp.assert_not_called()

    def test_dns_worker_not_yet_scheduled_cannot_start_lookup_after_timeout(self):
        saved = {}
        class DelayedThread:
            def __init__(self, **kwargs): saved.update(kwargs)
            def start(self): pass
        with patch.object(https.threading, 'Thread', DelayedThread):
            request = self.request_for(self.recipe, limits=ReadLimits(.1, .1, .1))
            self.error(request, code='KIS_HTTPS_DNS_UNAVAILABLE')
            saved['target']()
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_dns_uses_shared_connect_phase_deadline(self):
        def lookup(*args, **kwargs):
            self.clock.value += 2
            return [self.address]
        self.dns.side_effect = lookup
        def connect(): self.clock.value += .5
        self.raw.connect_hook = connect
        self.client.open_get(self.request).close()
        self.assertEqual(self.raw.timeouts[0], 1)
        self.assertEqual(self.tls.timeouts[0], .5)

    def test_private_loopback_multicast_or_unscoped_dns_rejected(self):
        for address in ['127.0.0.1', '10.0.0.1', '169.254.1.1', '0.0.0.0', '224.0.0.1', 'not-an-ip']:
            self.dns.return_value = [(*self.address[:4], (address, 9443))]
            self.error()
        self.tcp.assert_not_called()

    def test_ipv6_global_exact_address_supported_without_second_lookup(self):
        self.dns.return_value = [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                                  '', ('2001:4860:4860::8888', 9443, 0, 0))]
        self.client.open_get(self.request).close()
        self.assertEqual(self.raw.addresses, [('2001:4860:4860::8888', 9443, 0, 0)])
        self.assertEqual(self.dns.call_count, 1)

    def test_malformed_dns_family_port_scope_or_excess_results_rejected(self):
        for entries in [[], [self.address] * 65, [('bad',)],
                        [(*self.address[:4], ('8.8.8.8', 443))],
                        [(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, '', ('8.8.8.8', 9443))],
                        [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
                          ('2001:4860:4860::8888', 9443, 0, 1))]]:
            self.dns.return_value = entries
            self.error()
        self.tcp.assert_not_called()

    def test_tcp_failure_does_not_retry_other_dns_addresses(self):
        self.dns.return_value = [self.address, (*self.address[:4], ('1.1.1.1', 9443))]
        def fail(): raise OSError(SECRET)
        self.raw.connect_hook = fail
        self.error(code='KIS_HTTPS_IO_FAILED')
        self.assertEqual(len(self.raw.addresses), 1); self.assertEqual(self.raw.closed, 1)
        self.assertEqual(self.tls.sent, [])

    def test_tcp_elapsed_deadline_stops_before_tls_or_send(self):
        self.raw.connect_hook = lambda: setattr(self.clock, 'value', 104)
        self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED')
        self.assertEqual(self.context.wrap_calls, []); self.assertEqual(self.raw.closed, 1)

    def test_tls_handshake_failure_redacted_and_never_sends_get(self):
        def fail(): raise ssl.SSLCertVerificationError(SECRET)
        self.tls.handshake_hook = fail
        self.error(code='KIS_HTTPS_IO_FAILED')
        self.assertEqual(self.tls.sent, []); self.assertEqual(self.tls.closed, 1)

    def test_tls_wrap_failure_closes_raw_socket(self):
        def fail(): raise RuntimeError(SECRET)
        self.context.wrap_hook = fail
        self.error(); self.assertEqual(self.raw.closed, 1); self.assertEqual(self.tls.sent, [])

    def test_tls_handshake_deadline_includes_prior_connect(self):
        self.raw.connect_hook = lambda: setattr(self.clock, 'value', 102)
        self.tls.handshake_hook = lambda: setattr(self.clock, 'value', 103.1)
        self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED')
        self.assertEqual(self.tls.timeouts[0], 1); self.assertEqual(self.tls.sent, [])

    def test_tls_version_or_http2_protocol_blocked_without_request(self):
        for protocol, alpn in [('TLSv1.1', 'http/1.1'), ('TLSv1.3', 'h2')]:
            self.tls.protocol = protocol; self.tls.alpn = alpn
            self.error(code='KIS_HTTPS_TLS_FAILED')
        self.assertEqual(self.tls.sent, [])

    def test_no_alpn_and_tls12_allowed(self):
        self.tls.protocol = 'TLSv1.2'; self.tls.alpn = None
        self.client.open_get(self.request).close()
        self.assertEqual(len(self.tls.sent), 1)

    def test_send_failure_or_timeout_never_retries(self):
        def fail(): raise TimeoutError(SECRET)
        self.tls.send_hook = fail
        self.error(code='KIS_HTTPS_IO_FAILED')
        self.assertEqual(len(self.tls.sent), 1); self.assertEqual(self.tls.receives, [])
        self.assertEqual(self.tls.closed, 1)

    def test_send_elapsed_deadline_prevents_header_read(self):
        self.tls.send_hook = lambda: setattr(self.clock, 'value', 111)
        self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED')
        self.assertEqual(self.tls.receives, [])

    def test_redirect_status_or_location_never_followed(self):
        for status, fields in [('301 Moved', [('Location', 'https://evil.example/' + SECRET)]),
                               ('302 Found', []), ('307 Temporary Redirect', []),
                               ('308 Permanent Redirect', []), ('200 OK', [('Location', 'https://evil.example')])]:
            self.tls.chunks = [wire(status=status, fields=fields), b'ignored body']
            self.error(code='KIS_HTTPS_REDIRECT_BLOCKED')
        self.assertEqual(len(self.tls.sent), 5); self.assertEqual(self.dns.call_count, 5)
        self.assertTrue(all(call.startswith(b'GET /uapi/') for call in self.tls.sent))

    def test_http_error_and_429_return_closed_metadata_without_error_body(self):
        for status in ['401 Unauthorized', '403 Forbidden', '429 Too Many Requests', '503 Unavailable']:
            self.tls.chunks = [wire(body=b'', status=status, fields=[('Retry-After', '7200')], length=False), SECRET.encode()]
            response = self.client.open_get(self.request)
            self.assertEqual(response.status_code, int(status[:3]))
            self.assertEqual(response.headers['retry-after'], '7200')
            self.assertTrue(response._closed)
            self.assertEqual(self.tls.chunks, [SECRET.encode()])

    def test_success_requires_exact_content_length_and_no_transfer_encoding(self):
        for data in [wire(length=False), wire(fields=[('Transfer-Encoding', 'chunked')]),
                     wire(fields=[('Content-Length', '1')]),
                     wire(fields=[('content-length', str(len(BODY)))]),
                     wire(length=False, fields=[('Content-Length', '-1')]),
                     wire(length=False, fields=[('Content-Length', '1, 2')]),
                     wire(length=False, fields=[('Content-Length', '999999999999999')])]:
            self.tls.chunks = [data]
            self.error()

    def test_content_encoding_type_duplicate_headers_and_obs_fold_blocked(self):
        for data in [wire(fields=[('Content-Encoding', 'gzip')]),
                     wire(fields=[('content-type', 'text/html')]),
                     wire().replace(b'application/json', b'text/html'),
                     wire().replace(b'Content-Type:', b' Content-Type:'),
                     wire().replace(b'tr_cont: D', b'tr_cont: D\r\n folded'),
                     wire().replace(b'tr_cont: D', b'tr_cont: D\x00')]:
            self.tls.chunks = [data]
            self.error()

    def test_status_line_and_interim_responses_fail_closed(self):
        for line in [b'HTTP/1.1 100 Continue', b'HTTP/1.1 101 Switching Protocols', b'HTTP/2 200 OK',
                     b'HTTP/1.1 200OK', b'HTTP/1.1 999 Bad', b'HTTP/1.1 200 ' + b'x' * 257]:
            self.tls.chunks = [line + b'\r\nContent-Length: 0\r\n\r\n']
            self.error(code='KIS_HTTPS_HEADERS_INVALID')

    def test_lf_only_and_embedded_newline_headers_rejected(self):
        for data in [wire().replace(b'\r\n', b'\n'), wire().replace(b'tr_cont: D', b'tr_cont: D\nX: Y')]:
            self.tls.chunks = [data]
            self.error(code='KIS_HTTPS_HEADERS_INVALID')

    def test_header_count_line_and_total_bytes_bounded_while_receiving(self):
        for data in [wire(fields=[(f'h{i}', 'v') for i in range(65)]),
                     wire(fields=[('large', 'x' * 8193)]),
                     b'HTTP/1.1 200 OK\r\n' + b'A' * 20000]:
            self.tls.chunks = [data]
            self.error(code='KIS_HTTPS_HEADERS_INVALID')
        self.assertTrue(all(size <= 16384 for size in self.tls.receives))
        self.assertTrue(len(self.tls.chunks[0]) > 0)  # did not consume unbounded header stream

    def test_headers_split_at_every_delimiter_boundary(self):
        data = wire()
        for offset in [1, 16, data.index(b'\r\n\r\n') + 1, data.index(b'\r\n\r\n') + 3]:
            self.tls.chunks = [data[:offset], data[offset:]]
            response = self.client.open_get(self.request)
            self.assertEqual(b''.join(response.iter_bytes(5)), BODY)

    def test_declared_oversize_body_rejected_before_body_download(self):
        self.tls.chunks = [wire(body=b'', length=False, fields=[('Content-Length', '1048577')]), b'x' * 1000]
        self.error(code='KIS_HTTPS_BODY_INVALID')
        self.assertEqual(self.tls.chunks, [b'x' * 1000])

    def test_initial_extra_body_beyond_declared_length_rejected(self):
        self.tls.chunks = [wire(body=BODY, length=False, fields=[('Content-Length', '1')])]
        self.error(code='KIS_HTTPS_BODY_INVALID')

    def test_premature_body_eof_rejected_and_closed(self):
        self.tls.chunks = [wire(body=b'x', length=False, fields=[('Content-Length', '10')])]
        self.error(code='KIS_HTTPS_BODY_INVALID', consume=True)
        self.assertEqual(self.tls.closed, 1)

    def test_body_stream_chunk_bound_and_exact_length(self):
        data = b'x' * 50000
        self.tls.chunks = [wire(body=b'', length=False, fields=[('Content-Length', str(len(data)))]), data]
        response = self.client.open_get(self.request)
        chunks = list(response.iter_bytes(4096))
        self.assertTrue(all(0 < len(chunk) <= 4096 for chunk in chunks))
        self.assertEqual(b''.join(chunks), data)
        self.assertLessEqual(max(self.tls.receives[1:]), 4096)

    def test_total_deadline_defeats_trickle_headers(self):
        self.tls.chunks = [bytes([x]) for x in wire()]
        self.tls.recv_hook = lambda: setattr(self.clock, 'value', self.clock.value + 1)
        self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED')
        self.assertEqual(self.tls.closed, 1)
        self.assertLessEqual(len(self.tls.receives), 10)

    def test_total_deadline_defeats_trickle_body_and_shrinks_timeout(self):
        self.tls.chunks = [wire(body=b'', length=False, fields=[('Content-Length', '10')])] + [b'x'] * 10
        response = self.client.open_get(self.request)
        self.tls.recv_hook = lambda: setattr(self.clock, 'value', self.clock.value + 1.1)
        with self.assertRaisesRegex(Blocked, 'KIS_HTTPS_DEADLINE_EXCEEDED'):
            list(response.iter_bytes(1))
        self.assertLess(self.tls.timeouts[-1], 1)
        self.assertEqual(self.tls.closed, 1)

    def test_read_socket_exception_and_private_error_are_redacted(self):
        for exception in [OSError(SECRET), https._Failure(SECRET)]:
            self.tls.chunks = [wire()]
            def fail(): raise exception
            self.tls.recv_hook = fail
            try:
                self.client.open_get(self.request)
            except Blocked as exc:
                rendered = ''.join(traceback.format_exception(exc))
            self.assertNotIn(SECRET, rendered)
            self.assertIn('KIS_HTTPS_IO_FAILED', rendered)

    def test_body_read_exception_redacted_and_cleanup_idempotent(self):
        self.tls.chunks = [wire(body=b'', length=False, fields=[('Content-Length', '1')]), b'x']
        response = self.client.open_get(self.request)
        def fail(): raise OSError(SECRET)
        self.tls.recv_hook = fail
        with self.assertRaisesRegex(Blocked, 'KIS_HTTPS_IO_FAILED'):
            list(response.iter_bytes(1))
        response.close(); response.close()
        self.assertEqual(self.tls.closed, 1)

    def test_response_cannot_be_consumed_twice_or_after_close(self):
        response = self.client.open_get(self.request)
        self.assertEqual(b''.join(response.iter_bytes(10)), BODY)
        with self.assertRaisesRegex(Blocked, 'KIS_HTTPS_RESPONSE_CLOSED'):
            list(response.iter_bytes(10))
        response.close(); self.assertEqual(self.tls.closed, 1)

    def test_caller_close_without_consuming_body_is_local_and_idempotent(self):
        response = self.client.open_get(self.request)
        count = len(self.tls.receives)
        response.close(); response.close()
        self.assertEqual(len(self.tls.receives), count); self.assertEqual(self.tls.closed, 1)
        self.assertEqual(len(self.tls.sent), 1)

    def test_repr_hides_binding_url_headers_and_payload(self):
        self.tls.chunks = [wire(body=json.dumps({'private': SECRET}).encode(), fields=[('Set-Cookie', SECRET)])]
        response = self.client.open_get(self.request)
        for value in [self.client, response]:
            self.assertNotIn(BINDING.cano, repr(value)); self.assertNotIn(SECRET, repr(value))
        response.close()

    def test_real_client_composes_with_transport_and_existing_history_shape(self):
        transport = KisGetOnlyTransport(self.client, BINDING, monotonic=self.clock,
                                       limiter=ReadRateLimiter(monotonic=self.clock))
        result = transport.read(self.recipe)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, {'rt_cd': '0', 'output1': []})
        self.assertEqual(result.headers, {'tr_cont': 'D'})
        self.assertEqual(self.tls.closed, 1)

    def test_transport_handles_concrete_429_without_retry_or_body(self):
        self.tls.chunks = [wire(body=b'', status='429 Too Many Requests', fields=[('Retry-After', '7200')]), SECRET.encode()]
        transport = KisGetOnlyTransport(self.client, BINDING, monotonic=self.clock,
                                       limiter=ReadRateLimiter(monotonic=self.clock))
        with self.assertRaisesRegex(Blocked, 'KIS_TRANSPORT_REMOTE_RATE_LIMITED'):
            transport.read(self.recipe)
        self.clock.value += 3600
        with self.assertRaisesRegex(Blocked, 'KIS_TRANSPORT_RATE_LIMITED'):
            transport.read(self.recipe)
        self.assertEqual(len(self.tls.sent), 1); self.assertEqual(self.tls.chunks, [SECRET.encode()])

    def test_compressed_or_chunked_429_still_applies_server_cooldown(self):
        for extra in [('Transfer-Encoding', 'chunked'), ('Content-Encoding', 'gzip')]:
            self.tls.chunks = [wire(body=b'', status='429 Too Many Requests',
                                   fields=[('Retry-After', '7200'), extra], length=False), SECRET.encode()]
            transport = KisGetOnlyTransport(self.client, BINDING, monotonic=self.clock,
                                           limiter=ReadRateLimiter(monotonic=self.clock))
            with self.assertRaisesRegex(Blocked, 'KIS_TRANSPORT_REMOTE_RATE_LIMITED'):
                transport.read(self.recipe)
            before = len(self.tls.sent)
            self.clock.value += 3600
            with self.assertRaisesRegex(Blocked, 'KIS_TRANSPORT_RATE_LIMITED'):
                transport.read(self.recipe)
            self.assertEqual(len(self.tls.sent), before)
            self.assertEqual(self.tls.chunks, [SECRET.encode()])

    def test_production_wiring_and_write_methods_absent(self):
        root = Path(__file__).resolve().parents[2]
        for directory in ['oneil_runtime_v2', 'oneil_control_v2', 'oneil_policy_v2']:
            for path in (root / directory).glob('*.py'):
                self.assertNotIn('kis_https', path.read_text())
        for method in ['post', 'request', 'submit', 'cancel', 'refresh_token']:
            self.assertFalse(hasattr(self.client, method))


if __name__ == '__main__': unittest.main()
