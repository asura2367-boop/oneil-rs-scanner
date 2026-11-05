"""Durable local SIMULATION-ONLY reconciliation dependency, never an order store.

This is a real isolated SQLite adapter for the strict Worker read/recovery path.
It has no enqueue, claim, admission or resume implementation. It never opens a
socket, reads credentials, impersonates PostgreSQL or touches production tables.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading

from oneil_execution_v2.domain import BrokerOrder, Order, RiskPolicy, UNRESOLVED, require

POLICY = RiskPolicy('offline-reconciliation-only', 1, 1, 1, 1, 1)


class RehearsalStore:
    @classmethod
    def initialize(cls, path, account):
        from .supervisor import ACCOUNT, safe_path
        require(account == ACCOUNT, 'FIXED_OFFLINE_ACCOUNT_REQUIRED')
        path = Path(path)
        safe_path(path.parent, directory=True)
        with path.open('xb'):
            pass
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute('PRAGMA synchronous=FULL')
            conn.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE metadata(version INTEGER NOT NULL CHECK(version=1),
                    mode TEXT NOT NULL CHECK(mode='ISOLATED_RECONCILIATION_ONLY'));
                INSERT INTO metadata VALUES(1,'ISOLATED_RECONCILIATION_ONLY');
                CREATE TABLE control(account TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode='SIMULATION'),
                    halted INTEGER NOT NULL CHECK(halted=1), revision INTEGER NOT NULL CHECK(revision>0));
                CREATE TABLE requests(request_id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL CHECK(attempts IN (0,1)),
                    trading_day TEXT, broker_order_id TEXT, filled_quantity INTEGER NOT NULL CHECK(filled_quantity>=0),
                    reason TEXT NOT NULL);
                CREATE UNIQUE INDEX request_native_identity ON requests(account,trading_day,broker_order_id)
                    WHERE broker_order_id IS NOT NULL;
                CREATE TABLE synthetic_history(request_id TEXT PRIMARY KEY, observation TEXT NOT NULL);
                CREATE TABLE history_state(id INTEGER PRIMARY KEY CHECK(id=1), complete INTEGER NOT NULL CHECK(complete IN (0,1)));
                INSERT INTO history_state VALUES(1,1);
                CREATE TABLE health(id INTEGER PRIMARY KEY CHECK(id=1), worker_id TEXT NOT NULL,
                    heartbeat_at REAL NOT NULL, reconciled_at REAL, status TEXT NOT NULL);
                CREATE TABLE incident(reason TEXT PRIMARY KEY);
                COMMIT;
            ''')
            conn.execute("INSERT INTO control VALUES(?,'SIMULATION',1,1)", (account,))
        finally:
            conn.close()
        return cls(path)

    def __init__(self, path, *, now=None):
        from .supervisor import ACCOUNT, safe_path
        self.account = ACCOUNT
        self.path = safe_path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._owner = threading.get_ident()
        self.runtime_guard = None
        self._account_scope = False
        self.conn = sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw', uri=True,
                                   isolation_level=None, timeout=0.5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA synchronous=FULL')
        try:
            require([tuple(x) for x in self.conn.execute('SELECT * FROM metadata')] ==
                    [(1, 'ISOLATED_RECONCILIATION_ONLY')], 'ISOLATED_REHEARSAL_STORE_REQUIRED')
            require(self.conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'REHEARSAL_STORE_CORRUPT')
            self.control(ACCOUNT)
            for row in self.conn.execute('SELECT * FROM requests'):
                self._decode(row)
            self.history_snapshot()
        except BaseException:
            self.conn.close()
            raise

    def assert_owner(self):
        require(threading.get_ident() == self._owner, 'DEDICATED_CONNECTION_REQUIRED')

    def now(self):
        from oneil_execution_v2.domain import aware
        self.assert_owner()
        return aware(self._now())

    def _account(self, account):
        self.assert_owner()
        require(account == self.account, 'FIXED_OFFLINE_ACCOUNT_REQUIRED')

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

    def control(self, account):
        self._account(account)
        rows = list(self.conn.execute('SELECT * FROM control'))
        require(len(rows) == 1 and rows[0]['account'] == account and rows[0]['mode'] == 'SIMULATION'
                and rows[0]['halted'] == 1 and type(rows[0]['revision']) is int and rows[0]['revision'] > 0,
                'REHEARSAL_CONTROL_MUST_REMAIN_HALTED')
        return {**dict(rows[0]), 'halted': True, 'policy': asdict(POLICY)}

    def bind_runtime_guard(self, guard):
        from .orchestrator import SimulationRuntimeGuard
        self.assert_owner()
        require(type(guard) is SimulationRuntimeGuard and self.runtime_guard is None
                and guard.lease.account == self.account, 'STRICT_RUNTIME_GUARD_REQUIRED')
        self.runtime_guard = guard

    @contextmanager
    def account_lock(self, account):
        self._account(account)
        require(not self._account_scope, 'REHEARSAL_ACCOUNT_LOCK_REENTRANT')
        # A SQLite write transaction serializes the actual local read/recovery
        # updates. It is not a PostgreSQL advisory lock or production fence.
        with self.transaction():
            self._account_scope = True
            try:
                yield True
            finally:
                self._account_scope = False

    def _decode(self, row):
        data = dict(row)
        self._account(data['account'])
        data['payload'] = json.loads(data['payload'])
        order = Order.from_payload(data['payload'])
        require(order.account == self.account and type(data['request_id']) is str
                and 1 <= len(data['request_id']) <= 128 and data['state'] in
                {'QUEUED','VALIDATING','SUBMISSION_UNKNOWN','ACKNOWLEDGED','PART_FILLED',
                 'FILLED','CANCELLED','REJECTED','EXPIRED','BLOCKED'}
                and type(data['attempts']) is int and data['attempts'] in (0,1)
                and type(data['filled_quantity']) is int and 0 <= data['filled_quantity'] <= order.quantity,
                'REHEARSAL_REQUEST_INVALID')
        if data['state'] in UNRESOLVED or data['state'] in {'FILLED','REJECTED'}:
            require(data['attempts'] == 1, 'REHEARSAL_REQUEST_INVALID')
        if data['state'] in {'QUEUED','VALIDATING','EXPIRED','BLOCKED'}:
            require(data['attempts'] == 0 and data['filled_quantity'] == 0, 'REHEARSAL_REQUEST_INVALID')
        if data['state'] in {'SUBMISSION_UNKNOWN','ACKNOWLEDGED','REJECTED'}:
            require(data['filled_quantity'] == 0, 'REHEARSAL_REQUEST_INVALID')
        if data['state'] == 'PART_FILLED':
            require(0 < data['filled_quantity'] < order.quantity, 'REHEARSAL_REQUEST_INVALID')
        if data['state'] == 'FILLED':
            require(data['filled_quantity'] == order.quantity, 'REHEARSAL_REQUEST_INVALID')
        if data['attempts']:
            require(type(data['trading_day']) is str and len(data['trading_day']) == 10
                    and date.fromisoformat(data['trading_day']).isoformat() == data['trading_day'],
                    'REHEARSAL_REQUEST_INVALID')
        return data

    def get(self, account, request_id):
        self._account(account)
        row = self.conn.execute('SELECT * FROM requests WHERE account=? AND request_id=?',
                                (account, str(request_id))).fetchone()
        require(row is not None, 'REQUEST_NOT_FOUND')
        return self._decode(row)

    def unresolved(self, account):
        self._account(account)
        rows = [self._decode(row) for row in self.conn.execute('SELECT * FROM requests ORDER BY request_id')]
        return [row for row in rows if row['state'] in UNRESOLVED]

    def recovery_requests(self, account):
        self._account(account)
        rows = [self._decode(row) for row in self.conn.execute('SELECT * FROM requests ORDER BY request_id')]
        return [row for row in rows if row['attempts'] == 0 and row['state'] in ('QUEUED','VALIDATING')]

    def fail_closed(self, account, reason):
        self._account(account)
        require(type(reason) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,95}', reason), 'INVALID_REASON_CODE')
        with self.transaction():
            self.control(account)  # CHECK(halted=1) is permanent for this adapter.
            self.conn.execute('INSERT OR IGNORE INTO incident VALUES(?)', (reason,))

    def heartbeat(self, account, worker_id, status, reconciled=False):
        self._account(account)
        stamp = self.now().timestamp()
        self.conn.execute('''INSERT INTO health VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            worker_id=excluded.worker_id, heartbeat_at=excluded.heartbeat_at,
            reconciled_at=COALESCE(excluded.reconciled_at,health.reconciled_at),status=excluded.status''',
            (worker_id, stamp, stamp if reconciled else None, status))

    def health(self):
        self.assert_owner()
        row = self.conn.execute('SELECT * FROM health WHERE id=1').fetchone()
        return dict(row) if row else None

    def history_snapshot(self):
        self.assert_owner()
        states = list(self.conn.execute('SELECT * FROM history_state'))
        require(len(states) == 1 and states[0]['id'] == 1 and states[0]['complete'] in (0,1),
                'SYNTHETIC_HISTORY_STATE_INVALID')
        orders = {}
        identities = set()
        for row in self.conn.execute('SELECT * FROM synthetic_history ORDER BY request_id'):
            observed = BrokerOrder(**json.loads(row['observation']))
            require(observed.request_id == row['request_id'] and observed.account == self.account,
                    'SYNTHETIC_HISTORY_IDENTITY_MISMATCH')
            identity = (observed.account, observed.trading_day, observed.broker_order_id)
            require(identity not in identities, 'SYNTHETIC_HISTORY_NATIVE_ID_AMBIGUOUS')
            identities.add(identity)
            orders[row['request_id']] = observed
        return bool(states[0]['complete']), orders

    def apply_observation(self, row, observed):
        self.assert_owner()
        require(self._account_scope, 'REHEARSAL_ACCOUNT_LOCK_REQUIRED')
        current = self.get(row['account'], row['request_id'])
        order = Order.from_payload(current['payload'])
        observed.validate(current['request_id'], order, current['trading_day'])
        require(current['attempts'] == 1 and row['attempts'] == 1, 'ATTEMPT_MISMATCH')
        require(current['state'] in UNRESOLVED or current['state'] == observed.status,
                'INVALID_BROKER_TRANSITION')
        require(current['broker_order_id'] in (None, observed.broker_order_id), 'BROKER_ID_CHANGED')
        require(observed.filled_quantity >= current['filled_quantity'], 'FILL_REGRESSION')
        require(not (current['state'] == 'PART_FILLED' and observed.status in {'ACKNOWLEDGED','REJECTED'}),
                'STATE_REGRESSION')
        self.conn.execute('''UPDATE requests SET state=?,broker_order_id=?,filled_quantity=?,
            reason='SYNTHETIC_HISTORY_CONFIRMED' WHERE request_id=?''',
            (observed.status, observed.broker_order_id, observed.filled_quantity, current['request_id']))

    def enqueue(self, *_args, **_kwargs):
        require(False, 'REHEARSAL_NEW_ORDERS_DISABLED')

    claim = enqueue
    reserve_attempt = enqueue
    set_halt = enqueue

    def close(self):
        self.assert_owner()
        self.conn.close()
