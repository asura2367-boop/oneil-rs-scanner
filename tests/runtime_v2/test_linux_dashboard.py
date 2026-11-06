"""Offline Linux checks: actual POSIX files/clocks/SQLite; mount and TLS doubles."""
from dataclasses import asdict
from functools import wraps
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'policy_v2'))
import policy_support as f
from oneil_runtime_v2 import linux_dashboard as dashboard
from oneil_runtime_v2 import linux_storage as storage
from oneil_runtime_v2.health import ClockSample, Evidence, HealthSample, evaluate
from oneil_runtime_v2.linux_clock import elapsed_linux, sample_linux_clock
from oneil_runtime_v2.simulation_host import MODE
from oneil_runtime_v2.simulation_store import SQLiteSimulationStore
from oneil_runtime_v2.supervisor import ACCOUNT, SupervisorState

ROOT = Path(__file__).resolve().parents[2]
MOUNTS = '24 1 8:1 / / rw,relatime - ext4 /dev/example rw\n'


def native_or_rejection(test):
    """Unsupported hosts test the native-entry rejection, never claim native QA."""
    @wraps(test)
    def checked(self, *args, **kwargs):
        if sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() == 0:
            with self.assertRaises(Exception): storage.require_linux_user()
            return
        return test(self, *args, **kwargs)
    return checked


class LinuxClockTests(unittest.TestCase):
    def test_native_sample(self):
        sample = sample_linux_clock()
        self.assertTrue(sample.valid())
        self.assertIsNotNone(sample.awake)

    def test_unsupported_clock(self):
        with patch('oneil_runtime_v2.linux_clock.sys.platform', 'win32'):
            with self.assertRaisesRegex(RuntimeError, 'LINUX_SUSPEND_CLOCK_REQUIRED'):
                sample_linux_clock()

    def test_slow_reversed_and_invalid_sample(self):
        for clocks in ([100, 200, 100, 300], [0, 0, 1, 300_000_000], [0, 0, 100, 10]):
            with self.subTest(clocks=clocks), patch('time.clock_gettime_ns', side_effect=clocks):
                with self.assertRaisesRegex(RuntimeError, 'LINUX_CLOCK_SAMPLE_UNCERTAIN'):
                    sample_linux_clock()
        with patch('time.clock_gettime_ns', side_effect=[0, 0, 10, 20]), patch('time.time_ns', return_value=-1):
            with self.assertRaisesRegex(RuntimeError, 'LINUX_CLOCK_SAMPLE_INVALID'):
                sample_linux_clock()

    def test_suspend_and_clock_change_stay_unhealthy(self):
        previous = ClockSample(1000, 100, 100)
        for clock, reason in ((ClockSample(1030, 130, 101), 'SLEEP_OR_HIBERNATION_DETECTED'),
                              (ClockSample(1009, 101, 101), 'CLOCK_DISCONTINUITY')):
            sample = HealthSample(clock, True, clock.utc, Evidence(True, clock.utc),
                                  Evidence(True, clock.utc), 2**40, False)
            result = evaluate(sample, previous)
            self.assertIn(reason, result.reasons)
            self.assertFalse(result.ready_to_trade)


class MountTests(unittest.TestCase):
    def test_local_ext4_xfs(self):
        for kind in ('ext4', 'xfs'):
            self.assertEqual(storage._mount_for(Path('/home/user/state'), MOUNTS.replace('ext4', kind)), kind)

    def test_remote_overlay_bind_readonly_ambiguous_and_missing(self):
        for data in (MOUNTS.replace('ext4', 'nfs4'), MOUNTS.replace('ext4', 'overlay'),
                     MOUNTS.replace('/ / rw', '/sub / rw'), MOUNTS.replace('rw,relatime', 'ro,relatime'),
                     MOUNTS.replace('/dev/example rw', '/dev/example ro'), MOUNTS + MOUNTS, '', 'broken'):
            with self.subTest(data=data), self.assertRaises(Exception):
                storage._mount_for(Path('/home/user/state'), data)

    def test_longest_mount_wins_and_escaped_paths(self):
        nested = '30 24 0:1 / /home/user rw - nfs4 host:/share rw\n'
        with self.assertRaises(Exception):
            storage._mount_for(Path('/home/user/state'), MOUNTS + nested)
        data = MOUNTS + r'30 24 8:2 / /home/a\040b rw - xfs /dev/example rw' + '\n'
        self.assertEqual(storage._mount_for(Path('/home/a b/state'), data), 'xfs')
        with self.assertRaises(Exception):
            storage._mount_for(Path('/home/x'), MOUNTS.replace(' / / ', r' / /bad\999 '))


class PrivateFilesTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() == 0:
            return
        # The mount type is an explicit double, not an ext4/XFS acceptance claim.
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.file = self.directory / 'private.json'
        self.file.write_text('{}')
        self.file.chmod(0o600)
        guard = patch('socket.socket', side_effect=AssertionError('NO_NETWORK'))
        guard.start(); self.addCleanup(guard.stop)
        mount = patch.object(storage, '_mount_for', return_value='ext4')
        mount.start(); self.addCleanup(mount.stop)

    def test_native_metadata_and_xattrs(self):
        self.assertEqual(storage.inspect_private_path(self.file), self.file)
        self.assertEqual(storage.inspect_profile(self.directory), self.directory)

    def test_root_setuid_and_nonlinux_denied(self):
        for target, value in (('os.geteuid', 0), ('os.getuid', os.getuid() + 1),
                              ('sys.platform', 'darwin')):
            with patch(target, return_value=value) if target.startswith('os.') else patch(target, value):
                with self.assertRaises(Exception): storage.require_linux_user()

    def test_broad_private_mode_and_special_modes_denied(self):
        for mode in (0o644, 0o660, 0o700, 0o1600, 0o2600, 0o4600):
            with self.subTest(mode=mode):
                self.file.chmod(mode)
                with self.assertRaises(Exception): storage.inspect_private_path(self.file)
        self.file.chmod(0o600)

    def test_relative_symlink_hardlink_fifo_and_subdirectory_denied(self):
        with self.assertRaises(Exception): storage.inspect_private_path(Path('relative'))
        with self.assertRaises(Exception): storage.inspect_private_path(self.directory / '..' / self.directory.name / self.file.name)
        link = self.directory / 'link'; link.symlink_to(self.file)
        with self.assertRaises(Exception): storage.inspect_private_path(link)
        link.unlink()
        os.link(self.file, link)
        with self.assertRaises(Exception): storage.inspect_private_path(self.file)
        link.unlink()
        os.mkfifo(link)
        with self.assertRaises(Exception): storage.inspect_private_path(link)
        link.unlink(); link.mkdir(mode=0o700)
        with self.assertRaises(Exception): storage.inspect_profile(self.directory)

    def test_broad_ancestor_and_wrong_owner_denied(self):
        self.directory.chmod(0o777)
        try:
            with self.assertRaises(Exception): storage.inspect_private_path(self.file)
        finally: self.directory.chmod(0o700)
        original = Path.lstat
        def wrong_owner(path):
            info = original(path)
            if path == self.file:
                fields = list(info); fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return info
        with patch.object(Path, 'lstat', wrong_owner):
            with self.assertRaises(Exception): storage.inspect_private_path(self.file)

    def test_acl_or_inspection_error_denied(self):
        for name in ('system.posix_acl_access', 'system.posix_acl_default'):
            with patch('os.listxattr', return_value=[name]):
                with self.assertRaises(Exception): storage.inspect_private_path(self.file)
        with patch('os.listxattr', side_effect=OSError('private path')):
            with self.assertRaises(Exception): storage.inspect_private_path(self.file)
        with patch('os.listxattr', return_value=['security.selinux']):
            storage.inspect_private_path(self.file)


