"""Offline SQLite + scripted-core rehearsals, NOT PostgreSQL/Windows proof."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'policy_v2'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'execution_v2'))
import test_policy_admission as policy_fixtures
from oneil_execution_v2.api import AuthenticatedOwner
from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import SimulationPolicySource
from oneil_runtime_v2.health import ClockSample, Evidence, HealthSample
from oneil_runtime_v2.intent import encode_intent, decode_intent
from oneil_runtime_v2.orchestrator import SimulationRuntime, SimulationRuntimeGuard
from oneil_runtime_v2.state import RuntimeState
from policy_support import ACCOUNT, NOW


class RuntimeStore(policy_fixtures.PolicyStore):
    def bind_runtime_guard(self, guard):
        self.runtime_guard = guard

    def recovery_requests(self, account):
        return [r for r in self.rows if r['attempts'] == 0 and r['state'] in ('QUEUED','VALIDATING')]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        fixture = policy_fixtures.PolicyAdmissionTests('test_exact_policy_admitted_and_full_evidence_audited')
        fixture.setUp()
        fixture.store = RuntimeStore(fixture.p)
        fixture.store.control_row['mode'] = 'SIMULATION'
        fixture.broker.now = fixture.store.now
        fixture.worker = Worker(fixture.store, fixture.broker, policy_source=fixture.source)
        fixture.queue()
        self.f = fixture
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'runtime.sqlite'
        self.state = RuntimeState.initialize(self.path)
        self.addCleanup(self.close_state)
        self.state.register_simulation_account(ACCOUNT)
        self.clock_value = ClockSample(NOW.timestamp(), 1000, 900)
        self.runtime = SimulationRuntime(self.state, fixture.worker, ACCOUNT, lambda: self.clock_value)
        self.lease = self.runtime.start()
        self.state.save_intent(str(fixture.row['request_id']), fixture.admission)
        self.owner = AuthenticatedOwner('simulation-owner', ACCOUNT)

    def close_state(self):
        try: self.state.close()
        except sqlite3.ProgrammingError: pass

    def sample(self, **changes):
        c = self.clock_value
        return replace(HealthSample(c, True, c.utc, Evidence(True,c.utc), Evidence(True,c.utc), 2**31, False), **changes)

    def advance(self, seconds=1, *, awake=None):
        old = self.clock_value
        self.clock_value = ClockSample(old.utc+seconds, old.elapsed+seconds,
                                     old.awake+(seconds if awake is None else awake))
        self.f.store.clock += timedelta(seconds=seconds)

    def ready(self):
        self.assertEqual(self.runtime.step(self.sample()), 'RECONCILED_ONLY')
        self.runtime.restore_pending({str(self.f.row['request_id']): self.f.context})
        self.advance()
        self.assertEqual(self.runtime.step(self.sample()), 'RECONCILED_ONLY')
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'AWAITING_OWNER')
        # Only a separate trusted simulated-owner resume can clear core halt.
        self.f.store.control_row['halted'] = False
        self.runtime.activate_simulation(self.owner, expected_control_revision=7)
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'ACTIVE_SIMULATION')

    def test_start_halts_core_discards_sources_and_does_not_claim(self):
        self.assertTrue(self.f.store.control_row['halted'])
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            self.f.worker.policy_source.read(self.f.row['request_id'])
        for _ in range(3):
            self.runtime.step(self.sample()); self.advance()
        self.assertEqual(self.f.row['state'], 'QUEUED')
        self.assertEqual(self.f.broker.calls, [])

    def test_full_strict_simulation_path_is_fenced_and_audited(self):
        self.ready()
        def during_submit():
            self.assertEqual(self.state._scope, self.lease)
            self.assertEqual(self.f.row['attempts'], 1)
            self.assertEqual(self.f.row['admission_evidence']['runtime']['epoch'], self.lease.epoch)
        self.f.broker.submit_hook = during_submit
        self.assertEqual(self.runtime.step(self.sample()), 'ACKNOWLEDGED')
        self.assertEqual(len(self.f.broker.calls), 1)
        self.assertFalse(self.f.row['admission_evidence']['runtime']['live_capable'])

    def test_health_recovery_never_reactivates(self):
        self.ready()
        self.runtime.step(self.sample(database=Evidence(False,self.clock_value.utc)))
        self.assertEqual(self.f.broker.calls, [])
        self.advance()
        self.runtime.step(self.sample())
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'HALTED')
        self.assertTrue(self.f.store.control_row['halted'])

    def test_core_resume_alone_is_not_runtime_activation(self):
        self.runtime.step(self.sample())
        self.runtime.restore_pending({str(self.f.row['request_id']): self.f.context})
        self.advance(); self.runtime.step(self.sample())
        self.f.store.control_row['halted'] = False
        self.assertEqual(self.runtime.step(self.sample()), 'RECONCILED_ONLY')
        self.assertEqual(self.f.broker.calls, [])

    def test_owner_account_mode_revision_and_core_resume_required(self):
        self.runtime.step(self.sample())
        self.runtime.restore_pending({str(self.f.row['request_id']): self.f.context})
        self.advance(); self.runtime.step(self.sample())
        with self.assertRaisesRegex(Blocked, 'CORE_OWNER_RESUME_REQUIRED'):
            self.runtime.activate_simulation(self.owner, expected_control_revision=7)
        self.f.store.control_row['halted'] = False
        for owner, revision in ((None,7),(AuthenticatedOwner('wrong','b'*64),7),(self.owner,8),(self.owner,True)):
            with self.assertRaises(Blocked): self.runtime.activate_simulation(owner,expected_control_revision=revision)
        self.f.store.control_row['mode'] = 'LIVE'
        with self.assertRaises(Blocked): self.runtime.activate_simulation(self.owner,expected_control_revision=7)

    def test_final_boundary_rechecks_stale_health(self):
        self.ready()
        self.f.store.before_admission = lambda: self.advance(16)
        self.assertEqual(self.runtime.step(self.sample()), 'BLOCKED')
        self.assertEqual(self.f.row['attempts'], 0)
        self.assertEqual(self.f.broker.calls, [])
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'HALTED')

    def test_sleep_between_validation_and_submit_preserves_unknown_without_call(self):
        self.ready()
        original = self.f.store.reserve_attempt
        def delayed(*args):
            result = original(*args)
            self.advance(5, awake=0)
            return result
        self.f.store.reserve_attempt = delayed
        self.assertEqual(self.runtime.step(self.sample()), 'SUBMISSION_UNKNOWN')
        self.assertEqual(self.f.row['attempts'], 1)
        self.assertEqual(self.f.broker.calls, [])
        self.runtime.step(self.sample())
        self.assertEqual(self.f.broker.calls, [])

    def test_continuously_healthy_reconciliation_refreshes_beyond_fifteen_seconds(self):
        self.ready()
        self.f.row['state'] = 'CANCELLED'  # no queued order; this tests service health only
        for _ in range(8):
            self.advance(4)
            self.assertEqual(self.runtime.step(self.sample()), 'IDLE')
            self.assertEqual(self.state.status(ACCOUNT)['phase'], 'ACTIVE_SIMULATION')
            self.assertEqual(self.state.status(ACCOUNT)['reconciled_at'], self.f.store.now().timestamp())
        self.assertEqual(self.f.broker.calls, [])

    def test_failed_history_does_not_refresh_reconciliation_or_claim(self):
        self.ready()
        self.f.broker.history_complete = False
        self.advance()
        self.assertEqual(self.runtime.step(self.sample()), 'RECONCILIATION_BLOCKED')
        self.assertIsNone(self.state.status(ACCOUNT)['reconciled_at'])
        self.assertEqual(self.f.row['state'], 'QUEUED')

    def test_core_host_clock_disagreement_blocks_final_gate(self):
        self.ready()
        self.f.store.before_admission = lambda: setattr(self.f.store, 'clock', self.f.store.clock+timedelta(seconds=3))
        self.assertEqual(self.runtime.step(self.sample()), 'BLOCKED')
        self.assertEqual(self.f.row['reason'], 'RUNTIME_CORE_CLOCK_DISAGREEMENT')
        self.assertEqual(self.f.broker.calls, [])

    def test_loss_of_policy_after_activation_cannot_use_core_fixture_fallback(self):
        self.ready()
        self.f.worker.policy_source = SimulationPolicySource()
        self.assertEqual(self.runtime.step(self.sample()), 'BLOCKED')
        self.assertEqual(self.f.row['reason'], 'POLICY_ADMISSION_REQUIRED')
        self.assertEqual(self.f.broker.calls, [])

    def test_reopened_state_does_not_grant_existing_owner_claim(self):
        self.ready()
        other = RuntimeState(self.path)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(Blocked, 'RUNTIME_ACCOUNT_ALREADY_OWNED'):
            other.start(ACCOUNT,now=self.clock_value.utc)
        self.assertEqual(other.status(ACCOUNT)['phase'], 'ACTIVE_SIMULATION')
        # The durable record is not permission for a new runtime without its lease.
        with self.assertRaisesRegex(Blocked, 'RUNTIME_ADMISSION_SCOPE_REQUIRED'):
            self.guard_check_outside_scope()

    def guard_check_outside_scope(self):
        self.runtime.guard.check(self.f.store.now())

    def test_changed_core_revision_invalidates_prior_runtime_activation(self):
        self.ready()
        self.f.store.control_row['revision'] = 8
        self.assertEqual(self.runtime.step(self.sample()), 'RUNTIME_BLOCKED')
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'HALTED')
        self.assertEqual(self.f.row['state'], 'QUEUED')
        self.assertEqual(self.f.broker.calls, [])

    def test_core_halt_clears_runtime_activation(self):
        self.ready()
        self.f.store.control_row['halted'] = True
        self.assertEqual(self.runtime.step(self.sample()), 'HALTED')
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'HALTED')
        self.assertIsNone(self.state.status(ACCOUNT)['activation_revision'])
        self.assertEqual(self.f.broker.calls, [])

    def test_stale_reconciliation_cannot_activate(self):
        self.runtime.step(self.sample())
        self.runtime.restore_pending({str(self.f.row['request_id']): self.f.context})
        self.advance(); self.runtime.step(self.sample()); self.advance(16)
        self.f.store.control_row['halted'] = False
        with self.assertRaises(Blocked): self.runtime.activate_simulation(self.owner,expected_control_revision=7)
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'HALTED')

    def test_malformed_health_persistence_failure_fails_closed(self):
        self.ready()
        with self.assertRaises(ValueError): self.runtime.step(self.sample(free_bytes=float('nan')))
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'HALTED')
        self.assertTrue(self.f.store.control_row['halted'])
        self.assertEqual(self.f.broker.calls, [])

    def test_unresolved_never_reconstructed_or_retried(self):
        self.ready(); self.f.broker.behavior = 'timeout_before_accept'
        self.assertEqual(self.runtime.step(self.sample()), 'SUBMISSION_UNKNOWN')
        self.runtime.restore_pending({})
        self.advance()
        self.assertEqual(self.runtime.step(self.sample()), 'UNRESOLVED_ORDER')
        self.assertEqual(self.state.status(ACCOUNT)['phase'], 'HALTED')
        self.assertEqual(len(self.f.broker.calls), 1)

    def test_missing_or_changed_pending_intent_aborts_whole_restore(self):
        for contexts in ({}, {'other': self.f.context}):
            with self.assertRaisesRegex(Blocked,'EXPLICIT_PENDING_CONTEXTS_REQUIRED'):
                self.runtime.restore_pending(contexts)
        self.f.row['payload']['quantity'] += 1
        with self.assertRaisesRegex(Blocked,'DURABLE_POLICY_INTENT_REQUIRED'):
            self.runtime.restore_pending({str(self.f.row['request_id']): self.f.context})
        with self.assertRaises(Blocked): self.f.worker.policy_source.read(self.f.row['request_id'])

    def test_intent_reopens_original_without_authorization_or_expiry_extension(self):
        original = self.f.admission
        self.state.close(); self.state = RuntimeState(self.path)
        fresh_context = replace(self.f.context, control_revision=99)
        restored = self.state.load_intent(str(self.f.row['request_id']),account=ACCOUNT,
            order_digest=original.proposal.order.digest,current_context=fresh_context)
        self.assertEqual(restored.original_context, original.original_context)
        self.assertEqual(restored.context, fresh_context)
        self.assertEqual(restored.proposal.order.expires_at, original.proposal.order.expires_at)
        self.assertFalse(restored.proposal.authorized)

    def test_intent_immutable_and_codec_rejects_corruption_unknown_types(self):
        with self.assertRaisesRegex(Blocked,'POLICY_INTENT_IMMUTABLE'):
            self.state.save_intent(str(self.f.row['request_id']), replace(self.f.admission,mode='MANUAL_ADD'))
        text,digest = encode_intent(self.f.admission)
        with self.assertRaisesRegex(Blocked,'INTENT_DIGEST_MISMATCH'):
            decode_intent(text+' ',digest,current_context=self.f.context)
        for value in ('{"version":1,"version":1,"intent":null}',
                      '{"version":1,"intent":{"type":"os.system","value":"x"}}'):
            with self.assertRaises(Blocked): decode_intent(value, hashlib.sha256(value.encode()).hexdigest(),current_context=self.f.context)
        with self.assertRaisesRegex(Blocked,'EXPLICIT_CURRENT_POLICY_CONTEXT_REQUIRED'):
            decode_intent(text,digest,current_context=None)

    def test_different_policy_cannot_be_substituted_on_restart(self):
        text,digest = encode_intent(self.f.admission)
        with self.assertRaisesRegex(Blocked,'POLICY_CHANGED'):
            decode_intent(text,digest,current_context=replace(self.f.context,policy=replace(self.f.p,reserve_pct=99)))

    def test_stop_survives_reopen_and_requires_owner_start(self):
        self.runtime.stop()
        self.state.close(); self.state = RuntimeState(self.path)
        with self.assertRaisesRegex(Blocked,'AUTHENTICATED_SIMULATION_OWNER_REQUIRED'):
            self.state.start(ACCOUNT,now=self.clock_value.utc)
        lease = self.state.start(ACCOUNT,now=self.clock_value.utc,owner_request=self.owner)
        self.assertGreater(lease.epoch,self.lease.epoch)
        self.assertEqual(self.state.status(ACCOUNT)['phase'],'RECONCILING')

    def test_restart_budget_and_backoff_persist_cannot_reset_by_reopen(self):
        lease = self.lease
        now = self.clock_value.utc
        for delay in (5,10,20):
            plan = self.state.record_exit(lease,exit_code=1,now=now)
            self.assertEqual(plan.delay_seconds,delay)
            self.state.close(); self.state = RuntimeState(self.path)
            with self.assertRaisesRegex(Blocked,'RUNTIME_RESTART_BACKOFF'):
                self.state.start(ACCOUNT,now=now+delay-0.01)
            now += delay
            lease = self.state.start(ACCOUNT,now=now)
            self.assertEqual(self.state.status(ACCOUNT)['phase'],'RECONCILING')
        self.assertIn('EXHAUSTED',self.state.record_exit(lease,exit_code=1,now=now).action)
        with self.assertRaisesRegex(Blocked,'RUNTIME_START_REQUIRES_OPERATOR'):
            self.state.start(ACCOUNT,now=now+10000,owner_request=self.owner)

    def test_stale_heartbeat_never_transfers_ownership(self):
        with self.assertRaisesRegex(Blocked,'RUNTIME_ACCOUNT_ALREADY_OWNED'):
            self.state.start(ACCOUNT,engine='LEGACY_SIMULATION',now=self.clock_value.utc+100000)
        with self.assertRaisesRegex(Blocked,'CONFIRMED_PROCESS_EXIT_REQUIRED'):
            self.state.record_exit(self.lease,exit_code=None,now=self.clock_value.utc)

    def test_cooperative_legacy_contends_same_fence_held_through_effect(self):
        other = RuntimeState(self.path)
        self.addCleanup(other.close)
        with self.state.scope(self.lease):
            with self.assertRaises(sqlite3.OperationalError):
                other.record_exit(self.lease,exit_code=1,now=self.clock_value.utc)
        plan = self.state.record_exit(self.lease,exit_code=1,now=self.clock_value.utc)
        legacy = other.start(ACCOUNT,engine='LEGACY_SIMULATION',now=self.clock_value.utc+plan.delay_seconds)
        with self.assertRaisesRegex(Blocked,'RUNTIME_OWNERSHIP_LOST'):
            with self.state.scope(self.lease): pass
        self.assertEqual(other.status(ACCOUNT)['engine'],'LEGACY_SIMULATION')
        with self.assertRaises(Blocked): SimulationRuntimeGuard(other,legacy,lambda:self.clock_value)

    def test_safety_trip_is_committed_even_when_guard_scope_raises(self):
        with self.assertRaises(RuntimeError):
            with self.state.scope(self.lease):
                self.state.trip(self.lease,'SIMULATED_FAILURE')
                raise RuntimeError('fixture')
        other = RuntimeState(self.path)
        self.addCleanup(other.close)
        self.assertEqual(other.status(ACCOUNT)['phase'],'HALTED')

    def test_fixture_worker_factory_rejected(self):
        with self.assertRaisesRegex(Blocked,'STRICT_POLICY_WORKER_REQUIRED'):
            SimulationRuntime(self.state,Worker(self.f.store,self.f.broker,core_fixture_only=True),ACCOUNT,lambda:self.clock_value)

    def test_loop_obeys_stop_without_claiming_and_retains_stop(self):
        class Stop:
            def is_set(self): return True
        self.runtime.run(Stop(),lambda: self.sample())
        self.assertEqual(self.state.status(ACCOUNT)['phase'],'STOPPED')
        self.assertEqual(self.f.broker.calls,[])

    def test_loop_failure_debits_once_and_has_no_automatic_restart(self):
        class Stop:
            def is_set(self): return False
            def wait(self, _): raise AssertionError('failed step must not continue')
        with self.assertRaises(ValueError):
            self.runtime.run(Stop(),lambda: self.sample(free_bytes=float('nan')))
        row = self.state.status(ACCOUNT)
        self.assertEqual((row['phase'], row['restart_count']), ('RESTART_WAIT', 1))
        self.assertIsNone(row['owner'])
        self.assertEqual(self.f.broker.calls, [])

    def test_failed_state_write_never_runs_worker(self):
        self.ready()
        self.state.conn.execute('PRAGMA query_only=ON')
        with self.assertRaises(sqlite3.OperationalError): self.runtime.step(self.sample())
        self.assertEqual(self.f.broker.calls, [])
        self.assertEqual(self.f.row['state'], 'QUEUED')
        self.assertTrue(self.f.store.control_row['halted'])
        self.assertTrue(self.runtime.guard.poisoned)
        self.state.conn.execute('PRAGMA query_only=OFF')

    def test_missing_corrupt_or_existing_journal_never_reset(self):
        with self.assertRaises(FileExistsError): RuntimeState.initialize(self.path)
        missing = Path(self.tmp.name)/'missing.sqlite'
        with self.assertRaises(Blocked): RuntimeState(missing)
        self.assertFalse(missing.exists())
        missing.write_bytes(b'broken')
        with self.assertRaises(sqlite3.DatabaseError): RuntimeState(missing)
        self.assertEqual(missing.read_bytes(),b'broken')


if __name__ == '__main__': unittest.main()
