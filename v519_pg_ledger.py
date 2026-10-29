"""V5.19 durable PostgreSQL order-intent ledger. No broker order submission.

Uses a single atomic INSERT under a PostgreSQL unique constraint. Unknown
submission outcomes remain locked; never retry them automatically.
"""
import os
import re
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS v519_order_intents (
 intent_id text PRIMARY KEY CHECK (length(intent_id) BETWEEN 16 AND 128),
 account_fingerprint text NOT NULL CHECK (length(account_fingerprint) = 64),
 ticker text NOT NULL CHECK (ticker ~ '^[0-9]{6}$'),
 side text NOT NULL CHECK (side IN ('BUY','SELL')),
 venue text NOT NULL CHECK (venue IN ('KRX','NXT')),
 quantity integer NOT NULL CHECK (quantity > 0),
 status text NOT NULL DEFAULT 'SUBMISSION_UNKNOWN'
   CHECK (status IN ('SUBMISSION_UNKNOWN','ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED','REJECTED')),
 broker_order_id text UNIQUE,
 filled_quantity integer NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK (filled_quantity <= quantity)
);
CREATE TABLE IF NOT EXISTS v519_control (
 singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
 halted boolean NOT NULL DEFAULT true,
 updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO v519_control(singleton,halted) VALUES (true,true) ON CONFLICT DO NOTHING;
'''

class LedgerBlocked(RuntimeError): pass

@contextmanager
def connect():
    url = os.environ.get('SUPABASE_DB_URL', '')
    if not url or not url.startswith('postgresql://'):
        raise LedgerBlocked('DATABASE_URL_MISSING_OR_INVALID')
    if 'sslmode=disable' in url.lower():
        raise LedgerBlocked('DATABASE_TLS_REQUIRED')
    import psycopg
    with psycopg.connect(url, connect_timeout=8, sslmode='require',
                         options='-c statement_timeout=10000') as conn:
        yield conn

def initialize(conn):
    # Requires a trusted migration connection; do not expose DB credentials in logs.
    with conn.cursor() as cur:
        cur.execute(SCHEMA)

def status(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT halted FROM v519_control WHERE singleton=true')
        row = cur.fetchone()
        if row is None: raise LedgerBlocked('HALT_CONTROL_MISSING')
        cur.execute("SELECT count(*) FROM v519_order_intents WHERE status NOT IN ('FILLED','CANCELLED','REJECTED')")
        unresolved = cur.fetchone()[0]
    return {'halted': row[0], 'unresolved': unresolved, 'gate': 'DENY', 'orders_sent': 0}

def reserve(conn, *, intent_id, account_fingerprint, ticker, side, venue, quantity):
    """Atomic duplicate prevention; durable reservation is NOT permission to send orders."""
    if (not isinstance(intent_id,str) or not 16 <= len(intent_id) <= 128 or
        not isinstance(account_fingerprint,str) or not re.fullmatch('[0-9a-f]{64}', account_fingerprint) or
        not isinstance(ticker,str) or not re.fullmatch('[0-9]{6}',ticker) or
        side not in ('BUY','SELL') or venue not in ('KRX','NXT') or
        type(quantity) is not int or quantity <= 0):
        raise LedgerBlocked('INVALID_INTENT')
    with conn.cursor() as cur:
        cur.execute('SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE')
        row=cur.fetchone()
        if row is None or row[0]: raise LedgerBlocked('EMERGENCY_HALT_OR_UNINITIALIZED')
        cur.execute("SELECT count(*) FROM v519_order_intents WHERE account_fingerprint=%s AND status NOT IN ('FILLED','CANCELLED','REJECTED')", (account_fingerprint,))
        if cur.fetchone()[0]: raise LedgerBlocked('UNRESOLVED_ORDERS_BLOCK_NEW_INTENT')
        cur.execute('''INSERT INTO v519_order_intents
            (intent_id,account_fingerprint,ticker,side,venue,quantity)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING intent_id''',
            (intent_id,account_fingerprint,ticker,side,venue,quantity))
        if cur.fetchone() is None: raise LedgerBlocked('DUPLICATE_INTENT_NO_RETRY')
    return {'reserved': True, 'gate': 'DENY', 'orders_sent': 0}

if __name__ == '__main__':
    import argparse, json
    p=argparse.ArgumentParser()
    p.add_argument('--initialize',action='store_true')
    p.add_argument('--status',action='store_true')
    a=p.parse_args()
    try:
        with connect() as db:
            if a.initialize: initialize(db)
            print(json.dumps(status(db)))
    except Exception as e:
        print(json.dumps({'gate':'DENY','orders_sent':0,'error':str(e) if isinstance(e,LedgerBlocked) else 'DATABASE_OPERATION_FAILED'}))
        raise SystemExit(2)

# Idempotent migration of the existing ledger, not a parallel order database.
RUNTIME_SCHEMA = '''
ALTER TABLE v519_order_intents ADD COLUMN IF NOT EXISTS trading_day date;
ALTER TABLE v519_order_intents ADD COLUMN IF NOT EXISTS limit_price bigint CHECK (limit_price > 0);
ALTER TABLE v519_order_intents ADD COLUMN IF NOT EXISTS signal text;
ALTER TABLE v519_order_intents DROP CONSTRAINT IF EXISTS v519_order_intents_broker_order_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS v519_broker_identity
 ON v519_order_intents(account_fingerprint,trading_day,broker_order_id)
 WHERE broker_order_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS v519_one_buy_per_day
 ON v519_order_intents(account_fingerprint,trading_day,ticker) WHERE side='BUY';
CREATE TABLE IF NOT EXISTS v519_runs (
 run_id text PRIMARY KEY, account_fingerprint text NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 result jsonb NOT NULL DEFAULT '{"status":"STARTED"}'::jsonb
);
ALTER TABLE v519_order_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE v519_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE v519_runs ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS v519_live_activation (
 account_fingerprint text PRIMARY KEY CHECK (length(account_fingerprint)=64),
 release_fingerprint text NOT NULL CHECK (length(release_fingerprint)=64),
 operator_login text NOT NULL,
 activated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE v519_live_activation ENABLE ROW LEVEL SECURITY;
ALTER TABLE v519_order_intents ADD COLUMN IF NOT EXISTS verified_not_sent boolean NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS v519_sell_alerts (
 alert_id text PRIMARY KEY, account_fingerprint text NOT NULL,
 trading_day date NOT NULL, ticker text NOT NULL, reason text NOT NULL,
 quantity integer NOT NULL CHECK (quantity>0), reference_price bigint NOT NULL CHECK(reference_price>0),
 created_at timestamptz NOT NULL DEFAULT now(), last_observed_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(account_fingerprint,trading_day,ticker,reason)
);
ALTER TABLE v519_sell_alerts ENABLE ROW LEVEL SECURITY;
'''


def migrate_runtime(conn):
    initialize(conn)
    with conn.cursor() as cur:
        cur.execute(RUNTIME_SCHEMA)


class PgRuntimeLedger:
    """Session-lock-backed runtime. Requires a DIRECT or SESSION pool connection.

    Transaction-pool URLs (port 6543) cannot preserve advisory session locks.
    Use a restricted trusted server role. No browser/anon policies are installed.
    """
    def __init__(self, conn, fingerprint):
        if not re.fullmatch('[0-9a-f]{64}',fingerprint):
            raise LedgerBlocked('ACCOUNT_FINGERPRINT_INVALID')
        if not conn.autocommit:
            raise LedgerBlocked('RUNTIME_REQUIRES_AUTOCOMMIT_CONNECTION')
        self.conn=conn; self.fingerprint=fingerprint
        self.lock_key=int(fingerprint[:16],16) % (2**63)

    def rows(self, sql, params=()):
        from psycopg.rows import dict_row
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql,params)
            return cur.fetchall()

    @contextmanager
    def lock(self):
        with self.conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s)',(self.lock_key,))
            if cur.fetchone()[0] is not True:
                raise LedgerBlocked('ACCOUNT_RUNTIME_ALREADY_RUNNING')
        try:
            yield
        finally:
            with self.conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(%s)',(self.lock_key,))

    def halted(self):
        rows=self.rows('SELECT halted FROM v519_control WHERE singleton=true')
        if len(rows)!=1:
            raise LedgerBlocked('HALT_CONTROL_MISSING')
        return rows[0]['halted']

    def halt(self):
        with self.conn.cursor() as cur:
            cur.execute('UPDATE v519_control SET halted=true,updated_at=now() WHERE singleton=true')
            if cur.rowcount!=1:
                raise LedgerBlocked('HALT_CONTROL_MISSING')

    @contextmanager
    def send_guard(self):
        with self.conn.transaction():
            rows=self.rows('SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE')
            if len(rows)!=1 or rows[0]['halted'] is not False:
                raise LedgerBlocked('EMERGENCY_HALT_ACTIVE')
            yield

    def release_authorized(self, release):
        rows=self.rows('SELECT release_fingerprint FROM v519_live_activation WHERE account_fingerprint=%s',
            (self.fingerprint,))
        return len(rows)==1 and rows[0]['release_fingerprint']==release

    def activate_release(self, release, operator):
        if not re.fullmatch('[0-9a-f]{64}',release) or not re.fullmatch('[A-Za-z0-9-]{1,39}',operator):
            raise LedgerBlocked('ACTIVATION_IDENTITY_INVALID')
        with self.conn.transaction():
            rows=self.rows('SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE')
            if len(rows)!=1 or self.pending():raise LedgerBlocked('ACTIVATION_NOT_RECONCILED')
            self.conn.execute('''INSERT INTO v519_live_activation(account_fingerprint,release_fingerprint,operator_login)
                VALUES(%s,%s,%s) ON CONFLICT(account_fingerprint) DO UPDATE SET
                release_fingerprint=excluded.release_fingerprint,operator_login=excluded.operator_login,activated_at=now()''',
                (self.fingerprint,release,operator))
            self.conn.execute('UPDATE v519_control SET halted=false,updated_at=now() WHERE singleton=true')

    def pending(self):
        return self.rows('''SELECT * FROM v519_order_intents WHERE account_fingerprint=%s
            AND status NOT IN ('FILLED','CANCELLED','REJECTED') ORDER BY created_at''',(self.fingerprint,))

    def managed(self):
        rows=self.rows('''SELECT ticker, sum(CASE WHEN side='BUY' THEN filled_quantity
            ELSE -filled_quantity END) AS quantity FROM v519_order_intents
            WHERE account_fingerprint=%s GROUP BY ticker''',(self.fingerprint,))
        if any(r['quantity']<0 for r in rows):
            raise LedgerBlocked('MANAGED_POSITION_NEGATIVE')
        return {r['ticker']:int(r['quantity']) for r in rows if r['quantity']>0}

    def bought_today(self, day):
        return {r['ticker'] for r in self.rows('''SELECT ticker FROM v519_order_intents
            WHERE account_fingerprint=%s AND trading_day=%s AND side='BUY' ''',(self.fingerprint,day))}

    def reserve_plan(self, plan, day):
        import hashlib
        if (not re.fullmatch('[0-9]{6}',plan.ticker) or plan.side not in ('BUY','SELL') or
                type(plan.quantity) is not int or plan.quantity<=0 or
                type(plan.price) is not int or plan.price<=0):
            raise LedgerBlocked('INVALID_ORDER_PLAN')
        progress=''
        if plan.side=='SELL':
            # A confirmed fill advances the exit intent. An unknown or zero-fill
            # cancelled order cannot be replayed under a fresh random ID.
            rows=self.rows('''SELECT coalesce(sum(filled_quantity),0) AS filled FROM v519_order_intents
                WHERE account_fingerprint=%s AND ticker=%s AND side='SELL' ''',(self.fingerprint,plan.ticker))
            progress='|'+str(rows[0]['filled'])
            rows=self.rows('''SELECT count(*) AS voided FROM v519_order_intents
                WHERE account_fingerprint=%s AND ticker=%s AND side='SELL' AND trading_day=%s AND verified_not_sent''',
                (self.fingerprint,plan.ticker,day))
            if rows[0]['voided']:progress+='|not-sent:'+str(rows[0]['voided'])
        ident=hashlib.sha256(f'v519|{self.fingerprint}|{day}|{plan.ticker}|{plan.side}{progress}'.encode()).hexdigest()
        # Own transaction commits the UNKNOWN record before any broker send.
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute('SELECT halted FROM v519_control WHERE singleton=true FOR UPDATE')
                row=cur.fetchone()
                if row is None or row[0] is not False:
                    raise LedgerBlocked('EMERGENCY_HALT_ACTIVE')
                if self.pending():
                    raise LedgerBlocked('UNRESOLVED_ORDERS')
                cur.execute('''INSERT INTO v519_order_intents
                    (intent_id,account_fingerprint,ticker,side,venue,quantity,trading_day,limit_price,signal)
                    VALUES(%s,%s,%s,%s,'KRX',%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING intent_id''',
                    (ident,self.fingerprint,plan.ticker,plan.side,plan.quantity,day,plan.price,plan.signal))
                if not cur.fetchone():
                    raise LedgerBlocked('DUPLICATE_INTENT_NO_RETRY')
        return ident

    def void_unsent_sell(self,ident):
        # Only the synchronous permission check BEFORE the broker POST may call
        # this. Unknown network outcomes never use this path or gain a retry.
        rows=self.rows('''UPDATE v519_order_intents SET status='REJECTED',verified_not_sent=true,updated_at=now()
            WHERE intent_id=%s AND account_fingerprint=%s AND side='SELL'
            AND status='SUBMISSION_UNKNOWN' AND broker_order_id IS NULL AND filled_quantity=0
            RETURNING intent_id''',(ident,self.fingerprint))
        if len(rows)!=1:raise LedgerBlocked('UNSENT_SELL_STATE_CONFLICT')

    def record_sell_alerts(self,plans,day):
        import hashlib
        for plan in plans:
            if plan.side!='SELL':continue
            ident=hashlib.sha256(f'sell-alert-v1|{self.fingerprint}|{day}|{plan.ticker}|{plan.reason}'.encode()).hexdigest()
            self.conn.execute('''INSERT INTO v519_sell_alerts(alert_id,account_fingerprint,trading_day,ticker,reason,quantity,reference_price)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(account_fingerprint,trading_day,ticker,reason)
                DO UPDATE SET quantity=excluded.quantity,reference_price=excluded.reference_price,last_observed_at=now()''',
                (ident,self.fingerprint,day,plan.ticker,plan.reason,plan.quantity,plan.price))

    def sell_alerts(self):
        return self.rows('''SELECT alert_id,trading_day,ticker,reason,quantity,reference_price,created_at,last_observed_at
            FROM v519_sell_alerts WHERE account_fingerprint=%s ORDER BY created_at DESC LIMIT 100''',(self.fingerprint,))

    def ack(self, ident, order_id):
        if not isinstance(order_id,str) or not re.fullmatch('[0-9]{1,20}',order_id):
            raise LedgerBlocked('INVALID_BROKER_ID')
        with self.conn.cursor() as cur:
            cur.execute('''UPDATE v519_order_intents SET broker_order_id=%s,status='ACKNOWLEDGED',updated_at=now()
                WHERE intent_id=%s AND account_fingerprint=%s AND status='SUBMISSION_UNKNOWN'
                RETURNING intent_id''',(order_id,ident,self.fingerprint))
            if cur.fetchone() is None:
                raise LedgerBlocked('ACK_CONFLICT')

    def reconcile(self, order, state, filled):
        from v519_pg_reconcile import FINAL, ALLOWED
        if type(filled) is not int or state not in ALLOWED:
            raise LedgerBlocked('INVALID_RECONCILIATION')
        with self.conn.transaction():
            rows=self.rows('''SELECT * FROM v519_order_intents WHERE intent_id=%s
                AND account_fingerprint=%s FOR UPDATE''',(order['intent_id'],self.fingerprint))
            if len(rows)!=1:
                raise LedgerBlocked('INTENT_NOT_FOUND')
            old=rows[0]
            if (old['status'] in FINAL or old['broker_order_id']!=order['broker_order_id'] or
                    not old['filled_quantity']<=filled<=old['quantity']):
                raise LedgerBlocked('ORDER_STATE_CONFLICT')
            if (state=='FILLED' and filled!=old['quantity'] or
                state=='PART_FILLED' and not 0<filled<old['quantity'] or
                old['status']=='PART_FILLED' and state=='ACKNOWLEDGED'):
                raise LedgerBlocked('FILL_STATE_CONFLICT')
            with self.conn.cursor() as cur:
                cur.execute('''UPDATE v519_order_intents SET status=%s,filled_quantity=%s,
                    updated_at=now() WHERE intent_id=%s''',(state,filled,order['intent_id']))

    def start_run(self, run_id):
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO v519_runs(run_id,account_fingerprint) VALUES(%s,%s)',
                        (run_id,self.fingerprint))

    def finish_run(self, run_id, result):
        from psycopg.types.json import Jsonb
        with self.conn.cursor() as cur:
            cur.execute('UPDATE v519_runs SET finished_at=now(),result=%s WHERE run_id=%s',
                        (Jsonb(result),run_id))
