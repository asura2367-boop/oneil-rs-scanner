"""Socket-free authenticated control + runtime + strict worker integration.

Real local SQLite journals, policy planners, coordinator, runtime and simulator;
explicit core SQL double only. This is not PostgreSQL or Windows process proof.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_policy_flow as flows
from oneil_control_v2 import SimulationPolicyCoordinator
from oneil_control_v2.bridge import CoreSimulationBackend
from oneil_execution_v2.domain import Blocked, Order, require
from oneil_execution_v2.worker import Worker
from oneil_policy_v2.admission import SimulationPolicySource
from oneil_runtime_v2.health import ClockSample, Evidence, HealthSample
from oneil_runtime_v2.intent import encode_intent
from oneil_runtime_v2.orchestrator import SimulationRuntime, SimulationRuntimeGuard
from oneil_runtime_v2.state import RuntimeState


class RuntimeCoreDouble(flows.ControlPolicyStoreDouble):
    def __init__(self, policy):
        super().__init__(policy)
        self.runtime_guard = None
        # Real halt/resume revision semantics: startup 5->6; owner resume 6->7.
        self.control_row['revision'] = 5

    def bind_runtime_guard(self, guard):
        require(type(guard) is SimulationRuntimeGuard and self.runtime_guard is None,
                'STRICT_RUNTIME_GUARD_REQUIRED')
        self.runtime_guard = guard

    def fail_closed(self, account, reason):
        if not self.control_row['halted']:
            self.control_row.update(halted=True, revision=self.control_row['revision'] + 1)
        self.events.append(reason)

    def set_halt(self, account, *, halted, expected_revision):
        require(self.control_row['revision'] == expected_revision, 'CONTROL_REVISION_CONFLICT')
        self.control_row.update(halted=halted, revision=expected_revision + 1)
        return expected_revision + 1

    def recovery_requests(self, account):
        return [r for r in self.rows if r['attempts'] == 0 and r['state'] in ('QUEUED', 'VALIDATING')]

    def reserve_attempt(self, row, day, policy, validate):
        require(validate.runtime_guard is self.runtime_guard, 'RUNTIME_ADMISSION_GUARD_REQUIRED')
        return super().reserve_attempt(row, day, policy, validate)


class RuntimePolicyFlowTests(unittest.TestCase):
    def setUp(self):
        socket_guard = patch('socket.socket', side_effect=AssertionError('NETWORK FORBIDDEN'))
        socket_guard.start()
        self.addCleanup(socket_guard.stop)
        self.h = flows.PolicyFlowTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.bind_runtime()

    def bind_runtime(self, *, manual_mode=False):
        h = self.h
        if manual_mode:
            h.configure(manual_mode=True)
        h.store = RuntimeCoreDouble(h.f.p)
        h.f.broker.now = h.store.now
        h.worker = Worker(h.store, h.f.broker, policy_source=SimulationPolicySource())
        with patch('oneil_control_v2.bridge.PostgresStore', RuntimeCoreDouble):
            h.backend = CoreSimulationBackend(h.store)
        self.path = Path(h.tmp.name) / ('runtime-manual.sqlite' if manual_mode else 'runtime.sqlite')
        self.state = RuntimeState.initialize(self.path)
        self.addCleanup(self.close_state, self.state)
        self.state.register_simulation_account(flows.ACCOUNT)
        self.clock = ClockSample(flows.NOW.timestamp(), 1000, 900)
        self.runtime = SimulationRuntime(self.state, h.worker, flows.ACCOUNT, lambda: self.clock)
        self.lease = self.runtime.start()
        h.coordinator = SimulationPolicyCoordinator(state=h.state, backend=h.backend, worker=h.worker,
                                                    runtime=self.runtime)
        h.app.backend = h.backend
        self.ready_done = False

    @staticmethod
    def close_state(state):
        try:
            state.close()
        except sqlite3.ProgrammingError:
            pass

    def advance(self, seconds=1):
        from datetime import timedelta
        self.clock = ClockSample(self.clock.utc + seconds, self.clock.elapsed + seconds, self.clock.awake + seconds)
        self.h.store.clock += timedelta(seconds=seconds)

    def sample(self):
        c = self.clock
        return HealthSample(c, True, c.utc, Evidence(True, c.utc), Evidence(True, c.utc), 2**31, False)

    def awaiting_owner(self):
        self.assertEqual(self.runtime.step(self.sample()), 'RECONCILED_ONLY')
        self.runtime.restore_pending({})
        self.advance()
        self.assertEqual(self.runtime.step(self.sample()), 'RECONCILED_ONLY')
        self.assertEqual(self.state.status(flows.ACCOUNT)['phase'], 'AWAITING_OWNER')
        self.assertEqual(self.h.backend.api.resume_simulation(self.h.owner, 6), 7)

    def ready(self):
        if not self.ready_done:
            self.awaiting_owner()
            self.runtime.activate_simulation(self.h.owner, expected_control_revision=7)
            self.ready_done = True

    def preview(self):
        self.ready()
        status, preview = self.h.preview()
        self.assertEqual(status, 201)
        return preview

    def confirm(self):
        preview = self.preview()
        command = self.h.command(preview)
        result = self.h.post(command, key='runtime-command-0001')
        self.assertEqual(result[0], 202)
        return result[1], command

    def assert_unknown(self, command):
        self.assertEqual(self.h.post(command, key='runtime-unknown-0001'),
                         (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        self.assertTrue(self.runtime.guard.poisoned)
        self.assertTrue(self.h.store.control_row['halted'])
        self.assertEqual(self.h.f.broker.calls, [])

    def test_automatic_api_runtime_archive_and_strict_worker_submit(self):
        result, command = self.confirm()
        h = self.h
        saved = self.state.load_intent(result['request_id'], account=flows.ACCOUNT,
            order_digest=Order.from_payload(h.store.rows[0]['payload']).digest, current_context=h.f.context)
        self.assertEqual(encode_intent(saved), encode_intent(h.worker.policy_source.read(result['request_id'])))
        self.assertEqual(self.state.status(flows.ACCOUNT)['phase'], 'ACTIVE_SIMULATION')
        self.assertEqual(self.runtime.step(self.sample()), 'ACKNOWLEDGED')
        audit = h.store.rows[0]['admission_evidence']
        self.assertEqual(audit['runtime']['epoch'], self.lease.epoch)
        self.assertEqual(h.post(command, key='runtime-command-0001'), (202, result))
        self.assertEqual(len(h.f.broker.calls), 1)

    def test_manual_api_runtime_uses_exact_manual_id_and_quantity(self):
        self.bind_runtime(manual_mode=True)
        result, _ = self.confirm()
        row = self.h.store.rows[0]
        self.assertEqual(row['idempotency_key'], 'policy-manual-' + self.h.manual.request_id)
        self.assertEqual(row['payload']['quantity'], self.h.manual.quantity)
        self.assertEqual(self.runtime.step(self.sample()), 'ACKNOWLEDGED')
        self.assertEqual(self.state.conn.execute('SELECT count(*) FROM intent').fetchone()[0], 1)

    def test_runtime_core_and_archive_lock_order_enforced(self):
        preview = self.preview()
        h = self.h
        account_lock = h.store.account_lock
        save_intent = self.state.save_intent
        events = []
        @contextmanager
        def account_scope(account):
            self.assertEqual(self.state._scope, self.lease)
            self.assertTrue(self.state.conn.in_transaction)
            events.append('ACCOUNT_ENTER')
            with account_lock(account) as acquired:
                yield acquired
            self.assertEqual(self.state._scope, self.lease)
            events.append('ACCOUNT_EXIT')
        def archive(request_id, admission, *, lease=None):
            self.assertIs(lease, self.lease)
            self.assertTrue(h.store.locked)
            self.assertGreater(h.store.conn.depth, 0)
            result = save_intent(request_id, admission, lease=lease)
            events.append('ARCHIVED_IN_RUNTIME_SCOPE')
            return result
        with patch.object(h.store, 'account_lock', side_effect=account_scope), \
             patch.object(self.state, 'save_intent', side_effect=archive):
            self.assertEqual(h.post(h.command(preview))[0], 202)
        self.assertEqual(events, ['ACCOUNT_ENTER', 'ARCHIVED_IN_RUNTIME_SCOPE', 'ACCOUNT_EXIT'])
        self.assertIsNone(self.state._scope)
        self.assertFalse(self.state.conn.in_transaction)
        independent = RuntimeState(self.path)
        try:
            self.assertEqual(independent.conn.execute('SELECT count(*) FROM intent').fetchone()[0], 1)
        finally:
            independent.close()

    def test_startup_and_core_resume_alone_never_activate_from_proposal(self):
        with self.assertRaisesRegex(Blocked, 'HALTED|CONTROL_REVISION_CONFLICT'):
            self.h.propose()
        self.awaiting_owner()
        with self.assertRaisesRegex(Blocked, 'RUNTIME_OWNER_ACTIVATION_REQUIRED'):
            self.h.propose()
        self.assertNotEqual(self.state.status(flows.ACCOUNT)['phase'], 'ACTIVE_SIMULATION')
        self.assertEqual(self.h.store.rows, [])

    def test_preview_and_refresh_cannot_reactivate_runtime(self):
        self.ready()
        proposal_id = self.h.propose()
        self.state.trip(self.lease, 'TEST_OWNER_HALT')
        self.assertEqual(self.h.preview(proposal_id)[0], 409)
        with self.assertRaises(Blocked):
            self.h.coordinator.refresh(self.h.owner, proposal_id, self.h.f.context)
        self.assertEqual(self.state.status(flows.ACCOUNT)['phase'], 'HALTED')
        self.assertEqual(self.h.store.rows, [])

    def test_missing_explicit_runtime_and_unstarted_runtime_are_refused(self):
        h = self.h
        h.backend.policy_coordinator = None
        with self.assertRaisesRegex(Blocked, 'EXPLICIT_STARTED_RUNTIME_REQUIRED'):
            SimulationPolicyCoordinator(state=h.state, backend=h.backend, worker=h.worker)
        not_started = SimulationRuntime(self.state, Worker(h.store, h.f.broker,
            policy_source=SimulationPolicySource()), flows.ACCOUNT, lambda: self.clock)
        with self.assertRaisesRegex(Blocked, 'EXPLICIT_STARTED_RUNTIME_REQUIRED'):
            SimulationPolicyCoordinator(state=h.state, backend=h.backend, worker=h.worker, runtime=not_started)

    def test_replaced_worker_store_runtime_guard_or_lease_is_refused(self):
        self.ready()
        h = self.h
        original = self.runtime.guard
        for target, attribute, replacement in ((h.worker, 'runtime_guard', None),
            (h.store, 'runtime_guard', None), (self.runtime, 'guard', object()),
            (self.runtime, 'worker', object()), (original, 'lease', replace(self.lease))):
            with self.subTest(attribute=attribute):
                with patch.object(target, attribute, replacement):
                    with self.assertRaisesRegex(Blocked, 'RUNTIME_BINDING_CHANGED'):
                        h.propose()
        self.assertEqual(h.store.rows, [])

    def test_changed_activation_revision_blocks_after_core_lock(self):
        preview = self.preview()
        self.h.store.conn.after_lock = lambda: self.state.conn.execute(
            'UPDATE runtime SET activation_revision=6 WHERE account=?', (flows.ACCOUNT,))
        self.assertEqual(self.h.post(self.h.command(preview))[0], 409)
        self.assertEqual(self.h.store.rows, [])
        self.assertEqual(self.state.status(flows.ACCOUNT)['phase'], 'HALTED')

    def test_stale_health_after_lock_blocks_without_enqueue(self):
        preview = self.preview()
        self.h.store.conn.after_lock = lambda: self.state.conn.execute(
            'UPDATE runtime SET healthy=0 WHERE account=?', (flows.ACCOUNT,))
        self.assertEqual(self.h.post(self.h.command(preview))[0], 409)
        self.assertEqual(self.h.store.rows, [])
        self.assertTrue(self.h.store.control_row['halted'])

    def test_health_change_during_archive_is_unknown_and_halted(self):
        preview = self.preview()
        command = self.h.command(preview)
        save = self.state.save_intent
        def changed(*args, **kwargs):
            result = save(*args, **kwargs)
            self.state.conn.execute('UPDATE runtime SET healthy=0 WHERE account=?', (flows.ACCOUNT,))
            return result
        with patch.object(self.state, 'save_intent', side_effect=changed):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assert_unknown(command)
        self.assertEqual(self.state.status(flows.ACCOUNT)['phase'], 'HALTED')

    def test_revoke_session_during_archive_is_unknown_and_halted(self):
        preview = self.preview()
        command = self.h.command(preview)
        save = self.state.save_intent
        def revoked(*args, **kwargs):
            result = save(*args, **kwargs)
            self.h.state.revoke_session(self.h.record.token_digest)
            return result
        with patch.object(self.state, 'save_intent', side_effect=revoked):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assertTrue(self.runtime.guard.poisoned)
        self.assertTrue(self.h.store.control_row['halted'])
        self.assertEqual(self.h.f.broker.calls, [])

    def test_archive_failure_cannot_install_admission_or_send(self):
        preview = self.preview()
        command = self.h.command(preview)
        with patch.object(self.state, 'save_intent', side_effect=RuntimeError('synthetic archive full')):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assert_unknown(command)
        self.assertEqual(self.state.conn.execute('SELECT count(*) FROM intent').fetchone()[0], 0)
        self.assertEqual(self.h.store.rows, [])

    def test_registration_failure_can_leave_only_non_authorizing_orphan_archive(self):
        preview = self.preview()
        command = self.h.command(preview)
        with patch.object(self.h.worker.policy_source, 'register', side_effect=Blocked('synthetic source block')):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assert_unknown(command)
        self.assertEqual(self.h.store.rows, [])
        self.assertEqual(self.state.conn.execute('SELECT count(*) FROM intent').fetchone()[0], 1)

    def test_lost_core_commit_poisoned_even_when_queue_exists(self):
        preview = self.preview()
        command = self.h.command(preview)
        self.h.store.conn.fail_commit = True
        self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.h.store.conn.fail_commit = False
        self.assert_unknown(command)
        self.assertEqual(len(self.h.store.rows), 1)
        self.assertEqual(self.h.worker.tick(flows.ACCOUNT), 'HALTED')

    def test_lost_runtime_commit_poisoned_after_successful_core_commit(self):
        preview = self.preview()
        command = self.h.command(preview)
        original_scope = self.state.scope
        @contextmanager
        def lost_commit(lease):
            with original_scope(lease):
                yield
            raise RuntimeError('synthetic lost runtime commit acknowledgement')
        with patch.object(self.state, 'scope', side_effect=lost_commit):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assert_unknown(command)
        self.assertEqual(len(self.h.store.rows), 1)
        self.assertEqual(self.h.worker.tick(flows.ACCOUNT), 'HALTED')

    def test_failed_command_receipt_poisoned_after_both_dispatch_commits(self):
        preview = self.preview()
        command = self.h.command(preview)
        with patch.object(self.h.state, 'complete', side_effect=RuntimeError('synthetic command journal full')):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assert_unknown(command)
        self.assertEqual(len(self.h.store.rows), 1)
        self.assertEqual(self.state.conn.execute('SELECT count(*) FROM intent').fetchone()[0], 1)
        self.assertEqual(self.h.worker.tick(flows.ACCOUNT), 'HALTED')

    def test_runtime_commit_failure_after_pre_enqueue_rejection_also_poisons(self):
        preview = self.preview()
        command = self.h.command(preview)
        original_scope = self.state.scope
        @contextmanager
        def lost_commit(lease):
            try:
                with original_scope(lease):
                    yield
            finally:
                raise RuntimeError('synthetic runtime commit failed while propagating body rejection')
        self.h.store.conn.after_lock = lambda: self.state.conn.execute(
            'UPDATE runtime SET healthy=0 WHERE account=?', (flows.ACCOUNT,))
        with patch.object(self.state, 'scope', side_effect=lost_commit):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assert_unknown(command)
        self.assertEqual(self.h.store.rows, [])

    def test_failed_best_effort_halts_cannot_unpoison_guard_or_clear_unknown(self):
        preview = self.preview()
        command = self.h.command(preview)
        with patch.object(self.state, 'save_intent', side_effect=RuntimeError('synthetic archive fail')), \
             patch.object(self.state, 'trip', side_effect=RuntimeError('synthetic runtime write fail')), \
             patch.object(self.h.store, 'fail_closed', side_effect=RuntimeError('synthetic core write fail')):
            self.assertEqual(self.h.post(command, key='runtime-unknown-0001')[0], 503)
        self.assertTrue(self.runtime.guard.poisoned)
        self.assertEqual(self.h.post(command, key='runtime-unknown-0001'), (409, {'error': 'COMMAND_OUTCOME_UNKNOWN'}))
        # Even if durable halt writes failed, a queued request cannot use this guard.
        self.assertEqual(self.h.worker.tick(flows.ACCOUNT), 'RUNTIME_BLOCKED')
        self.assertEqual(self.h.f.broker.calls, [])

    def test_scripted_process_exit_preserves_unknown_and_poison(self):
        preview = self.preview()
        command = self.h.command(preview)
        with patch.object(self.h.worker.policy_source, 'register', side_effect=SystemExit('synthetic exit')):
            with self.assertRaises(SystemExit):
                self.h.post(command, key='runtime-unknown-0001')
        self.assert_unknown(command)

    def test_scoped_archive_requires_real_held_lease_and_matching_account(self):
        result, _ = self.confirm()
        admission = self.h.worker.policy_source.read(result['request_id'])
        with self.assertRaisesRegex(Blocked, 'RUNTIME_ADMISSION_SCOPE_REQUIRED'):
            self.state.save_intent(result['request_id'], admission, lease=self.lease)
        with self.runtime.guard.scope(flows.ACCOUNT):
            with self.assertRaisesRegex(Blocked, 'RUNTIME_ADMISSION_SCOPE_REQUIRED'):
                self.state.save_intent(result['request_id'], admission, lease=replace(self.lease, account='b' * 64))
            with self.assertRaisesRegex(Blocked, 'RUNTIME_REENTRANCY_DENIED'):
                self.state.save_intent(result['request_id'], admission)
            foreign = replace(admission, proposal=replace(admission.proposal,
                order=replace(admission.proposal.order, account='b' * 64)))
            with self.assertRaisesRegex(Blocked, 'RUNTIME_ADMISSION_SCOPE_REQUIRED'):
                self.state.save_intent('foreign-request', foreign, lease=self.lease)

    def test_stale_runtime_ownership_cannot_queue_or_replace_new_owner(self):
        preview = self.preview()
        self.state.conn.execute('UPDATE runtime SET epoch=epoch+1 WHERE account=?', (flows.ACCOUNT,))
        self.assertEqual(self.h.post(self.h.command(preview))[0], 409)
        self.assertEqual(self.h.store.rows, [])
        self.assertEqual(self.state.status(flows.ACCOUNT)['epoch'], self.lease.epoch + 1)
        self.assertEqual(self.h.f.broker.calls, [])

    def test_new_source_after_reconstruction_is_used_and_archived(self):
        preview = self.preview()
        old = self.h.worker.policy_source
        self.h.worker.policy_source = SimulationPolicySource()
        status, result = self.h.post(self.h.command(preview))
        self.assertEqual(status, 202)
        self.h.worker.policy_source.read(result['request_id'])
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            old.read(result['request_id'])
        self.assertEqual(self.state.conn.execute('SELECT count(*) FROM intent').fetchone()[0], 1)

    def test_restore_keeps_original_intent_and_cannot_upgrade_old_approval_revision(self):
        result, _ = self.confirm()
        row = self.h.store.rows[0]
        original_digest = Order.from_payload(row['payload']).digest
        self.state.trip(self.lease, 'TEST_RECONCILIATION_REQUIRED')
        self.h.store.fail_closed(flows.ACCOUNT, 'TEST_RECONCILIATION_REQUIRED')
        self.runtime.restore_pending({result['request_id']: self.h.f.context})
        admission = self.h.worker.policy_source.read(result['request_id'])
        self.assertEqual(admission.proposal.order.digest, original_digest)
        self.assertEqual(row['control_revision'], 7)
        self.assertEqual(self.h.store.control_row['revision'], 8)
        self.assertEqual(self.state.status(flows.ACCOUNT)['phase'], 'HALTED')
        self.assertEqual(self.runtime.step(self.sample()), 'RECONCILED_ONLY')
        self.assertEqual(self.h.f.broker.calls, [])

    def test_missing_current_context_never_falls_back_to_runtime_archive(self):
        result, _ = self.confirm()
        with self.assertRaisesRegex(Blocked, 'EXPLICIT_PENDING_CONTEXTS_REQUIRED'):
            self.runtime.restore_pending({})
        with self.assertRaisesRegex(Blocked, 'POLICY_ADMISSION_REQUIRED'):
            self.h.worker.policy_source.read(result['request_id'])
        self.assertTrue(self.h.store.control_row['halted'])


if __name__ == '__main__':
    unittest.main()
