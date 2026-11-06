"""Explicit Windows simulation-only HTTPS host. No broker or production identity."""
import argparse
from dataclasses import asdict
from datetime import datetime
import hmac
import json
import os
import re
from io import BytesIO
from pathlib import Path
import secrets
import ssl
import sys
import time
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler

from oneil_control_v2 import SessionRecord
from oneil_control_v2.app import COOKIE
from oneil_control_v2.state import APIError, check, digest
from oneil_execution_v2.domain import RiskPolicy, require
from oneil_execution_v2.api import AuthenticatedOwner
from oneil_policy_v2.policy import TradingPolicy
from .simulation_host import CONFIG, MODE, SupervisedSimulationHost, initialize_simulation_host
from .supervisor import ACCOUNT, InstanceLock, SupervisorState, safe_path
from .state import RuntimeState
from .windows_clock import sample_windows_clock
from .windows_storage import inspect_private_path, inspect_profile


class PairingApplication:
    """One-use 120-second console challenge, 900-second process-bound session."""
    def __init__(self, host, *, monotonic=time.monotonic):
        self.host, self.monotonic = host, monotonic
        self.code = secrets.token_urlsafe(32)
        self.deadline = monotonic()+120
        self.attempts = 0
        self.closed = False

    def close(self):
        self.closed = True
        self.code = None
        self.host.close()

    def __call__(self, env, start_response):
        def response(status, data, cookie=None):
            body = json.dumps(data).encode()
            headers = [('Content-Type','application/json'),('Content-Length',str(len(body))),
                       ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),
                       ('Referrer-Policy','no-referrer')]
            if cookie: headers.append(('Set-Cookie',cookie))
            start_response(status,headers)
            return [body]
        try:
            check(not self.closed and not self.host.closed,503,'HOST_CLOSED')
            self.host._thread()
            check(env.get('wsgi.url_scheme') == 'https' and env.get('HTTP_HOST') == self.host.origin[8:]
                  and not env.get('QUERY_STRING'),400,'TRUSTED_HTTPS_HOST_REQUIRED')
            path = env.get('PATH_INFO')
            if path not in ('/api/v2/pair','/api/v2/logout'):
                return self.host(env,start_response)
            check(env.get('REQUEST_METHOD') == 'POST',405,'POST_REQUIRED')
            check(env.get('HTTP_ORIGIN') == self.host.origin and
                  env.get('HTTP_SEC_FETCH_SITE','same-origin') == 'same-origin',403,'SAME_ORIGIN_REQUIRED')
            check(not self.host.supervisor.status()['stop_requested'],409,'SUPERVISOR_STOP_REQUESTED')
            data, _ = self.host.api._body(env)
            if path.endswith('/pair'):
                check(self.code is not None and self.monotonic() < self.deadline and self.attempts < 5,
                      401,'PAIRING_EXPIRED_OR_USED')
                self.attempts += 1
                check(type(data) is dict and set(data) == {'code'} and type(data['code']) is str
                      and hmac.compare_digest(data['code'],self.code),401,'PAIRING_DENIED')
                self.code = None  # Consume before any potentially uncertain session write.
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                now = int(self.host.clock().utc)
                record = SessionRecord(digest(token),digest(csrf),'simulation:local-console',ACCOUNT,
                                       frozenset({'monitor.read','simulation.command'}),now+900,now)
                try:
                    self.host.inject_simulation_session(record,csrf)
                except BaseException:
                    self.host._bootstraps.pop(record.token_digest,None)
                    with self.host.commands.transaction() as conn:
                        conn.execute('UPDATE sessions SET revoked=1 WHERE token_digest=?',(record.token_digest,))
                    raise
                if self.host.supervisor.status()['stop_requested']:
                    with self.host.commands.transaction() as conn:
                        conn.execute('UPDATE sessions SET revoked=1 WHERE token_digest=?',(record.token_digest,))
                    self.host._bootstraps.pop(record.token_digest,None)
                    raise APIError(409,'SUPERVISOR_STOP_REQUESTED')
                return response('200 OK',{'mode':MODE,'expires_in':900},
                    f'{COOKIE}={token}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=900')
            session = self.host._session(env)
            check(type(data) is dict and set(data) == {'csrf_token'} and type(data['csrf_token']) is str
                  and hmac.compare_digest(digest(data['csrf_token']),session.csrf_digest),403,'CSRF_REQUIRED')
            with self.host.commands.transaction() as conn:
                conn.execute('UPDATE sessions SET revoked=1 WHERE token_digest=?',(session.token_digest,))
            self.host._bootstraps.pop(session.token_digest,None)
            return response('200 OK',{'logged_out':True},
                f'{COOKIE}=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        except APIError as exc:
            return response(f'{exc.status} Rejected',{'error':exc.code})
        except Exception:
            return response('503 Service Unavailable',{'error':'SIMULATION_HOST_UNAVAILABLE'})


class BoundedServer(WSGIServer):
    allow_reuse_address = False
    request_queue_size = 4
    timeout = 0.25
    def get_request(self):
        connection, address = self.socket.accept()
        try:
            clock = getattr(self, 'deadline_clock', None) or time.monotonic
            deadline = clock()+2
            connection.settimeout(2)
            require(address[0] == '127.0.0.1','LOOPBACK_REQUIRED')
            connection = self.tls_context.wrap_socket(connection,server_side=True)
            connection._oneil_deadline = deadline
            connection._oneil_clock = clock
            return connection,address
        except BaseException:
            connection.close()
            raise
    def handle_error(self, request, client_address):
        pass  # Never dump requests, session headers or filesystem paths.


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_): pass
    def handle(self):
        # One total deadline covers TLS, headers, body, dispatch and response.
        connection = self.connection
        deadline = connection._oneil_deadline
        clock = vars(connection).get('_oneil_clock', time.monotonic)
        def remaining():
            left = deadline-clock()
            if left <= 0: raise TimeoutError('REQUEST_DEADLINE')
            connection.settimeout(left)
        try:
            raw = bytearray()
            while not raw.endswith(b'\r\n\r\n'):
                remaining()
                part = connection.recv(1)
                if not part: return
                raw.extend(part)
                if len(raw) > 16384: return
            lines = bytes(raw[:-4]).decode('ascii').split('\r\n')
            request = lines.pop(0).split(' ')
            if len(request) != 3: return
            method,path,version = request
            if method not in ('GET','POST') or version != 'HTTP/1.1': return
            if not re.fullmatch(r'/[A-Za-z0-9/_.-]*',path): return
            headers = {}
            for line in lines:
                name,sep,value = line.partition(':')
                if not sep or not re.fullmatch('[A-Za-z0-9-]+',name): return
                name = name.lower()
                if name in headers or any(ord(ch) < 32 for ch in value): return
                headers[name] = value.strip()
            if 'host' not in headers or any(x in headers for x in
                    ('transfer-encoding','content-encoding','expect','upgrade')): return
            length = headers.get('content-length','0')
            if not re.fullmatch('0|[1-9][0-9]{0,4}',length) or int(length) > 8192: return
            length = int(length)
            if (method == 'GET' and length) or (method == 'POST' and not length): return
            body = bytearray()
            while len(body) < length:
                remaining()
                part = connection.recv(length-len(body))
                if not part: return
                body.extend(part)
            env = {'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'',
                   'wsgi.url_scheme':'https','wsgi.input':BytesIO(body),
                   'CONTENT_TYPE':headers.get('content-type',''),'CONTENT_LENGTH':str(length)}
            for name,value in headers.items():
                if name not in ('content-type','content-length'):
                    env['HTTP_'+name.upper().replace('-','_')] = value
            result = []
            def start(status, response_headers): result.extend((status,response_headers))
            app = self.server.get_app()
            remaining()
            output = app(env,start)
            try: payload = b''.join(output)
            finally:
                if hasattr(output,'close'): output.close()
            remaining()
            # A stop racing authentication/dispatch must not release a session or data.
            if app.closed or app.host.closed or app.host.supervisor.status()['stop_requested']: return
            status,response_headers = result
            wire = ('HTTP/1.1 '+status+'\r\n'+''.join(k+': '+v+'\r\n' for k,v in response_headers)
                    +'Connection: close\r\n\r\n').encode('ascii')+payload
            remaining()
            connection.sendall(wire)
        except Exception:
            return  # Silent bounded close, never request/error logging.


