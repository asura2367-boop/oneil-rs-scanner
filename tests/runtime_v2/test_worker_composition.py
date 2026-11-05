"""Real local SQLite/Worker composition with test-only synthetic history.

No PostgreSQL, network, production account, submit call or native Windows claim.
The subprocess test explicitly injects a simulated clock in test code only.
"""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import Blocked, BrokerOrder, Order
from oneil_runtime_v2.health import ClockSample
from oneil_runtime_v2.rehearsal_store import RehearsalStore
from oneil_runtime_v2.state import RuntimeState
from oneil_runtime_v2.supervisor import ACCOUNT, ROOT, InstanceLock, OfflineSupervisor, SupervisorState, report
from oneil_runtime_v2.worker_child import run_worker


class WorkerCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)/'separate synthetic worker'
        self.supervisor = SupervisorState.initialize(self.directory)
        self.addCleanup(self.supervisor.close)
        self.clock_value = 1_800_000_000.0
        self.calls = 0
        self.observed_states = []
        self.stop_at = 3
        self.rt = RuntimeState(self.directory/'runtime.sqlite')
        self.addCleanup(self.rt.close)
        self.lease = None

    def launch_receipt(self):
        self.supervisor.update(phase='STARTING')
        self.lease = self.rt.start(ACCOUNT, now=self.clock_value)
        self.supervisor.update(lease=json.dumps(asdict(self.lease)))
        return self.lease

    def clock(self):
        return ClockSample(self.clock_value, self.clock_value, self.clock_value)

    def sleep(self, seconds):
        self.calls += 1
        self.observed_states.append(self.rt.status(ACCOUNT)['phase'])
        self.clock_value += seconds
        if self.calls >= self.stop_at:
            self.supervisor.request_stop()

    def run_child(self):
        lease = self.lease or self.launch_receipt()
        return run_worker(self.directory, lease.owner, lease.epoch, clock=self.clock, sleep=self.sleep)

    def core(self):
        return RehearsalStore(self.directory/'rehearsal.sqlite',
                              now=lambda: datetime.fromtimestamp(self.clock_value, timezone.utc))

    def seed_request(self, *, state='SUBMISSION_UNKNOWN', attempts=1):
        # Explicit TEST-ONLY direct fixture insertion. There is no CLI/store API
        # for enqueuing this request or representing it as a real account order.
        order = Order(ACCOUNT, '005930', 'BUY', 'KRX', 3, 1000,
                      datetime.fromtimestamp(self.clock_value, timezone.utc)+timedelta(minutes=5),
                      'offline-reconciliation-only')
        request_id = 'synthetic-scenario-0001'
        day = '2027-01-15'
        core = self.core()
        try:
            core.conn.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?)',
                (request_id, ACCOUNT, json.dumps(order.payload()), state, attempts,
                 day if attempts else None, None, 0, 'TEST_ONLY_SYNTHETIC_SEED'))
        finally:
            core.close()
        return request_id, order, day

    def write_history(self, observation):
        core = self.core()
        try:
            core.conn.execute('INSERT OR REPLACE INTO synthetic_history VALUES(?,?)',
                              (observation.request_id, json.dumps(asdict(observation))))
        finally:
            core.close()

    def test_real_classes_reconcile_then_await_owner_without_activation(self):
        with patch.object(SimulatedBroker, 'submit', side_effect=AssertionError('submit forbidden')):
            self.assertEqual(self.run_child(), 0)
        self.assertIn('AWAITING_OWNER', self.observed_states)
        row = self.rt.status(ACCOUNT)
        self.assertEqual(row['phase'], 'HALTED')  # Child quiesced; parent owns exit receipt.
        self.assertIsNotNone(row['owner'])
        self.assertIsNone(row['activation_revision'])
        core = self.core()
        try:
            self.assertEqual(core.health()['status'], 'RECONCILED')
            self.assertTrue(core.control(ACCOUNT)['halted'])
            self.assertEqual(core.recovery_requests(ACCOUNT), [])
            self.assertEqual(core.unresolved(ACCOUNT), [])
        finally:
            core.close()
        status = report(self.supervisor)
        self.assertTrue(status['simulation_worker_composed'])
        self.assertEqual(status['execution_dependency'], 'ISOLATED_SQLITE_RECONCILIATION_ONLY')
        self.assertFalse(status['real_broker_connected'])
        self.assertFalse(status['commands_enabled'])
        self.assertFalse(status['ready_to_trade'])

    def test_no_launch_receipt_no_runtime_attachment(self):
        lease = self.rt.start(ACCOUNT, now=self.clock_value)
        with self.assertRaisesRegex(Blocked, 'SUPERVISOR_LAUNCH_RECEIPT_REQUIRED'):
            run_worker(self.directory, lease.owner, lease.epoch, clock=self.clock, sleep=self.sleep)
        self.assertEqual(self.rt.status(ACCOUNT)['phase'], 'RECONCILING')

    def test_receipt_wrong_owner_is_rejected(self):
        lease = self.launch_receipt()
        with self.assertRaisesRegex(Blocked, 'SUPERVISOR_LAUNCH_RECEIPT_REQUIRED'):
            run_worker(self.directory, '0'*32, lease.epoch, clock=self.clock, sleep=self.sleep)

    def test_existing_child_instance_lock_blocks_duplicate(self):
        lease = self.launch_receipt()
        with InstanceLock(self.directory/'worker.lock'):
            with self.assertRaisesRegex(RuntimeError, 'SUPERVISOR_ALREADY_RUNNING'):
                self.run_child()
        self.assertEqual(self.rt.status(ACCOUNT)['epoch'], lease.epoch)

    def test_stop_before_child_loop_produces_no_health_or_observation(self):
        self.launch_receipt()
        self.supervisor.request_stop()
        with patch.object(SimulatedBroker, 'history', side_effect=AssertionError('history not requested')):
            self.assertEqual(self.run_child(), 0)
        core = self.core()
        try:
            self.assertIsNone(core.health())
        finally:
            core.close()
        self.assertIsNone(self.rt.status(ACCOUNT)['health'])

    def test_store_has_no_enqueue_claim_admission_or_resume(self):
        core = self.core()
        try:
            for name in ('enqueue', 'claim', 'reserve_attempt', 'set_halt'):
                with self.subTest(name=name), self.assertRaisesRegex(Blocked, 'REHEARSAL_NEW_ORDERS_DISABLED'):
                    getattr(core, name)(ACCOUNT)
            with self.assertRaises(sqlite3.IntegrityError):
                core.conn.execute('UPDATE control SET halted=0')
        finally:
            core.close()

    def test_pending_intent_without_current_context_blocks_without_claim(self):
        request, _, _ = self.seed_request(state='QUEUED', attempts=0)
        with self.assertRaisesRegex(Blocked, 'EXPLICIT_PENDING_CONTEXTS_REQUIRED'):
            self.run_child()
        core = self.core()
        try:
            row = core.get(ACCOUNT, request)
            self.assertEqual((row['state'], row['attempts']), ('QUEUED', 0))
        finally:
            core.close()
        self.assertEqual(self.rt.status(ACCOUNT)['phase'], 'HALTED')

    def test_unknown_with_empty_history_survives_restart_without_retry(self):
        request, _, _ = self.seed_request()
        with patch.object(SimulatedBroker, 'submit', side_effect=AssertionError('no retry')):
            self.run_child()
        core = self.core()
        try:
            row = core.get(ACCOUNT, request)
            self.assertEqual((row['state'], row['attempts']), ('SUBMISSION_UNKNOWN', 1))
        finally:
            core.close()
        self.assertNotIn('AWAITING_OWNER', self.observed_states)
        self.assertEqual(self.rt.status(ACCOUNT)['phase'], 'HALTED')
        # Stand-in for the supervisor's observed exact-process exit, test only.
        plan = self.rt.record_exit(self.lease, exit_code=1, now=self.clock_value)
        self.clock_value += plan.delay_seconds
        self.supervisor.update(phase='RESTART_WAIT', lease=None, delay_seconds=plan.delay_seconds)
        self.supervisor.conn.execute('UPDATE supervisor SET stop_requested=0')
        self.calls = 0
        self.lease = None
        self.run_child()
        core = self.core()
        try:
            self.assertEqual(core.get(ACCOUNT, request)['state'], 'SUBMISSION_UNKNOWN')
        finally:
            core.close()
        self.assertEqual(self.rt.status(ACCOUNT)['restart_count'], 1)

    def test_synthetic_scenario_partial_then_filled_is_durable_not_replayed(self):
        request, order, day = self.seed_request()
        partial = BrokerOrder(request, order.digest, 'test-only-native-1', ACCOUNT,
            day, order.ticker, order.side, order.venue, order.quantity, order.limit_price, 'PART_FILLED', 1)
        self.write_history(partial)
        self.launch_receipt()
        def progress(seconds):
            self.calls += 1
            self.clock_value += seconds
            if self.calls == 1:
                self.write_history(replace(partial, status='FILLED', filled_quantity=3))
            if self.calls >= 4:
                self.supervisor.request_stop()
        with patch.object(SimulatedBroker, 'submit', side_effect=AssertionError('no simulated submission')):
            self.assertEqual(run_worker(self.directory, self.lease.owner, self.lease.epoch,
                                       clock=self.clock, sleep=progress), 0)
        core = self.core()
        try:
            row = core.get(ACCOUNT, request)
            self.assertEqual((row['state'], row['attempts'], row['filled_quantity']), ('FILLED', 1, 3))
            self.assertTrue(core.control(ACCOUNT)['halted'])
        finally:
            core.close()

    def test_synthetic_history_wrong_identity_cannot_clear_unknown(self):
        request, order, day = self.seed_request()
        bad = BrokerOrder(request, order.digest, 'test-only-native-1', ACCOUNT,
            day, '000660', order.side, order.venue, order.quantity, order.limit_price, 'FILLED', 3)
        self.write_history(bad)
        self.run_child()
        core = self.core()
        try:
            self.assertEqual(core.get(ACCOUNT, request)['state'], 'SUBMISSION_UNKNOWN')
            self.assertEqual(core.health()['status'], 'RECONCILIATION_BLOCKED')
        finally:
            core.close()

    def test_incomplete_synthetic_history_never_becomes_reconciled(self):
        core = self.core()
        try:
            core.conn.execute('UPDATE history_state SET complete=0')
        finally:
            core.close()
        self.run_child()
        self.assertNotIn('AWAITING_OWNER', self.observed_states)
        self.assertEqual(self.rt.status(ACCOUNT)['healthy'], 0)

    def test_missing_or_corrupt_core_never_creates_clean_replacement(self):
        self.launch_receipt()
        path = self.directory/'rehearsal.sqlite'
        path.write_bytes(b'corrupt')
        with self.assertRaises(sqlite3.DatabaseError):
            self.run_child()
        self.assertEqual(path.read_bytes(), b'corrupt')
        self.assertIsNotNone(self.rt.status(ACCOUNT)['owner'])

    def test_corrupt_durable_fill_state_rejected_before_worker_attachment(self):
        request, _, _ = self.seed_request()
        core = self.core()
        try:
            core.conn.execute("UPDATE requests SET state='FILLED',filled_quantity=1 WHERE request_id=?", (request,))
        finally:
            core.close()
        with self.assertRaisesRegex(Blocked, 'REHEARSAL_REQUEST_INVALID'):
            self.run_child()
        self.assertIsNotNone(self.rt.status(ACCOUNT)['owner'])
        self.assertIsNone(self.rt.status(ACCOUNT)['activation_revision'])

    def test_attempted_pending_state_is_not_filtered_out_as_safe(self):
        request, _, _ = self.seed_request()
        core = self.core()
        try:
            core.conn.execute("UPDATE requests SET state='QUEUED' WHERE request_id=?", (request,))
            for name in ('recovery_requests', 'unresolved'):
                with self.subTest(name=name), self.assertRaisesRegex(Blocked, 'REHEARSAL_REQUEST_INVALID'):
                    getattr(core, name)(ACCOUNT)
        finally:
            core.close()
        with self.assertRaisesRegex(Blocked, 'REHEARSAL_REQUEST_INVALID'):
            self.run_child()
        self.assertNotEqual(self.rt.status(ACCOUNT)['phase'], 'AWAITING_OWNER')

    def test_duplicate_native_identity_cannot_resolve_two_requests(self):
        request, order, day = self.seed_request()
        second = 'synthetic-scenario-0002'
        core = self.core()
        try:
            core.conn.execute('''INSERT INTO requests SELECT ?,account,payload,state,attempts,
                trading_day,broker_order_id,filled_quantity,reason FROM requests WHERE request_id=?''', (second, request))
            first = BrokerOrder(request, order.digest, 'shared-test-native', ACCOUNT, day,
                order.ticker, order.side, order.venue, order.quantity, order.limit_price, 'FILLED', 3)
            for observed in (first, replace(first, request_id=second)):
                core.conn.execute('INSERT INTO synthetic_history VALUES(?,?)',
                                  (observed.request_id, json.dumps(asdict(observed))))
            with self.assertRaisesRegex(Blocked, 'SYNTHETIC_HISTORY_NATIVE_ID_AMBIGUOUS'):
                core.history_snapshot()
            with core.account_lock(ACCOUNT):
                core.apply_observation(core.get(ACCOUNT, request), first)
            with self.assertRaises(sqlite3.IntegrityError):
                with core.account_lock(ACCOUNT):
                    core.apply_observation(core.get(ACCOUNT, second), replace(first, request_id=second))
            self.assertEqual(core.get(ACCOUNT, second)['state'], 'SUBMISSION_UNKNOWN')
        finally:
            core.close()
        with self.assertRaisesRegex(Blocked, 'SYNTHETIC_HISTORY_NATIVE_ID_AMBIGUOUS'):
            self.run_child()
        self.assertNotEqual(self.rt.status(ACCOUNT)['phase'], 'AWAITING_OWNER')

    def test_history_read_failure_halts_and_retains_process_lease(self):
        self.launch_receipt()
        original = RehearsalStore.history_snapshot
        calls = 0
        def fail_after_open(core):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise sqlite3.OperationalError('synthetic disk read failure')
            return original(core)
        with patch.object(RehearsalStore, 'history_snapshot', fail_after_open):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_child()
        self.assertEqual(self.rt.status(ACCOUNT)['phase'], 'HALTED')
        self.assertIsNotNone(self.rt.status(ACCOUNT)['owner'])

    def test_real_supervisor_process_composition_with_test_only_clock(self):
        # Real process + SQLite, but deliberately injected synthetic awake clock.
        # No executable/CLI platform bypass is added to shipped runtime code.
        def spawn(directory, lease):
            code = ("import sys,time;sys.path.insert(0,"+repr(str(ROOT))+");"
                "from oneil_runtime_v2.worker_child import run_worker;"
                "from oneil_runtime_v2.health import ClockSample;"
                "raise SystemExit(run_worker("+repr(str(directory))+","+repr(lease.owner)+","+str(lease.epoch)+","
                "clock=lambda: ClockSample(time.time(),time.monotonic(),time.monotonic())))")
            return subprocess.Popen([sys.executable, '-I', '-B', '-c', code], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic()+8
        observed = []
        def sleep(seconds):
            row = self.rt.status(ACCOUNT)
            observed.append(row['phase'])
            if row['phase'] == 'AWAITING_OWNER' or time.monotonic() >= deadline:
                self.supervisor.request_stop()
            time.sleep(seconds)
        self.assertEqual(OfflineSupervisor(self.supervisor, spawn=spawn, sleep=sleep).run(), 0)
        self.assertIn('AWAITING_OWNER', observed)
        self.assertEqual(self.supervisor.status()['phase'], 'STOPPED')
        self.assertIsNone(self.rt.status(ACCOUNT)['owner'])
        self.assertEqual(self.rt.status(ACCOUNT)['restart_count'], 0)


if __name__ == '__main__':
    unittest.main()
