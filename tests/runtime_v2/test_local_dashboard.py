"""Offline protocol/TLS doubles; never starts a socket or native Windows API."""
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).parent))
import test_simulation_host as fixture
from oneil_runtime_v2.local_dashboard import PairingApplication, QuietHandler, listener, main
from oneil_runtime_v2.windows_storage import inspect_private_path, inspect_profile


class PairingTests(fixture.SimulationHostTests):
    # Reuse setup only, not inherited tests.
    def setUp(self):
        super().setUp()
        self.app = PairingApplication(self.host,monotonic=lambda:10)
    def pair_call(self, body, path='/api/v2/pair', **changes):
        raw=json.dumps(body).encode(); captured=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':path,'QUERY_STRING':'','wsgi.url_scheme':'https',
             'HTTP_HOST':'simulation.invalid','HTTP_ORIGIN':'https://simulation.invalid',
             'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),**changes}
        payload=b''.join(self.app(env,lambda s,h:captured.append((int(s.split()[0]),dict(h)))))
        return *captured[0],json.loads(payload)
    def test_pair_replay_expiry_logout(self):
        code=self.app.code
        status,headers,_=self.pair_call({'code':code})
        self.assertEqual(status,200)
        cookie=headers['Set-Cookie']
        for flag in ('Secure','HttpOnly','SameSite=Strict','Max-Age=900'): self.assertIn(flag,cookie)
        self.assertEqual(self.pair_call({'code':code})[0],401)
        session=next(iter(self.host._bootstraps.values()))
        self.assertEqual(session[0].expires_at-session[0].authenticated_at,900)
        status,_,_=self.pair_call({'csrf_token':session[1]},'/api/v2/logout',HTTP_COOKIE=cookie.split(';')[0])
        self.assertEqual(status,200)
        self.assertEqual(self.host._bootstraps,{})
    def test_attempts_origin_stop_and_closed(self):
        self.assertEqual(self.pair_call({'code':self.app.code},HTTP_ORIGIN='https://evil.invalid')[0],403)
        for _ in range(5): self.assertEqual(self.pair_call({'code':'bad'})[0],401)
        self.assertEqual(self.pair_call({'code':self.app.code})[0],401)
        self.supervisor.request_stop()
        self.assertEqual(self.pair_call({'code':'bad'})[0],409)
        self.app.close()
        self.assertEqual(self.pair_call({'code':'bad'})[0],503)
    def test_logout_denies_missing_wrong_cookie_csrf_and_expired_session(self):
        _,headers,_=self.pair_call({'code':self.app.code})
        cookie=headers['Set-Cookie'].split(';')[0]
        _,csrf=next(iter(self.host._bootstraps.values()))
        self.assertEqual(self.pair_call({'csrf_token':csrf},'/api/v2/logout')[0],401)
        self.assertEqual(self.pair_call({'csrf_token':csrf},'/api/v2/logout',HTTP_COOKIE='__Host-oneil_session=wrong')[0],401)
        self.assertEqual(self.pair_call({'csrf_token':'wrong'},'/api/v2/logout',HTTP_COOKIE=cookie)[0],403)
        self.advance(900)
        self.assertEqual(self.pair_call({'csrf_token':csrf},'/api/v2/logout',HTTP_COOKIE=cookie)[0],401)

    def test_expired(self):
        self.app.monotonic=lambda:130
        self.assertEqual(self.pair_call({'code':self.app.code})[0],401)
    def test_stop_during_injection_never_emits_cookie(self):
        original=self.host.inject_simulation_session
        def inject(*args): original(*args); self.supervisor.request_stop()
        with patch.object(self.host,'inject_simulation_session',side_effect=inject):
            status,headers,_=self.pair_call({'code':self.app.code})
        self.assertEqual(status,409); self.assertNotIn('Set-Cookie',headers)
        self.app.close()
        self.assertEqual(self.host._bootstraps,{})

# Don't duplicate inherited composition tests in focused counts.
for name in list(vars(fixture.SimulationHostTests)):
    if name.startswith('test_') and name not in PairingTests.__dict__: setattr(PairingTests,name,None)