def listener(app, port, cert, key, *, server_type=BoundedServer, context_factory=ssl.SSLContext, monotonic=None):
    require(type(port) is int and 1024 <= port <= 65535,'EXPLICIT_UNPRIVILEGED_PORT_REQUIRED')
    context = context_factory(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert),str(key), password=lambda: (_ for _ in ()).throw(RuntimeError('ENCRYPTED_KEY_UNSUPPORTED')))
    server = server_type(('127.0.0.1',port),QuietHandler,bind_and_activate=False)
    try:
        server.tls_context = context
        server.deadline_clock = monotonic or time.monotonic
        server.set_app(app)
        server.server_bind()
        server.server_activate()
        return server
    except BaseException:
        server.server_close()
        raise


def check_profile(directory, cert, key, port):
    require(os.name == 'nt','WINDOWS_REQUIRED')
    require(type(port) is int and 1024 <= port <= 65535,'EXPLICIT_UNPRIVILEGED_PORT_REQUIRED')
    inspect_profile(directory)
    for path in (cert,key): inspect_private_path(safe_path(path))
    config = json.loads(safe_path(Path(directory)/CONFIG).read_text())
    require(config.get('mode') == MODE and config.get('account') == ACCOUNT and
            config.get('production_identity_available') is False and
            config.get('origin') == f'https://127.0.0.1:{port}','EXACT_SIMULATION_ORIGIN_REQUIRED')
    sample_windows_clock()


