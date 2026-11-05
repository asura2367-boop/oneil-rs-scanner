"""Synthetic-only authenticated transport tests. All DNS, socket and TLS patched."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import errno
import pickle
import socket
import ssl
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import AccountBinding, HistoryQuery, prepare_history_query
from oneil_execution_v2.kis_authenticated import AuthenticatedGet, Cancellation
from oneil_execution_v2.kis_local_authorization import LocalPermission
from oneil_execution_v2.kis_transport import OutboundGet, ReadLimits, REAL_ORIGIN, DEMO_ORIGIN
from oneil_execution_v2 import kis_authenticated_https as h

BINDING = AccountBinding('a' * 64, 'real', '12345678', '01')
SECRET = 'SYNTHETIC_ONLY_NEVER_REAL'
BODY = b'{"rt_cd":"0"}'
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def wire(body=BODY, status='200 OK', extra=b''):
    return (b'HTTP/1.1 ' + status.encode() + b'\r\nContent-Type: application/json\r\nContent-Length: '
            + str(len(body)).encode() + b'\r\n' + extra + b'\r\n' + body)


class Raw:
    def __init__(self): self.closed = 0; self.addresses = []; self.result = 0; self.blocking = []
    def setblocking(self, value): self.blocking.append(value)
    def connect_ex(self, address): self.addresses.append(address); return self.result
    def getsockopt(self, *args): return 0
    def close(self): self.closed += 1


class TLS(Raw):
    def __init__(self):
        super().__init__(); self.sent = []; self.chunks = [wire()]; self.protocol = 'TLSv1.3'
        self.alpn = 'http/1.1'; self.hook = None; self.send_hook = None; self.recv_hook = None
    def do_handshake(self):
        if self.hook: self.hook()
    def version(self): return self.protocol
    def selected_alpn_protocol(self): return self.alpn
    def send(self, value):
        if self.send_hook: self.send_hook()
        self.sent.append(bytes(value)); return len(value)
    def recv(self, maximum):
        if self.recv_hook: self.recv_hook()
        if not self.chunks: return b''
        value = self.chunks[0][:maximum]; self.chunks[0] = self.chunks[0][maximum:]
        if not self.chunks[0]: self.chunks.pop(0)
        return value


class Context:
    def __init__(self, tls): self.tls = tls; self.options = 0; self.wraps = []
    def load_default_certs(self, purpose): self.purpose = purpose
    def set_alpn_protocols(self, protocols): self.protocols = protocols
    def wrap_socket(self, raw, **kwargs): self.wraps.append(kwargs); return self.tls


class AuthenticatedHttpsTests(unittest.TestCase):
    def setUp(self):
        self.tick = 100.; self.now = NOW
        self.clock = lambda: self.tick
        self.raw = Raw(); self.tls = TLS(); self.context = Context(self.tls)
        self.address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('8.8.8.8', 9443))
        self.patches = [patch.object(h.socket, 'getaddrinfo', return_value=[self.address]),
                        patch.object(h.socket, 'socket', return_value=self.raw),
                        patch.object(h.ssl, 'SSLContext', return_value=self.context),
                        patch.object(h.select, 'select', side_effect=lambda r,w,x,t: (r,w,[])),
                        patch.object(h, '_DNS_SLOT', threading.BoundedSemaphore(1)),
                        patch.dict('os.environ', {}, clear=True),
                        patch.object(h.sys, 'platform', 'win32')]
        self.dns, self.tcp, self.ctx, self.select, *_ = [p.start() for p in self.patches]
        for p in self.patches: self.addCleanup(p.stop)
        self.permission = self.grant()
        self.client = h.KisAuthenticatedHttpsGetClient(BINDING, permission=self.permission, monotonic=self.clock)
        self.request = self.request_for(prepare_history_query(HistoryQuery(BINDING, '2026-11-05')))
        self.cancel = Cancellation()
        self.envelope = AuthenticatedGet(self.request, SECRET+'KEY', SECRET+'APP', SECRET+'TOKEN', self.cancel)

    def grant(self, binding=BINDING, actions=('authenticated_get',)):
        return LocalPermission(binding, actions=actions, issued_at=NOW, expires_at=NOW+timedelta(minutes=5),
                               monotonic=self.clock, utc_now=lambda: self.now)

    def request_for(self, recipe, environment='real'):
        origin = REAL_ORIGIN if environment == 'real' else DEMO_ORIGIN
        return OutboundGet(environment, recipe.path, origin+recipe.path+'?'+urlencode(recipe.parameters),
                           (('accept','application/json'), ('accept-encoding','identity'),
                            ('tr_id',recipe.tr_id), ('tr_cont',recipe.tr_cont), ('custtype','P')),
                           ReadLimits(), self.tick+10)

    def error(self, envelope=None, code=None, consume=False):
        with self.assertRaises(Blocked) as caught:
            response = self.client.open_authenticated_get(envelope or self.envelope)
            if consume: list(response.iter_bytes(16384))
        self.assertNotIn(SECRET, str(caught.exception))
        if code: self.assertEqual(str(caught.exception), code)

    def test_construction_no_io_and_no_ungranted_default(self):
        self.dns.assert_not_called(); self.tcp.assert_not_called(); self.ctx.assert_not_called()
        with self.assertRaises(TypeError): h.KisAuthenticatedHttpsGetClient(BINDING)
        with self.assertRaises(Blocked): h.KisAuthenticatedHttpsGetClient(BINDING, permission=None)

    def test_exact_get_auth_headers_and_direct_tls(self):
        response = self.client.open_authenticated_get(self.envelope)
        self.assertEqual(b''.join(response.iter_bytes(2)), BODY)
        sent = b''.join(self.tls.sent)
        self.assertTrue(sent.startswith(b'GET /uapi/'))
        for key, suffix in [('authorization: Bearer ', 'TOKEN'), ('appkey: ', 'KEY'), ('appsecret: ', 'APP')]:
            self.assertEqual(sent.count((key+SECRET+suffix+'\r\n').encode()), 1)
        self.assertNotIn(b'Cookie:', sent); self.assertNotIn(b'POST ', sent)
        self.assertEqual(self.raw.addresses, [('8.8.8.8', 9443)])
        self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(self.context.check_hostname)
        self.assertIsNone(self.context.keylog_filename)
        self.assertEqual(self.context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(self.context.protocols, ['http/1.1'])
        self.assertEqual(self.context.wraps[0]['server_hostname'], 'openapi.koreainvestment.com')
        self.assertEqual(self.raw.blocking, [False]); self.assertEqual(self.tls.blocking, [False])
        self.assertEqual(self.tls.closed, 1)

    def test_wrong_scope_binding_expired_or_revoked_grant(self):
        for permission in [self.grant(actions=()), self.grant(replace(BINDING, cano='87654321'))]:
            self.client = h.KisAuthenticatedHttpsGetClient(BINDING, permission=permission, monotonic=self.clock)
            self.error(code='KIS_AUTH_HTTPS_GRANT_INVALID')
        self.client = h.KisAuthenticatedHttpsGetClient(BINDING, permission=self.permission, monotonic=self.clock)
        self.permission.revoke(); self.error(code='KIS_AUTH_HTTPS_GRANT_INVALID')
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_grant_expiry_and_rollback_block(self):
        for now in [NOW+timedelta(minutes=6), NOW-timedelta(seconds=1)]:
            self.now = now; self.error(code='KIS_AUTH_HTTPS_GRANT_INVALID')
        self.dns.assert_not_called()

    def test_permission_rechecked_after_tls_before_secrets_sent(self):
        self.tls.hook = self.permission.revoke
        self.error(code='KIS_AUTH_HTTPS_GRANT_INVALID'); self.assertEqual(self.tls.sent, [])
        self.assertEqual(self.tls.closed, 1)

    def test_cancel_before_io(self):
        self.cancel.cancel(); self.error(code='KIS_AUTH_HTTPS_CANCELLED'); self.dns.assert_not_called()

    def test_cancel_during_dns_does_not_connect(self):
        def lookup(*args, **kwargs): self.cancel.cancel(); return [self.address]
        self.dns.side_effect = lookup
        self.error(code='KIS_AUTH_HTTPS_CANCELLED'); self.tcp.assert_not_called()

    def test_cancel_during_waiting_dns_and_late_thread_no_connect(self):
        release = threading.Event(); entered = threading.Event()
        def lookup(*args, **kwargs):
            entered.set(); release.wait(2); return [self.address]
        self.dns.side_effect = lookup
        def cancel(): entered.wait(1); self.cancel.cancel()
        worker = threading.Thread(target=cancel); worker.start()
        try: self.error(code='KIS_AUTH_HTTPS_CANCELLED')
        finally: release.set(); worker.join(1)
        self.tcp.assert_not_called()

    def test_cancel_connect_tls_send_and_read(self):
        for phase in ['connect', 'tls', 'send', 'read']:
            with self.subTest(phase=phase):
                self.cancel = Cancellation(); envelope = replace(self.envelope, cancellation=self.cancel)
                self.tls.hook = self.tls.send_hook = self.tls.recv_hook = None
                self.raw.result = errno.EINPROGRESS if phase == 'connect' else 0
                self.select.side_effect = lambda r,w,x,t: (r,w,[])
                if phase == 'connect':
                    self.select.side_effect = lambda *args: (self.cancel.cancel() or [], [], [])
                if phase == 'tls': self.tls.hook = self.cancel.cancel
                if phase == 'send': self.tls.send_hook = self.cancel.cancel
                if phase == 'read': self.tls.recv_hook = self.cancel.cancel
                self.error(envelope, code='KIS_AUTH_HTTPS_CANCELLED')

    def test_invalid_secrets_block_before_dns(self):
        for secret in ['', 'short', SECRET+'\r\nHost: evil', 'é'*20, 'x'*4097, None]:
            for field in ['app_key', 'app_secret', 'bearer']:
                self.error(replace(self.envelope, **{field:secret}))
        self.dns.assert_not_called()

    def test_origin_recipe_and_headers_revalidated(self):
        for url in [self.request.url.replace('https:', 'http:'), self.request.url.replace(':9443', ':443'),
                    self.request.url.replace('openapi.', 'evil.'), self.request.url+'#x',
                    self.request.url+'&EXTRA=x', self.request.url.replace('inquire-daily-ccld', 'order-cash'),
                    self.request.url.replace('CANO=12345678', 'CANO=87654321')]:
            self.error(replace(self.envelope, request=replace(self.request, url=url)))
        for key,value in [('method','POST'), ('follow_redirects',True), ('verify_tls',False),
                          ('use_environment',True), ('automatic_retries',1), ('chunk_bytes',65536),
                          ('max_header_bytes',65536), ('max_header_count',100)]:
            request = replace(self.request); object.__setattr__(request,key,value)
            self.error(replace(self.envelope,request=request))
        for key in ['Host', 'cookie', 'appkey', 'authorization']:
            self.error(replace(self.envelope,request=replace(self.request,headers=self.request.headers+((key,SECRET),))))
        self.dns.assert_not_called()

    def test_dns_address_policy(self):
        for entries in [[], [self.address]*65, [('bad',)],
                        [(*self.address[:4], ('127.0.0.1',9443))],
                        [(*self.address[:4], ('10.0.0.1',9443))],
                        [(*self.address[:4], ('8.8.8.8',443))],
                        [(socket.AF_INET6,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('2001:4860:4860::8888',9443,0,1))]]:
            self.dns.return_value = entries; self.error()
        self.tcp.assert_not_called()

    def test_resolver_exception_sanitized_and_no_retry(self):
        self.dns.side_effect = OSError(SECRET); self.error(code='KIS_HTTPS_DNS_UNAVAILABLE')
        self.dns.assert_called_once(); self.tcp.assert_not_called()

    def test_tls_failures_never_transmit(self):
        def fail(): raise ssl.SSLCertVerificationError(SECRET)
        self.tls.hook = fail; self.error(); self.assertEqual(self.tls.sent, [])
        self.tls.hook = None
        for protocol,alpn in [('TLSv1.1','http/1.1'), ('TLSv1.3','h2')]:
            self.tls.protocol=protocol; self.tls.alpn=alpn; self.error(code='KIS_HTTPS_TLS_FAILED')
        self.assertEqual(self.tls.sent, [])

    def test_ssl_want_read_write_and_partial_send(self):
        attempts = [ssl.SSLWantReadError(), ssl.SSLWantWriteError(), None]
        def handshake():
            value=attempts.pop(0)
            if value: raise value
        self.tls.hook=handshake
        def send(value):
            data=bytes(value[:7]); self.tls.sent.append(data); return len(data)
        self.tls.send=send
        response=self.client.open_authenticated_get(self.envelope); response.close()
        self.assertEqual(self.select.call_count,2)
        self.assertTrue(b''.join(self.tls.sent).endswith(b'\r\n\r\n'))

    def test_connect_not_ready_is_not_success(self):
        self.raw.result=errno.EINPROGRESS
        def wait(*args): self.tick+=4; return [],[],[]
        self.select.side_effect=wait
        self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED'); self.assertEqual(self.context.wraps,[])

    def test_read_timeout_sanitized_no_retry(self):
        def fail(): raise TimeoutError(SECRET)
        self.tls.recv_hook=fail; self.error(); self.dns.assert_called_once(); self.tcp.assert_called_once()

    def test_redirects_and_bad_framing_block(self):
        values=[wire(status='302 Found'), wire(extra=b'Location: https://evil.example\r\n'),
                wire(extra=b'Transfer-Encoding: chunked\r\n'), wire(extra=b'Content-Encoding: gzip\r\n'),
                wire(extra=b'Content-Length: 1\r\n'), wire().replace(b'application/json',b'text/html'),
                wire().replace(b'HTTP/1.1 200 OK',b'HTTP/1.1 100 Continue'),
                wire(extra=b'x: '+b'a'*8193+b'\r\n'),
                wire(extra=b''.join(b'x%d: y\r\n'%i for i in range(65)))]
        for value in values:
            self.tls.chunks=[value]; self.error()

    def test_body_limits_truncation_and_single_use(self):
        self.tls.chunks=[wire(body=b'x'*262145)]; self.error()
        self.tls.chunks=[wire()[:-2]]; self.error(consume=True)
        self.tls.chunks=[wire()]
        response=self.client.open_authenticated_get(self.envelope)
        self.assertEqual(b''.join(response.iter_bytes(3)), BODY)
        with self.assertRaises(Blocked): list(response.iter_bytes(3))

    def test_non_200_closed_no_body_retry(self):
        for status in ['401 Unauthorized','429 Too Many Requests','503 Unavailable']:
            self.tls.chunks=[wire(b'',status),SECRET.encode()]
            response=self.client.open_authenticated_get(self.envelope)
            self.assertEqual(response.status_code,int(status[:3])); self.assertTrue(response._closed)
            self.assertEqual(self.tls.chunks,[SECRET.encode()])
        self.assertEqual(self.dns.call_count,3)

    def test_ambient_proxy_keylog_ignored_trust_override_rejected(self):
        with patch.dict('os.environ',{'HTTPS_PROXY':SECRET,'SSLKEYLOGFILE':SECRET,'NETRC':SECRET}):
            self.client.open_authenticated_get(self.envelope).close()
        self.assertIsNone(self.context.keylog_filename)
        for key in ['SSL_CERT_FILE','SSL_CERT_DIR']:
            with patch.dict('os.environ',{key:SECRET}): self.error(code='KIS_HTTPS_CONFIG_BLOCKED')
        self.assertEqual(self.dns.call_count,1)

    def test_safe_repr_and_pickle_block(self):
        response=self.client.open_authenticated_get(self.envelope)
        for value in [self.client,self.envelope,response,self.permission]:
            self.assertNotIn(SECRET,repr(value))
            with self.assertRaises(TypeError): pickle.dumps(value)
        response.close(); response.close(); self.assertEqual(self.tls.closed,1)

    def test_demo_exact_origin_sni_and_account(self):
        binding=replace(BINDING,environment='demo')
        client=h.KisAuthenticatedHttpsGetClient(binding,permission=self.grant(binding),monotonic=self.clock)
        request=self.request_for(prepare_history_query(HistoryQuery(binding,'2026-11-05')), 'demo')
        self.dns.return_value=[(*self.address[:4],('8.8.8.8',29443))]
        client.open_authenticated_get(replace(self.envelope,request=request)).close()
        self.assertEqual(self.context.wraps[0]['server_hostname'],'openapivts.koreainvestment.com')
        self.assertIn(b'tr_id: VTTC0081R',b''.join(self.tls.sent))

    def test_internal_connection_rejects_arbitrary_host_before_io(self):
        with self.assertRaises(Exception): h._connect('evil.example',443,None,3,3)
        self.ctx.assert_not_called(); self.dns.assert_not_called()

    def test_non_windows_blocks_before_any_network(self):
        with patch.object(h.sys, 'platform', 'linux'):
            self.error(code='KIS_AUTH_HTTPS_WINDOWS_REQUIRED')
        self.dns.assert_not_called(); self.tcp.assert_not_called(); self.ctx.assert_not_called()

    def test_all_seven_exact_read_recipes_and_no_arbitrary_query(self):
        from oneil_execution_v2.kis_evidence import (
            BalanceQuery, BuyingPowerQuery, SellableQuery, MarketQuery, HolidayQuery,
            prepare_balance_query, prepare_buying_power_query, prepare_sellable_query,
            prepare_market_query, prepare_holiday_query)
        day = '2026-11-05'
        recipes = [prepare_history_query(HistoryQuery(BINDING, day)),
                   prepare_balance_query(BalanceQuery(BINDING, day)),
                   prepare_buying_power_query(BuyingPowerQuery(BINDING, day, '005930')),
                   prepare_sellable_query(SellableQuery(BINDING, day, '005930')),
                   prepare_market_query(MarketQuery('real', day, '005930', 'KRX')),
                   prepare_market_query(MarketQuery('real', day, '005930', 'NXT'), book=True),
                   prepare_holiday_query(HolidayQuery(day))]
        for recipe in recipes:
            self.tls.chunks = [wire()]
            request = self.request_for(recipe)
            response = self.client.open_authenticated_get(replace(self.envelope, request=request))
            response.close()
            calls = self.dns.call_count
            self.error(replace(self.envelope, request=replace(request, url=request.url+'&extra=x')))
            self.assertEqual(self.dns.call_count, calls)
        self.assertEqual(self.dns.call_count, 7)

    def test_stream_cancel_and_revoke_cleanup(self):
        response = self.client.open_authenticated_get(self.envelope)
        self.cancel.cancel()
        with self.assertRaisesRegex(Blocked, 'KIS_AUTH_HTTPS_CANCELLED'):
            list(response.iter_bytes(1))
        self.assertEqual(self.tls.closed, 1)
        self.cancel = Cancellation(); self.tls.chunks = [wire()]
        response = self.client.open_authenticated_get(replace(self.envelope, cancellation=self.cancel))
        self.permission.revoke()
        with self.assertRaisesRegex(Blocked, 'KIS_AUTH_HTTPS_GRANT_INVALID'):
            list(response.iter_bytes(1))
        self.assertEqual(self.tls.closed, 2)

    def test_cancelled_close_still_cleans_without_http_or_unwrap(self):
        response = self.client.open_authenticated_get(self.envelope)
        calls = len(self.tls.sent)
        self.cancel.cancel(); response.close(); response.close()
        self.assertEqual(self.tls.closed, 1); self.assertEqual(len(self.tls.sent), calls)

    def test_waiting_dns_retains_single_slot_and_never_connects_late(self):
        release = threading.Event(); done = threading.Event()
        def lookup(*args, **kwargs):
            try:
                self.tick = 104
                release.wait(2)
                return [self.address]
            finally: done.set()
        self.dns.side_effect = lookup
        try:
            self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED')
            request = replace(self.request, deadline_monotonic=114)
            self.error(replace(self.envelope, request=request), code='KIS_HTTPS_DNS_UNAVAILABLE')
            self.assertEqual(self.dns.call_count, 1)
        finally:
            release.set(); self.assertTrue(done.wait(1))
        self.tcp.assert_not_called()

    def test_delayed_dns_worker_cannot_lookup_after_cancellation(self):
        saved = {}
        class Delayed:
            def __init__(self, **kwargs): saved.update(kwargs)
            def start(inner): self.cancel.cancel()
        with patch.object(h.threading, 'Thread', Delayed):
            self.error(code='KIS_AUTH_HTTPS_CANCELLED')
            saved['target']()
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_read_wait_is_bounded_and_checks_cancellation(self):
        def recv(maximum): raise ssl.SSLWantReadError()
        self.tls.recv = recv
        def wait(*args): self.tick += 4; return [], [], []
        self.select.side_effect = wait
        self.error(code='KIS_HTTPS_DEADLINE_EXCEEDED')
        self.assertEqual(self.tls.closed, 1)

    def test_send_zero_and_exception_are_not_retried(self):
        self.tls.send = lambda value: 0
        self.error(code='KIS_HTTPS_IO_FAILED'); self.assertEqual(self.dns.call_count, 1)
        def fail(value): raise OSError(SECRET)
        self.tls.send = fail
        self.error(code='KIS_AUTH_HTTPS_IO_FAILED'); self.assertEqual(self.dns.call_count, 2)

    def test_bad_deadline_or_clock_before_dns(self):
        for end in [100, 99, 131, float('nan'), True]:
            self.error(replace(self.envelope, request=replace(self.request, deadline_monotonic=end)))
        self.tick = 99
        self.error()
        self.dns.assert_not_called()

    def test_dns_thread_start_failure_releases_slot(self):
        with patch.object(h.threading.Thread, 'start', side_effect=RuntimeError(SECRET)):
            self.error(code='KIS_AUTH_HTTPS_IO_FAILED')
        self.client.open_authenticated_get(self.envelope).close()
        self.dns.assert_called_once()

    def test_tls_wrap_failure_closes_raw_without_send(self):
        with patch.object(self.context, 'wrap_socket', side_effect=RuntimeError(SECRET)):
            self.error(code='KIS_AUTH_HTTPS_IO_FAILED')
        self.assertEqual(self.raw.closed, 1); self.assertEqual(self.tls.sent, [])

    def test_socket_creation_and_connect_failures_no_retry(self):
        self.tcp.side_effect = OSError(SECRET)
        self.error(code='KIS_AUTH_HTTPS_IO_FAILED'); self.tcp.assert_called_once()
        self.tcp.side_effect = None; self.raw.result = errno.ECONNREFUSED
        self.error(code='KIS_HTTPS_IO_FAILED')
        self.assertEqual(self.raw.closed, 1); self.assertEqual(self.context.wraps, [])

    def test_oversize_recv_and_early_eof_fail_closed(self):
        self.tls.recv = lambda maximum: b'x'*(maximum+1)
        self.error(code='KIS_HTTPS_BODY_INVALID')
        self.tls.recv = lambda maximum: b''
        self.error(code='KIS_HTTPS_HEADERS_INVALID')

    def test_close_failure_is_local_suppressed_and_not_retried(self):
        response = self.client.open_authenticated_get(self.envelope)
        def fail(): self.tls.closed += 1; raise OSError(SECRET)
        self.tls.close = fail
        response.close(); response.close()
        self.assertEqual(self.tls.closed, 1); self.dns.assert_called_once()
