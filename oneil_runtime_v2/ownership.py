"""One-file offline recovery + cross-engine ownership rehearsal.

This does NOT fence the real legacy executor or the existing runtime/core. All
participants must use this same authoritative store. Production admission remains
unimplemented. There is no network, submit method, worker factory or activation.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid

from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import Blocked, Order, aware, require
from oneil_execution_v2.kis_adapter import NativeOrderId
from oneil_execution_v2.recovery import (Attempt, FixtureEvidenceAuthority,
    UNRESOLVED, bind_identity, canonical, digest, fields, reconcile)

ENGINES = ('LEGACY_SIMULATION', 'V2_SIMULATION')


def fingerprint(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value), 'OWNERSHIP_INVALID_DIGEST')
    return value


@dataclass(frozen=True)
class OwnershipLease:
    coordinator: str
    account: str
    engine: str
    incarnation: str
    epoch: int


class OfflineRecoveryCoordinator:
    live_capable = False

    @classmethod
    def initialize(cls, path, authority):
        require(type(authority) is FixtureEvidenceAuthority, 'OFFLINE_FIXTURE_AUTHORITY_REQUIRED')
        path = Path(path)
        with path.open('xb'): pass
        conn = sqlite3.connect(str(path),isolation_level=None)
        try:
            conn.execute('PRAGMA synchronous=FULL')
            conn.executescript('''BEGIN IMMEDIATE;
                CREATE TABLE metadata(version INTEGER, coordinator TEXT, issuer TEXT);
                CREATE TABLE account(account TEXT PRIMARY KEY, manifest TEXT,
                    generation INTEGER NOT NULL, epoch INTEGER NOT NULL,
                    engine TEXT, incarnation TEXT, coverage_from REAL, coverage_until REAL, halted INTEGER NOT NULL);
                CREATE TABLE attempt(request_id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL,
                    account TEXT NOT NULL, data TEXT NOT NULL, native TEXT UNIQUE, native_collision TEXT UNIQUE);
                CREATE TABLE event(id INTEGER PRIMARY KEY, account TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TRIGGER event_immutable_update BEFORE UPDATE ON event BEGIN
                    SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
                CREATE TRIGGER event_immutable_delete BEFORE DELETE ON event BEGIN
                    SELECT RAISE(ABORT,'AUDIT_IMMUTABLE'); END;
                COMMIT;''')
            conn.execute('INSERT INTO metadata VALUES(1,?,?)',(uuid.uuid4().hex,authority.issuer))
        finally: conn.close()
        return cls(path,authority)

    def __init__(self,path,authority):
        require(type(authority) is FixtureEvidenceAuthority, 'OFFLINE_FIXTURE_AUTHORITY_REQUIRED')
        path = Path(path)
        require(not path.is_symlink() and path.is_file(), 'OWNERSHIP_STATE_MISSING_OR_UNSAFE')
        self.conn = sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=.1)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA synchronous=FULL')
        self.authority = authority; self._scope = None; self._poisoned = False
        try:
            rows = self.conn.execute('SELECT * FROM metadata').fetchall()
            require(len(rows)==1 and rows[0]['version']==1 and rows[0]['issuer']==authority.issuer,
                    'OWNERSHIP_STATE_OR_AUTHORITY_MISMATCH')
            self.coordinator = rows[0]['coordinator']
            require(type(self.coordinator) is str and re.fullmatch('[0-9a-f]{32}',self.coordinator),
                    'OWNERSHIP_STATE_CORRUPT')
            require(self.conn.execute('PRAGMA quick_check').fetchone()[0]=='ok','OWNERSHIP_STATE_CORRUPT')
            for row in self.conn.execute('SELECT account FROM account'): self.status(row[0])
            for row in self.conn.execute('SELECT request_id FROM attempt'): self.get(row[0])
            self.conn.execute('SELECT id,account,kind,data FROM event LIMIT 1')
        except BaseException:
            self.conn.close(); raise

    @contextmanager
    def transaction(self):
        require(not self._poisoned and self._scope is None and not self.conn.in_transaction,
                'OWNERSHIP_UNAVAILABLE_OR_REENTRANT')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction: self.conn.execute('ROLLBACK')
            raise

    def _event(self,account,kind,value):
        self.conn.execute('INSERT INTO event(account,kind,data) VALUES(?,?,?)',(account,kind,canonical(value)))

    def register_fixture_account(self,account,*,boundary_manifest=None):
        fingerprint(account)
        if boundary_manifest is not None: fingerprint(boundary_manifest)
        with self.transaction():
            self.conn.execute('INSERT INTO account VALUES(?,?,0,0,NULL,NULL,NULL,NULL,1)',(account,boundary_manifest))
            self._event(account,'REGISTERED',{'live_capable':False})

    def status(self,account):
        fingerprint(account)
        row = self.conn.execute('SELECT * FROM account WHERE account=?',(account,)).fetchone()
        require(row is not None,'OWNERSHIP_ACCOUNT_MISSING')
        value = dict(row)
        require(type(value['generation']) is int and value['generation']>=0
            and type(value['epoch']) is int and value['epoch']>=0 and value['halted'] in (0,1),
            'OWNERSHIP_STATE_CORRUPT')
        if value['manifest'] is not None: fingerprint(value['manifest'])
        if value['engine'] is None:
            require(value['incarnation'] is None and value['epoch']==0 and value['coverage_until'] is None and value['coverage_from'] is None
                and value['halted']==1,'OWNERSHIP_STATE_CORRUPT')
        else:
            require(value['engine'] in ENGINES and type(value['incarnation']) is str
                and re.fullmatch('[0-9a-f]{32}',value['incarnation']) and value['epoch']>0
                and type(value['coverage_until']) in (int,float) and type(value['coverage_from']) in (int,float)
                and math.isfinite(value['coverage_from']) and math.isfinite(value['coverage_until'])
                and 0<value['coverage_until']-value['coverage_from']<=30,'OWNERSHIP_STATE_CORRUPT')
        return value

    @staticmethod
    def current_holder(row):
        if row['engine'] is None: return None
        return {key:row[key] for key in ('engine','incarnation','epoch')}

    def _lease(self,lease):
        require(type(lease) is OwnershipLease and lease.coordinator==self.coordinator
            and type(lease.epoch) is int and lease.epoch>0,'OWNERSHIP_LEASE_REQUIRED')
        row = self.status(lease.account)
        require(self.current_holder(row)=={'engine':lease.engine,'incarnation':lease.incarnation,'epoch':lease.epoch},
                'OWNERSHIP_LOST')
        return row

    def halt(self,account,reason='RECOVERY_REVIEW_REQUIRED'):
        # Fixed codes only: never persist arbitrary transport or credential text.
        require(reason in {'RECOVERY_REVIEW_REQUIRED','RECOVERY_EVIDENCE_REJECTED','OWNERSHIP_PROOF_REJECTED'},
                'OWNERSHIP_INVALID_REASON')
        try:
            with self.transaction():
                self.status(account)
                self.conn.execute('UPDATE account SET halted=1,generation=generation+1 WHERE account=?',(account,))
                self._event(account,'HALT',{'reason':reason})
        except BaseException:
            self._poisoned = True
            raise

    def handover(self,account,engine,receipt,now):
        """No expiry takeover. Exact signed stop + both-engine coverage required.

        Positive fixture attestations are bound to the current local generation.
        Empty local state alone is never proof external/legacy work has drained.
        Handover remains HALTED; it never resumes simulation or sends an order.
        """
        aware(now)
        try:
            require(engine in ENGINES,'OFFLINE_ENGINES_REQUIRED')
            with self.transaction():
                row = self.status(account)
                value = self.authority.open(receipt,audience=self.coordinator,kind='handover',now=now)
                fields(value,'account generation from_holder to_engine boundary_manifest participants stopped_holder legacy_shutdown broker_snapshot legacy_snapshot')
                require(value['account']==account and value['generation']==row['generation']
                    and type(value['generation']) is int and value['to_engine']==engine
                    and value['from_holder']==self.current_holder(row),'OWNERSHIP_PROOF_SCOPE_MISMATCH')
                require(row['manifest'] is not None and value['boundary_manifest']==row['manifest']
                    and value['participants']==list(ENGINES),'BOTH_ENGINE_BOUNDARIES_UNPROVEN')
                # A signed exact-incarnation stop is required even if a lease is old.
                require(value['stopped_holder']==self.current_holder(row),'PREVIOUS_PROCESS_EXIT_UNPROVEN')
                fields(value['legacy_shutdown'],'incarnation evidence_digest')
                require(type(value['legacy_shutdown']['incarnation']) is str
                    and re.fullmatch('[0-9a-f]{32}',value['legacy_shutdown']['incarnation']),
                    'LEGACY_PROCESS_EXIT_UNPROVEN')
                fingerprint(value['legacy_shutdown']['evidence_digest'])
                require(row['engine']!='LEGACY_SIMULATION' or
                    value['legacy_shutdown']['incarnation']==row['incarnation'], 'LEGACY_PROCESS_EXIT_UNPROVEN')
                for name in ('broker_snapshot','legacy_snapshot'):
                    fields(value[name],'digest unresolved')
                    fingerprint(value[name]['digest'])
                    require(value[name]['unresolved']==[],'EXTERNAL_UNRESOLVED_ORDER')
                require(not self.unresolved(account),'UNRESOLVED_ORDER_PREVENTS_HANDOVER')
                incarnation = uuid.uuid4().hex; epoch = row['epoch']+1
                # Use receipt's authenticated expiry, not caller-provided lifetime.
                sealed = json.loads(receipt.content); expiry = sealed['expires_at']; issued = sealed['issued_at']
                self.conn.execute('''UPDATE account SET generation=generation+1,epoch=?,engine=?,
                    incarnation=?,coverage_from=?,coverage_until=?,halted=1 WHERE account=?''',
                    (epoch,engine,incarnation,issued,expiry,account))
                self._event(account,'HANDOVER',{'proof_digest':digest(value),'engine':engine,'epoch':epoch})
                return OwnershipLease(self.coordinator,account,engine,incarnation,epoch)
        except Exception:
            self.halt(account,'OWNERSHIP_PROOF_REJECTED')
            raise

    def resume_simulation(self,lease,owner,now,*,account_evidence=None):
        try:
            aware(now)
            with self.transaction():
                row = self._lease(lease)
                require(type(owner) is AuthenticatedOwner and owner.account==lease.account
                    and type(owner.principal) is str and 1<=len(owner.principal)<=128,
                    'AUTHENTICATED_SIMULATION_OWNER_REQUIRED')
                require(row['coverage_from']<=now.timestamp()<row['coverage_until'],'OWNERSHIP_COVERAGE_EXPIRED')
                require(not self.unresolved(lease.account),'UNRESOLVED_ORDER_PREVENTS_RESUME')
                proof=self.authority.open(account_evidence,audience=self.coordinator,kind='account_reconciliation',now=now)
                fields(proof,'account generation fill_watermarks completeness external_unresolved account_snapshot_digest observed_at')
                require(proof['account']==lease.account and type(proof['generation']) is int
                    and proof['generation']==row['generation'] and proof['completeness']=='COMPLETE'
                    and proof['external_unresolved']==[], 'ACCOUNT_RECONCILIATION_UNPROVEN')
                fingerprint(proof['account_snapshot_digest'])
                observed_at=aware(datetime.fromisoformat(proof['observed_at']))
                require(0<=(now-observed_at).total_seconds()<=10,'ACCOUNT_RECONCILIATION_STALE')
                for item in self.conn.execute('SELECT request_id FROM attempt WHERE account=?',(lease.account,)):
                    prior=self.get(item[0]).observed_at
                    require(prior is None or prior<=observed_at,'ACCOUNT_NOT_CAUSALLY_RECONCILED')
                required=self.fill_requirements(lease.account)
                require(type(proof['fill_watermarks']) is dict and set(proof['fill_watermarks'])==set(required)
                    and all(type(proof['fill_watermarks'][key]) is int and proof['fill_watermarks'][key]==qty
                            for key,qty in required.items()), 'ACCOUNT_NOT_CAUSALLY_RECONCILED')
                self.conn.execute('UPDATE account SET halted=0,generation=generation+1 WHERE account=?',(lease.account,))
                self._event(lease.account,'SIMULATION_RESUME',{'principal':owner.principal,'epoch':lease.epoch})
        except Exception:
            self.halt(lease.account,'RECOVERY_EVIDENCE_REJECTED')
            raise

    @contextmanager
    def scope(self,lease,now):
        """Cooperative exclusion held through a caller's offline fixture operation.

        This lock is not a broker capability. Do not pass live effects to this
        model. Reservation has a separate committed method; scope itself cannot
        submit, reserve, reenter or clear any order state.
        """
        aware(now)
        with self.transaction():
            row = self._lease(lease)
            require(row['halted']==0 and row['coverage_from']<=now.timestamp()<row['coverage_until'],'OWNERSHIP_NOT_READY')
            require(not self.unresolved(lease.account),'UNRESOLVED_ORDER_PREVENTS_ADMISSION')
            self._scope = lease
            try: yield
            finally: self._scope = None

    @staticmethod
    def _encode(attempt):
        value = asdict(attempt); value['order']=attempt.order.payload()
        value['attempted_at']=attempt.attempted_at.isoformat()
        value['observed_at']=attempt.observed_at.isoformat() if attempt.observed_at else None
        return canonical(value)

    def get(self,request_id):
        row = self.conn.execute('SELECT * FROM attempt WHERE request_id=?',(request_id,)).fetchone()
        require(row is not None,'RECOVERY_ATTEMPT_MISSING')
        value = json.loads(row['data']); value['order']=Order.from_payload(value['order'])
        value['attempted_at']=datetime.fromisoformat(value['attempted_at'])
        value['observed_at']=datetime.fromisoformat(value['observed_at']) if value['observed_at'] else None
        value['native_id']=NativeOrderId(**value['native_id']) if value['native_id'] else None
        attempt = Attempt(**value).validate()
        require((attempt.request_id,attempt.attempt_id,attempt.order.account)==
                (row['request_id'],row['attempt_id'],row['account']),'RECOVERY_STORAGE_MISMATCH')
        expected = canonical(asdict(attempt.native_id)) if attempt.native_id else None
        require(row['native']==expected and row['native_collision']==self._collision(attempt.native_id),
                'RECOVERY_STORAGE_MISMATCH')
        return attempt

    @staticmethod
    def _collision(native):
        if native is None: return None
        # Numeric aliases are NOT accepted as identity matches. This extra unique
        # guard only refuses ambiguous reuse of zero-padded representations.
        return canonical([native.account,native.environment,native.trading_day,native.venue,
                          int(native.branch),int(native.number)])

    def unresolved(self,account):
        return tuple(a for r in self.conn.execute('SELECT request_id FROM attempt WHERE account=?',(account,))
                     if (a:=self.get(r[0])).state in UNRESOLVED)

    def reserve_fixture_attempt(self,lease,attempt,now):
        """Persist one UNKNOWN attempt, no send. Reusing either ID is rejected.

        A crash here must remain unknown. There is intentionally no retry/reset or
        dispatch method. The future real boundary must atomically fence the core's
        actual attempt reservation, not mirror it into this separate journal.
        """
        attempt.validate(); aware(now)
        require(attempt.state=='SUBMISSION_UNKNOWN' and attempt.native_id is None
            and attempt.sequence==0 and attempt.order.account==lease.account
            and attempt.attempted_at==now,'RECOVERY_INITIAL_ATTEMPT_REQUIRED')
        with self.transaction():
            row = self._lease(lease)
            require(row['halted']==0 and row['coverage_from']<=now.timestamp()<row['coverage_until'],'OWNERSHIP_NOT_READY')
            require(not self.unresolved(lease.account),'UNRESOLVED_ORDER_PREVENTS_ADMISSION')
            self.conn.execute('INSERT INTO attempt VALUES(?,?,?,?,NULL,NULL)',
                (attempt.request_id,attempt.attempt_id,lease.account,self._encode(attempt)))
            self.conn.execute('UPDATE account SET generation=generation+1 WHERE account=?',(lease.account,))
            self._event(lease.account,'ATTEMPT_RESERVED',{'request_id':attempt.request_id,'attempt_id':attempt.attempt_id,
                'engine':lease.engine,'epoch':lease.epoch})

    def apply_batch(self,account,updates,now):
        """All-or-nothing exact recovery; rejected batch durably halts the account.

        Each update is (request_id, binding_receipt_or_None, observation_receipt).
        A binding alone cannot settle UNKNOWN. An empty batch is not reconciliation
        evidence. Successful observations never clear halt or change attempt IDs.
        """
        aware(now)
        try:
            require(type(updates) is tuple and 0<len(updates)<=100,'RECOVERY_NONEMPTY_BATCH_REQUIRED')
            with self.transaction():
                self.status(account); plans=[]; seen=set()
                for entry in updates:
                    require(type(entry) is tuple and len(entry)==3,'RECOVERY_INVALID_BATCH')
                    request_id,binding,observation=entry
                    require(request_id not in seen,'RECOVERY_DUPLICATE_REQUEST'); seen.add(request_id)
                    attempt=self.get(request_id)
                    require(attempt.order.account==account,'RECOVERY_ACCOUNT_MISMATCH')
                    if binding is not None:
                        attempt=bind_identity(attempt,binding,self.authority,self.coordinator,now)
                    plans.append(reconcile(attempt,observation,self.authority,self.coordinator,now))
                # No transition is written before the entire batch is validated.
                for attempt in plans:
                    self.conn.execute('UPDATE attempt SET data=?,native=?,native_collision=? WHERE request_id=?',
                        (self._encode(attempt),canonical(asdict(attempt.native_id)),self._collision(attempt.native_id),attempt.request_id))
                    self._event(account,'OBSERVATION',{'request_id':attempt.request_id,'state':attempt.state,
                        'filled_quantity':attempt.filled_quantity,'sequence':attempt.sequence,
                        'proof_digest':attempt.observation_digest})
                self.conn.execute('UPDATE account SET halted=1,generation=generation+1 WHERE account=?',(account,))
                return tuple(plans)
        except Exception:
            self.halt(account,'RECOVERY_EVIDENCE_REJECTED')
            raise

    def fill_requirements(self,account):
        return {a.request_id:a.filled_quantity
            for r in self.conn.execute('SELECT request_id FROM attempt WHERE account=?',(account,))
            if (a:=self.get(r[0])).filled_quantity}

    def close(self):
        require(self._scope is None,'OWNERSHIP_SCOPE_ACTIVE')
        self.conn.close()
