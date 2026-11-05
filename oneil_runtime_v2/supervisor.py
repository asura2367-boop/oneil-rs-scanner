"""Local, fixed-child OFFLINE rehearsal supervisor. Never an execution service.

The default child profile is reconciliation-only. An explicitly initialized local
control profile accepts exact simulated commands, never real broker requests. No arbitrary command,
module, account, credential, endpoint, installer or automatic startup is accepted.
"""
from contextlib import contextmanager
from dataclasses import asdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time

from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import require
from .state import RuntimeLease, RuntimeState

ACCOUNT = hashlib.sha256(b'ONEIL_FIXED_OFFLINE_HOST_REHEARSAL_NOT_AN_ACCOUNT').hexdigest()
ROOT = Path(__file__).resolve().parents[1]
PHASES = {'READY', 'STARTING', 'RUNNING', 'RESTART_WAIT', 'STOPPED', 'FAILED'}


def safe_path(path, *, directory=False):
    """Reject obvious remote/reparse paths, not a Windows ACL or sync audit."""
    path = Path(path).absolute()
    require(not str(path).startswith(('\\\\', '//')), 'LOCAL_STATE_PATH_REQUIRED')
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            require(not stat.S_ISLNK(info.st_mode)
                    and not getattr(info, 'st_file_attributes', 0) & 0x400,
                    'REPARSE_STATE_PATH_DENIED')
    require(path.is_dir() if directory else path.is_file(), 'SUPERVISOR_STATE_MISSING')
    return path


class InstanceLock:
    """Non-blocking OS lock, held for the complete supervisor lifetime.

    msvcrt byte-range lock on Windows; flock only for Linux test portability.
    No stale-PID deletion, heartbeat takeover, lock-file unlink or network socket.
    """
    def __init__(self, path):
        self.file = safe_path(path).open('r+b', buffering=0)
        self.locked = False

    def __enter__(self):
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.locked = True
            return self
        except BaseException:
            self.file.close()
            raise RuntimeError('SUPERVISOR_ALREADY_RUNNING_OR_LOCK_UNAVAILABLE') from None

    def __exit__(self, *_):
        # Closing the handle releases its OS lock, including process exit.
        self.file.close()
        self.locked = False


