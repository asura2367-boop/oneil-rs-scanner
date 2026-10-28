"""Persisted broker-order reconciliation core. Never transmits orders.

Designed to run on a persistent host, not ephemeral GitHub Actions. Broker
snapshots must be supplied by a separately verified complete query adapter.
"""
import datetime as dt
import json
import os
import re
import sqlite3
from pathlib import Path

CODE = re.compile(r'^[0-9]{6}$')
TERMINAL = {'FILLED', 'CANCELLED', 'REJECTED'}
STATUSES = {'SUBMISSION_UNKNOWN','ACKNOWLEDGED','PART_FILLED',*TERMINAL}

class ReconcileBlocked(ValueError): pass

class OrderJournal:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.parent.is_dir(): raise ReconcileBlocked('PERSISTENT_DIR_MISSING')
        self.db=sqlite3.connect(self.path,timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS journal(
            intent TEXT PRIMARY KEY, ticker TEXT NOT NULL, side TEXT NOT NULL,
            venue TEXT NOT NULL, qty INTEGER NOT NULL, filled INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, broker_id TEXT UNIQUE, updated_at TEXT NOT NULL)''')
        self.db.commit()
    def prepare(self, intent, ticker, side, venue, qty):
        if not (isinstance(intent,str) and len(intent)>=16 and CODE.fullmatch(ticker)
                and side in {'BUY','SELL'} and venue in {'KRX','NXT'} and type(qty) is int and qty>0):
            raise ReconcileBlocked('INTENT_INVALID')
        try:
            with self.db:
                self.db.execute('INSERT INTO journal(intent,ticker,side,venue,qty,status,updated_at) VALUES(?,?,?,?,?,?,?)',
                  (intent,ticker,side,venue,qty,'SUBMISSION_UNKNOWN',dt.datetime.now(dt.timezone.utc).isoformat()))
        except sqlite3.IntegrityError:raise ReconcileBlocked('DUPLICATE_INTENT_NO_RETRY') from None
    def reconcile(self, intent, broker_snapshot, *, full_history_verified=False):
        if not full_history_verified:raise ReconcileBlocked('BROKER_HISTORY_INCOMPLETE')
        if not isinstance(broker_snapshot,dict):raise ReconcileBlocked('BROKER_SNAPSHOT_INVALID')
        if set(broker_snapshot)!={'broker_id','status','filled_qty'}:raise ReconcileBlocked('BROKER_SNAPSHOT_SCHEMA')
        broker_id=broker_snapshot['broker_id'];status=broker_snapshot['status'];filled=broker_snapshot['filled_qty']
        if not isinstance(broker_id,str) or not broker_id or status not in STATUSES-{ 'SUBMISSION_UNKNOWN'}:
            raise ReconcileBlocked('BROKER_STATUS_INVALID')
        with self.db:
            row=self.db.execute('SELECT qty,filled,status,broker_id FROM journal WHERE intent=?',(intent,)).fetchone()
            if row is None:raise ReconcileBlocked('INTENT_UNKNOWN')
            qty,previous,prior,old_id=row
            if prior in TERMINAL:raise ReconcileBlocked('TERMINAL_STATE_NO_REPLAY')
            if old_id and old_id!=broker_id:raise ReconcileBlocked('BROKER_ID_CONFLICT')
            if type(filled) is not int or not previous<=filled<=qty:raise ReconcileBlocked('FILL_REGRESSION_OR_OVERFILL')
            if status=='FILLED' and filled!=qty:raise ReconcileBlocked('FILL_INCOMPLETE')
            if status=='PART_FILLED' and not 0<filled<qty:raise ReconcileBlocked('PART_FILL_INVALID')
            if prior=='PART_FILLED' and status=='ACKNOWLEDGED':raise ReconcileBlocked('STATE_REGRESSION')
            try:
                self.db.execute('UPDATE journal SET filled=?,status=?,broker_id=?,updated_at=? WHERE intent=?',
                  (filled,status,broker_id,dt.datetime.now(dt.timezone.utc).isoformat(),intent))
            except sqlite3.IntegrityError:raise ReconcileBlocked('BROKER_ID_DUPLICATED') from None
        return {'status':status,'unfilled_qty':qty-filled,'automatic_resubmission_allowed':False}
    def pending(self):
        return self.db.execute("SELECT intent,ticker,status FROM journal WHERE status NOT IN ('FILLED','CANCELLED','REJECTED')").fetchall()
    def close(self):self.db.close()

def readiness(journal_path):
    path=Path(journal_path)
    return {'gate':'DENY','orders_sent':0,'live_enabled':False,
        'journal_file_exists':path.is_file(),'durable_host_attested':False,
        'broker_recovery_attested':False,'auto_retry_enabled':False}

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--journal',required=True);a=ap.parse_args()
    print(json.dumps(readiness(a.journal)))