class WireTests(unittest.TestCase):
    def setUp(self):
        guard=patch('socket.socket',side_effect=AssertionError('NO_NETWORK'))
        guard.start(); self.addCleanup(guard.stop)

    def wire(self, raw, *, stop=False, clock=lambda:0):
        conn=Mock(); conn._oneil_deadline=2
        source=io.BytesIO(raw)
        conn.recv.side_effect=lambda n:source.read(n)
        app=Mock(); app.closed=False; app.host.closed=False
        app.host.supervisor.status.return_value={'stop_requested':stop}
        def respond(env,start): start('200 OK',[('Content-Length','2')]); return [b'{}']
        app.side_effect=respond
        handler=object.__new__(QuietHandler); handler.connection=conn
        handler.server=Mock(); handler.server.get_app.return_value=app
        with patch('oneil_runtime_v2.local_dashboard.time.monotonic',side_effect=clock): handler.handle()
        return app,conn
    def test_valid_and_bad_framing(self):
        valid=b'GET / HTTP/1.1\r\nHost: 127.0.0.1:9443\r\n\r\n'
        app,conn=self.wire(valid); self.assertEqual(app.call_count,1); conn.sendall.assert_called_once()
        for raw in (valid.replace(b'\r\n\r\n',b'\r\nHost: evil\r\n\r\n'),
                    valid.replace(b'GET /',b'GET https://evil/'),
                    b'POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
                    b'POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nContent-Length: 2\r\n\r\n{}',
                    b'POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 8193\r\n\r\n',
                    b'POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{',
                    b'GET / HTTP/1.1\r\nHost: '+b'x'*16384+b'\r\n\r\n'):
            app,conn=self.wire(raw); self.assertEqual(app.call_count,0); conn.sendall.assert_not_called()
    def test_deadline_and_stop_before_send(self):
        raw=b'GET / HTTP/1.1\r\nHost: x\r\n\r\n'
        app,conn=self.wire(raw,stop=True); conn.sendall.assert_not_called()
        ticks=iter([0,0,3]); app,conn=self.wire(raw,clock=lambda:next(ticks)); conn.sendall.assert_not_called()
    def test_final_body_receive_crossing_deadline_never_dispatches(self):
        raw=b'POST /api/v2/pair HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}'
        # One clock check per header byte and body recv; dispatch must check again.
        header_length=raw.index(b'\r\n\r\n')+4
        ticks=iter([0]*(header_length+1)+[3])
        app,conn=self.wire(raw,clock=lambda:next(ticks))
        self.assertEqual(app.call_count,0); conn.sendall.assert_not_called()

    def test_tls_bind_and_cleanup(self):
        context=Mock(); server=Mock(); factory=Mock(return_value=server)
        result=listener(Mock(),9443,'explicit-cert','explicit-key',server_type=factory,context_factory=lambda _:context)
        self.assertIs(result,server); self.assertEqual(factory.call_args.args[0],('127.0.0.1',9443))
        context.load_cert_chain.assert_called_once(); server.server_activate.assert_called_once()
        server.server_bind.side_effect=RuntimeError()
        with self.assertRaises(RuntimeError): listener(Mock(),9443,'c','k',server_type=factory,context_factory=lambda _:context)
        server.server_close.assert_called_once()
    def test_non_windows_denied(self):
        with patch('oneil_runtime_v2.windows_storage.os.name','posix'):
            with self.assertRaises(RuntimeError): inspect_private_path('.')
        with patch('oneil_runtime_v2.local_dashboard.os.name','posix'),patch('sys.stderr',new=io.StringIO()):
            self.assertEqual(main(['check','--state-dir','x','--cert','c','--key','k','--port','9443']),2)


class StoragePolicyTests(unittest.TestCase):
    def test_ancestor_read_allowed_write_and_delete_denied(self):
        from oneil_runtime_v2.windows_storage import validate_ace
        trusted={'owner','system'}
        validate_ace(0,'public',0x120089,trusted,ancestor=True)
        for mask in (2,4,0x40,0x10000,0x40000,0x80000,0x40000000,0x10000000):
            with self.assertRaises(RuntimeError): validate_ace(0,'public',mask,trusted,ancestor=True)
        with self.assertRaises(RuntimeError): validate_ace(0,'public',0x120089,trusted)
        with self.assertRaises(RuntimeError): validate_ace(5,'owner',0,trusted)

class LifecycleTests(fixture.SimulationHostTests):
    def test_stopped_restart_and_failures(self):
        from oneil_runtime_v2.local_dashboard import run_dashboard
        # Fixture already owns a lease; use independent profiles for CLI lifecycle tests.
        from oneil_runtime_v2.simulation_host import initialize_simulation_host
        from oneil_runtime_v2.supervisor import SupervisorState
        for fail in ('none','bind','tick'):
            directory=self.directory.parent/fail
            initialize_simulation_host(directory,self.policy,origin='https://127.0.0.1:9443',cash_krw=1,positions={})
            server=Mock()
            def stop():
                state=SupervisorState(directory)
                try: state.request_stop()
                finally: state.close()
            server.handle_request.side_effect=stop
            with patch('oneil_runtime_v2.local_dashboard.check_profile'), \
                 patch('oneil_runtime_v2.local_dashboard.sample_windows_clock',return_value=self.clock), \
                 patch('oneil_runtime_v2.local_dashboard.sys.stdout',new=Mock(isatty=lambda:True)), \
                 patch('oneil_runtime_v2.local_dashboard.listener',side_effect=RuntimeError('bind') if fail=='bind' else None,return_value=server):
                if fail=='tick':
                    with patch('oneil_runtime_v2.local_dashboard.SupervisedSimulationHost.tick',side_effect=RuntimeError('tick')):
                        with self.assertRaises(RuntimeError): run_dashboard(directory,'cert','key',9443)
                elif fail=='bind':
                    with self.assertRaises(RuntimeError): run_dashboard(directory,'cert','key',9443)
                else:
                    run_dashboard(directory,'cert','key',9443)
                    run_dashboard(directory,'cert','key',9443)
            state=SupervisorState(directory)
            try:
                self.assertEqual(state.status()['phase'],'STOPPED' if fail=='none' else 'FAILED')
                self.assertEqual(state.status()['last_exit'],0 if fail=='none' else 2)
            finally: state.close()
for name in vars(fixture.SimulationHostTests):
    if name.startswith('test_') and name not in LifecycleTests.__dict__: setattr(LifecycleTests,name,None)

class HandshakeTests(unittest.TestCase):
    def test_deadline_and_tls_failure_close_offline(self):
        from oneil_runtime_v2.local_dashboard import BoundedServer
        server=object.__new__(BoundedServer)
        raw=Mock(); wrapped=Mock()
        server.socket=Mock(); server.socket.accept.return_value=(raw,('127.0.0.1',1000))
        server.tls_context=Mock(); server.tls_context.wrap_socket.return_value=wrapped
        with patch('oneil_runtime_v2.local_dashboard.time.monotonic',return_value=100):
            connection,_=server.get_request()
        self.assertEqual(connection._oneil_deadline,102)
        raw.settimeout.assert_called_once_with(2)
        server.tls_context.wrap_socket.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError): server.get_request()
        raw.close.assert_called_once()

if __name__=='__main__': unittest.main()
