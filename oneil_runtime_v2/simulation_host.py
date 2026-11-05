"""One-process durable SQLite simulation runtime and authenticated WSGI dashboard.

No listener, login issuer, production identity, network transport or live broker.
An authorized embedding host may inject short-lived simulation session digests
and dispatch WSGI calls on this owning thread. Missing injection stays closed.
"""
from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time

from oneil_control_v2 import ControlAPI, ControlState, SessionRecord, Settings, SimulationPolicyCoordinator
from oneil_control_v2.bridge import CoreSimulationBackend
from oneil_control_v2.state import APIError, canonical, check, digest
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import BrokerOrder, Order, SessionPermit, require, integer
from oneil_execution_v2.worker import Worker
from oneil_monitor_v2 import project_status
from oneil_policy_v2.admission import SimulationPolicySource
from oneil_policy_v2.policy import TradingPolicy
from .health import Evidence, HealthSample
from .orchestrator import SimulationRuntime
from .simulation_store import SQLiteSimulationStore
from .state import RuntimeLease, RuntimeState
from .supervisor import ACCOUNT, InstanceLock, SupervisorState, safe_path

CONFIG = 'simulation-host.json'
MODE = 'LOCAL_SQLITE_SIMULATION_ONLY'
ASSETS = {'/logout.html': ('logout.html', 'text/html; charset=utf-8'),
          '/logout.mjs': ('logout.mjs', 'text/javascript; charset=utf-8'),
          '/pair.html': ('pair.html', 'text/html; charset=utf-8'),
          '/pair.mjs': ('pair.mjs', 'text/javascript; charset=utf-8'),
          '/': ('index.html', 'text/html; charset=utf-8'),
          '/simulation.mjs': ('simulation.mjs', 'text/javascript; charset=utf-8'),
          '/simulation.css': ('simulation.css', 'text/css; charset=utf-8')}


