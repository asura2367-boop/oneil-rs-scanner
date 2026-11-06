"""Linux sealed input tests: synthetic values only, no broker or stored secret."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
import pickle
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import AccountBinding
from oneil_execution_v2.kis_app_credentials import AppCredentials
from oneil_execution_v2.kis_authenticated import Cancellation
from oneil_execution_v2.kis_local_authorization import LocalPermission
from oneil_execution_v2.kis_linux_credentials import (LinuxCredentialInput, _Failure,
                                                      linux_credential_elapsed)
from oneil_execution_v2 import kis_linux_credentials as module

if sys.platform == 'linux':
    import fcntl

BINDING = AccountBinding('a' * 64, 'demo', '12345678', '01')
KEY = 'SYNTHETIC_LINUX_APP_KEY'
SECRET = 'SYNTHETIC_LINUX_APP_SECRET'
F_ADD_SEALS = 1033
REQUIRED = 0x0008 | 0x0004 | 0x0002 | 0x0001


def payload(binding=BINDING, key=KEY, secret=SECRET):
    # Test-only writer. The production reader supplies no provisioning function.
    digest = hashlib.sha256('\0'.join((binding.account, binding.environment,
                          binding.cano, binding.product_code)).encode('ascii')).digest()
    return b'ONL1' + digest + len(key).to_bytes(2, 'big') + key.encode('ascii') + secret.encode('ascii')


def permission(*actions, binding=BINDING):
    now = datetime.now(timezone.utc)
    return LocalPermission(binding, actions=tuple(actions), issued_at=now,
                           expires_at=now + timedelta(seconds=120))


class LinuxCredentialTests(unittest.TestCase):
    def setUp(self):
        self.reader = LinuxCredentialInput()

    def descriptor(self, body=None, seals=REQUIRED):
        fd = os.memfd_create('oneil-synthetic-test', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        self.addCleanup(os.close, fd)
        os.write(fd, payload() if body is None else body)
        if seals:
            fcntl.fcntl(fd, F_ADD_SEALS, seals)
        return fd

    def kwargs(self, descriptor=None, *actions):
        return dict(descriptor=self.descriptor() if descriptor is None else descriptor,
            permission=permission(*(actions or ('read_app_credentials',))),
            deadline_monotonic=linux_credential_elapsed()+20, cancellation=Cancellation())

    def test_native_sealed_input_and_original_offset_preserved(self):
        fd = self.descriptor()
        os.lseek(fd, 4, os.SEEK_SET)
        result = self.reader.read(BINDING, **self.kwargs(fd))
        self.assertEqual(result, AppCredentials(KEY, SECRET))
        self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 4)
        self.assertEqual(os.fstat(fd).st_size, len(payload()))
        self.assertFalse(self.reader.live_capable)

    def test_constructor_has_no_input_or_network_side_effect(self):
        with patch.object(module, '_read_sealed_descriptor', side_effect=AssertionError), \
             patch.object(module.os, 'getenv', side_effect=AssertionError), \
             patch('builtins.open', side_effect=AssertionError):
            reader = LinuxCredentialInput()
            self.assertIn('inactive=True', repr(reader))
            self.assertFalse(hasattr(reader, 'write'))
            self.assertFalse(hasattr(reader, 'acquire'))

    def test_neutral_type_is_windows_compatible_and_redacted(self):
        from oneil_execution_v2.kis_windows_credentials import AppCredentials as WindowsPair
        from oneil_execution_v2.kis_token_issuer import AppCredentials as IssuerPair
        self.assertIs(AppCredentials, WindowsPair)
        self.assertIs(AppCredentials, IssuerPair)
        result = self.reader.read(BINDING, **self.kwargs())
        for item in (result, self.reader):
            self.assertNotIn(KEY, repr(item)); self.assertNotIn(SECRET, repr(item))
            with self.assertRaises(TypeError):
                pickle.dumps(item)
        with self.assertRaises(TypeError):
            vars(result)

    def test_linux_read_does_not_import_windows_credentials(self):
        # The new module and shared pair have no Windows store dependency.
        import ast
        from pathlib import Path
        for name in ('kis_linux_credentials.py', 'kis_app_credentials.py'):
            tree = ast.parse((Path(module.__file__).parent/name).read_text())
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertNotIn('kis_windows_credentials', imports)

    def test_missing_or_wrong_action_permission_prevents_os_read(self):
        for grant in (None, object(), permission(), permission('store_app_credentials'),
                      permission('issue_token'), permission('authenticated_get')):
            kwargs = self.kwargs(); kwargs['permission'] = grant
            with patch.object(module, '_read_sealed_descriptor') as native:
                with self.assertRaisesRegex(Blocked, 'PERMISSION_REQUIRED'):
                    self.reader.read(BINDING, **kwargs)
                native.assert_not_called()

    def test_permission_scope_mismatch_prevents_os_read(self):
        for other in (replace(BINDING, account='b'*64), replace(BINDING, environment='real'),
                      replace(BINDING, cano='87654321'), replace(BINDING, product_code='02')):
            with self.subTest(other=other), patch.object(module, '_read_sealed_descriptor') as native:
                with self.assertRaisesRegex(Blocked, 'PERMISSION_REQUIRED'):
                    self.reader.read(other, **self.kwargs())
                native.assert_not_called()

    def test_payload_binding_mismatch_is_rejected(self):
        for other in (replace(BINDING, account='b'*64), replace(BINDING, environment='real'),
                      replace(BINDING, cano='87654321'), replace(BINDING, product_code='02')):
            fd = self.descriptor(payload(other))
            with self.assertRaisesRegex(Blocked, 'INPUT_REJECTED'):
                self.reader.read(BINDING, **self.kwargs(fd))

    def test_platform_rejected_before_input_read(self):
        kwargs = self.kwargs()
        for platform in ('win32', 'darwin', 'freebsd'):
            with patch.object(module.sys, 'platform', platform), \
                 patch.object(module, '_read_sealed_descriptor') as native:
                with self.assertRaisesRegex(Blocked, 'PLATFORM_REQUIRED'):
                    self.reader.read(BINDING, **kwargs)
                native.assert_not_called()

    def test_invalid_descriptors_and_binding_rejected(self):
        for fd in (-1, 0, 1, 2, True, 1048576, 3.0, '3'):
            kwargs = self.kwargs(); kwargs['descriptor'] = fd
            with patch.object(module, '_read_sealed_descriptor') as native:
                with self.assertRaises(Blocked):
                    self.reader.read(BINDING, **kwargs)
                native.assert_not_called()
        for binding in (None, object(), replace(BINDING, cano='secret path')):
            with self.assertRaises(Blocked) as ctx:
                self.reader.read(binding, **self.kwargs())
            self.assertNotIn('secret path', str(ctx.exception))

    def test_closed_descriptor_is_sanitized(self):
        fd = os.dup(self.descriptor()); os.close(fd)
        with self.assertRaisesRegex(Blocked, '^KIS_LINUX_CREDENTIALS_READ_FAILED$'):
            self.reader.read(BINDING, **self.kwargs(fd))

    def test_each_required_seal_is_enforced(self):
        for missing in (0x0008, 0x0004, 0x0002, 0x0001):
            fd = self.descriptor(seals=REQUIRED & ~missing)
            with self.assertRaisesRegex(Blocked, 'INPUT_REJECTED'):
                self.reader.read(BINDING, **self.kwargs(fd))

    def test_unsealed_input_is_rejected_without_pread(self):
        fd = self.descriptor(seals=0)
        with patch.object(module.os, 'preadv') as pread:
            with self.assertRaisesRegex(Blocked, 'INPUT_REJECTED'):
                self.reader.read(BINDING, **self.kwargs(fd))
            pread.assert_not_called()

    def test_regular_path_and_unlinked_file_are_not_fallbacks(self):
        with tempfile.NamedTemporaryFile() as regular, tempfile.TemporaryFile() as unlinked:
            for stream in (regular, unlinked):
                stream.write(payload()); stream.flush()
                with patch.object(module.os, 'preadv') as pread:
                    with self.assertRaises(Blocked):
                        self.reader.read(BINDING, **self.kwargs(stream.fileno()))
                    pread.assert_not_called()

    def test_pipe_is_rejected_without_read(self):
        read, write = os.pipe()
        self.addCleanup(os.close, read); self.addCleanup(os.close, write)
        with patch.object(module.os, 'preadv') as pread:
            with self.assertRaisesRegex(Blocked, 'INPUT_REJECTED'):
                self.reader.read(BINDING, **self.kwargs(read))
            pread.assert_not_called()

    def test_owner_mismatch_rejected(self):
        fd = self.descriptor()
        with patch.object(module.os, 'geteuid', return_value=os.geteuid()+1), \
             patch.object(module.os, 'preadv') as pread:
            with self.assertRaisesRegex(Blocked, 'INPUT_REJECTED'):
                self.reader.read(BINDING, **self.kwargs(fd))
            pread.assert_not_called()

    def test_size_bounds_checked_before_read(self):
        for body in (b'', b'x'*53, b'x'*2087):
            fd = self.descriptor(body)
            with patch.object(module.os, 'preadv') as pread:
                with self.assertRaisesRegex(Blocked, 'INPUT_REJECTED'):
                    self.reader.read(BINDING, **self.kwargs(fd))
                pread.assert_not_called()

    def test_minimum_and_maximum_pair_lengths(self):
        for size in (8, 1024):
            fd = self.descriptor(payload(key='K'*size, secret='S'*size))
            result = self.reader.read(BINDING, **self.kwargs(fd))
            self.assertEqual(len(result.app_key), size)
            self.assertEqual(len(result.app_secret), size)

    def test_malformed_payload_rejected(self):
        valid = payload()
        cases = [b'BAD1'+valid[4:], valid[:36]+b'\xff\xff'+valid[38:],
                 payload(key='short', secret=SECRET), payload(key=KEY, secret='bad secret'),
                 valid[:-1]+b'\xff', valid[:-1]+b'\x00', valid+b'\n']
        for body in cases:
            with self.assertRaises(Blocked) as ctx:
                self.reader.read(BINDING, **self.kwargs(self.descriptor(body)))
            self.assertNotIn(KEY, str(ctx.exception)); self.assertNotIn(SECRET, str(ctx.exception))

    def test_native_read_buffer_wiped_on_success_and_decode_failure(self):
        for body in (payload(), b'BAD1'+payload()[4:]):
            buffer = bytearray(body)
            with patch.object(module, '_read_sealed_descriptor', return_value=buffer):
                if body[:4] == b'ONL1':
                    self.reader.read(BINDING, **self.kwargs())
                else:
                    with self.assertRaises(Blocked):
                        self.reader.read(BINDING, **self.kwargs())
            self.assertEqual(set(buffer), {0})

    def test_short_native_read_wipes_buffer(self):
        seen = []
        def short(fd, buffers, offset):
            seen.append(buffers[0]); buffers[0][:] = payload()
            return len(payload())-1
        with patch.object(module.os, 'preadv', side_effect=short):
            with self.assertRaisesRegex(Blocked, 'INPUT_REJECTED'):
                self.reader.read(BINDING, **self.kwargs())
        self.assertEqual(set(seen[0]), {0})

    def test_cancel_before_read_and_invalid_deadlines(self):
        kwargs = self.kwargs(); kwargs['cancellation'].cancel()
        with patch.object(module, '_read_sealed_descriptor') as native:
            with self.assertRaisesRegex(Blocked, 'CANCELLED'):
                self.reader.read(BINDING, **kwargs)
            native.assert_not_called()
        for deadline in (0, True, float('nan'), float('inf'), linux_credential_elapsed()+121):
            kwargs = self.kwargs(); kwargs['deadline_monotonic'] = deadline
            with patch.object(module, '_read_sealed_descriptor') as native:
                with self.assertRaisesRegex(Blocked, 'DEADLINE_EXCEEDED'):
                    self.reader.read(BINDING, **kwargs)
                native.assert_not_called()

    def test_default_clock_counts_suspend_and_rejects_late_result(self):
        ticks = [100_000_000_000]
        buffer = bytearray(payload())
        kwargs = self.kwargs(); kwargs['deadline_monotonic'] = 102.0
        def suspended_read(fd, guard):
            ticks[0] = 221_000_000_000
            return buffer
        with patch.object(module.time, 'clock_gettime_ns', side_effect=lambda clock: ticks[0]) as clock, \
             patch.object(module, '_read_sealed_descriptor', side_effect=suspended_read):
            with self.assertRaisesRegex(Blocked, 'DEADLINE_EXCEEDED'):
                self.reader.read(BINDING, **kwargs)
        self.assertTrue(clock.call_args_list)
        self.assertTrue(all(call.args == (time.CLOCK_BOOTTIME,) for call in clock.call_args_list))
        self.assertEqual(set(buffer), {0})

    def test_unavailable_suspend_clock_has_no_monotonic_fallback(self):
        kwargs = self.kwargs()
        with patch.object(module.time, 'clock_gettime_ns', side_effect=OSError('private clock detail')), \
             patch.object(module, '_read_sealed_descriptor') as native:
            with self.assertRaisesRegex(Blocked, '^KIS_LINUX_CREDENTIALS_READ_FAILED$'):
                self.reader.read(BINDING, **kwargs)
            native.assert_not_called()
            with self.assertRaisesRegex(Blocked, '^KIS_LINUX_CREDENTIALS_CLOCK_INVALID$'):
                linux_credential_elapsed()

    def test_rollback_and_invalid_clock_fail_closed(self):
        for tick in (-1, True, float('inf'), float('nan')):
            reader = LinuxCredentialInput(monotonic=lambda: tick)
            with self.assertRaisesRegex(Blocked, 'CLOCK_INVALID'):
                reader.read(BINDING, **self.kwargs())
        ticks = iter((100, 99))
        reader = LinuxCredentialInput(monotonic=lambda: next(ticks, 99))
        kwargs = self.kwargs(); kwargs['deadline_monotonic'] = 102
        with self.assertRaisesRegex(Blocked, 'CLOCK_INVALID'):
            reader.read(BINDING, **kwargs)

    def test_stalled_read_cancellation_deadline_and_revocation_drop_late_result(self):
        for stop in ('cancel', 'deadline', 'revoke'):
            ticks = [100.0]
            reader = LinuxCredentialInput(monotonic=lambda: ticks[0])
            entered = threading.Event(); release = threading.Event()
            buffer = bytearray(payload()); errors = []
            kwargs = self.kwargs(); kwargs['deadline_monotonic'] = 102.0
            def stalled(fd, guard):
                entered.set(); release.wait(5); return buffer
            def run():
                try: reader.read(BINDING, **kwargs)
                except Blocked as exc: errors.append(str(exc))
            with patch.object(module, '_read_sealed_descriptor', side_effect=stalled):
                caller = threading.Thread(target=run); caller.start()
                try:
                    self.assertTrue(entered.wait(1))
                    if stop == 'cancel': kwargs['cancellation'].cancel()
                    if stop == 'revoke': kwargs['permission'].revoke()
                    if stop == 'deadline': ticks[0] = 103.0
                    caller.join(2); self.assertFalse(caller.is_alive())
                    self.assertEqual(len(errors), 1)
                    expected = {'cancel': 'CANCELLED', 'deadline': 'DEADLINE_EXCEEDED',
                                'revoke': 'PERMISSION_REQUIRED'}[stop]
                    self.assertTrue(errors[0].endswith(expected))
                    with self.assertRaisesRegex(Blocked, 'BUSY'):
                        busy_kwargs = self.kwargs(); busy_kwargs['deadline_monotonic'] = 105.0
                        reader.read(BINDING, **busy_kwargs)
                finally:
                    release.set(); caller.join(1)
                    self.assertTrue(reader._busy.acquire(timeout=1)); reader._busy.release()
            self.assertEqual(set(buffer), {0})

    def test_revoked_after_read_never_returns_pair(self):
        kwargs = self.kwargs(); buffer = bytearray(payload())
        def read(fd, guard):
            kwargs['permission'].revoke(); return buffer
        with patch.object(module, '_read_sealed_descriptor', side_effect=read):
            with self.assertRaisesRegex(Blocked, 'PERMISSION_REQUIRED'):
                self.reader.read(BINDING, **kwargs)
        self.assertEqual(set(buffer), {0})

    def test_failed_guard_cannot_recover_and_read_reused_original(self):
        ticks = [100.0]; entered = threading.Event(); release = threading.Event()
        reader = LinuxCredentialInput(monotonic=lambda: ticks[0])
        fd = self.descriptor(); replacement = self.descriptor(payload(key='ANOTHER_SYNTHETIC_KEY'))
        kwargs = self.kwargs(fd); kwargs['deadline_monotonic'] = 110.0
        native_read = module._read_sealed_descriptor; native_pread = os.preadv
        errors = []; owned_seen = []
        def stalled(owned, guard):
            owned_seen.append(owned); entered.set(); release.wait(5)
            return native_read(owned, guard)
        def run():
            try: reader.read(BINDING, **kwargs)
            except Blocked as exc: errors.append(str(exc))
        with patch.object(module, '_read_sealed_descriptor', side_effect=stalled), \
             patch.object(module.os, 'preadv', wraps=native_pread) as pread:
            caller = threading.Thread(target=run); caller.start()
            try:
                self.assertTrue(entered.wait(2)); self.assertNotEqual(owned_seen[0], fd)
                ticks[0] = float('nan')
                caller.join(2); self.assertFalse(caller.is_alive())
                self.assertEqual(errors, ['KIS_LINUX_CREDENTIALS_CLOCK_INVALID'])
                # Once read returns, its caller is allowed to reuse the original.
                os.dup2(replacement, fd); ticks[0] = 100.0
            finally:
                release.set(); caller.join(2)
                self.assertTrue(reader._busy.acquire(timeout=2)); reader._busy.release()
            pread.assert_not_called()

    def test_keyboard_interrupt_abandons_and_wipes_late_worker_buffer(self):
        entered = threading.Event(); release = threading.Event(); buffer = bytearray(payload())
        kwargs = self.kwargs(); event_type = threading.Event; events = []
        def event_factory():
            event = event_type()
            if not events:
                def interrupted_wait(timeout=None):
                    self.assertTrue(entered.wait(2))
                    raise KeyboardInterrupt()
                event.wait = interrupted_wait
            events.append(event)
            return event
        def stalled(fd, guard):
            entered.set(); release.wait(5); return buffer
        with patch.object(module.threading, 'Event', side_effect=event_factory), \
             patch.object(module, '_read_sealed_descriptor', side_effect=stalled):
            try:
                with self.assertRaises(KeyboardInterrupt):
                    self.reader.read(BINDING, **kwargs)
            finally:
                release.set()
                self.assertTrue(self.reader._busy.acquire(timeout=2)); self.reader._busy.release()
        self.assertEqual(set(buffer), {0})

    def test_interrupted_thread_start_reclaims_unclaimed_descriptor(self):
        from pathlib import Path
        kwargs = self.kwargs(); before = len(list(Path('/proc/self/fd').iterdir()))
        with patch('threading.Thread.start', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.reader.read(BINDING, **kwargs)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)
        self.assertEqual(self.reader.read(BINDING, **kwargs), AppCredentials(KEY, SECRET))

    def test_coordination_allocation_failure_owns_no_descriptor_or_busy_lock(self):
        from pathlib import Path
        kwargs = self.kwargs(); before = len(list(Path('/proc/self/fd').iterdir()))
        for error in (MemoryError(SECRET), KeyboardInterrupt()):
            with patch.object(module.threading, 'Event', side_effect=error):
                with self.assertRaises(KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else Blocked):
                    self.reader.read(BINDING, **kwargs)
            self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)
            self.assertEqual(self.reader.read(BINDING, **kwargs), AppCredentials(KEY, SECRET))

    def test_interruption_after_result_handoff_still_wipes_owned_buffer(self):
        buffer = bytearray(payload()); original = self.reader._run
        def interrupt_after_transfer(descriptor, guard, slot):
            original(descriptor, guard, slot)
            self.assertIs(slot['buffer'], buffer)
            raise KeyboardInterrupt()
        with patch.object(module, '_read_sealed_descriptor', return_value=buffer), \
             patch.object(self.reader, '_run', side_effect=interrupt_after_transfer):
            with self.assertRaises(KeyboardInterrupt):
                self.reader.read(BINDING, **self.kwargs())
        self.assertEqual(set(buffer), {0})

    def test_interrupted_start_never_closes_descriptor_owned_by_worker(self):
        entered = threading.Event(); release = threading.Event(); buffer = bytearray(payload())
        kwargs = self.kwargs(); real_start = threading.Thread.start; owned_seen = []
        def stalled(owned, guard):
            owned_seen.append(owned); entered.set(); release.wait(5); return buffer
        def interrupted_start(thread):
            real_start(thread)
            self.assertTrue(entered.wait(2))
            raise KeyboardInterrupt()
        with patch('threading.Thread.start', new=interrupted_start), \
             patch.object(module, '_read_sealed_descriptor', side_effect=stalled):
            try:
                with self.assertRaises(KeyboardInterrupt):
                    self.reader.read(BINDING, **kwargs)
                self.assertEqual(os.fstat(owned_seen[0]).st_size, len(payload()))
            finally:
                release.set()
                self.assertTrue(self.reader._busy.acquire(timeout=2)); self.reader._busy.release()
        self.assertEqual(set(buffer), {0})
        with self.assertRaises(OSError):
            os.fstat(owned_seen[0])

    def test_private_dependency_errors_never_escape(self):
        for error in (RuntimeError(SECRET), _Failure(SECRET), OSError(SECRET)):
            with patch.object(module, '_read_sealed_descriptor', side_effect=error):
                with self.assertRaisesRegex(Blocked, '^KIS_LINUX_CREDENTIALS_READ_FAILED$'):
                    self.reader.read(BINDING, **self.kwargs())

    def test_thread_start_failure_releases_single_flight(self):
        kwargs = self.kwargs()
        with patch('threading.Thread.start', side_effect=RuntimeError(SECRET)):
            with self.assertRaisesRegex(Blocked, 'READ_FAILED'):
                self.reader.read(BINDING, **kwargs)
        self.assertEqual(self.reader.read(BINDING, **kwargs), AppCredentials(KEY, SECRET))

    def test_repeated_reads_do_not_cache_or_leak_descriptors(self):
        from pathlib import Path
        fd = self.descriptor()
        before = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(25):
            self.assertEqual(self.reader.read(BINDING, **self.kwargs(fd)), AppCredentials(KEY, SECRET))
        self.assertTrue(self.reader._busy.acquire(timeout=1)); self.reader._busy.release()
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    def test_existing_linux_network_gates_stay_blocked(self):
        from oneil_execution_v2.kis_token_issuer import KisTokenIssuer
        from oneil_execution_v2.kis_authenticated_https import KisAuthenticatedHttpsGetClient
        with self.assertRaisesRegex(Blocked, 'PLATFORM_BLOCKED'):
            KisTokenIssuer().issue(BINDING, AppCredentials(KEY, SECRET),
                permission=permission('issue_token'), minimum_validity_seconds=30,
                deadline_monotonic=time.monotonic()+2, cancellation=Cancellation())
        client = KisAuthenticatedHttpsGetClient(BINDING, permission=permission('authenticated_get'))
        with self.assertRaisesRegex(Blocked, 'WINDOWS_REQUIRED'):
            client.open_authenticated_get(None)


class PortablePlatformRefusalTests(unittest.TestCase):
    def isolated(self, script):
        from pathlib import Path
        root = str(Path(__file__).resolve().parents[2])
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, root],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
            env={name: os.environ[name] for name in ('PATH', 'LANG', 'SYSTEMROOT')
                 if name in os.environ})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dashboard_and_oracle_imports_never_load_credentials_or_network(self):
        self.isolated("""
