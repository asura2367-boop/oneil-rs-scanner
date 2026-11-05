from dataclasses import replace
from pathlib import Path
import tempfile
import sqlite3
import unittest

from oneil_runtime_v2.health import ClockSample, Evidence, HealthPolicy, HealthSample, evaluate
from oneil_runtime_v2.journal import IncidentJournal
from oneil_runtime_v2.restart import plan_restart


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.previous = ClockSample(1000, 100, 90)
        self.sample = HealthSample(ClockSample(1001, 101, 91), True, 1001,
                                   Evidence(True, 1001), Evidence(True, 1001), 2**31, False)

    def test_healthy_is_never_trade_permission(self):
        result = evaluate(self.sample, self.previous)
        self.assertTrue(result.operationally_healthy)
        self.assertFalse(result.live_capable)
        self.assertFalse(result.admission_enforced)
        self.assertFalse(result.ready_to_trade)

    def test_startup_requires_reconciliation(self):
        self.assertIn('STARTUP_RECONCILIATION_REQUIRED', evaluate(self.sample).reasons)

    def test_suspend_and_stale_dependencies(self):
        result = evaluate(replace(self.sample, clock=ClockSample(1301, 401, 91)), self.previous)
        self.assertIn('SLEEP_OR_HIBERNATION_DETECTED', result.reasons)
        self.assertIn('LOOP_GAP_REQUIRES_RECONCILIATION', result.reasons)
        self.assertIn('BROKER_READ_STALE_OR_FUTURE', result.reasons)

    def test_large_pause_even_when_clock_counts_sleep(self):
        result = evaluate(replace(self.sample, clock=ClockSample(1301, 401, 391)), self.previous)
        self.assertIn('LOOP_GAP_REQUIRES_RECONCILIATION', result.reasons)
        self.assertNotIn('SLEEP_OR_HIBERNATION_DETECTED', result.reasons)

    def test_missing_suspend_detector_not_healthy(self):
        result = evaluate(replace(self.sample, clock=ClockSample(1001, 101)), self.previous)
        self.assertIn('SUSPEND_DETECTION_UNAVAILABLE', result.reasons)

    def test_backwards_clock_or_new_host(self):
        for clock in (ClockSample(999, 101, 91), ClockSample(1001, 1, 1)):
            with self.subTest(clock=clock):
                self.assertIn('CLOCK_REVERSED_OR_HOST_RESTARTED',
                              evaluate(replace(self.sample, clock=clock), self.previous).reasons)

    def test_wall_jump_not_explained_by_elapsed(self):
        self.assertIn('CLOCK_DISCONTINUITY', evaluate(
            replace(self.sample, clock=ClockSample(1101, 101, 91)), self.previous).reasons)

    def test_awake_clock_reversal_or_exceeds_elapsed(self):
        for awake in (80, 99):
            self.assertIn('AWAKE_CLOCK_INVALID', evaluate(
                replace(self.sample, clock=ClockSample(1001, 101, awake)), self.previous).reasons)

    def test_invalid_clock_values(self):
        for value in (True, float('nan'), float('inf'), -1, '1001'):
            self.assertEqual(('CLOCK_INVALID',), evaluate(
                replace(self.sample, clock=ClockSample(value, 101, 91)), self.previous).reasons)

    def test_stale_or_future_heartbeat(self):
        for at in (None, 0, 1002, True, float('nan')):
            self.assertIn('HEARTBEAT_MISSING_STALE_OR_FUTURE', evaluate(
                replace(self.sample, heartbeat_at=at), self.previous).reasons)

    def test_exact_freshness_boundary(self):
        self.assertTrue(evaluate(replace(self.sample, heartbeat_at=986), self.previous).operationally_healthy)
        self.assertFalse(evaluate(replace(self.sample, heartbeat_at=985.99), self.previous).operationally_healthy)

    def test_internet_or_database_failure(self):
        for key, prefix in (('database', 'DATABASE'), ('broker_read', 'BROKER_READ')):
            for evidence, suffix in ((Evidence(False, 1001), '_UNAVAILABLE'),
                                     (Evidence(True, 900), '_STALE_OR_FUTURE'),
                                     (Evidence(True, 1002), '_STALE_OR_FUTURE'),
                                     (Evidence(1, 1001), '_UNAVAILABLE'),
                                     (None, '_UNAVAILABLE')):
                self.assertIn(prefix+suffix, evaluate(replace(self.sample, **{key: evidence}), self.previous).reasons)

    def test_process_disk_and_unknown_fail_closed(self):
        result = evaluate(replace(self.sample, worker_alive=1, free_bytes=True, unresolved_orders=None), self.previous)
        self.assertIn('WORKER_NOT_ALIVE', result.reasons)
        self.assertIn('DISK_SPACE_LOW_OR_UNKNOWN', result.reasons)
        self.assertIn('UNRESOLVED_ORDER_OR_UNKNOWN_STATE', result.reasons)

    def test_policy_validation(self):
        for value in (0, -1, True, float('nan')):
            with self.assertRaises(ValueError):
                HealthPolicy(max_loop_gap_seconds=value)

    def test_previous_invalid(self):
        self.assertIn('PREVIOUS_CLOCK_INVALID', evaluate(self.sample, ClockSample(-1, 1, 1)).reasons)


