"""Fail-closed reconciliation of already registered V5.19 PostgreSQL intents.
This module NEVER sends orders or clears the emergency halt.
"""
import re
from v519_pg_ledger import connect, LedgerBlocked

FINAL = {'FILLED', 'CANCELLED', 'REJECTED'}
ALLOWED = {'ACKNOWLEDGED', 'PART_FILLED', *FINAL}

def reconcile_existing(intent_id, broker_order_id, status, filled_quantity, *, broker_history_complete=False):
    if not broker_history_complete:
        raise LedgerBlocked('BROKER_HISTORY_INCOMPLETE')
    if not isinstance(intent_id, str) or not 16 <= len(intent_id) <= 128:
        raise LedgerBlocked('INTENT_INVALID')
    if not isinstance(broker_order_id, str) or not re.fullmatch(r'[0-9A-Za-z-]{1,64}', broker_order_id):
        raise LedgerBlocked('BROKER_ORDER_ID_INVALID')
    if status not in ALLOWED or type(filled_quantity) is not int or filled_quantity < 0:
        raise LedgerBlocked('BROKER_STATUS_INVALID')
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT quantity, filled_quantity, status, broker_order_id
                           FROM v519_order_intents WHERE intent_id=%s FOR UPDATE''', (intent_id,))
            row = cur.fetchone()
            if row is None: raise LedgerBlocked('INTENT_NOT_FOUND')
            qty, old_fill, old_status, old_id = row
            if old_status in FINAL: raise LedgerBlocked('TERMINAL_INTENT_IMMUTABLE')
            if old_id and old_id != broker_order_id: raise LedgerBlocked('BROKER_ID_CONFLICT')
            if not old_fill <= filled_quantity <= qty: raise LedgerBlocked('FILL_REGRESSION_OR_OVERFILL')
            if status == 'FILLED' and filled_quantity != qty: raise LedgerBlocked('INCOMPLETE_FILL')
            if status == 'PART_FILLED' and not 0 < filled_quantity < qty: raise LedgerBlocked('INVALID_PART_FILL')
            if old_status == 'PART_FILLED' and status == 'ACKNOWLEDGED': raise LedgerBlocked('STATUS_REGRESSION')
            cur.execute('''UPDATE v519_order_intents SET status=%s, filled_quantity=%s,
                           broker_order_id=%s, updated_at=now() WHERE intent_id=%s''',
                        (status, filled_quantity, broker_order_id, intent_id))
    return {'updated': True, 'orders_sent': 0, 'gate': 'DENY'}


def unresolved_summary():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,count(*) FROM v519_order_intents WHERE status NOT IN ('FILLED','CANCELLED','REJECTED') GROUP BY status")
            rows = cur.fetchall()
    return {'unresolved_by_status': dict(rows), 'orders_sent': 0, 'gate': 'DENY'}

if __name__ == '__main__':
    import json
    print(json.dumps(unresolved_summary()))
