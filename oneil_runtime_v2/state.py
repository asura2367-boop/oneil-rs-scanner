"""Durable lifecycle and cooperative ownership MODEL for offline simulation.

A single SQLite file coordinates participating simulation engines. An untouched
legacy process, another database, a restored file or a hostile writer is NOT fenced.
The lock must cover the entire simulated worker tick; it is not a broker token.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sqlite3
import uuid

from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import require
from .health import ClockSample, evaluate, number
from .intent import encode_intent, decode_intent
from .restart import plan_restart


@dataclass(frozen=True)
class RuntimeLease:
    account: str
    engine: str
    owner: str
    epoch: int


class RuntimeState:
    @classmethod
    def initialize(cls, path):
        path = Path(path)
        with path.open('xb'):
            pass
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute('PRAGMA synchronous=FULL')
            conn.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE metadata (version INTEGER NOT NULL CHECK(version=1));
                INSERT INTO metadata VALUES(1);
                CREATE TABLE runtime (
                    account TEXT PRIMARY KEY, engine TEXT, owner TEXT, epoch INTEGER NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT 'NEW', restart_count INTEGER NOT NULL DEFAULT 0,
                    restart_at REAL, health TEXT, last_clock TEXT, healthy INTEGER NOT NULL DEFAULT 0,
                    reconciled_at REAL, reconstructed TEXT, activation_principal TEXT, activation_revision INTEGER);
                CREATE TABLE incident (account TEXT NOT NULL, epoch INTEGER NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(account,epoch,reason));
                CREATE TABLE intent (request_id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    order_digest TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL);
                CREATE TRIGGER intent_no_update BEFORE UPDATE ON intent BEGIN
                    SELECT RAISE(ABORT, 'INTENT_IMMUTABLE'); END;
                CREATE TRIGGER intent_no_delete BEFORE DELETE ON intent BEGIN
                    SELECT RAISE(ABORT, 'INTENT_IMMUTABLE'); END;
                COMMIT;
            ''')
        finally:
            conn.close()
        return cls(path)

    def __init__(self, path):
        path = Path(path)
        require(not path.is_symlink() and path.is_file(), 'RUNTIME_STATE_MISSING_OR_UNSAFE')
        self.conn = sqlite3.connect(path.resolve().as_uri()+'?mode=rw', uri=True,
                                   isolation_level=None, timeout=0.25)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA synchronous=FULL')
        self._scope = None
        try:
            require([tuple(x) for x in self.conn.execute('SELECT version FROM metadata')] == [(1,)],
                    'RUNTIME_VERSION_INVALID')
            require(self.conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'RUNTIME_STATE_CORRUPT')
            for row in self.conn.execute('SELECT * FROM runtime'):
                self._valid(dict(row))
            self.conn.execute('SELECT request_id,data,digest FROM intent LIMIT 1')
            self.conn.execute('SELECT account,epoch,reason FROM incident LIMIT 1')
        except BaseException:
            self.conn.close()
            raise

    @staticmethod
    def _account(account):
        require(type(account) is str and re.fullmatch('[0-9a-f]{64}', account), 'BAD_ACCOUNT')

    @staticmethod
    def _valid(row):
        RuntimeState._account(row['account'])
        require(type(row['epoch']) is int and row['epoch'] >= 0
                and type(row['restart_count']) is int and 0 <= row['restart_count'] <= 3
                and row['healthy'] in (0, 1)
                and row['phase'] in {'NEW','RECONCILING','AWAITING_OWNER','ACTIVE_SIMULATION',
                    'HALTED','RESTART_WAIT','STOPPED','FAILED'}, 'RUNTIME_STATE_CORRUPT')
        require((row['owner'] is None) == (row['engine'] is None), 'RUNTIME_STATE_CORRUPT')
        require((row['owner'] is None) == (row['phase'] in ('NEW','RESTART_WAIT','STOPPED','FAILED')),
                'RUNTIME_STATE_CORRUPT')
        if row['owner'] is not None:
            require(type(row['owner']) is str and re.fullmatch('[0-9a-f]{32}', row['owner'])
                    and row['engine'] in ('V2_SIMULATION','LEGACY_SIMULATION'), 'RUNTIME_STATE_CORRUPT')
        if row['phase'] == 'ACTIVE_SIMULATION':
            require(type(row['activation_revision']) is int and row['activation_revision'] > 0
                    and type(row['activation_principal']) is str and 1 <= len(row['activation_principal']) <= 128,
                    'RUNTIME_STATE_CORRUPT')
        if row['restart_at'] is not None:
            require(number(row['restart_at']), 'RUNTIME_STATE_CORRUPT')
        return row

    @contextmanager
    def transaction(self):
        require(self._scope is None and not self.conn.in_transaction, 'RUNTIME_REENTRANCY_DENIED')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute('ROLLBACK')
            raise

    def register_simulation_account(self, account):
        self._account(account)
        with self.transaction():
            self.conn.execute('INSERT INTO runtime(account) VALUES(?)', (account,))

    def status(self, account):
        self._account(account)
        row = self.conn.execute('SELECT * FROM runtime WHERE account=?', (account,)).fetchone()
        require(row is not None, 'RUNTIME_ACCOUNT_MISSING')
        return self._valid(dict(row))

    def _lease(self, lease):
        require(type(lease) is RuntimeLease, 'RUNTIME_LEASE_REQUIRED')
        row = self.status(lease.account)
        require((row['engine'], row['owner'], row['epoch']) == (lease.engine, lease.owner, lease.epoch),
                'RUNTIME_OWNERSHIP_LOST')
        return row

    def start(self, account, *, engine='V2_SIMULATION', now, owner_request=None):
        require(engine in ('V2_SIMULATION','LEGACY_SIMULATION') and number(now), 'SIMULATION_RUNTIME_REQUIRED')
        with self.transaction():
            row = self.status(account)
            require(row['owner'] is None, 'RUNTIME_ACCOUNT_ALREADY_OWNED')
            require(row['phase'] in ('NEW','RESTART_WAIT','STOPPED'), 'RUNTIME_START_REQUIRES_OPERATOR')
            if row['phase'] == 'STOPPED':
                self._owner(owner_request, account)
            if row['phase'] == 'RESTART_WAIT':
                require(number(row['restart_at']) and now >= row['restart_at'], 'RUNTIME_RESTART_BACKOFF')
            owner = uuid.uuid4().hex
            epoch = row['epoch'] + 1
            self.conn.execute('''UPDATE runtime SET owner=?,engine=?,epoch=?,phase='RECONCILING',
                health=NULL,last_clock=NULL,healthy=0,reconciled_at=NULL,reconstructed=NULL,
                activation_principal=NULL,activation_revision=NULL,restart_at=NULL WHERE account=?''', (owner, engine, epoch, account))
            self._incident(account, epoch, 'STARTUP_RECONCILIATION_REQUIRED')
            return RuntimeLease(account, engine, owner, epoch)

    @staticmethod
    def _owner(owner, account):
        require(type(owner) is AuthenticatedOwner and owner.account == account
                and type(owner.principal) is str and 1 <= len(owner.principal) <= 128,
                'AUTHENTICATED_SIMULATION_OWNER_REQUIRED')

    def _incident(self, account, epoch, reason):
        require(type(reason) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,95}', reason), 'INVALID_REASON_CODE')
        self.conn.execute('INSERT OR IGNORE INTO incident VALUES(?,?,?)', (account, epoch, reason))

    def _trip(self, lease, reason):
        self._lease(lease)
        self._incident(lease.account, lease.epoch, reason)
        self.conn.execute('''UPDATE runtime SET phase='HALTED',healthy=0,reconciled_at=NULL,
            reconstructed=NULL,activation_principal=NULL,activation_revision=NULL WHERE account=?''', (lease.account,))

    def trip(self, lease, reason):
        if self._scope == lease:
            self._trip(lease, reason)
        else:
            with self.transaction():
                self._trip(lease, reason)

    @contextmanager
    def scope(self, lease):
        # Hold ownership through the entire simulated effect, not merely validation.
        require(self._scope is None and not self.conn.in_transaction, 'RUNTIME_REENTRANCY_DENIED')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            self._lease(lease)
            self._scope = lease
            yield
        finally:
            self._scope = None
            # Safety trips survive an exception, rather than rolling back the halt.
            if self.conn.in_transaction:
                self.conn.execute('COMMIT')

    def observe(self, lease, sample, policy):
        with self.transaction():
            row = self._lease(lease)
            previous = ClockSample(**json.loads(row['last_clock'])) if row['last_clock'] else None
            result = evaluate(sample, previous, policy)
            for reason in result.reasons:
                self._incident(lease.account, lease.epoch, reason)
            self.conn.execute('UPDATE runtime SET health=?,last_clock=?,healthy=? WHERE account=?',
                (json.dumps(asdict(sample), allow_nan=False), json.dumps(asdict(sample.clock), allow_nan=False),
                 int(result.operationally_healthy), lease.account))
            if result.reasons:
                self.conn.execute('''UPDATE runtime SET phase='HALTED',reconciled_at=NULL,
                    reconstructed=NULL,activation_principal=NULL,activation_revision=NULL WHERE account=?''', (lease.account,))
            return result

    def reconstructed(self, lease, digest):
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest), 'RECONSTRUCTION_DIGEST_REQUIRED')
        with self.transaction():
            self._lease(lease)
            self.conn.execute('UPDATE runtime SET reconstructed=? WHERE account=?', (digest, lease.account))

    def reconciled(self, lease, now):
        require(number(now), 'INVALID_RUNTIME_CLOCK')
        with self.transaction():
            row = self._lease(lease)
            require(row['healthy'] == 1 and row['reconstructed'] is not None, 'RUNTIME_NOT_RECONSTRUCTED')
            self.conn.execute("UPDATE runtime SET reconciled_at=?,phase='AWAITING_OWNER' WHERE account=?",
                              (now, lease.account))

    def activate_simulation(self, lease, owner, control_revision):
        self._owner(owner, lease.account)
        require(type(control_revision) is int and control_revision > 0, 'INVALID_CONTROL_REVISION')
        # Guard validates fresh health, core revision and unresolved orders before this.
        require(self._scope == lease, 'RUNTIME_ADMISSION_SCOPE_REQUIRED')
        row = self._lease(lease)
        require(row['phase'] == 'AWAITING_OWNER' and row['healthy'] == 1
                and row['reconciled_at'] is not None and row['reconstructed'] is not None,
                'RUNTIME_RECONCILIATION_REQUIRED')
        self.conn.execute("UPDATE runtime SET phase='ACTIVE_SIMULATION',activation_principal=?,activation_revision=? WHERE account=?",
                          (owner.principal, control_revision, lease.account))

    def record_exit(self, lease, *, exit_code, now, explicit_stop=False):
        require(number(now), 'INVALID_RUNTIME_CLOCK')
        with self.transaction():
            row = self._lease(lease)
            require(exit_code is not None, 'CONFIRMED_PROCESS_EXIT_REQUIRED')
            plan = plan_restart(exit_code=exit_code, restart_count=row['restart_count'], explicit_stop=explicit_stop)
            phase = 'RESTART_WAIT' if plan.delay_seconds is not None else (
                'STOPPED' if plan.action in ('STOPPED_BY_OWNER','STOPPED_CLEANLY') else 'FAILED')
            self._incident(lease.account, lease.epoch, 'PROCESS_EXIT_REQUIRES_RECONCILIATION')
            self.conn.execute('''UPDATE runtime SET owner=NULL,engine=NULL,phase=?,restart_count=?,restart_at=?,
                health=NULL,last_clock=NULL,healthy=0,reconciled_at=NULL,reconstructed=NULL,
                activation_principal=NULL,activation_revision=NULL WHERE account=?''',
                (phase, plan.restart_count, now+plan.delay_seconds if plan.delay_seconds else None, lease.account))
            return plan

    def save_intent(self, request_id, admission):
        require(type(request_id) is str and 1 <= len(request_id) <= 128, 'POLICY_REQUEST_ID_INVALID')
        data, digest = encode_intent(admission)
        order = admission.proposal.order
        with self.transaction():
            row = self.conn.execute('SELECT * FROM intent WHERE request_id=?', (request_id,)).fetchone()
            if row is not None:
                require((row['account'],row['order_digest'],row['data'],row['digest']) ==
                        (order.account,order.digest,data,digest), 'POLICY_INTENT_IMMUTABLE')
            else:
                self.conn.execute('INSERT INTO intent VALUES(?,?,?,?,?)', (request_id,order.account,order.digest,data,digest))
        return digest

    def load_intent(self, request_id, *, account, order_digest, current_context):
        row = self.conn.execute('SELECT * FROM intent WHERE request_id=?', (request_id,)).fetchone()
        require(row is not None and row['account'] == account and row['order_digest'] == order_digest,
                'DURABLE_POLICY_INTENT_REQUIRED')
        return decode_intent(row['data'], row['digest'], current_context=current_context)

    def close(self):
        require(self._scope is None, 'RUNTIME_ADMISSION_SCOPE_ACTIVE')
        self.conn.close()