import sys, runpy
from pathlib import Path
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system'):
        raise AssertionError('INACTIVE_IMPORT_STARTED_IO')
sys.addaudithook(audit)
import oneil_runtime_v2.linux_dashboard
for name in ('prepare_offline.py', 'rehearse_offline.py', 'run_dashboard.py'):
    runpy.run_path(str(Path(sys.argv[1])/'deploy/oracle'/name), run_name='inert_review')
for name in ('kis_linux_credentials', 'kis_windows_credentials',
             'kis_token_issuer', 'kis_authenticated_https'):
    assert 'oneil_execution_v2.' + name not in sys.modules, name
""")

    def test_fresh_credential_import_and_constructor_are_inert(self):
        self.isolated("""
import sys, os, threading
sys.path.insert(0, sys.argv[1])
def denied(*args, **kwargs):
    raise AssertionError('INACTIVE_CREDENTIAL_IO')
def audit(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system'):
        denied()
sys.addaudithook(audit)
for name in ('preadv', 'memfd_create'):
    if hasattr(os, name): setattr(os, name, denied)
threading.Thread.start = denied
from oneil_execution_v2.kis_linux_credentials import LinuxCredentialInput
reader = LinuxCredentialInput()
assert reader.live_capable is False
assert 'oneil_execution_v2.kis_windows_credentials' not in sys.modules
assert 'oneil_execution_v2.kis_token_issuer' not in sys.modules
assert 'oneil_execution_v2.kis_authenticated_https' not in sys.modules
""")

    def test_non_linux_never_reads_supplied_descriptor(self):
        for platform in ('win32', 'darwin', 'freebsd'):
            with patch.object(module.sys, 'platform', platform), \
                 patch.object(module, '_read_sealed_descriptor') as native:
                with self.assertRaisesRegex(Blocked, 'PLATFORM_REQUIRED'):
                    LinuxCredentialInput().read(BINDING, descriptor=3,
                        permission=permission('read_app_credentials'),
                        deadline_monotonic=time.monotonic()+20, cancellation=Cancellation())
                native.assert_not_called()


def load_tests(loader, tests, pattern):
    # Native memfd acceptance is Linux-only. Other platforms run the refusal
    # boundary, not fake passing native tests or suite-failing skip placeholders.
    suite = loader.loadTestsFromTestCase(PortablePlatformRefusalTests)
    if sys.platform == 'linux':
        suite.addTests(loader.loadTestsFromTestCase(LinuxCredentialTests))
    return suite


if __name__ == '__main__':
    unittest.main()