def run_dashboard(directory, cert, key, port):
    check_profile(directory,cert,key,port)
    return _run_dashboard(directory,cert,key,port,clock=sample_windows_clock)


def _run_dashboard(directory, cert, key, port, *, clock, deadline_clock=None):
    """Shared lifecycle; callers must first enforce their native profile checks."""
    require(sys.stdout.isatty(),'PRIVATE_CONSOLE_REQUIRED')
    state = SupervisorState(directory)
    runtime = host = app = server = None
    lease = None
    try:
        with InstanceLock(state.directory/'supervisor.lock'):
            row = state.status()
            require(row['phase'] in ('READY','STOPPED'),'EXPLICIT_READY_PROFILE_REQUIRED')
            restarting = row['phase'] == 'STOPPED'
            if restarting: state.clear_stopped_request(row['stop_revision'])
            require(not state.status()['stop_requested'],'STOP_REQUEST_WINS')
            runtime = RuntimeState(state.directory/'runtime.sqlite')
            state.update(phase='STARTING',reason='LOCAL_SIMULATION_CONSOLE_START')
            lease = runtime.start(ACCOUNT,now=time.time(),owner_request=
                                  AuthenticatedOwner('simulation:explicit-console-restart',ACCOUNT) if restarting else None)
            state.update(lease=json.dumps(asdict(lease)),pid=os.getpid())
            stopped = False
            try:
                host = SupervisedSimulationHost(directory,lease,clock=clock)
                app = PairingApplication(host, monotonic=deadline_clock) if deadline_clock else PairingApplication(host)
                server = listener(app,port,cert,key,monotonic=deadline_clock)
                state.update(phase='RUNNING',reason='LOCAL_SIMULATION_HTTPS_ONLY')
                print(f'SIMULATION ONLY https://127.0.0.1:{port}/pair.html\nOne-use pairing code (120 seconds): {app.code}',flush=True)
                while host.tick() != 'STOPPED':
                    server.handle_request()  # No request threads: SQLite stays on its owner.
                stopped = True
            except KeyboardInterrupt:
                state.request_stop()
                stopped = True
            finally:
                if app: app.close()
                elif host: host.close()
                if server: server.server_close()
                if lease:
                    runtime.record_exit(lease,exit_code=0 if stopped else 2,now=time.time(),explicit_stop=stopped)
                    state.update(phase='STOPPED' if stopped else 'FAILED',lease=None,pid=None,last_exit=0 if stopped else 2,
                                 reason='LOCAL_SIMULATION_HOST_STOPPED' if stopped else 'LOCAL_SIMULATION_HOST_FAILED')
    finally:
        if runtime: runtime.close()
        state.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action',required=True)
    init = sub.add_parser('init')
    init.add_argument('--state-dir',required=True)
    init.add_argument('--policy-json',required=True)
    init.add_argument('--cash-krw',required=True,type=int)
    init.add_argument('--positions-json',required=True)
    init.add_argument('--port',required=True,type=int)
    for action in ('check','run-dashboard'):
        p = sub.add_parser(action)
        for arg in ('state-dir','cert','key'): p.add_argument('--'+arg,required=True)
        p.add_argument('--port',required=True,type=int)
    args = parser.parse_args(argv)
    try:
        require(os.name == 'nt','WINDOWS_REQUIRED')
        require(1024 <= args.port <= 65535,'EXPLICIT_UNPRIVILEGED_PORT_REQUIRED')
        if args.action == 'init':
            inspect_private_path(Path(args.state_dir).absolute().parent)
            raw = json.loads(safe_path(args.policy_json).read_text())
            raw['risk'] = RiskPolicy(**raw['risk'])
            raw['source_observed_at'] = datetime.fromisoformat(raw['source_observed_at'])
            policy = TradingPolicy(**raw).validate()
            positions = json.loads(safe_path(args.positions_json).read_text())
            initialize_simulation_host(args.state_dir,policy,origin=f'https://127.0.0.1:{args.port}',
                                       cash_krw=args.cash_krw,positions=positions)
            inspect_profile(args.state_dir)
        elif args.action == 'check': check_profile(args.state_dir,args.cert,args.key,args.port)
        else: run_dashboard(args.state_dir,args.cert,args.key,args.port)
        print(json.dumps({'mode':MODE,'live_capable':False,'ready_to_trade':False,'result':'completed'}))
        return 0
    except Exception:
        print(json.dumps({'mode':MODE,'live_capable':False,'ready_to_trade':False,'error':'LOCAL_SIMULATION_REQUIREMENTS_NOT_MET'}),file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