class JournalTests(unittest.TestCase):
    setUp = HealthTests.setUp
    def test_missing_journal_never_creates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'missing.sqlite'
            with self.assertRaises(ValueError): IncidentJournal(path)
            self.assertFalse(path.exists())

    def test_recovery_does_not_clear_latch_or_reopen_for_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'incident.sqlite'
            journal = IncidentJournal.initialize(path)
            journal.record(evaluate(replace(self.sample, database=Evidence(False, 1001)), self.previous))
            journal.close()
            journal = IncidentJournal(path)
            reasons = journal.record(evaluate(self.sample, self.previous))
            self.assertIn('DATABASE_UNAVAILABLE', reasons)
            self.assertIn('STARTUP_RECONCILIATION_REQUIRED', reasons)
            journal.close()

    def test_initialization_cannot_reset_existing_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'incident.sqlite'
            journal = IncidentJournal.initialize(path)
            journal.close()
            with self.assertRaises(FileExistsError): IncidentJournal.initialize(path)

    def test_repeated_faults_only_store_one_first_seen_latch(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = IncidentJournal.initialize(Path(tmp)/'incident.sqlite')
            failed = evaluate(replace(self.sample, database=Evidence(False, 1001)), self.previous)
            for _ in range(100): journal.record(failed)
            self.assertEqual(2, journal.connection.execute('SELECT COUNT(*) FROM incident').fetchone()[0])
            journal.close()

    def test_empty_journal_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = IncidentJournal.initialize(Path(tmp)/'incident.sqlite')
            journal.connection.execute('DELETE FROM incident')
            with self.assertRaises(ValueError): journal.reasons()
            journal.close()

    def test_failed_persistence_is_not_a_successful_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = IncidentJournal.initialize(Path(tmp)/'incident.sqlite')
            journal.connection.execute('PRAGMA query_only=ON')
            failed = evaluate(replace(self.sample, database=Evidence(False, 1001)), self.previous)
            with self.assertRaises(sqlite3.OperationalError): journal.record(failed)
            self.assertNotIn('DATABASE_UNAVAILABLE', journal.reasons())
            journal.close()

    def test_corrupt_file_and_schema_never_reinitialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'incident.sqlite'
            path.write_bytes(b'not a sqlite database')
            with self.assertRaises(sqlite3.DatabaseError): IncidentJournal(path)
            self.assertEqual(b'not a sqlite database', path.read_bytes())

    def test_corruption_and_empty_journal_fail_closed(self):
        for payload in ('{}', '[]', '["secret message"]'):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'incident.sqlite'
                journal = IncidentJournal.initialize(path)
                journal.connection.execute('UPDATE incident SET reason=?', (payload,))
                with self.assertRaises(ValueError): journal.reasons()
                journal.close()


class RestartTests(unittest.TestCase):
    def test_backoff_and_budget_do_not_auto_resume(self):
        for count, delay in enumerate((5, 10, 20)):
            result = plan_restart(exit_code=1, restart_count=count)
            self.assertEqual('RESTART_RECONCILIATION_ONLY', result.action)
            self.assertEqual(delay, result.delay_seconds)
            self.assertEqual(count+1, result.restart_count)
            self.assertFalse(result.commands_enabled)
        self.assertIn('EXHAUSTED', plan_restart(exit_code=1, restart_count=3).action)

    def test_running_clean_exit_and_owner_stop_do_not_restart(self):
        for kwargs in ({'exit_code': None}, {'exit_code': 0}, {'exit_code': 1, 'explicit_stop': True}):
            result = plan_restart(restart_count=0, **kwargs)
            self.assertIsNone(result.delay_seconds)
            self.assertEqual(0, result.restart_count)

    def test_invalid_inputs(self):
        for kwargs in ({'restart_count': True}, {'max_restarts': -1}, {'exit_code': True}, {'explicit_stop': 1}):
            args = dict(exit_code=1, restart_count=0)
            args.update(kwargs)
            with self.assertRaises(ValueError): plan_restart(**args)


if __name__ == '__main__': unittest.main()