class SupervisorState:
    @classmethod
    def initialize(cls, directory):
        directory = Path(directory).absolute()
        safe_path(directory.parent, directory=True)
        require(not str(directory).startswith(('\\\\', '//')), 'LOCAL_STATE_PATH_REQUIRED')
        # An existing directory, even empty, is never reinitialized.
        directory.mkdir()
        (directory/'supervisor.lock').write_bytes(b'0')
        (directory/'worker.lock').write_bytes(b'0')
        runtime = RuntimeState.initialize(directory/'runtime.sqlite')
        try:
            runtime.register_simulation_account(ACCOUNT)
        finally:
            runtime.close()
        from .rehearsal_store import RehearsalStore
        core = RehearsalStore.initialize(directory/'rehearsal.sqlite', ACCOUNT)
        core.close()
        conn = sqlite3.connect(str(directory/'supervisor.sqlite'), isolation_level=None)
        try:
            conn.execute('PRAGMA synchronous=FULL')
            conn.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE metadata (version INTEGER NOT NULL CHECK(version=1));
                INSERT INTO metadata VALUES(1);
                CREATE TABLE supervisor (
                    id INTEGER PRIMARY KEY CHECK(id=1), phase TEXT NOT NULL,
                    stop_requested INTEGER NOT NULL CHECK(stop_requested IN (0,1)),
                    stop_revision INTEGER NOT NULL CHECK(stop_revision >= 0),
                    lease TEXT, pid INTEGER, delay_seconds INTEGER,
                    last_exit INTEGER, reason TEXT NOT NULL);
                INSERT INTO supervisor VALUES(1,'READY',0,0,NULL,NULL,NULL,NULL,'OFFLINE_ONLY');
                COMMIT;
            ''')
        finally:
            conn.close()
        return cls(directory)

    def __init__(self, directory):
        self.directory = safe_path(directory, directory=True)
        path = safe_path(self.directory/'supervisor.sqlite')
        safe_path(self.directory/'runtime.sqlite')
        safe_path(self.directory/'supervisor.lock')
        safe_path(self.directory/'worker.lock')
        safe_path(self.directory/'rehearsal.sqlite')
        self.conn = sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True,
                                   isolation_level=None, timeout=1)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA synchronous=FULL')
        try:
            require([tuple(x) for x in self.conn.execute('SELECT version FROM metadata')] == [(1,)],
                    'SUPERVISOR_VERSION_INVALID')
            require(self.conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok',
                    'SUPERVISOR_STATE_CORRUPT')
            self.status()
        except BaseException:
            self.conn.close()
            raise

    def status(self):
        rows = list(self.conn.execute('SELECT * FROM supervisor'))
        require(len(rows) == 1, 'SUPERVISOR_STATE_CORRUPT')
        row = dict(rows[0])
        require(row['id'] == 1 and row['phase'] in PHASES and row['stop_requested'] in (0, 1)
                and type(row['stop_revision']) is int and row['stop_revision'] >= 0,
                'SUPERVISOR_STATE_CORRUPT')
        require(row['pid'] is None or type(row['pid']) is int and row['pid'] > 0,
                'SUPERVISOR_STATE_CORRUPT')
        require(row['delay_seconds'] is None or row['delay_seconds'] in (5, 10, 20),
                'SUPERVISOR_STATE_CORRUPT')
        if row['lease'] is not None:
            lease = RuntimeLease(**json.loads(row['lease']))
            require(lease.account == ACCOUNT and lease.engine == 'V2_SIMULATION',
                    'FIXED_OFFLINE_ACCOUNT_REQUIRED')
        return row

    @contextmanager
    def transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute('ROLLBACK')
            raise

    def update(self, **fields):
        require(fields and set(fields) <= {'phase','lease','pid','delay_seconds','last_exit','reason'},
                'INVALID_SUPERVISOR_UPDATE')
        with self.transaction():
            self.conn.execute('UPDATE supervisor SET '+','.join(k+'=?' for k in fields)+' WHERE id=1',
                              tuple(fields.values()))
            self.status()

    def request_stop(self):
        with self.transaction():
            self.status()
            self.conn.execute("UPDATE supervisor SET stop_requested=1,stop_revision=stop_revision+1,reason='OWNER_STOP_REQUESTED' WHERE id=1")

    def clear_stopped_request(self, expected_revision):
        with self.transaction():
            row = self.status()
            require(row['phase'] == 'STOPPED', 'EXPLICIT_STOPPED_START_ONLY')
            require(row['stop_revision'] == expected_revision, 'NEW_STOP_REQUEST_WINS')
            self.conn.execute('UPDATE supervisor SET stop_requested=0 WHERE id=1')

    def close(self):
        self.conn.close()


def spawn_observer(directory, lease):
    """Only the repository-owned reconciliation-only child, using an absolute Python."""
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    return subprocess.Popen([
        sys.executable, '-I', '-B', str(ROOT/'oneil_runtime_v2/worker_child.py'),
        '--state-dir', str(directory), '--owner', lease.owner, '--epoch', str(lease.epoch),
    ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
       stderr=subprocess.DEVNULL, shell=False, close_fds=True)


class OfflineSupervisor:
    def __init__(self, state, *, spawn=spawn_observer, sleep=time.sleep,
                 monotonic=time.monotonic, now=time.time):
        require(type(state) is SupervisorState, 'SUPERVISOR_STATE_REQUIRED')
        self.state, self.spawn, self.sleep, self.monotonic, self.now = state, spawn, sleep, monotonic, now
        self.child = None
        self.lease = None

    def _wait(self, seconds):
        deadline = self.monotonic()+seconds
        while self.monotonic() < deadline:
            if self.state.status()['stop_requested']:
                return False
            self.sleep(min(0.1, max(0, deadline-self.monotonic())))
        return not self.state.status()['stop_requested']

    def _stop_child(self):
        # Cooperative durable flag first. Terminate only our exact Popen handle,
        # never a PID read from disk or a process name. Never retry an order.
        self.state.request_stop()
        if self.child is None:
            return None
        deadline = self.monotonic()+5
        while self.child.poll() is None and self.monotonic() < deadline:
            self.sleep(0.1)
        if self.child.poll() is None:
            self.child.terminate()
        try:
            return self.child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Retain ownership and STARTING/RUNNING. No next child may launch.
            raise RuntimeError('CHILD_EXIT_UNCONFIRMED_OPERATOR_REQUIRED') from None

    def _stop_idle(self, runtime):
        # Synthetic rehearsal lifecycle only, with no owner to release.
        with runtime.transaction():
            row = runtime.status(ACCOUNT)
            require(row['owner'] is None, 'PRIOR_CHILD_OUTCOME_UNKNOWN_OPERATOR_REQUIRED')
            runtime._incident(ACCOUNT, row['epoch'], 'OWNER_STOPPED_RUNTIME')
            runtime.conn.execute("UPDATE runtime SET phase='STOPPED',restart_at=NULL WHERE account=?", (ACCOUNT,))
        self.state.update(phase='STOPPED', delay_seconds=None, reason='OWNER_STOPPED_BEFORE_LAUNCH')

    def _finish_exit(self, runtime, code, *, stopped):
        require(type(code) is int, 'CONFIRMED_PROCESS_EXIT_REQUIRED')
        plan = runtime.record_exit(self.lease, exit_code=code, now=self.now(), explicit_stop=stopped)
        phase = 'RESTART_WAIT' if plan.delay_seconds else (
            'STOPPED' if stopped or code == 0 else 'FAILED')
        self.state.update(phase=phase, lease=None, pid=None, last_exit=code,
                          delay_seconds=plan.delay_seconds, reason=plan.action)
        self.child = self.lease = None
        return plan

    def run(self, *, start_stopped=False):
        require(type(start_stopped) is bool, 'INVALID_START_OPTION')
        with InstanceLock(self.state.directory/'supervisor.lock'):
            row = self.state.status()
            # A released lock proves only the old supervisor ended. It does NOT
            # prove its child ended. No PID-age heuristic or force-recover switch.
            require(row['phase'] not in {'STARTING','RUNNING'}, 'PRIOR_CHILD_OUTCOME_UNKNOWN_OPERATOR_REQUIRED')
            require(row['phase'] != 'FAILED', 'RESTART_BUDGET_EXHAUSTED_REQUIRE_OPERATOR')
            if row['phase'] == 'STOPPED':
                require(start_stopped, 'EXPLICIT_START_STOPPED_REQUIRED')
                self.state.clear_stopped_request(row['stop_revision'])
            runtime = RuntimeState(self.state.directory/'runtime.sqlite')
            try:
                while True:
                    row = self.state.status()
                    if row['stop_requested']:
                        self._stop_idle(runtime)
                        return 0
                    if row['phase'] == 'RESTART_WAIT':
                        # Full monotonic backoff also after supervisor reopening.
                        # RuntimeState additionally enforces its UTC deadline.
                        require(row['delay_seconds'] in (5, 10, 20), 'RESTART_DELAY_MISSING')
                        if not self._wait(row['delay_seconds']):
                            continue
                    # STARTING is committed BEFORE ownership/spawn. Any uncertain
                    # interruption is blocked on the next run, not guessed clean.
                    self.state.update(phase='STARTING', reason='STARTUP_RECONCILIATION_REQUIRED')
                    request = AuthenticatedOwner('explicit-local-offline-start', ACCOUNT) if start_stopped else None
                    self.lease = runtime.start(ACCOUNT, now=self.now(), owner_request=request)
                    self.state.update(lease=json.dumps(asdict(self.lease), sort_keys=True))
                    try:
                        self.child = self.spawn(self.state.directory, self.lease)
                    except Exception:
                        # Even a raised spawn API may have an uncertain side effect.
                        # Keep the ownership claim; no automatic retry.
                        self.state.update(reason='SPAWN_OUTCOME_UNKNOWN_OPERATOR_REQUIRED')
                        raise
                    self.state.update(phase='RUNNING', pid=self.child.pid, reason='OFFLINE_RECONCILIATION_REQUIRED')
                    while True:
                        code = self.child.poll()
                        if self.state.status()['stop_requested']:
                            code = self._stop_child()
                            self._finish_exit(runtime, code, stopped=True)
                            return 0
                        if code is not None:
                            plan = self._finish_exit(runtime, code, stopped=False)
                            if plan.delay_seconds is None:
                                return 0 if code == 0 else 2
                            break
                        self.sleep(0.1)
            except KeyboardInterrupt:
                code = self._stop_child()
                if self.lease is not None and code is not None:
                    self._finish_exit(runtime, code, stopped=True)
                elif self.lease is None:
                    self._stop_idle(runtime)
                return 130
            except BaseException:
                # Best effort cleanup never converts uncertain storage/process
                # outcomes into a clean journal or permits a second worker.
                if self.child is not None:
                    try:
                        self._stop_child()
                    except Exception:
                        pass
                raise
            finally:
                runtime.close()


def report(state):
    row = state.status()
    runtime = RuntimeState(state.directory/'runtime.sqlite')
    try:
        status = runtime.status(ACCOUNT)
        from .simulation_host import CONFIG, MODE
        if (state.directory/CONFIG).exists():
            from .simulation_store import SQLiteSimulationStore
            core = SQLiteSimulationStore(state.directory/'simulation.sqlite')
            try:
                health, control = core.health(), core.control(ACCOUNT)
            finally:
                core.close()
            return {'mode': MODE, 'live_capable': False, 'ready_to_trade': False,
                'commands_enabled': False, 'production_identity_available': False,
                'simulation_dashboard_composed': True, 'listener_started': False,
                'session_injection_required': True, 'phase': row['phase'], 'reason': row['reason'],
                'stop_requested': bool(row['stop_requested']), 'child_pid': row['pid'],
                'runtime_phase': status['phase'], 'control_revision': control['revision'],
                'simulation_halted': control['halted'], 'restart_count': status['restart_count'],
                'simulation_worker_health': ({k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                    for k,v in health.items()} if health else None),
                'execution_dependency': 'ISOLATED_SQLITE_SIMULATION',
                'actual_windows_validated': False, 'real_broker_connected': False}
        from .rehearsal_store import RehearsalStore
        core = RehearsalStore(state.directory/'rehearsal.sqlite')
        try:
            core_health = core.health()
            core.control(ACCOUNT)
        finally:
            core.close()
        return {'mode': 'OFFLINE_WORKER_RECONCILIATION', 'live_capable': False,
                'commands_enabled': False, 'ready_to_trade': False,
                'phase': row['phase'], 'reason': row['reason'],
                'stop_requested': bool(row['stop_requested']), 'child_pid': row['pid'],
                'runtime_phase': status['phase'], 'restart_count': status['restart_count'],
                'actual_windows_validated': False, 'broker_reconciled': False,
                'worker_connected': False,
                'simulation_worker_composed': core_health is not None,
                'simulation_worker_health': core_health,
                'execution_dependency': 'ISOLATED_SQLITE_RECONCILIATION_ONLY',
                'simulation_reconciled': status['phase'] == 'AWAITING_OWNER',
                'real_broker_connected': False}
    finally:
        runtime.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'run', 'stop', 'status'))
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--start-stopped', action='store_true')
    args = parser.parse_args(argv)
    if args.start_stopped and args.action != 'run':
        parser.error('--start-stopped is only valid for run')
    state = None
    try:
        # No operational CLI override on other OSes. Unit tests inject adapters.
        require(os.name == 'nt', 'WINDOWS_REQUIRED_NOT_VALIDATED_ON_THIS_HOST')
        state = SupervisorState.initialize(args.state_dir) if args.action == 'init' else SupervisorState(args.state_dir)
        code = 0
        if args.action == 'run':
            code = OfflineSupervisor(state).run(start_stopped=args.start_stopped)
        elif args.action == 'stop':
            state.request_stop()
        print(json.dumps(report(state), ensure_ascii=True, indent=2))
        return code
    except Exception as exc:
        # No exception data containing paths/account data/credentials is logged.
        print(json.dumps({'mode': 'OFFLINE_WORKER_RECONCILIATION', 'ready_to_trade': False,
                          'error': str(exc) if type(exc).__name__ in {'Blocked','RuntimeError'} else type(exc).__name__}),
              file=sys.stderr)
        return 2
    finally:
        if state is not None:
            state.close()


if __name__ == '__main__':
    raise SystemExit(main())