def initialize_simulation_host(directory, policy, *, origin, cash_krw, positions):
    """Explicit new-directory setup, never migration/fallback or account inference."""
    require(type(policy) is TradingPolicy and policy.account == ACCOUNT, 'FIXED_SIMULATION_POLICY_REQUIRED')
    policy.validate()
    Settings(origin, 10, False).validate()
    require(integer(cash_krw, 0) and type(positions) is dict, 'EXPLICIT_SIMULATION_BALANCE_REQUIRED')
    for ticker, value in positions.items():
        require(type(ticker) is str and re.fullmatch('[0-9]{6}', ticker) and type(value) is dict
            and set(value) == {'quantity', 'market_value_krw'}
            and integer(value['quantity'], 1) and integer(value['market_value_krw'], 0),
            'EXPLICIT_SIMULATION_BALANCE_REQUIRED')
    state = SupervisorState.initialize(directory)
    try:
        # Private journals contain only simulation metadata and session digests.
        os.chmod(state.directory, 0o700)
        core = SQLiteSimulationStore.initialize(state.directory/'simulation.sqlite', ACCOUNT, policy.core_risk)
        core.close()
        commands = ControlState(state.directory/'control.sqlite')
        commands.initialize()
        config = {'version': 1, 'mode': MODE, 'origin': origin, 'account': ACCOUNT,
                  'policy_version': policy.version, 'production_identity_available': False}
        with os.fdopen(os.open(state.directory/CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
            file.write(canonical(config))
        with os.fdopen(os.open(state.directory/'simulator.sqlite', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb'):
            pass
        conn = sqlite3.connect(state.directory/'simulator.sqlite', isolation_level=None)
        try:
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('CREATE TABLE simulator(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            conn.execute('INSERT INTO simulator VALUES(1,?)', (canonical({'version': 1, 'mode': MODE,
                'cash_krw': cash_krw, 'positions': positions, 'balances': {}, 'orders': {}, 'specs': {}, 'calls': [],
                'history_complete': True, 'daily_loss_krw': 0}),))
        finally:
            conn.close()
    finally:
        state.close()


class SupervisedSimulationHost:
    """Owns every execution/control object on one thread and one supervised lease."""
    def __init__(self, directory, lease, *, clock):
        self.owner_thread = threading.get_ident()
        self.clock = clock
        self.supervisor = SupervisorState(directory)
        self.state = self.core = self.simulator_state = self.runtime = None
        self.lock = None
        self.closed = False
        self._bootstraps = {}
        self._kis_diagnostics = None
        try:
            self.lock = InstanceLock(self.supervisor.directory/'worker.lock')
            self.lock.__enter__()
            launch = self.supervisor.status()
            require(type(lease) is RuntimeLease and lease.account == ACCOUNT and lease.engine == 'V2_SIMULATION'
                and launch['phase'] in ('STARTING', 'RUNNING') and launch['lease'] is not None
                and json.loads(launch['lease']) == asdict(lease) and not launch['stop_requested'],
                'SUPERVISOR_LAUNCH_RECEIPT_REQUIRED')
            path = safe_path(self.supervisor.directory/CONFIG)
            config = json.loads(path.read_text())
            require(type(config) is dict and set(config) == {'version','mode','origin','account','policy_version','production_identity_available'}
                and config['version'] == 1 and config['mode'] == MODE and config['account'] == ACCOUNT
                and config['production_identity_available'] is False, 'SIMULATION_HOST_CONFIG_INVALID')
            self.origin = Settings(config['origin'], 10, False).validate().origin
            self.state = RuntimeState(self.supervisor.directory/'runtime.sqlite')
            self.core = SQLiteSimulationStore(self.supervisor.directory/'simulation.sqlite',
                now=lambda: datetime.fromtimestamp(self.clock().utc, timezone.utc))
            require(self.core.control(ACCOUNT)['policy']['version'] == config['policy_version'], 'POLICY_CHANGED')
            self.commands = ControlState(self.supervisor.directory/'control.sqlite')
            # Session injection is process/lease scoped. A restart never revives
            # a bearer session from an earlier process, even if unexpired.
            with self.commands.transaction() as conn:
                conn.execute('UPDATE sessions SET revoked=1')
            self.simulator_state = sqlite3.connect(safe_path(self.supervisor.directory/'simulator.sqlite').resolve().as_uri()+'?mode=rw',
                uri=True, isolation_level=None)
            self.simulator_state.execute('PRAGMA synchronous=FULL')
            self.broker = self._load_simulator()
            self.worker = Worker(self.core, self.broker, policy_source=SimulationPolicySource())
            self.runtime = SimulationRuntime(self.state, self.worker, ACCOUNT, clock)
            self.runtime.attach_supervised(lease)
            # Pending work keeps execution closed, but the host remains
            # available for authenticated cancellation or explicit reconstruction.
            # Never guess original intent or current evidence on startup.
            if not self.core.recovery_requests(ACCOUNT):
                self.runtime.restore_pending({})
            self.backend = CoreSimulationBackend(self.core)
            self.coordinator = SimulationPolicyCoordinator(state=self.commands, backend=self.backend,
                worker=self.worker, runtime=self.runtime)
            self.api = ControlAPI(state=self.commands, backend=self.backend, projector=project_status,
                settings=Settings(self.origin, 10, False), clock=lambda: self.clock().utc)
        except BaseException:
            self.close()
            raise

    def _thread(self):
        require(threading.get_ident() == self.owner_thread and not self.closed, 'SIMULATION_HOST_OWNER_REQUIRED')

    def _load_simulator(self):
        rows = list(self.simulator_state.execute('SELECT data FROM simulator WHERE id=1'))
        require(len(rows) == 1, 'SIMULATOR_STATE_REQUIRED')
        value = json.loads(rows[0][0])
        require(type(value) is dict and set(value) == {'version','mode','cash_krw','positions','balances','orders','specs','calls','history_complete','daily_loss_krw'}
            and value['version'] == 1 and value['mode'] == MODE, 'SIMULATOR_STATE_INVALID')
        require(integer(value['cash_krw'], 0) and type(value['positions']) is dict
            and type(value['balances']) is dict and type(value['orders']) is dict
            and type(value['specs']) is dict and type(value['calls']) is list
            and type(value['history_complete']) is bool and integer(value['daily_loss_krw'], 0), 'SIMULATOR_STATE_INVALID')
        broker = SimulatedBroker(self.core.now, cash_krw=value['cash_krw'], positions=value['positions'])
        broker.history_complete = value['history_complete']
        broker.daily_loss_krw = value['daily_loss_krw']
        broker.balances = value['balances']
        broker.calls = value['calls']
        require(set(broker.balances) <= {ACCOUNT} and len(set(broker.calls)) == len(broker.calls), 'SIMULATOR_STATE_INVALID')
        for key, raw in value['specs'].items():
            require(type(raw) is dict and set(raw) == {'order','permit'}, 'SIMULATOR_STATE_INVALID')
            order = Order.from_payload(raw['order']).validate()
            permit = dict(raw['permit'])
            for name in ('opens_at', 'closes_at', 'verified_at'):
                permit[name] = datetime.fromisoformat(permit[name])
            permit = SessionPermit(**permit)
            require(order.account == ACCOUNT, 'FIXED_OFFLINE_ACCOUNT_REQUIRED')
            broker.specs[key] = (order, permit)
        for key, raw in value['orders'].items():
            observed = BrokerOrder(**raw)
            require(key in broker.specs, 'SIMULATOR_STATE_INVALID')
            order, permit = broker.specs[key]
            observed.validate(key, order, permit.trading_day)
            broker.orders[key] = observed
        require(set(broker.specs) == set(broker.calls), 'SIMULATOR_STATE_INVALID')
        # A crash before simulator checkpoint never invents history from a core
        # receipt. Missing causal checkpoint data blocks the whole next lifecycle.
        for row in self.core._rows(ACCOUNT):
            if row['attempts'] and (row['request_id'] not in broker.specs or
                    row['filled_quantity'] > broker.balances.get(ACCOUNT, {}).get('fills', {}).get(row['request_id'], 0)):
                broker.history_complete = False
        return broker

    def _checkpoint_simulator(self):
        value = {'version': 1, 'mode': MODE, 'cash_krw': self.broker.cash_krw, 'positions': self.broker.positions,
            'history_complete': self.broker.history_complete, 'daily_loss_krw': self.broker.daily_loss_krw,
            'balances': self.broker.balances, 'orders': {k: asdict(v) for k,v in self.broker.orders.items()},
            'specs': {k: {'order': o.payload(), 'permit': {n: (x.isoformat() if isinstance(x, datetime) else x)
                for n,x in asdict(p).items()}} for k,(o,p) in self.broker.specs.items()}, 'calls': self.broker.calls}
        self.simulator_state.execute('BEGIN IMMEDIATE')
        try:
            self.simulator_state.execute('UPDATE simulator SET data=? WHERE id=1', (canonical(value),))
            self.simulator_state.execute('COMMIT')
        except BaseException:
            if self.simulator_state.in_transaction:
                self.simulator_state.execute('ROLLBACK')
            raise

    def inject_simulation_session(self, record, csrf_token):
        """Trusted issuer call only. No login route, file/env token or default user.

        Raw session cookie is delivered separately by the authorized HTTPS issuer.
        Only its digest and a transient, matching CSRF bootstrap are accepted here.
        """
        self._thread()
        require(type(record) is SessionRecord and record.account == ACCOUNT
            and record.principal.startswith('simulation:') and type(csrf_token) is str
            and re.fullmatch('[A-Za-z0-9_-]{43,128}', csrf_token)
            and digest(csrf_token) == record.csrf_digest, 'EXPLICIT_SIMULATION_SESSION_REQUIRED')
        record.validate()
        require(record.authenticated_at <= self.clock().utc < record.expires_at, 'SIMULATION_SESSION_EXPIRED')
        self.commands.install_session_record(record)
        self._bootstraps[record.token_digest] = (record, csrf_token)
        if 'simulation.command' in record.scopes:
            self.api.settings = Settings(self.origin, 10, True)

    def _session(self, env):
        session = self.api._session(env, int(self.clock().utc))
        check('monitor.read' in session.scopes, 403, 'READ_SCOPE_REQUIRED')
        check(session.token_digest in self._bootstraps and self._bootstraps[session.token_digest][0] == session,
              401, 'CURRENT_PROCESS_SESSION_REQUIRED')
        return session

    def restore_pending(self, current_contexts):
        """Trusted provider recovery only, never browser JSON or inferred evidence."""
        self._thread()
        return self.runtime.restore_pending(current_contexts)

    def attach_kis_diagnostics(self, diagnostics):
        """Trusted observer attachment only. Never constructs or starts a source.

        This does not bind a real account to the fixed synthetic trading account,
        register policy evidence, create credentials, or authorize any reads.
        No browser route calls it and no default factory attaches a diagnostic.
        """
        from .kis_diagnostics import KisDiagnosticRun
        self._thread()
        require(type(diagnostics) is KisDiagnosticRun and self._kis_diagnostics is None
                and not self.supervisor.status()['stop_requested'],
                'EXPLICIT_KIS_DIAGNOSTIC_ATTACHMENT_REQUIRED')
        self._kis_diagnostics = diagnostics

    def _diagnostic_status(self):
        from .kis_diagnostics import unavailable_diagnostics
        if self._kis_diagnostics is None:
            return unavailable_diagnostics()
        if self.supervisor.status()['stop_requested']:
            self._kis_diagnostics.stop()
        return self._kis_diagnostics.snapshot()

    def tick(self):
        self._thread()
        try:
            if self.supervisor.status()['stop_requested']:
                if self._kis_diagnostics is not None:
                    self._kis_diagnostics.stop()
                self.runtime.quiesce('SUPERVISED_CHILD_STOPPED')
                return 'STOPPED'
            self.core.control(ACCOUNT)
            database_observed_at = self.clock().utc
            history = self.broker.history(ACCOUNT)
            health = self.core.health()
            heartbeat = health['heartbeat_at'].timestamp() if health and health['worker_id'] == self.worker.worker_id else None
            try:
                free = shutil.disk_usage(self.supervisor.directory).free
            except OSError:
                free = -1
            # Sample after dependency observations: an advancing real clock must not
            # make freshly read broker evidence appear to come from the future.
            stamp = self.clock()
            sample = HealthSample(stamp, True, heartbeat, Evidence(True, database_observed_at),
                Evidence(history.complete, history.observed_at.timestamp()), free, bool(self.core.unresolved(ACCOUNT)))
            outcome = self.runtime.step(sample)
            self._checkpoint_simulator()
            if (outcome == 'RECONCILED_ONLY' and self.state.status(ACCOUNT)['reconstructed'] is None
                    and not self.core.recovery_requests(ACCOUNT)):
                self.runtime.restore_pending({})
            return outcome
        except BaseException:
            self.coordinator.outcome_unknown(ACCOUNT)
            raise

    def __call__(self, env, start_response):
        self._thread()
        def respond(status, data, content_type='application/json; charset=utf-8'):
            body = data if type(data) is bytes else canonical(data).encode()
            start_response(status, [('Content-Type', content_type), ('Content-Length', str(len(body))),
                ('Cache-Control','no-store'), ('X-Content-Type-Options','nosniff'),
                ('Referrer-Policy','no-referrer'), ('Strict-Transport-Security','max-age=31536000'),
                ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")])
            return [body]
        try:
            check(env.get('wsgi.url_scheme') == 'https' and env.get('HTTP_HOST') == self.origin.split('://',1)[1]
                  and not env.get('QUERY_STRING'), 400, 'TRUSTED_HTTPS_HOST_REQUIRED')
            path, method = env.get('PATH_INFO'), env.get('REQUEST_METHOD')
            if method == 'GET' and path in ASSETS:
                name, kind = ASSETS[path]
                return respond('200 OK', (Path(__file__).parent/'web'/name).read_bytes(), kind)
            if method == 'GET' and path in ('/api/v2/session', '/api/v2/host-state'):
                session = self._session(env)
                if path.endswith('/session'):
                    return respond('200 OK', {'mode': MODE, 'csrf_token': self._bootstraps[session.token_digest][1],
                        'scopes': sorted(session.scopes), 'production_identity_available': False})
                control = self.core.control(ACCOUNT)
                with self.commands.transaction() as conn:
                    proposals = [dict(r) for r in conn.execute('SELECT proposal_id,intent_digest FROM policy_proposals WHERE principal=? AND account=? AND proposal_id NOT IN (SELECT proposal_id FROM policy_dispatches)',
                        (session.principal, ACCOUNT)) if r['proposal_id'] in self.coordinator._contexts]
                payload = {'mode': MODE, 'live_capable': False, 'production_identity_available': False,
                    'runtime_phase': self.state.status(ACCOUNT)['phase'], 'control_revision': control['revision'],
                    'halted': control['halted'], 'proposals': proposals, 'stop_requested': bool(self.supervisor.status()['stop_requested']),
                    'kis_diagnostics': self._diagnostic_status(),
                    'monitor': project_status(self.core.read_model(ACCOUNT), now=self.core.now(), broker_max_age_seconds=10)}
                check(self._session(env) == session, 401, 'CURRENT_PROCESS_SESSION_REQUIRED')
                return respond('200 OK', payload)
            if method == 'POST':
                check(not self.supervisor.status()['stop_requested'], 409, 'SUPERVISOR_STOP_REQUESTED')
                self._session(env)
            return self.api(env, start_response)
        except APIError as exc:
            return respond(f'{exc.status} ' + ('Unauthorized' if exc.status == 401 else 'Forbidden' if exc.status == 403 else 'Conflict' if exc.status == 409 else 'Bad Request'), {'error': exc.code})
        except Exception:
            return respond('503 Service Unavailable', {'error':'SIMULATION_HOST_UNAVAILABLE'})

    def run(self, *, dispatch=None, sleep=time.sleep):
        """Same-thread embedding loop; dispatch is an authorized host, not an IPC/listener fallback."""
        self._thread()
        require(dispatch is None or callable(dispatch), 'EXPLICIT_DISPATCH_ADAPTER_REQUIRED')
        try:
            while self.tick() != 'STOPPED':
                if dispatch is not None:
                    dispatch(self)
                sleep(0.5)
            return 0
        finally:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._bootstraps.clear()
        if self._kis_diagnostics is not None:
            self._kis_diagnostics.stop()
        if self.runtime is not None:
            try:
                self.runtime.quiesce('SUPERVISED_CHILD_STOPPED')
            except Exception:
                if self.runtime.guard is not None:
                    self.runtime.guard.poisoned = True
        if hasattr(self, 'commands'):
            try:
                with self.commands.transaction() as conn:
                    conn.execute('UPDATE sessions SET revoked=1')
            except Exception:
                pass
        for obj in (self.core, self.state, self.simulator_state, self.supervisor):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self.lock is not None:
            self.lock.__exit__(None, None, None)
