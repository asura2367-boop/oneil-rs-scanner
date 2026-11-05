"""SQLite synthetic integration. No sockets, KIS, PostgreSQL or real legacy code."""
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'execution_v2'))
import test_recovery as recovery_fixtures
ACCOUNT, NOW = recovery_fixtures.ACCOUNT, recovery_fixtures.NOW
from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.recovery import FixtureEvidenceAuthority
from oneil_runtime_v2.ownership import ENGINES, OfflineRecoveryCoordinator


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.f=recovery_fixtures.RecoveryTests(); self.f.setUp()
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'coordinator.sqlite'
        self.state=OfflineRecoveryCoordinator.initialize(self.path,self.f.authority)
        self.addCleanup(self.close)
        self.f.audience=self.state.coordinator
        self.state.register_fixture_account(ACCOUNT,boundary_manifest='b'*64)
        self.owner=AuthenticatedOwner('fixture-owner',ACCOUNT)
        self.lease=self.state.handover(ACCOUNT,'V2_SIMULATION',self.handover_proof(),NOW)

    def close(self):
        try: self.state.close()
        except sqlite3.ProgrammingError: pass

    def seal(self,kind,payload,now=NOW):
        return self.f.authority.seal(audience=self.state.coordinator,kind=kind,payload=payload,now=now)

    def handover_payload(self,engine='V2_SIMULATION'):
        row=self.state.status(ACCOUNT)
        return {'account':ACCOUNT,'generation':row['generation'],'from_holder':self.state.current_holder(row),
            'to_engine':engine,'boundary_manifest':'b'*64,'participants':list(ENGINES),
            'stopped_holder':self.state.current_holder(row),'legacy_shutdown':{'incarnation':row['incarnation'] if row['engine']=='LEGACY_SIMULATION' else 'c'*32,'evidence_digest':'d'*64},
            'broker_snapshot':{'digest':'e'*64,'unresolved':[]},'legacy_snapshot':{'digest':'f'*64,'unresolved':[]}}

    def handover_proof(self,engine='V2_SIMULATION',**changes):
        return self.seal('handover',dict(self.handover_payload(engine),**changes))

    def account_proof(self,**changes):
        value={'account':ACCOUNT,'generation':self.state.status(ACCOUNT)['generation'],
            'fill_watermarks':self.state.fill_requirements(ACCOUNT),'completeness':'COMPLETE',
            'external_unresolved':[],'account_snapshot_digest':'e'*64,'observed_at':NOW.isoformat()}
        return self.seal('account_reconciliation',dict(value,**changes))

    def resume(self):
        self.state.resume_simulation(self.lease,self.owner,NOW,account_evidence=self.account_proof())

    def reserve(self):
        self.resume(); self.state.reserve_fixture_attempt(self.lease,self.f.attempt,NOW)

    def update(self,**changes):
        binding=self.f.seal('native_binding',self.f.binding)
        observation=self.f.seal('order_observation',dict(self.f.payload,**changes))
        return (self.f.attempt.request_id,binding,observation)

    def test_handover_stays_halted_until_owner_and_causal_proof(self):
        self.assertEqual(self.state.status(ACCOUNT)['halted'],1)
        with self.assertRaisesRegex(Blocked,'AUTHENTICATED_FIXTURE'):
            self.state.resume_simulation(self.lease,self.owner,NOW)
        self.resume()
        with self.state.scope(self.lease,NOW): self.assertEqual(self.state._scope,self.lease)

    def test_both_engines_share_exact_owner_epoch_and_stale_lease_rejected(self):
        new=self.state.handover(ACCOUNT,'LEGACY_SIMULATION',self.handover_proof('LEGACY_SIMULATION'),NOW)
        self.assertGreater(new.epoch,self.lease.epoch)
        with self.assertRaisesRegex(Blocked,'OWNERSHIP_LOST'):
            self.state.resume_simulation(self.lease,self.owner,NOW,account_evidence=self.account_proof())
        self.lease=new; self.resume()
        with self.state.scope(new,NOW): pass

    def test_missing_legacy_proof_blocks_even_without_local_orders(self):
        for changes in [dict(legacy_shutdown={}),dict(participants=['V2_SIMULATION']),dict(boundary_manifest='f'*64),
                        dict(stopped_holder=None),dict(legacy_snapshot={'digest':'f'*64,'unresolved':['old-order']})]:
            with self.subTest(changes=changes),self.assertRaises(Blocked):
                self.state.handover(ACCOUNT,'LEGACY_SIMULATION',self.handover_proof('LEGACY_SIMULATION',**changes),NOW)
            self.assertEqual(self.state.status(ACCOUNT)['engine'],'V2_SIMULATION')

    def test_no_manifest_no_bootstrap(self):
        other='f'*64; self.state.register_fixture_account(other)
        value=self.handover_payload(); value.update(account=other,generation=0,from_holder=None,stopped_holder=None)
        with self.assertRaisesRegex(Blocked,'BOUNDARIES_UNPROVEN'):
            self.state.handover(other,'V2_SIMULATION',self.seal('handover',value),NOW)

    def test_expiry_never_steals_ownership(self):
        proof=self.handover_proof('LEGACY_SIMULATION')
        with self.assertRaisesRegex(Blocked,'EVIDENCE_STALE'):
            self.state.handover(ACCOUNT,'LEGACY_SIMULATION',proof,NOW+timedelta(seconds=11))
        self.assertEqual(self.state.status(ACCOUNT)['incarnation'],self.lease.incarnation)

    def test_proof_replay_or_local_change_invalidates_generation(self):
        receipt=self.handover_proof('LEGACY_SIMULATION')
        self.state.halt(ACCOUNT)
        with self.assertRaisesRegex(Blocked,'PROOF_SCOPE_MISMATCH'):
            self.state.handover(ACCOUNT,'LEGACY_SIMULATION',receipt,NOW)

    def test_exact_process_incarnation_required(self):
        old=self.handover_payload()['stopped_holder']; old['incarnation']='0'*32
        with self.assertRaisesRegex(Blocked,'PROCESS_EXIT_UNPROVEN'):
            self.state.handover(ACCOUNT,'LEGACY_SIMULATION',self.handover_proof('LEGACY_SIMULATION',stopped_holder=old),NOW)

    def test_unknown_attempt_survives_reopen_and_no_handover_or_second_attempt(self):
        self.reserve(); self.state.close(); self.state=OfflineRecoveryCoordinator(self.path,self.f.authority)
        self.assertEqual(self.state.get('request-1').state,'SUBMISSION_UNKNOWN')
        with self.assertRaisesRegex(Blocked,'UNRESOLVED_ORDER_PREVENTS_HANDOVER'):
            self.state.handover(ACCOUNT,'LEGACY_SIMULATION',self.handover_proof('LEGACY_SIMULATION'),NOW)
        with self.assertRaises(Blocked):
            self.state.reserve_fixture_attempt(self.lease,replace(self.f.attempt,request_id='request-2',attempt_id='attempt-2'),NOW)
        with self.assertRaisesRegex(Blocked,'UNRESOLVED_ORDER_PREVENTS_RESUME'): self.resume()

    def test_empty_history_and_missing_observation_never_settle_unknown(self):
        self.reserve()
        for updates in [(),(('request-1',self.f.seal('native_binding',self.f.binding),None),)]:
            with self.assertRaises(Blocked): self.state.apply_batch(ACCOUNT,updates,NOW)
            self.assertEqual(self.state.get('request-1').state,'SUBMISSION_UNKNOWN')
            self.assertEqual(self.state.status(ACCOUNT)['halted'],1)

    def test_partial_full_recovery_never_clears_halt_and_fills_persist(self):
        self.reserve()
        self.state.apply_batch(ACCOUNT,(self.update(status='PART_FILLED',filled_quantity=3),),NOW)
        with self.assertRaisesRegex(Blocked,'UNRESOLVED_ORDER_PREVENTS_RESUME'): self.resume()
        self.state.apply_batch(ACCOUNT,(self.update(sequence=2,status='FILLED',filled_quantity=10),),NOW)
        self.assertEqual(self.state.status(ACCOUNT)['halted'],1)
        self.assertEqual(self.state.fill_requirements(ACCOUNT),{'request-1':10})
        with self.assertRaisesRegex(Blocked,'CAUSALLY_RECONCILED'):
            self.state.resume_simulation(self.lease,self.owner,NOW,account_evidence=self.account_proof(fill_watermarks={}))
        self.resume()

    def test_duplicate_request_batch_atomic(self):
        self.reserve(); update=self.update(status='FILLED',filled_quantity=10)
        with self.assertRaisesRegex(Blocked,'DUPLICATE_REQUEST'):
            self.state.apply_batch(ACCOUNT,(update,update),NOW)
        self.assertEqual(self.state.get('request-1').state,'SUBMISSION_UNKNOWN')
        self.assertIsNone(self.state.get('request-1').native_id)

    def test_valid_first_invalid_second_batch_rolls_back_every_transition(self):
        self.reserve(); update=self.update(status='FILLED',filled_quantity=10)
        with self.assertRaisesRegex(Blocked,'ATTEMPT_MISSING'):
            self.state.apply_batch(ACCOUNT,(update,('nonexistent',None,None)),NOW)
        self.assertEqual(self.state.get('request-1').sequence,0)
        self.assertIsNone(self.state.get('request-1').native_id)

    def test_same_native_identity_cannot_bind_second_request(self):
        self.reserve()
        self.state.apply_batch(ACCOUNT,(self.update(status='FILLED',filled_quantity=10),),NOW); self.resume()
        second=replace(self.f.attempt,request_id='request-2',attempt_id='attempt-2')
        self.state.reserve_fixture_attempt(self.lease,second,NOW)
        binding=self.seal('native_binding',dict(self.f.binding,identity=second.identity()))
        observed=self.seal('order_observation',dict(self.f.payload,identity=second.identity()))
        with self.assertRaises(sqlite3.IntegrityError):
            self.state.apply_batch(ACCOUNT,(('request-2',binding,observed),),NOW)
        self.assertIsNone(self.state.get('request-2').native_id)
        self.assertEqual(self.state.status(ACCOUNT)['halted'],1)

    def test_zero_padded_native_alias_blocks_reuse_without_fuzzy_matching(self):
        self.reserve()
        self.state.apply_batch(ACCOUNT,(self.update(status='FILLED',filled_quantity=10),),NOW); self.resume()
        second=replace(self.f.attempt,request_id='request-2',attempt_id='attempt-2')
        self.state.reserve_fixture_attempt(self.lease,second,NOW)
        alias=dict(asdict(self.f.native),branch='1',number='1')
        binding=self.seal('native_binding',dict(self.f.binding,identity=second.identity(),native_id=alias))
        observed=self.seal('order_observation',dict(self.f.payload,identity=second.identity(),native_id=alias,
            status='FILLED',filled_quantity=10))
        with self.assertRaises(sqlite3.IntegrityError):
            self.state.apply_batch(ACCOUNT,(('request-2',binding,observed),),NOW)
        self.assertEqual(self.state.get('request-2').state,'SUBMISSION_UNKNOWN')
        self.assertEqual(self.state.fill_requirements(ACCOUNT),{'request-1':10})
        self.assertEqual(self.state.status(ACCOUNT)['halted'],1)

    def test_scope_holds_sqlite_lock_against_other_connection(self):
        self.resume(); other=OfflineRecoveryCoordinator(self.path,self.f.authority); self.addCleanup(other.close)
        with self.state.scope(self.lease,NOW):
            with self.assertRaises(sqlite3.OperationalError):
                other.register_fixture_account('e'*64,boundary_manifest='b'*64)
        other.register_fixture_account('e'*64,boundary_manifest='b'*64)

    def test_scope_disallows_reentry_and_expired_or_rollback_clock(self):
        self.resume()
        with self.state.scope(self.lease,NOW):
            with self.assertRaisesRegex(Blocked,'REENTRANT'):
                with self.state.scope(self.lease,NOW): pass
        for stamp in [NOW-timedelta(seconds=1),NOW+timedelta(seconds=10)]:
            with self.assertRaisesRegex(Blocked,'NOT_READY'):
                with self.state.scope(self.lease,stamp): pass

    def test_wrong_owner_and_account_evidence_rejected(self):
        for owner in [None,AuthenticatedOwner('other','b'*64)]:
            with self.assertRaisesRegex(Blocked,'AUTHENTICATED_SIMULATION_OWNER'):
                self.state.resume_simulation(self.lease,owner,NOW,account_evidence=self.account_proof())
        for changes in [dict(generation=0),dict(completeness='PARTIAL'),dict(external_unresolved=['order'])]:
            with self.assertRaisesRegex(Blocked,'RECONCILIATION_UNPROVEN'):
                self.state.resume_simulation(self.lease,self.owner,NOW,account_evidence=self.account_proof(**changes))

    def test_rejected_new_account_proof_halts_previously_active_account(self):
        self.resume()
        with self.assertRaisesRegex(Blocked,'RECONCILIATION_UNPROVEN'):
            self.state.resume_simulation(self.lease,self.owner,NOW,
                account_evidence=self.account_proof(external_unresolved=['broker-order']))
        self.assertEqual(self.state.status(ACCOUNT)['halted'],1)
        with self.assertRaisesRegex(Blocked,'NOT_READY'):
            with self.state.scope(self.lease,NOW): pass

    def test_current_legacy_holder_shutdown_must_match_exact_incarnation(self):
        self.lease=self.state.handover(ACCOUNT,'LEGACY_SIMULATION',self.handover_proof('LEGACY_SIMULATION'),NOW)
        with self.assertRaisesRegex(Blocked,'LEGACY_PROCESS_EXIT_UNPROVEN'):
            self.state.handover(ACCOUNT,'V2_SIMULATION',self.handover_proof(
                legacy_shutdown={'incarnation':'0'*32,'evidence_digest':'d'*64}),NOW)
        new=self.state.handover(ACCOUNT,'V2_SIMULATION',self.handover_proof(),NOW)
        self.assertGreater(new.epoch,self.lease.epoch)

    def test_expired_causal_account_capture_blocks_even_fresh_signature(self):
        with self.assertRaisesRegex(Blocked,'RECONCILIATION_STALE'):
            self.state.resume_simulation(self.lease,self.owner,NOW,
                account_evidence=self.account_proof(observed_at=(NOW-timedelta(seconds=11)).isoformat()))
        self.assertEqual(self.state.status(ACCOUNT)['halted'],1)

    def test_corrupt_or_missing_or_wrong_issuer_never_resets(self):
        with self.assertRaises(Blocked): OfflineRecoveryCoordinator(self.path,FixtureEvidenceAuthority())
        with self.assertRaises(Blocked): OfflineRecoveryCoordinator(self.path.parent/'missing',self.f.authority)
        with self.assertRaises(FileExistsError): OfflineRecoveryCoordinator.initialize(self.path,self.f.authority)
        self.state.conn.execute('UPDATE metadata SET version=2')
        with self.assertRaises(Blocked): OfflineRecoveryCoordinator(self.path,self.f.authority)

    def test_audit_is_append_only(self):
        for sql in ['DELETE FROM event','UPDATE event SET kind="FORGED"']:
            with self.assertRaisesRegex(sqlite3.IntegrityError,'AUDIT_IMMUTABLE'): self.state.conn.execute(sql)

    def test_persistence_failure_poison_prevents_admission(self):
        self.resume(); self.state.conn.execute('DROP TABLE event')
        with self.assertRaises(sqlite3.OperationalError): self.state.halt(ACCOUNT)
        self.assertTrue(self.state._poisoned)
        with self.assertRaisesRegex(Blocked,'UNAVAILABLE'):
            with self.state.scope(self.lease,NOW): pass

    def test_entire_flow_has_no_network_or_live_executor(self):
        with patch('socket.socket',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            self.reserve(); self.state.apply_batch(ACCOUNT,(self.update(status='FILLED',filled_quantity=10),),NOW)
        self.assertFalse(self.state.live_capable)
        self.assertFalse(hasattr(self.state,'submit'))

if __name__=='__main__': unittest.main()
