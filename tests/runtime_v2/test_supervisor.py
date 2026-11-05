"""Linux/mock lifecycle coverage; NOT proof of Windows/PowerShell behavior."""
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked
from oneil_runtime_v2.health import ClockSample
from oneil_runtime_v2.observer_child import observe
from oneil_runtime_v2.state import RuntimeState
from oneil_runtime_v2.supervisor import (
    ACCOUNT, ROOT, InstanceLock, OfflineSupervisor, SupervisorState, main, report, spawn_observer)


class FakeClock:
    def __init__(self):
        self.value = 1_000_000.0
        self.sleeps = []
        self.hook = None

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds
        if self.hook:
            self.hook()


class Child:
    pid = 1234

    def __init__(self, code=None):
        self.code = code
        self.terminated = False
        self.waits = []

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.code


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)/'isolated'
        self.state = SupervisorState.initialize(self.directory)
        self.addCleanup(self.state.close)
        self.clock = FakeClock()
        self.spawns = []

    def runner(self, children):
        def spawn(directory, lease):
            self.assertEqual(directory, self.directory)
            rt = RuntimeState(directory/'runtime.sqlite')
            try:
                self.assertEqual(rt.status(ACCOUNT)['phase'], 'RECONCILING')
                self.assertEqual(rt._lease(lease)['healthy'], 0)
                self.assertIsNone(rt.status(ACCOUNT)['activation_revision'])
            finally:
                rt.close()
            self.spawns.append((lease, self.clock.value))
            return children.pop(0)
        return OfflineSupervisor(self.state, spawn=spawn, sleep=self.clock.sleep,
                                 now=self.clock.now, monotonic=self.clock.now)

    def runtime_status(self):
        rt = RuntimeState(self.directory/'runtime.sqlite')
        try:
            return rt.status(ACCOUNT)
        finally:
            rt.close()

    def test_new_state_is_never_ready_or_reconciled(self):
        result = report(self.state)
        self.assertEqual(result['phase'], 'READY')
        for key in ('ready_to_trade', 'live_capable', 'commands_enabled',
                    'broker_reconciled', 'worker_connected', 'actual_windows_validated'):
            self.assertIs(result[key], False)

    def test_existing_folder_never_reinitialized(self):
        with self.assertRaises(FileExistsError):
            SupervisorState.initialize(self.directory)
        self.assertEqual(self.state.status()['phase'], 'READY')

    def test_missing_state_never_created_implicitly(self):
        path = Path(self.tmp.name)/'absent'
        with self.assertRaisesRegex(Blocked, 'SUPERVISOR_STATE_MISSING'):
            SupervisorState(path)
        self.assertFalse(path.exists())

    def test_corrupt_state_not_replaced(self):
        self.state.close()
        path = self.directory/'supervisor.sqlite'
        path.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):
            SupervisorState(self.directory)
        self.assertEqual(path.read_bytes(), b'not a database')
        self.state = SupervisorState.initialize(Path(self.tmp.name)/'cleanup')

    def test_schema_version_mismatch_rejected(self):
        self.state.conn.execute('PRAGMA ignore_check_constraints=ON')
        self.state.conn.execute('UPDATE metadata SET version=2')
        with self.assertRaisesRegex(Blocked, 'SUPERVISOR_VERSION_INVALID'):
            SupervisorState(self.directory)

    def test_reparse_or_symlink_directory_rejected(self):
        alias = Path(self.tmp.name)/'alias'
        # No symlink privileges needed on Windows: patch the metadata contract.
        with patch('oneil_runtime_v2.supervisor.stat.S_ISLNK', return_value=True):
            with self.assertRaisesRegex(Blocked, 'REPARSE_STATE_PATH_DENIED'):
                SupervisorState(self.directory)
        self.assertFalse(alias.exists())

    def test_same_directory_second_supervisor_denied(self):
        with InstanceLock(self.directory/'supervisor.lock'):
            with self.assertRaisesRegex(RuntimeError, 'SUPERVISOR_ALREADY_RUNNING'):
                with InstanceLock(self.directory/'supervisor.lock'):
                    self.fail('second lock admitted')
        with InstanceLock(self.directory/'supervisor.lock'):
            pass

    def test_restart_budget_5_10_20_and_then_exhausted(self):
        self.assertEqual(self.runner([Child(1) for _ in range(4)]).run(), 2)
        self.assertEqual(len(self.spawns), 4)
        self.assertEqual([round(self.spawns[i+1][1]-self.spawns[i][1]) for i in range(3)], [5, 10, 20])
        self.assertEqual([item[0].epoch for item in self.spawns], [1, 2, 3, 4])
        self.assertEqual(self.state.status()['phase'], 'FAILED')
        self.assertEqual(self.runtime_status()['restart_count'], 3)
        with self.assertRaisesRegex(Blocked, 'RESTART_BUDGET_EXHAUSTED'):
            self.runner([]).run(start_stopped=True)

    def test_clean_exit_never_auto_restarts(self):
        self.assertEqual(self.runner([Child(0)]).run(), 0)
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(self.state.status()['phase'], 'STOPPED')
        self.assertEqual(self.runtime_status()['restart_count'], 0)
        with self.assertRaisesRegex(Blocked, 'EXPLICIT_START_STOPPED_REQUIRED'):
            self.runner([]).run()
        self.assertEqual(self.runner([Child(0)]).run(start_stopped=True), 0)
        self.assertEqual(self.runtime_status()['epoch'], 2)

    def test_stop_before_launch_is_durable_and_explicit_restart_only(self):
        self.state.request_stop()
        self.assertEqual(self.runner([]).run(), 0)
        self.assertEqual(self.spawns, [])
        self.assertEqual(self.runtime_status()['phase'], 'STOPPED')
        reopened = SupervisorState(self.directory)
        try:
            self.assertEqual(reopened.status()['stop_requested'], 1)
        finally:
            reopened.close()
        self.assertEqual(self.runner([Child(0)]).run(start_stopped=True), 0)

    def test_new_stop_cannot_be_erased_by_concurrent_start(self):
        self.state.request_stop()
        self.runner([]).run()
        old = self.state.status()['stop_revision']
        self.state.request_stop()
        with self.assertRaisesRegex(Blocked, 'NEW_STOP_REQUEST_WINS'):
            self.state.clear_stopped_request(old)
        self.assertEqual(self.state.status()['stop_requested'], 1)

    def test_stop_in_backoff_never_launches_second_child_or_resets_budget(self):
        self.clock.hook = self.state.request_stop
        self.assertEqual(self.runner([Child(1)]).run(), 0)
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(self.runtime_status()['restart_count'], 1)
        self.assertEqual(self.runtime_status()['phase'], 'STOPPED')
        self.clock.hook = None
        self.assertEqual(self.runner([Child(0)]).run(start_stopped=True), 0)
        self.assertEqual(self.runtime_status()['restart_count'], 1)

    def test_stop_racing_admitted_spawn_launches_no_observation_or_restart(self):
        spawns = []
        def spawn(directory, lease):
            spawns.append(lease)
            self.state.request_stop()
            def forbidden_clock():
                self.fail('observer sampled after an already persisted stop')
            self.assertEqual(observe(directory, lease.owner, lease.epoch, clock=forbidden_clock), 0)
            return Child(0)
        runner = OfflineSupervisor(self.state, spawn=spawn, sleep=self.clock.sleep,
                                   now=self.clock.now, monotonic=self.clock.now)
        self.assertEqual(runner.run(), 0)
        self.assertEqual(len(spawns), 1)
        self.assertEqual(self.state.status()['stop_requested'], 1)
        self.assertEqual(self.state.status()['phase'], 'STOPPED')
        self.assertIsNone(self.runtime_status()['health'])
        self.assertEqual(self.runtime_status()['restart_count'], 0)

    def test_cooperative_stop_does_not_terminate_exited_child(self):
        child = Child()
        def stop():
            self.state.request_stop()
            child.code = 0
        self.clock.hook = stop
        self.assertEqual(self.runner([child]).run(), 0)
        self.assertFalse(child.terminated)
        self.assertEqual(child.waits, [5])
        self.assertEqual(self.runtime_status()['phase'], 'STOPPED')

    def test_stop_timeout_terminates_only_exact_owned_handle(self):
        child = Child()
        self.clock.hook = self.state.request_stop
        self.assertEqual(self.runner([child]).run(), 0)
        self.assertTrue(child.terminated)
        self.assertEqual(self.runtime_status()['restart_count'], 0)
        self.assertEqual(self.state.status()['phase'], 'STOPPED')

    def test_unconfirmed_termination_never_releases_ownership(self):
        class Unconfirmed(Child):
            def terminate(self):
                self.terminated = True
            def wait(self, timeout):
                raise subprocess.TimeoutExpired('fixed observer', timeout)
        self.clock.hook = self.state.request_stop
        with self.assertRaisesRegex(RuntimeError, 'CHILD_EXIT_UNCONFIRMED'):
            self.runner([Unconfirmed()]).run()
        self.assertEqual(self.state.status()['phase'], 'RUNNING')
        self.assertIsNotNone(self.runtime_status()['owner'])
        with self.assertRaisesRegex(Blocked, 'PRIOR_CHILD_OUTCOME_UNKNOWN'):
            self.runner([]).run()

    def test_spawn_failure_keeps_claim_and_does_not_retry(self):
        def fail(*_):
            raise OSError('uncertain spawn')
        runner = OfflineSupervisor(self.state, spawn=fail, now=self.clock.now)
        with self.assertRaises(OSError):
            runner.run()
        self.assertEqual(self.state.status()['phase'], 'STARTING')
        self.assertEqual(self.state.status()['reason'], 'SPAWN_OUTCOME_UNKNOWN_OPERATOR_REQUIRED')
        self.assertIsNotNone(self.runtime_status()['owner'])
        self.assertEqual(self.runtime_status()['restart_count'], 0)
        with self.assertRaisesRegex(Blocked, 'PRIOR_CHILD_OUTCOME_UNKNOWN'):
            self.runner([]).run(start_stopped=True)

    def test_interrupt_during_spawn_keeps_uncertain_ownership(self):
        def interrupted(*_):
            raise KeyboardInterrupt
        runner = OfflineSupervisor(self.state, spawn=interrupted, now=self.clock.now)
        self.assertEqual(runner.run(), 130)
        self.assertEqual(self.state.status()['phase'], 'STARTING')
        self.assertIsNotNone(self.runtime_status()['owner'])
        self.assertEqual(self.state.status()['stop_requested'], 1)
        with self.assertRaisesRegex(Blocked, 'PRIOR_CHILD_OUTCOME_UNKNOWN'):
            self.runner([]).run()

    def test_exit_journal_failure_retains_conservative_supervisor_claim(self):
        original = self.state.update
        def fail_exit(**fields):
            if fields.get('phase') == 'STOPPED':
                raise sqlite3.OperationalError('simulated disk failure')
            return original(**fields)
        with patch.object(self.state, 'update', side_effect=fail_exit):
            with self.assertRaises(sqlite3.OperationalError):
                self.runner([Child(0)]).run()
        self.assertEqual(self.state.status()['phase'], 'RUNNING')
        self.assertIsNone(self.runtime_status()['owner'])
        with self.assertRaisesRegex(Blocked, 'PRIOR_CHILD_OUTCOME_UNKNOWN'):
            self.runner([]).run()

    def test_crashed_supervisor_does_not_trust_reused_or_stale_pid(self):
        for phase in ('STARTING', 'RUNNING'):
            self.state.update(phase=phase, pid=1)
            with self.assertRaisesRegex(Blocked, 'PRIOR_CHILD_OUTCOME_UNKNOWN'):
                self.runner([]).run()
        self.assertEqual(self.spawns, [])

    def test_interrupt_stops_without_restart(self):
        def interrupt():
            self.clock.hook = None
            raise KeyboardInterrupt
        self.clock.hook = interrupt
        child = Child()
        self.assertEqual(self.runner([child]).run(), 130)
        self.assertTrue(child.terminated)
        self.assertEqual(self.state.status()['phase'], 'STOPPED')
        self.assertEqual(self.runtime_status()['restart_count'], 0)

    def test_stopped_state_never_allows_different_account(self):
        rt = RuntimeState(self.directory/'runtime.sqlite')
        try:
            lease = rt.start(ACCOUNT, now=self.clock.now())
        finally:
            rt.close()
        data = {'account': 'f'*64, 'engine': lease.engine, 'owner': lease.owner, 'epoch': lease.epoch}
        with self.assertRaisesRegex(Blocked, 'FIXED_OFFLINE_ACCOUNT_REQUIRED'):
            self.state.update(lease=json.dumps(data))
        self.assertIsNone(self.state.status()['lease'])

    def test_observer_keeps_missing_dependencies_unhealthy_and_does_not_reconcile(self):
        rt = RuntimeState(self.directory/'runtime.sqlite')
        try:
            lease = rt.start(ACCOUNT, now=self.clock.now())
        finally:
            rt.close()
        count = 0
        def sleep(_):
            nonlocal count
            count += 1
            self.clock.sleep(0.5)
            if count == 2:
                self.state.request_stop()
        self.assertEqual(observe(self.directory, lease.owner, lease.epoch,
            clock=lambda: ClockSample(self.clock.now(), self.clock.now(), self.clock.now()), sleep=sleep), 0)
        row = self.runtime_status()
        self.assertEqual(row['phase'], 'HALTED')
        self.assertEqual(row['healthy'], 0)
        self.assertIsNone(row['reconciled_at'])
        health = json.loads(row['health'])
        self.assertEqual(health['database'], {'ok': False, 'observed_at': None})
        self.assertEqual(health['broker_read'], {'ok': False, 'observed_at': None})
        self.assertTrue(health['unresolved_orders'])
        self.assertIsNotNone(row['owner'])  # Child never releases its own claim.

    def test_observer_rejects_stale_lease(self):
        rt = RuntimeState(self.directory/'runtime.sqlite')
        try:
            lease = rt.start(ACCOUNT, now=self.clock.now())
        finally:
            rt.close()
        with self.assertRaisesRegex(Blocked, 'RUNTIME_OWNERSHIP_LOST'):
            observe(self.directory, lease.owner, lease.epoch+1)

    def test_launch_has_fixed_isolated_command_no_shell_credentials_or_endpoint(self):
        rt = RuntimeState(self.directory/'runtime.sqlite')
        try:
            lease = rt.start(ACCOUNT, now=self.clock.now())
        finally:
            rt.close()
        with patch.dict(os.environ, {'KIS_SECRET': 'not-a-real-secret', 'PYTHONPATH': 'untrusted'}):
            with patch('oneil_runtime_v2.supervisor.subprocess.Popen') as popen:
                spawn_observer(self.directory, lease)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][:4], [sys.executable, '-I', '-B', str(ROOT/'oneil_runtime_v2/worker_child.py')])
        self.assertIs(kwargs['shell'], False)
        self.assertNotIn('KIS_SECRET', kwargs['env'])
        self.assertNotIn('PYTHONPATH', kwargs['env'])
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)

    def test_powershell_uses_only_fully_qualified_captured_paths_static_contract(self):
        # Parse the exact shared regex; this is not PowerShell/.NET execution.
        text = (ROOT/'deploy/windows/Start-OfflineRehearsal.ps1').read_text(encoding='utf-8')
        expression = next(line.split("'", 2)[1] for line in text.splitlines()
                          if line.startswith('$FullyQualifiedLocalPath = '))
        for path in ('C:\\Python312\\python.exe', 'D:/Program Files/Python/python.exe'):
            self.assertIsNotNone(re.match(expression, path))
        for path in ('C:python.exe', 'C:state', '\\Python\\python.exe', '/state',
                     'relative', '\\\\server\\share\\python.exe'):
            self.assertIsNone(re.match(expression, path))
        self.assertIn('& $PythonPath @Arguments', text)
        self.assertNotIn('& $PythonExe @Arguments', text)
        self.assertIn("'--state-dir',$StateDirectoryPath)", text)
        for assignment in ('$PythonPath =', '$StateDirectoryPath ='):
            self.assertLess(text.index(assignment), text.index('Push-Location -LiteralPath'))
        self.assertIn('Test-Path -LiteralPath $PythonPath -PathType Leaf', text)

    def test_cli_does_not_offer_non_windows_bypass(self):
        with patch('oneil_runtime_v2.supervisor.os.name', 'posix'):
            self.assertEqual(main(['status', '--state-dir', str(self.directory)]), 2)
        self.assertEqual(self.state.status()['phase'], 'READY')

    def test_real_child_exit_is_observed_no_socket_or_real_broker(self):
        # The subprocess runs a literal tiny stdlib-only local script. This tests
        # Popen exit ownership on this host, not the Windows observer adapter.
        def spawn(*_):
            return subprocess.Popen([sys.executable, '-I', '-c', 'raise SystemExit(0)'],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, shell=False)
        self.assertEqual(OfflineSupervisor(self.state, spawn=spawn).run(), 0)
        self.assertEqual(self.state.status()['last_exit'], 0)
        self.assertIsNone(self.runtime_status()['owner'])


if __name__ == '__main__':
    unittest.main()
