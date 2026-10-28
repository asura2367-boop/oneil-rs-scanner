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
