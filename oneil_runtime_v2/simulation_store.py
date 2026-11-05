"""Durable, fixed-account SQLite SIMULATION queue. No live broker capability.

The OS account lock is deliberately separate from short SQLite transactions:
UNKNOWN is fully committed before the worker invokes its exact SimulatedBroker.
This is local simulation storage, not a PostgreSQL/production substitute.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import uuid

from oneil_execution_v2.domain import (Approval, BrokerOrder, Order, RiskPolicy,
    TERMINAL, UNRESOLVED, aware, require)


class SQLiteSimulationStore:
    live_capable = False

    @classmethod
    def initialize(cls, path, account, policy, *, now=None):
        from .supervisor import ACCOUNT, safe_path
        require(account == ACCOUNT, 'FIXED_OFFLINE_ACCOUNT_REQUIRED')
        require(type(policy) is RiskPolicy and policy.version.startswith('policy-v2-'),
                'EXPLICIT_CORE_RISK_REQUIRED')
        policy.validate()
        path = Path(path)
        safe_path(path.parent, directory=True)
        # Never overwrite an existing journal or its stable lock inode.
        lock_path = Path(str(path) + '.lock')
        with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as lock:
            lock.write(b'0')
        with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb'):
            pass
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute('PRAGMA synchronous=FULL')
            conn.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE metadata(version INTEGER NOT NULL, mode TEXT NOT NULL);
                INSERT INTO metadata VALUES(1,'ISOLATED_SQLITE_SIMULATION');
                CREATE TABLE control(account TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode='SIMULATION'),
                    halted INTEGER NOT NULL CHECK(halted IN (0,1)), revision INTEGER NOT NULL CHECK(revision>0),
                    policy TEXT NOT NULL);
                CREATE TABLE requests(request_id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, approval_id TEXT NOT NULL UNIQUE,
                    data TEXT NOT NULL, UNIQUE(account,idempotency_key));
                CREATE TABLE native_ids(identity TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE);
                CREATE TABLE health(account TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE events(id INTEGER PRIMARY KEY, account TEXT NOT NULL, request_id TEXT,
                    event TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL);
                CREATE TRIGGER immutable_event_update BEFORE UPDATE ON events BEGIN
                    SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
                CREATE TRIGGER immutable_event_delete BEFORE DELETE ON events BEGIN
                    SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
                COMMIT;''')
            conn.execute("INSERT INTO control VALUES(?,'SIMULATION',1,1,?)", (account,json.dumps(asdict(policy))))
        finally:
            conn.close()
        return cls(path, now=now)

    def __init__(self, path, *, now=None):
        from .supervisor import ACCOUNT, safe_path
        self.account = ACCOUNT
        self.path = safe_path(path)
        self.lock_path = safe_path(str(self.path) + '.lock')
        for candidate in (self.path, self.lock_path):
            info = candidate.stat()
            require(info.st_nlink == 1 and (os.name == 'nt' or
                    (info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077)),
                    'PRIVATE_SIMULATION_STATE_REQUIRED')
        self._owner = threading.get_ident()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.runtime_guard = None
        self._account_scope = False
        self._execution_guard = threading.Lock()
        self.conn = sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw', uri=True,
                                   isolation_level=None, timeout=0.5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA synchronous=FULL')
        try:
            require([tuple(r) for r in self.conn.execute('SELECT * FROM metadata')] ==
                    [(1,'ISOLATED_SQLITE_SIMULATION')], 'ISOLATED_SIMULATION_STORE_REQUIRED')
            require(self.conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'SIMULATION_STORE_CORRUPT')
            self.control(ACCOUNT)
            self._rows(ACCOUNT)
        except BaseException:
            self.conn.close()
            raise

    def assert_owner(self):
        require(threading.get_ident() == self._owner, 'DEDICATED_CONNECTION_REQUIRED')

    def _account(self, account):
        self.assert_owner()
        require(account == self.account, 'FIXED_OFFLINE_ACCOUNT_REQUIRED')

    def now(self):
        self.assert_owner()
        return aware(self._now())

    @contextmanager
    def transaction(self):
        self.assert_owner()
        own = not self.conn.in_transaction
        if own:
            self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            if own:
                self.conn.execute('COMMIT')
        except BaseException:
            if own and self.conn.in_transaction:
                self.conn.execute('ROLLBACK')
            raise

    @contextmanager
    def revision_fence(self, account, expected):
        self._account(account)
        require(type(expected) is int and expected > 0, 'INVALID_CONTROL')
        with self.transaction():
            require(self.control(account)['revision'] == expected, 'CONTROL_REVISION_CONFLICT')
            yield

    @contextmanager
    def account_lock(self, account):
        from .supervisor import InstanceLock
        self._account(account)
        require(not self.conn.in_transaction, 'ACCOUNT_LOCK_REQUIRES_COMMITTED_STATE')
        if not self._execution_guard.acquire(blocking=False):
            yield False
            return
        lock = None
        try:
            try:
                lock = InstanceLock(self.lock_path)
                lock.__enter__()
            except RuntimeError:
                yield False
                return
            self._account_scope = True
            try:
                yield True
            finally:
                self._account_scope = False
                lock.__exit__(None, None, None)
        finally:
            self._execution_guard.release()

    def bind_runtime_guard(self, guard):
        from .orchestrator import SimulationRuntimeGuard
        self.assert_owner()
        require(type(guard) is SimulationRuntimeGuard and self.runtime_guard is None
                and guard.lease.account == self.account, 'STRICT_RUNTIME_GUARD_REQUIRED')
        self.runtime_guard = guard

    def control(self, account):
        self._account(account)
        rows = list(self.conn.execute('SELECT * FROM control'))
        require(len(rows) == 1, 'SIMULATION_CONTROL_INVALID')
        row = dict(rows[0])
        require(row['account'] == account and row['mode'] == 'SIMULATION'
                and row['halted'] in (0,1) and type(row['revision']) is int and row['revision'] > 0,
                'SIMULATION_CONTROL_INVALID')
        row['policy'] = json.loads(row['policy'])
        policy = RiskPolicy(**row['policy']).validate()
        require(policy.version.startswith('policy-v2-'), 'EXPLICIT_CORE_RISK_REQUIRED')
        row['halted'] = bool(row['halted'])
        return row

    def _event(self, account, request_id, event, reason):
        require(type(reason) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,95}', reason), 'INVALID_REASON_CODE')
        self.conn.execute('INSERT INTO events(account,request_id,event,reason,at) VALUES(?,?,?,?,?)',
                          (account,request_id,event,reason,self.now().isoformat()))

    def set_halt(self, account, *, halted, expected_revision):
        self._account(account)
        require(type(halted) is bool and type(expected_revision) is int, 'INVALID_CONTROL')
        with self.revision_fence(account, expected_revision):
            if not halted:
                require(not self.unresolved(account), 'UNRESOLVED_ORDER')
            self.conn.execute('UPDATE control SET halted=?,revision=revision+1 WHERE account=?', (int(halted),account))
            self._event(account,None,'HALT_CHANGED','HALTED' if halted else 'SIMULATION_RESUMED')
            return expected_revision + 1

    _DATES = ('approval_expires_at','expires_at','created_at','updated_at','lease_until')

    def _decode(self, raw):
        row = json.loads(raw['data'])
        self._account(row['account'])
        require(all(row[key] == raw[key] for key in ('account','request_id','idempotency_key','approval_id')),
                'SIMULATION_REQUEST_INVALID')
        for key in self._DATES:
            if row[key] is not None:
                row[key] = aware(datetime.fromisoformat(row[key]))
        order = Order.from_payload(row['payload'])
        require(order.account == self.account and order.digest == row['digest']
                and row['state'] in TERMINAL | UNRESOLVED | {'QUEUED','VALIDATING'}
                and type(row['attempts']) is int and row['attempts'] in (0,1)
                and type(row['filled_quantity']) is int and 0 <= row['filled_quantity'] <= order.quantity,
                'SIMULATION_REQUEST_INVALID')
        if row['attempts']:
            require(row['attempt_id'] is not None and row['state'] in UNRESOLVED | {'FILLED','CANCELLED','REJECTED'}
                    and date.fromisoformat(row['trading_day']).isoformat() == row['trading_day'], 'SIMULATION_REQUEST_INVALID')
        else:
            require(row['state'] in {'QUEUED','VALIDATING','BLOCKED','EXPIRED','CANCELLED'}
                    and row['attempt_id'] is None and row['filled_quantity'] == 0, 'SIMULATION_REQUEST_INVALID')
        if row['state'] == 'FILLED':
            require(row['filled_quantity'] == order.quantity, 'BAD_FILL')
        if row['state'] == 'PART_FILLED':
            require(0 < row['filled_quantity'] < order.quantity, 'BAD_FILL')
        if row['state'] in {'SUBMISSION_UNKNOWN','ACKNOWLEDGED','REJECTED'}:
            require(row['filled_quantity'] == 0, 'BAD_FILL')
        return row

    def _write(self, row, *, insert=False):
        data = {**row}
        for key in self._DATES:
            if data[key] is not None:
                data[key] = data[key].isoformat()
        encoded = json.dumps(data,sort_keys=True,allow_nan=False)
        if insert:
            self.conn.execute('INSERT INTO requests VALUES(?,?,?,?,?)',
                (row['request_id'],row['account'],row['idempotency_key'],row['approval_id'],encoded))
        else:
            self.conn.execute('UPDATE requests SET data=? WHERE request_id=?', (encoded,row['request_id']))
        return self.get(row['account'],row['request_id'])

    def get(self, account, request_id):
        self._account(account)
        raw = self.conn.execute('SELECT * FROM requests WHERE account=? AND request_id=?',(account,str(request_id))).fetchone()
        require(raw is not None, 'REQUEST_NOT_FOUND')
        return self._decode(raw)

    def _rows(self, account):
        self._account(account)
        return [self._decode(r) for r in self.conn.execute('SELECT * FROM requests ORDER BY request_id')]

    def enqueue(self, order, approval, idempotency_key):
        require(type(order) is Order and type(approval) is Approval, 'EXACT_APPROVAL_REQUIRED')
        order.validate(); self._account(order.account)
        require(type(idempotency_key) is str and re.fullmatch('[A-Za-z0-9_-]{16,128}', idempotency_key), 'BAD_IDEMPOTENCY_KEY')
        with self.transaction():
            control = self.control(order.account)
            raw = self.conn.execute('SELECT * FROM requests WHERE account=? AND idempotency_key=?',
                                    (order.account,idempotency_key)).fetchone()
            if raw:
                row = self._decode(raw)
                require(row['digest'] == order.digest and row['principal'] == approval.principal, 'IDEMPOTENCY_CONFLICT')
                return row
            now = self.now(); approval.validate(order,now)
            require(now < order.expires_at, 'REQUEST_EXPIRED')
            require(order.policy_version == control['policy']['version'], 'POLICY_CHANGED')
            require(not control['halted'], 'HALTED')
            require(not self.conn.execute('SELECT 1 FROM requests WHERE approval_id=?',(approval.approval_id,)).fetchone(), 'APPROVAL_ALREADY_USED')
            row = dict(request_id=str(uuid.uuid4()),account=order.account,idempotency_key=idempotency_key,
                approval_id=approval.approval_id,principal=approval.principal,payload=order.payload(),digest=order.digest,
                approval_expires_at=approval.expires_at,expires_at=order.expires_at,control_revision=control['revision'],
                state='QUEUED',reason='OWNER_CONFIRMED_SIMULATION',priority=100 if order.side=='SELL' else 0,
                attempts=0,attempt_id=None,claim_token=None,lease_until=None,trading_day=None,
                admission_evidence=None,broker_order_id=None,filled_quantity=0,created_at=now,updated_at=now)
            result = self._write(row,insert=True)
            self._event(order.account,row['request_id'],'REQUEST_ACCEPTED','OWNER_CONFIRMED_SIMULATION')
            return result

    def claim(self, account, lease_seconds=30):
        self._account(account)
        require(type(lease_seconds) is int and 1 <= lease_seconds <= 300, 'INVALID_LEASE')
        with self.transaction():
            now = self.now()
            rows = [r for r in self._rows(account) if r['state']=='QUEUED' or
                    (r['state']=='VALIDATING' and r['lease_until'] < now)]
            if not rows: return None
            row = min(rows,key=lambda r:(-r['priority'],r['created_at'],r['request_id']))
            row.update(state='VALIDATING',claim_token=uuid.uuid4().hex,lease_until=now+timedelta(seconds=lease_seconds),
                       updated_at=now,reason='CLAIMED')
            return self._write(row)

    def unresolved(self, account):
        return [r for r in self._rows(account) if r['state'] in UNRESOLVED]

    def recovery_requests(self, account):
        return [r for r in self._rows(account) if r['attempts']==0 and r['state'] in {'QUEUED','VALIDATING'}]

    def admission_ledger(self, account, day):
        return {'orders':tuple(Order.from_payload(r['payload']) for r in self._rows(account)
                               if r['attempts']==1 and r['trading_day']==day)}

    def daily_buy(self, account, day):
        return sum(o.notional for o in self.admission_ledger(account,day)['orders'] if o.side=='BUY')

    def finish_before_send(self, row, state, reason):
        require(state in {'BLOCKED','EXPIRED','CANCELLED'}, 'BAD_PRE_SEND_STATE')
        with self.transaction():
            current = self.get(row['account'],row['request_id'])
            if current['state']!='VALIDATING' or current['claim_token']!=row['claim_token'] or current['attempts']:
                return False
            current.update(state=state,reason=reason,lease_until=None,updated_at=self.now())
            self._write(current); self._event(row['account'],row['request_id'],'PRE_SEND_TERMINAL',reason)
            return True

    def cancel_pending(self, account, request_id):
        with self.transaction():
            row = self.get(account,request_id)
            if row['state']=='CANCELLED' and not row['attempts']: return row
            require(row['state'] in {'QUEUED','VALIDATING'} and not row['attempts'], 'ALREADY_ADMITTED_BROKER_CANCEL_REQUIRED')
            row.update(state='CANCELLED',reason='OWNER_CANCELLED_BEFORE_SEND',lease_until=None,updated_at=self.now())
            return self._write(row)

    def reserve_attempt(self, row, trading_day, policy, validate):
        from oneil_policy_v2.admission import PolicyAdmissionGate
        from .orchestrator import SimulationRuntimeGuard
        self.assert_owner()
        require(self._account_scope and not self.conn.in_transaction, 'COMMITTED_ACCOUNT_SCOPE_REQUIRED')
        require(type(validate) is PolicyAdmissionGate and type(self.runtime_guard) is SimulationRuntimeGuard
                and validate.runtime_guard is self.runtime_guard and validate.store is self,
                'RUNTIME_ADMISSION_GUARD_REQUIRED')
        require(type(trading_day) is str and date.fromisoformat(trading_day).isoformat()==trading_day, 'INVALID_TRADING_DAY')
        with self.transaction():
            current = self.get(row['account'],row['request_id']); control = self.control(row['account']); now = self.now()
            require(current['state']=='VALIDATING' and current['claim_token']==row['claim_token']
                    and current['lease_until'] > now and current['attempts']==0, 'CLAIM_LOST')
            require(not control['halted'], 'HALTED')
            require(control['revision']==current['control_revision'], 'CONTROL_CHANGED')
            require(type(policy) is RiskPolicy and control['policy']==asdict(policy), 'POLICY_CHANGED')
            require(now < current['approval_expires_at'] and now < current['expires_at'], 'APPROVAL_EXPIRED')
            require(not self.unresolved(row['account']), 'UNRESOLVED_ORDER')
            require(str(validate.row['request_id'])==current['request_id'] and validate.order.digest==current['digest']
                    and validate.permit.trading_day==trading_day, 'POLICY_ADMISSION_REQUIRED')
            evidence = validate(self.now(),self.daily_buy(row['account'],trading_day))
            require(type(evidence) is dict and evidence.get('order_digest')==current['digest']
                    and evidence.get('request_id')==current['request_id'] and evidence.get('control_revision')==control['revision']
                    and evidence.get('mode')=='SIMULATION' and evidence.get('live_capable') is False, 'POLICY_ADMISSION_REQUIRED')
            require(self.now() < current['approval_expires_at'] and self.now() < current['lease_until']
                    and self.now() < current['expires_at'], 'APPROVAL_OR_LEASE_EXPIRED')
            current.update(state='SUBMISSION_UNKNOWN',reason='ATTEMPT_DURABLY_ADMITTED',attempt_id=uuid.uuid4().hex,
                attempts=1,trading_day=trading_day,admission_evidence=evidence,lease_until=None,updated_at=self.now())
            result = self._write(current)
            self._event(row['account'],row['request_id'],'ATTEMPT_ADMITTED','NO_AUTOMATIC_RETRANSMISSION')
        return result

    def apply_observation(self, row, observed):
        require(type(observed) is BrokerOrder, 'EXACT_BROKER_OBSERVATION_REQUIRED')
        require(self._account_scope, 'SIMULATION_ACCOUNT_LOCK_REQUIRED')
        with self.transaction():
            current = self.get(row['account'],row['request_id'])
            observed.validate(current['request_id'],Order.from_payload(current['payload']),current['trading_day'])
            require(current['attempt_id']==row['attempt_id'] and current['attempts']==1, 'ATTEMPT_MISMATCH')
            require(current['state'] in UNRESOLVED or current['state']==observed.status, 'INVALID_BROKER_TRANSITION')
            require(current['broker_order_id'] in (None,observed.broker_order_id), 'BROKER_ID_CHANGED')
            require(observed.filled_quantity >= current['filled_quantity'], 'FILL_REGRESSION')
            require(current['state'] not in TERMINAL or current['filled_quantity']==observed.filled_quantity, 'TERMINAL_FILL_CHANGED')
            require(not(current['state']=='PART_FILLED' and observed.status in {'ACKNOWLEDGED','REJECTED'}), 'STATE_REGRESSION')
            identity = json.dumps([self.account,current['trading_day'],observed.broker_order_id])
            existing = self.conn.execute('SELECT request_id FROM native_ids WHERE identity=?',(identity,)).fetchone()
            require(existing is None or existing[0]==current['request_id'], 'BROKER_ID_REUSED')
            self.conn.execute('INSERT OR IGNORE INTO native_ids VALUES(?,?)',(identity,current['request_id']))
            current.update(state=observed.status,broker_order_id=observed.broker_order_id,
                           filled_quantity=observed.filled_quantity,reason='SIMULATED_BROKER_CONFIRMED',updated_at=self.now())
            return self._write(current)

    def fill_requirements(self, account):
        return {r['request_id']:r['filled_quantity'] for r in self._rows(account) if r['filled_quantity'] > 0}

    def fail_closed(self, account, reason):
        self._account(account)
        with self.transaction():
            control = self.control(account)
            if not control['halted']:
                self.conn.execute('UPDATE control SET halted=1,revision=revision+1 WHERE account=?',(account,))
            self._event(account,None,'SAFETY_HALT',reason)

    def heartbeat(self, account, worker_id, status, reconciled=False):
        self._account(account)
        require(type(reconciled) is bool, 'INVALID_HEARTBEAT')
        prior = self.health()
        now = self.now()
        data = dict(account=account,worker_id=worker_id,status=status,heartbeat_at=now.isoformat(),
                    reconciled_at=now.isoformat() if reconciled else
                    (prior['reconciled_at'].isoformat() if prior and prior['reconciled_at'] else None))
        self.conn.execute('INSERT INTO health VALUES(?,?) ON CONFLICT(account) DO UPDATE SET data=excluded.data',
                          (account,json.dumps(data)))

    def health(self):
        self.assert_owner()
        row = self.conn.execute('SELECT data FROM health WHERE account=?',(self.account,)).fetchone()
        if not row: return None
        data = json.loads(row[0])
        for key in ('heartbeat_at','reconciled_at'):
            if data[key] is not None: data[key] = aware(datetime.fromisoformat(data[key]))
        return data

    def read_model(self, account):
        control = self.control(account); health = self.health(); now = self.now()
        keys = ('request_id','state','reason','attempts','filled_quantity','created_at','updated_at')
        requests = [{k:r[k] for k in keys} for r in sorted(self._rows(account), key=lambda r:r['created_at'], reverse=True)[:100]]
        return dict(mode='SIMULATION',live_capable=False,halted=control['halted'],control_revision=control['revision'],
                    worker_alive=bool(health and 0 <= (now-health['heartbeat_at']).total_seconds() <= 15),
                    broker_reconciled_at=health['reconciled_at'] if health else None,health=health,requests=requests,as_of=now)

    def close(self):
        self.assert_owner()
        require(not self._account_scope and not self.conn.in_transaction, 'SIMULATION_SCOPE_ACTIVE')
        self.conn.close()