class LinuxLifecycleTests(PrivateFilesTests):
    def setUp(self):
        super().setUp()
        if not hasattr(self, 'directory'): return
        raw = asdict(f.policy(account=ACCOUNT))
        raw['source_observed_at'] = raw['source_observed_at'].isoformat()
        self.file.write_text(json.dumps(raw))
        self.positions = self.directory / 'positions.json'
        self.positions.write_text('{}'); self.positions.chmod(0o600)
        self.profile = self.directory / 'state'
        self.cert, self.key = self.directory / 'cert.pem', self.directory / 'key.pem'
        for path in (self.cert, self.key): path.write_text('PUBLIC TEST PLACEHOLDER'); path.chmod(0o600)

    def initialize(self):
        dashboard.initialize(self.profile, self.file, self.positions, 0, 9443)

    def test_private_initialization_halted_zero_orders_and_never_overwrites(self):
        self.initialize()
        dashboard.check_profile(self.profile, self.cert, self.key, 9443)
        for path in self.profile.iterdir(): self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.profile.stat().st_mode), 0o700)
        store = SQLiteSimulationStore(self.profile / 'simulation.sqlite')
        try: self.assertEqual(store.control(ACCOUNT)['halted'], 1)
        finally: store.close()
        import sqlite3
        with sqlite3.connect(self.profile / 'simulator.sqlite') as conn:
            self.assertEqual(json.loads(conn.execute('SELECT data FROM simulator').fetchone()[0])['calls'], [])
        with self.assertRaises(Exception): self.initialize()

    def test_exact_origin_config_and_inputs(self):
        self.initialize()
        for port in (9444, 443, True):
            with self.assertRaises(Exception): dashboard.check_profile(self.profile, self.cert, self.key, port)
        self.key.chmod(0o644)
        with self.assertRaises(Exception): dashboard.check_profile(self.profile, self.cert, self.key, 9443)
        self.key.chmod(0o600)
        config = self.profile / 'simulation-host.json'
        raw = json.loads(config.read_text()); raw['production_identity_available'] = True
        config.write_text(json.dumps(raw))
        with self.assertRaises(Exception): dashboard.check_profile(self.profile, self.cert, self.key, 9443)

    def test_oversized_json_and_umask_restored(self):
        self.positions.write_text(' ' * (64 * 1024 + 1))
        with self.assertRaises(Exception): self.initialize()
        self.assertFalse(self.profile.exists())
        previous = os.umask(0o022)
        try:
            with self.assertRaises(RuntimeError), dashboard.private_creation():
                self.assertEqual(os.umask(0o077), 0o077)
                raise RuntimeError()
            self.assertEqual(os.umask(0o022), 0o022)
        finally: os.umask(previous)

    def test_start_stop_restart_and_failure_without_socket(self):
        self.initialize()
        server = Mock()
        def stop():
            state = SupervisorState(self.profile)
            try: state.request_stop()
            finally: state.close()
        server.handle_request.side_effect = stop
        with patch.object(dashboard.local_dashboard, 'listener', return_value=server), \
             patch('sys.stdout', new=Mock(isatty=lambda: True)):
            dashboard.run_dashboard(self.profile, self.cert, self.key, 9443)
            dashboard.run_dashboard(self.profile, self.cert, self.key, 9443)
        state = SupervisorState(self.profile)
        try: self.assertEqual(state.status()['phase'], 'STOPPED')
        finally: state.close()
        with patch.object(dashboard.local_dashboard, 'listener', side_effect=RuntimeError('TLS not loaded')), \
             patch('sys.stdout', new=Mock(isatty=lambda: True)):
            with self.assertRaises(RuntimeError): dashboard.run_dashboard(self.profile, self.cert, self.key, 9443)
        state = SupervisorState(self.profile)
        try: self.assertEqual(state.status()['phase'], 'FAILED')
        finally: state.close()
        with patch.object(dashboard.local_dashboard, 'listener') as listener, \
             patch('sys.stdout', new=Mock(isatty=lambda: True)):
            with self.assertRaises(Exception): dashboard.run_dashboard(self.profile, self.cert, self.key, 9443)
            listener.assert_not_called()

    def test_nonterminal_and_untrusted_profile_never_listen(self):
        self.initialize()
        with patch.object(dashboard.local_dashboard, 'listener') as listener, \
             patch('sys.stdout', new=Mock(isatty=lambda: False)):
            with self.assertRaises(Exception): dashboard.run_dashboard(self.profile, self.cert, self.key, 9443)
            listener.assert_not_called()
        self.profile.chmod(0o755)
        with patch.object(dashboard.local_dashboard, 'listener') as listener:
            with self.assertRaises(Exception): dashboard.run_dashboard(self.profile, self.cert, self.key, 9443)
            listener.assert_not_called()

    def test_cli_failure_is_sanitized(self):
        error = io.StringIO()
        with patch('sys.stderr', error), patch.object(dashboard, 'check_profile', side_effect=RuntimeError('SECRET PATH')):
            self.assertEqual(dashboard.main(['check', '--state-dir', 'secret', '--cert', 'cert', '--key', 'key', '--port', '9443']), 2)
        self.assertNotIn('SECRET PATH', error.getvalue())
        self.assertEqual(json.loads(error.getvalue())['live_capable'], False)


