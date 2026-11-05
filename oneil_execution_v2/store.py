"""PostgreSQL queue and attempt ledger. Every order effect has a committed fence.

Only databases explicitly named oneil_v2_* are accepted by this prototype.
No production connection environment variables or v519 tables are accessed.
"""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import re
import uuid
import threading

from .domain import Blocked, Order, UNRESOLVED, require

S = 'oneil_execution_v2.'


class PostgresStore:
    def __init__(self, dsn):
        self._owner_thread = threading.get_ident()
        self.runtime_guard = None
        self._execution_guard = threading.Lock()
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                                   connect_timeout=5, options='-c statement_timeout=5000 -c lock_timeout=2000')
        name = self.conn.info.dbname
        if not name.startswith('oneil_v2_'):
            self.conn.close()
            raise Blocked('ISOLATED_SIMULATION_DATABASE_REQUIRED')

    def assert_owner(self):
        require(threading.get_ident() == self._owner_thread, 'DEDICATED_CONNECTION_REQUIRED')

    def bind_runtime_guard(self, guard):
        from oneil_runtime_v2.orchestrator import SimulationRuntimeGuard
        self.assert_owner()
        require(type(guard) is SimulationRuntimeGuard and self.runtime_guard is None,
                'STRICT_RUNTIME_GUARD_REQUIRED')
        self.runtime_guard = guard

    def close(self):
        self.assert_owner()
        self.conn.close()

    def initialize(self):
        self.assert_owner()
        with self.conn.transaction():
            self.conn.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))

    def now(self):
        self.assert_owner()
        return self.conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']

    def _event(self, account, request_id, event, reason, state=None):
        self.conn.execute(f'INSERT INTO {S}events(account,request_id,event,reason,state) VALUES (%s,%s,%s,%s,%s)',
                          (account, request_id, event, reason, state))

    def register_simulation_account(self, account, policy):
        self.assert_owner()
        from psycopg.types.json import Jsonb
        require(isinstance(account, str) and re.fullmatch('[0-9a-f]{64}', account), 'BAD_ACCOUNT')
        policy.validate()
        with self.conn.transaction():
            row = self.conn.execute(f'INSERT INTO {S}accounts(account,policy) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING account',
                                    (account, Jsonb(asdict(policy)))).fetchone()
            require(row is not None, 'ACCOUNT_ALREADY_REGISTERED')
            self._event(account, None, 'ACCOUNT_REGISTERED', 'SIMULATION_HALTED')

    def control(self, account):
        self.assert_owner()
        row = self.conn.execute(f'SELECT * FROM {S}accounts WHERE account=%s', (account,)).fetchone()
        require(row is not None and row['mode'] == 'SIMULATION', 'ACCOUNT_NOT_SIMULATION')
        return row

    def set_halt(self, account, *, halted, expected_revision):
        self.assert_owner()
        require(type(halted) is bool and type(expected_revision) is int, 'INVALID_CONTROL')
        with self.conn.transaction():
            row = self.conn.execute(f'''UPDATE {S}accounts SET halted=%s,revision=revision+1,
                updated_at=clock_timestamp() WHERE account=%s AND revision=%s RETURNING revision''',
                (halted, account, expected_revision)).fetchone()
            require(row is not None, 'CONTROL_REVISION_CONFLICT')
            self._event(account, None, 'HALT_CHANGED', 'HALTED' if halted else 'SIMULATION_RESUMED')
            return row['revision']

    def enqueue(self, order, approval, idempotency_key):
        self.assert_owner()
        from psycopg.types.json import Jsonb
        order.validate()
        require(isinstance(idempotency_key, str) and re.fullmatch('[A-Za-z0-9_-]{16,128}', idempotency_key), 'BAD_IDEMPOTENCY_KEY')
        with self.conn.transaction():
            control = self.conn.execute(f'SELECT * FROM {S}accounts WHERE account=%s FOR UPDATE', (order.account,)).fetchone()
            require(control is not None and control['mode'] == 'SIMULATION', 'ACCOUNT_NOT_SIMULATION')
            existing = self.conn.execute(f'SELECT * FROM {S}requests WHERE account=%s AND idempotency_key=%s', (order.account, idempotency_key)).fetchone()
            if existing:
                require(existing['digest'] == order.digest and existing['principal'] == approval.principal, 'IDEMPOTENCY_CONFLICT')
                return existing  # Return same receipt even if the original approval has since expired.
            now = self.now()
            approval.validate(order, now)
            require(now < order.expires_at, 'REQUEST_EXPIRED')
            require(order.policy_version == control['policy']['version'], 'POLICY_CHANGED')
            require(not control['halted'], 'HALTED')
            require(not self.conn.execute(f'SELECT 1 FROM {S}requests WHERE approval_id=%s', (approval.approval_id,)).fetchone(), 'APPROVAL_ALREADY_USED')
            request_id = uuid.uuid4()
            row = self.conn.execute(f'''INSERT INTO {S}requests(request_id,account,idempotency_key,payload,digest,
                approval_id,principal,approval_expires_at,expires_at,control_revision,state,reason,priority)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'QUEUED','OWNER_CONFIRMED_SIMULATION',%s) RETURNING *''',
                (request_id, order.account, idempotency_key, Jsonb(order.payload()), order.digest,
                 approval.approval_id, approval.principal, approval.expires_at, order.expires_at,
                 control['revision'], 100 if order.side == 'SELL' else 0)).fetchone()
            self._event(order.account, request_id, 'REQUEST_ACCEPTED', 'OWNER_CONFIRMED_SIMULATION', 'QUEUED')
            # Notification is a hint only. Polling the durable queue handles dropped hints.
            self.conn.execute("SELECT pg_notify('oneil_execution_v2_queue', %s)", (order.account,))
            return row

    def get(self, account, request_id):
        self.assert_owner()
        row = self.conn.execute(f'SELECT * FROM {S}requests WHERE account=%s AND request_id=%s', (account, request_id)).fetchone()
        require(row is not None, 'REQUEST_NOT_FOUND')
        return row

    def claim(self, account, lease_seconds=30):
        self.assert_owner()
        require(type(lease_seconds) is int and 1 <= lease_seconds <= 300, 'INVALID_LEASE')
        with self.conn.transaction():
            self.conn.execute(f'SELECT account FROM {S}accounts WHERE account=%s FOR UPDATE', (account,)).fetchone()
            row = self.conn.execute(f'''SELECT * FROM {S}requests WHERE account=%s AND
                (state='QUEUED' OR (state='VALIDATING' AND lease_until < clock_timestamp()))
                ORDER BY priority DESC,created_at,request_id FOR UPDATE SKIP LOCKED LIMIT 1''', (account,)).fetchone()
            if not row:
                return None
            token = uuid.uuid4()
            row = self.conn.execute(f'''UPDATE {S}requests SET state='VALIDATING',claim_token=%s,
                lease_until=clock_timestamp()+(%s * interval '1 second'), updated_at=clock_timestamp(),
                reason='CLAIMED' WHERE request_id=%s RETURNING *''', (token, lease_seconds, row['request_id'])).fetchone()
            self._event(account, row['request_id'], 'CLAIMED', 'LEASE_ACQUIRED', 'VALIDATING')
            return row

    @contextmanager
    def account_lock(self, account):
        # Session lock on dedicated connection, never transaction-pooling.
        self.assert_owner()
        guarded = self._execution_guard.acquire(blocking=False)
        if not guarded:
            yield False
            return
        key = int(account[:15], 16)
        acquired = False
        try:
            acquired = self.conn.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (key,)).fetchone()['acquired']
            yield acquired
        finally:
            try:
                if acquired and not self.conn.closed:
                    self.conn.execute('SELECT pg_advisory_unlock(%s)', (key,))
            finally:
                self._execution_guard.release()

    def unresolved(self, account):
        self.assert_owner()
        return self.conn.execute(f'SELECT * FROM {S}requests WHERE account=%s AND state=ANY(%s) ORDER BY created_at',
                                 (account, sorted(UNRESOLVED))).fetchall()

    def recovery_requests(self, account):
        """Read-only restart view; never replay admitted or terminal requests."""
        self.assert_owner()
        return self.conn.execute(f'''SELECT * FROM {S}requests WHERE account=%s
            AND attempts=0 AND state IN ('QUEUED','VALIDATING') ORDER BY created_at,request_id''',
            (account,)).fetchall()

    def daily_buy(self, account, day):
        self.assert_owner()
        # Conservative committed full notional, including reject/cancel/unknown. No
        # daily capacity refund until a future reviewed economic-ledger integration.
        row = self.conn.execute(f'''SELECT COALESCE(sum((payload->>'quantity')::numeric*(payload->>'limit_price')::numeric),0) AS amount
            FROM {S}requests WHERE account=%s AND trading_day=%s AND attempts=1 AND payload->>'side'='BUY' ''', (account, day)).fetchone()
        return int(row['amount'])

    def admission_ledger(self, account, day):
        """All same-day durable commitments; callable under admission row locks."""
        self.assert_owner()
        rows = self.conn.execute(f'''SELECT payload FROM {S}requests
            WHERE account=%s AND trading_day=%s AND attempts=1 ORDER BY request_id''',
            (account, day)).fetchall()
        return {'orders': tuple(Order.from_payload(row['payload']) for row in rows)}

    def finish_before_send(self, row, state, reason):
        self.assert_owner()
        require(state in {'BLOCKED', 'EXPIRED', 'CANCELLED'}, 'BAD_PRE_SEND_STATE')
        with self.conn.transaction():
            self.conn.execute(f'SELECT account FROM {S}accounts WHERE account=%s FOR UPDATE', (row['account'],)).fetchone()
            result = self.conn.execute(f'''UPDATE {S}requests SET state=%s,reason=%s,lease_until=NULL,
                updated_at=clock_timestamp() WHERE request_id=%s AND state='VALIDATING' AND claim_token=%s
                AND attempts=0 RETURNING account''', (state, reason, row['request_id'], row['claim_token'])).fetchone()
            if result:
                self._event(row['account'], row['request_id'], 'PRE_SEND_TERMINAL', reason, state)
            return bool(result)

    def cancel_pending(self, account, request_id):
        self.assert_owner()
        with self.conn.transaction():
            self.conn.execute(f'SELECT account FROM {S}accounts WHERE account=%s FOR UPDATE', (account,)).fetchone()
            row = self.get(account, request_id)
            if row['state'] == 'CANCELLED' and row['attempts'] == 0:
                return row
            result = self.conn.execute(f'''UPDATE {S}requests SET state='CANCELLED',reason='OWNER_CANCELLED_BEFORE_SEND',
                lease_until=NULL,updated_at=clock_timestamp() WHERE account=%s AND request_id=%s
                AND state IN ('QUEUED','VALIDATING') AND attempts=0 RETURNING *''', (account, request_id)).fetchone()
            require(result is not None, 'ALREADY_ADMITTED_BROKER_CANCEL_REQUIRED')
            self._event(account, request_id, 'CANCELLED', 'OWNER_CANCELLED_BEFORE_SEND', 'CANCELLED')
            return result

    def reserve_attempt(self, row, trading_day, policy, validate):
        """validate(now, daily_buy) runs under control and request row locks.

        Commit UNKNOWN before invoking any broker effect. Lease expiry can recover
        validation, never an order attempt. This method cannot admit a second send.
        """
        self.assert_owner()
        from psycopg.types.json import Jsonb
        with self.conn.transaction():
            control = self.conn.execute(f'SELECT * FROM {S}accounts WHERE account=%s FOR UPDATE', (row['account'],)).fetchone()
            current = self.conn.execute(f'SELECT * FROM {S}requests WHERE request_id=%s FOR UPDATE', (row['request_id'],)).fetchone()
            now = self.now()
            require(current['state']=='VALIDATING' and current['claim_token']==row['claim_token'] and current['lease_until'] > now and current['attempts']==0, 'CLAIM_LOST')
            require(control['mode']=='SIMULATION' and not control['halted'], 'HALTED')
            require(control['revision']==current['control_revision'], 'CONTROL_CHANGED')
            require(control['policy']==asdict(policy), 'POLICY_CHANGED')
            require(now < current['approval_expires_at'] and now < current['expires_at'], 'APPROVAL_EXPIRED')
            require(not self.unresolved(row['account']), 'UNRESOLVED_ORDER')
            if policy.version.startswith('policy-v2-'):
                from oneil_policy_v2.admission import PolicyAdmissionGate
                require(type(validate) is PolicyAdmissionGate
                        and str(validate.row['request_id']) == str(current['request_id'])
                        and validate.order.digest == current['digest']
                        and validate.store is self, 'POLICY_ADMISSION_REQUIRED')
            if self.runtime_guard is not None:
                from oneil_policy_v2.admission import PolicyAdmissionGate
                require(type(validate) is PolicyAdmissionGate
                        and validate.runtime_guard is self.runtime_guard,
                        'RUNTIME_ADMISSION_GUARD_REQUIRED')
            daily = self.daily_buy(row['account'], trading_day)
            admission_evidence = validate(self.now(), daily)
            if policy.version.startswith('policy-v2-'):
                require(type(admission_evidence) is dict
                        and admission_evidence.get('order_digest') == current['digest']
                        and admission_evidence.get('request_id') == str(current['request_id'])
                        and admission_evidence.get('control_revision') == control['revision']
                        and admission_evidence.get('mode') == 'SIMULATION'
                        and admission_evidence.get('live_capable') is False, 'POLICY_ADMISSION_REQUIRED')
            require(self.now() < current['approval_expires_at'] and self.now() < current['lease_until'], 'APPROVAL_OR_LEASE_EXPIRED')
            attempt_id = uuid.uuid4()
            result = self.conn.execute(f'''UPDATE {S}requests SET state='SUBMISSION_UNKNOWN',reason='ATTEMPT_DURABLY_ADMITTED',
                attempt_id=%s,attempts=1,trading_day=%s,admission_evidence=%s,lease_until=NULL,updated_at=clock_timestamp()
                WHERE request_id=%s RETURNING *''', (attempt_id, trading_day, Jsonb(admission_evidence) if admission_evidence is not None else None, row['request_id'])).fetchone()
            self._event(row['account'], row['request_id'], 'ATTEMPT_ADMITTED', 'NO_AUTOMATIC_RETRANSMISSION', 'SUBMISSION_UNKNOWN')
            return result

    def apply_observation(self, row, observed):
        self.assert_owner()
        order = Order.from_payload(row['payload'])
        observed.validate(str(row['request_id']), order, str(row['trading_day']))
        with self.conn.transaction():
            self.conn.execute(f'SELECT account FROM {S}accounts WHERE account=%s FOR UPDATE', (row['account'],)).fetchone()
            current = self.conn.execute(f'SELECT * FROM {S}requests WHERE request_id=%s FOR UPDATE', (row['request_id'],)).fetchone()
            require(current['attempt_id']==row['attempt_id'] and current['attempts']==1, 'ATTEMPT_MISMATCH')
            require(current['state'] in UNRESOLVED or current['state']==observed.status, 'INVALID_BROKER_TRANSITION')
            require(current['broker_order_id'] in (None, observed.broker_order_id), 'BROKER_ID_CHANGED')
            require(observed.filled_quantity >= current['filled_quantity'], 'FILL_REGRESSION')
            require(not (current['state']=='PART_FILLED' and observed.status in {'ACKNOWLEDGED','REJECTED'}), 'STATE_REGRESSION')
            if current['state']==observed.status and current['filled_quantity']==observed.filled_quantity:
                return current
            result = self.conn.execute(f'''UPDATE {S}requests SET state=%s,reason='BROKER_CONFIRMED',
                broker_order_id=%s,filled_quantity=%s,updated_at=clock_timestamp() WHERE request_id=%s RETURNING *''',
                (observed.status, observed.broker_order_id, observed.filled_quantity, row['request_id'])).fetchone()
            self._event(row['account'], row['request_id'], 'BROKER_OBSERVATION', 'EXACT_IDENTITY_MATCH', observed.status)
            return result

    def fail_closed(self, account, reason):
        self.assert_owner()
        with self.conn.transaction():
            changed = self.conn.execute(f'UPDATE {S}accounts SET halted=true,revision=revision+1,updated_at=clock_timestamp() WHERE account=%s AND halted=false RETURNING account', (account,)).fetchone()
            if changed:
                self._event(account, None, 'SAFETY_HALT', reason)

    def heartbeat(self, account, worker_id, status, reconciled=False):
        self.assert_owner()
        self.conn.execute(f'''INSERT INTO {S}health(account,worker_id,heartbeat_at,reconciled_at,status)
            VALUES (%s,%s,clock_timestamp(),CASE WHEN %s THEN clock_timestamp() ELSE NULL END,%s)
            ON CONFLICT(account) DO UPDATE SET worker_id=EXCLUDED.worker_id,heartbeat_at=EXCLUDED.heartbeat_at,
            reconciled_at=COALESCE(EXCLUDED.reconciled_at,{S}health.reconciled_at),status=EXCLUDED.status''',
            (account, worker_id, reconciled, status))

    def fill_requirements(self, account):
        self.assert_owner()
        rows = self.conn.execute(f'SELECT request_id,filled_quantity FROM {S}requests WHERE account=%s AND filled_quantity>0', (account,)).fetchall()
        return {str(row['request_id']):row['filled_quantity'] for row in rows}

    def read_model(self, account):
        self.assert_owner()
        control = self.control(account)
        health = self.conn.execute(f'SELECT * FROM {S}health WHERE account=%s', (account,)).fetchone()
        requests = self.conn.execute(f'''SELECT request_id,state,reason,attempts,filled_quantity,created_at,updated_at
            FROM {S}requests WHERE account=%s ORDER BY created_at DESC LIMIT 100''', (account,)).fetchall()
        now = self.now()
        worker_alive = bool(health and 0 <= (now-health['heartbeat_at']).total_seconds() <= 15)
        return {'mode':'SIMULATION', 'live_capable':False, 'halted':control['halted'],
                'worker_alive':worker_alive, 'broker_reconciled_at':health['reconciled_at'] if health else None,
                'control_revision':control['revision'], 'health':health, 'requests':requests, 'as_of':now}
