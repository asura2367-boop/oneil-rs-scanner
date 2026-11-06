"""Offline Oracle preparation boundaries, plus a real bounded SQLite smoke child."""
import ast
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'deploy/oracle'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prep = load('prepare_offline')
smoke = load('rehearse_offline')
PASS = {'status': 'PASSED', 'live_capable': False, 'halted_before': True,
        'halted_after': True, 'simulated_order_calls': 0, 'sqlite_reopened': True,
        'lock_exclusion': True, 'temporary_state_removed': True, 'peak_rss_kib': 16000}


class OraclePreparationTests(unittest.TestCase):
    def test_checker_parses_on_old_os_python(self):
        ast.parse((ROOT/'deploy/oracle/prepare_offline.py').read_text(), feature_version=(3, 9))

    def test_release_detection_requires_oracle_linux_9(self):
        for version in ('9', '9.5', '9.6'):
            self.assertTrue(prep.os_release(f'ID="ol"\nVERSION_ID="{version}"\n'))
        for value in ('', 'ID=ubuntu\nVERSION_ID=9', 'ID=ol\nVERSION_ID=8.9', 'ID=ol\nVERSION_ID=90'):
            self.assertFalse(prep.os_release(value))

    def test_one_gb_memory_conversion(self):
        self.assertEqual(prep.memory_mib('MemTotal: 1048576 kB\nMemAvailable: 393216 kB'),
                         {'MemTotal': 1024, 'MemAvailable': 384})

    def test_unknown_or_bad_memory_fails_closed(self):
        for value in ('', 'MemTotal: 2 kB', 'MemTotal: 2 kB\nMemAvailable: 3 kB',
                      'MemTotal: 2 kB\nMemAvailable: -1 kB', 'MemTotal: 2 MB\nMemAvailable: 1 kB'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prep.memory_mib(value)

    def test_result_requires_all_safety_flags(self):
        self.assertTrue(prep.valid_result(PASS))
        for key in PASS:
            bad = copy.deepcopy(PASS)
            bad.pop(key)
            self.assertFalse(prep.valid_result(bad))
        for key, value in [('live_capable', True), ('halted_after', False),
                           ('temporary_state_removed', False), ('simulated_order_calls', True),
                           ('simulated_order_calls', 1), ('peak_rss_kib', 0),
                           ('peak_rss_kib', 256*1024+1), ('status', 'PASSED_LIVE')]:
            self.assertFalse(prep.valid_result({**PASS, key: value}))
        for value in ([], None, 1, {'secret': 'not returned'}, {**PASS, 'unexpected': True}):
            self.assertFalse(prep.valid_result(value))

    def test_default_never_launches_or_creates_tempfiles(self):
        with patch.object(prep.sys, 'argv', ['prepare_offline.py']), \
             patch.object(prep, 'rehearse', side_effect=AssertionError('NO_LAUNCH')), \
             patch.object(prep.tempfile, 'TemporaryDirectory', side_effect=AssertionError('NO_WRITE')), \
             patch.object(prep.tempfile, 'gettempdir', side_effect=AssertionError('NO_PROBE_WRITE')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            prep.main()
        value = json.loads(output.getvalue())
        self.assertFalse(value['live_capable'])
        self.assertFalse(value['installed'])
        self.assertFalse(value['deployed'])
        self.assertFalse(value['ready_to_trade'])
        self.assertEqual(value['rehearsal']['status'], 'NOT_RUN')

    def test_old_python_and_low_memory_block_child(self):
        with patch.object(prep.sys, 'version_info', (3, 9)), \
             patch.object(prep.sys, 'platform', 'linux'), \
             patch.object(prep, 'memory_mib', return_value={'MemTotal':1024,'MemAvailable':383}):
            result = prep.inspect()
        self.assertFalse(result['rehearsal_prerequisites_met'])
        self.assertIn('PYTHON_311_OR_NEWER_REQUIRED', result['reasons'])
        self.assertIn('AVAILABLE_MEMORY_BELOW_384_MIB', result['reasons'])

    def test_exact_memory_boundary_is_not_rejected(self):
        with patch.object(prep.sys, 'platform', 'linux'), \
             patch.object(prep, 'memory_mib', return_value={'MemTotal':1024,'MemAvailable':384}):
            result = prep.inspect()
        self.assertNotIn('AVAILABLE_MEMORY_BELOW_384_MIB', result['reasons'])
        self.assertIn('ORACLE_VM_ACCEPTANCE', result['unverified'])

    def test_unreadable_os_and_memory_are_not_target_verification(self):
        with patch.object(prep.Path, 'read_text', side_effect=OSError):
            result = prep.inspect()
        self.assertFalse(result['oracle_linux_9'])
        self.assertIn('AVAILABLE_MEMORY_UNKNOWN', result['reasons'])

    def test_unavailable_disk_and_source_are_blockers(self):
        with patch.object(prep.shutil, 'disk_usage', side_effect=OSError), \
             patch.object(prep.Path, 'is_file', return_value=False):
            result = prep.inspect()
        self.assertIn('TEMP_DISK_UNKNOWN', result['reasons'])
        self.assertIn('SOURCE_FILES_MISSING', result['reasons'])

    def test_blocked_prerequisites_do_not_launch(self):
        with patch.object(prep, 'inspect', return_value={'rehearsal_prerequisites_met':False}), \
             patch.object(prep, 'rehearse', side_effect=AssertionError('NO_LAUNCH')), \
             patch.object(prep.sys, 'argv', ['prepare_offline.py', '--rehearse']), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(prep.main(), 2)
        self.assertEqual(json.loads(output.getvalue())['rehearsal']['status'], 'BLOCKED')

    def test_launch_has_fixed_arguments_limits_and_clean_environment(self):
        def fake_run(argv, **kwargs):
            self.assertEqual(argv, [sys.executable, '-I', '-B', str(ROOT/'deploy/oracle/rehearse_offline.py')])
            self.assertEqual(set(kwargs['env']), {'PATH','LANG','TZ','TMPDIR'})
            self.assertEqual(kwargs['timeout'], 45)
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertIs(kwargs['stdout'], kwargs['stderr'])
            kwargs['stdout'].write(json.dumps(PASS).encode())
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(prep.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(prep.rehearse(), PASS)

    def test_timeout_and_launch_failure_are_sanitized(self):
        for error, status in ((subprocess.TimeoutExpired('private', 45), 'TIMEOUT'),
                              (OSError('private'), 'LAUNCH_FAILED')):
            with patch.object(prep.subprocess, 'run', side_effect=error):
                self.assertEqual(prep.rehearse(), {'status':status,'live_capable':False})

    def test_temp_storage_creation_and_cleanup_failures_are_sanitized(self):
        for name in ('TemporaryDirectory', 'TemporaryFile'):
            with patch.object(prep.tempfile, name, side_effect=OSError('private path')):
                self.assertEqual(prep.rehearse(), {'status':'TEMP_STORAGE_FAILED','live_capable':False})
        class FailedCleanup:
            def __enter__(self):
                return '/tmp'
            def __exit__(self, *args):
                raise OSError('private cleanup path')
        with patch.object(prep.tempfile, 'TemporaryDirectory', return_value=FailedCleanup()), \
             patch.object(prep.subprocess, 'run', side_effect=OSError('unavailable')):
            self.assertEqual(prep.rehearse(), {'status':'TEMP_STORAGE_FAILED','live_capable':False})

    def test_invalid_and_failed_child_outputs_are_not_relayed(self):
        for payload, code, status in ((b'private output', 0, 'INVALID_RESULT'),
                                      (json.dumps({**PASS,'live_capable':True}).encode(), 0, 'INVALID_RESULT'),
                                      (b'private error', 2, 'FAILED'),
                                      (b'X'*9000, 0, 'INVALID_RESULT')):
            def fake_run(argv, **kwargs):
                kwargs['stdout'].write(payload)
                return subprocess.CompletedProcess(argv, code)
            with patch.object(prep.subprocess, 'run', side_effect=fake_run):
                self.assertEqual(prep.rehearse(), {'status':status,'live_capable':False})

    def test_network_and_descendant_process_audit_events_are_blocked(self):
        for event in ('socket.__new__', 'socket.connect', 'socket.getaddrinfo',
                      'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.fork', 'os.forkpty'):
            with self.subTest(event=event), self.assertRaises(RuntimeError):
                smoke.deny_external_operations(event, ())
        smoke.deny_external_operations('sqlite3.connect', ())

    def test_real_child_stays_halted_and_cleans_up(self):
        if sys.platform == 'linux':
            result = prep.rehearse()
            self.assertTrue(prep.valid_result(result), result)
        else:
            with self.assertRaisesRegex(RuntimeError, 'LINUX_PYTHON_311_REQUIRED'):
                smoke.run()


if __name__ == '__main__':
    unittest.main()