class LinuxDeadlineTests(unittest.TestCase):
    def test_pairing_expires_across_suspend(self):
        from oneil_runtime_v2.local_dashboard import PairingApplication
        host = Mock(); host.closed = False; host.origin = 'https://127.0.0.1:9443'
        host.supervisor.status.return_value = {'stop_requested': False}
        with patch('oneil_runtime_v2.linux_clock.sys.platform', 'linux'), \
             patch('time.CLOCK_BOOTTIME', 7, create=True), \
             patch('time.clock_gettime_ns', side_effect=[100_000_000_000, 221_000_000_000], create=True):
            app = PairingApplication(host, monotonic=elapsed_linux)
            host.api._body.return_value = ({'code': app.code}, None)
            captured = []
            env = {'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/v2/pair', 'QUERY_STRING': '',
                   'wsgi.url_scheme': 'https', 'HTTP_HOST': '127.0.0.1:9443',
                   'HTTP_ORIGIN': host.origin}
            payload = b''.join(app(env, lambda status, headers: captured.append(status)))
            self.assertTrue(captured[0].startswith('401'))
            self.assertEqual(json.loads(payload)['error'], 'PAIRING_EXPIRED_OR_USED')
            host.inject_simulation_session.assert_not_called()

    def test_clock_failure_closes_accepted_connection(self):
        from oneil_runtime_v2.local_dashboard import BoundedServer
        server = object.__new__(BoundedServer)
        connection = Mock(); server.socket = Mock()
        server.socket.accept.return_value = (connection, ('127.0.0.1', 2000))
        server.deadline_clock = Mock(side_effect=RuntimeError('unavailable clock'))
        with self.assertRaises(RuntimeError): server.get_request()
        connection.close.assert_called_once()

    def test_wire_uses_suspend_inclusive_deadline(self):
        from oneil_runtime_v2.local_dashboard import QuietHandler
        connection = Mock(); connection._oneil_deadline = 102
        connection._oneil_clock = lambda: 221
        connection.recv.return_value = b'G'
        handler = object.__new__(QuietHandler); handler.connection = connection; handler.server = Mock()
        handler.handle()
        connection.recv.assert_not_called(); connection.sendall.assert_not_called()
        handler.server.get_app.assert_not_called()


# On unsupported hosts, assert rejection without marking native behavior passed.
for cls in (LinuxClockTests, PrivateFilesTests, LinuxLifecycleTests):
    for name, method in list(vars(cls).items()):
        if name.startswith('test_') and name != 'test_unsupported_clock':
            setattr(cls, name, native_or_rejection(method))

del cls, name, method

# Share helpers, not inherited test cases.
for name in vars(PrivateFilesTests):
    if name.startswith('test_') and name not in LinuxLifecycleTests.__dict__:
        setattr(LinuxLifecycleTests, name, None)

if __name__ == '__main__': unittest.main()
