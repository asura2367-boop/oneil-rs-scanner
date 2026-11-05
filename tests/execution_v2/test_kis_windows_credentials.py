"""Synthetic-only OS-store tests. No Windows DLL, actual credential or network I/O."""
import ctypes
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import pickle
import threading
import time
import unittest
from unittest.mock import patch

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import AccountBinding
from oneil_execution_v2.kis_authenticated import Cancellation, CredentialLease
from oneil_execution_v2.kis_local_authorization import LocalPermission
from oneil_execution_v2.kis_windows_credentials import (
    AppCredentials, WindowsCredentialStore, WindowsCredentialProvider,
    credential_target, _Credential, _Win32Backend, _encode, _Failure)

BINDING = AccountBinding('a' * 64, 'demo', '12345678', '01')
CREDS = AppCredentials('SYNTHETIC_APP_KEY', 'SYNTHETIC_APP_SECRET')


def permission(*actions, binding=BINDING):
    now = datetime.now(timezone.utc)
    return LocalPermission(binding, actions=tuple(actions), issued_at=now,
                           expires_at=now + timedelta(minutes=1))


class Backend:
    def __init__(self):
        self.calls = []; self.buffer = None; self.started = threading.Event()
        self.release = None; self.finished = threading.Event()

    def read(self, target):
        self.calls.append(('read', target)); self.started.set()
        if self.release:
            self.release.wait(2)
        self.buffer = _encode(CREDS)
        self.finished.set()
        return self.buffer

    def write(self, target, blob):
        self.calls.append(('write', target)); self.buffer = blob; self.started.set()
        if self.release:
            self.release.wait(2)
        self.finished.set()


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.store = WindowsCredentialStore(backend=self.backend)
        self.platform = patch('oneil_execution_v2.kis_windows_credentials.sys.platform', 'win32')
        self.platform.start(); self.addCleanup(self.platform.stop)

    def kwargs(self, *actions):
        return dict(permission=permission(*actions), deadline_monotonic=time.monotonic()+2,
                    cancellation=Cancellation())

    def test_exact_scope_target(self):
        baseline = credential_target(BINDING)
        for other in (replace(BINDING, account='b'*64), replace(BINDING, environment='real'),
                      replace(BINDING, cano='87654321'), replace(BINDING, product_code='02')):
            self.assertNotEqual(credential_target(other), baseline)
        self.assertNotIn(BINDING.cano, baseline)

    def test_read_wipes_buffer_and_hides_secrets(self):
        result = self.store.read(BINDING, **self.kwargs('read_app_credentials'))
        self.assertEqual(result, CREDS)
        self.assertEqual(set(self.backend.buffer), {0})
        self.assertNotIn(CREDS.app_secret, repr(result))
        with self.assertRaises(TypeError):
            pickle.dumps(result)
        with self.assertRaises(TypeError):
            asdict(result)
        with self.assertRaises(TypeError):
            vars(result)

    def test_write_single_use_and_wiped(self):
        kwargs = self.kwargs('store_app_credentials')
        self.store.write(BINDING, CREDS, **kwargs)
        self.assertEqual(set(self.backend.buffer), {0})
        with self.assertRaises(Blocked):
            self.store.write(BINDING, CREDS, **kwargs)
        self.assertEqual(len(self.backend.calls), 1)

    def test_read_does_not_authorize_write(self):
        with self.assertRaises(Blocked):
            self.store.write(BINDING, CREDS, **self.kwargs('read_app_credentials'))
        self.assertEqual(self.backend.calls, [])

    def test_write_does_not_authorize_read(self):
        with self.assertRaises(Blocked):
            self.store.read(BINDING, **self.kwargs('store_app_credentials'))
        self.assertEqual(self.backend.calls, [])

    def test_scope_mismatch_and_non_windows_fail_closed(self):
        kwargs = self.kwargs('read_app_credentials')
        with self.assertRaises(Blocked):
            self.store.read(replace(BINDING, environment='real'), **kwargs)
        with patch('oneil_execution_v2.kis_windows_credentials.sys.platform', 'linux'):
            with self.assertRaisesRegex(Blocked, 'WINDOWS_REQUIRED'):
                self.store.read(BINDING, **kwargs)
        self.assertEqual(self.backend.calls, [])

    def test_cancel_and_expiry_before_call(self):
        kwargs = self.kwargs('read_app_credentials'); kwargs['cancellation'].cancel()
        with self.assertRaisesRegex(Blocked, 'CANCELLED'):
            self.store.read(BINDING, **kwargs)
        kwargs = self.kwargs('read_app_credentials'); kwargs['deadline_monotonic'] = 0
        with self.assertRaisesRegex(Blocked, 'DEADLINE'):
            self.store.read(BINDING, **kwargs)
        self.assertEqual(self.backend.calls, [])

    def test_cancel_inflight_read_wipes_late_buffer_and_caps_worker(self):
        self.backend.release = threading.Event()
        kwargs = self.kwargs('read_app_credentials')
        errors = []
        def read():
            try:
                self.store.read(BINDING, **kwargs)
            except Blocked as exc:
                errors.append(str(exc))
        reader = threading.Thread(target=read); reader.start()
        self.assertTrue(self.backend.started.wait(1)); kwargs['cancellation'].cancel()
        reader.join(1); self.assertFalse(reader.is_alive())
        self.assertEqual(errors, ['KIS_CREDENTIALS_CANCELLED'])
        with self.assertRaisesRegex(Blocked, 'BUSY'):
            self.store.read(BINDING, **self.kwargs('read_app_credentials'))
        self.backend.release.set()
        self.assertTrue(self.backend.finished.wait(1))
        self.assertTrue(self.store._busy.acquire(timeout=1)); self.store._busy.release()
        self.assertEqual(set(self.backend.buffer), {0})

    def test_timeout_write_is_unknown_and_grant_consumed(self):
        self.backend.release = threading.Event()
        kwargs = self.kwargs('store_app_credentials')
        kwargs['deadline_monotonic'] = time.monotonic()+0.05
        with self.assertRaisesRegex(Blocked, 'WRITE_OUTCOME_UNKNOWN'):
            self.store.write(BINDING, CREDS, **kwargs)
        with self.assertRaises(Blocked):
            kwargs['permission'].check(BINDING, 'store_app_credentials')
        self.backend.release.set()
        self.assertTrue(self.store._busy.acquire(timeout=1)); self.store._busy.release()
        self.assertEqual(set(self.backend.buffer), {0})

    def test_read_deadline_wipes_late_result(self):
        self.backend.release = threading.Event()
        kwargs = self.kwargs('read_app_credentials')
        kwargs['deadline_monotonic'] = time.monotonic()+0.05
        with self.assertRaisesRegex(Blocked, 'DEADLINE'):
            self.store.read(BINDING, **kwargs)
        self.backend.release.set()
        self.assertTrue(self.store._busy.acquire(timeout=1)); self.store._busy.release()
        self.assertEqual(set(self.backend.buffer), {0})

    def test_revocation_during_read_drops_result(self):
        kwargs = self.kwargs('read_app_credentials')
        original = self.backend.read
        def revoke(target):
            result = original(target)
            kwargs['permission'].revoke()
            return result
        self.backend.read = revoke
        with self.assertRaises(Blocked):
            self.store.read(BINDING, **kwargs)
        self.assertEqual(set(self.backend.buffer), {0})

    def test_provider_requires_issuance_permission_before_read(self):
        provider = WindowsCredentialProvider(self.store, object(), permission('read_app_credentials'))
        with self.assertRaises(Blocked):
            provider.acquire(BINDING, minimum_validity_seconds=30,
                deadline_monotonic=time.monotonic()+2, cancellation=Cancellation())
        self.assertEqual(self.backend.calls, [])

    def test_stalled_operations_observe_permission_revoke_and_expiry(self):
        for operation in ('read', 'write'):
            for change in ('revoke', 'expire'):
                with self.subTest(operation=operation, change=change):
                    backend = Backend(); backend.release = threading.Event()
                    store = WindowsCredentialStore(backend=backend)
                    now = datetime.now(timezone.utc)
                    clock = [now, 100.0]
                    action = 'read_app_credentials' if operation == 'read' else 'store_app_credentials'
                    grant = LocalPermission(BINDING, actions=(action,), issued_at=now,
                        expires_at=now+timedelta(seconds=30),
                        utc_now=lambda: clock[0], monotonic=lambda: clock[1])
                    errors = []
                    def run():
                        try:
                            args = (BINDING,) if operation == 'read' else (BINDING, CREDS)
                            getattr(store, operation)(*args, permission=grant,
                                deadline_monotonic=time.monotonic()+2, cancellation=Cancellation())
                        except Blocked as exc:
                            errors.append(str(exc))
                    caller = threading.Thread(target=run); caller.start()
                    try:
                        self.assertTrue(backend.started.wait(1))
                        if change == 'revoke':
                            grant.revoke()
                        else:
                            clock[0] += timedelta(seconds=31); clock[1] += 31
                        caller.join(0.5)
                        self.assertFalse(caller.is_alive())
                        self.assertEqual(len(errors), 1)
                        if operation == 'write':
                            self.assertEqual(errors, ['KIS_CREDENTIALS_WRITE_OUTCOME_UNKNOWN'])
                        self.assertFalse(backend.finished.is_set())
                    finally:
                        backend.release.set(); caller.join(1)
                    self.assertTrue(store._busy.acquire(timeout=1)); store._busy.release()
                    self.assertEqual(set(backend.buffer), {0})

    def test_injected_private_failure_cannot_leak_message(self):
        def bad_clock():
            raise _Failure(CREDS.app_secret)
        store = WindowsCredentialStore(backend=self.backend, monotonic=bad_clock)
        for operation in ('read', 'write'):
            args = (BINDING,) if operation == 'read' else (BINDING, CREDS)
            with self.assertRaises(Blocked) as ctx:
                getattr(store, operation)(*args,
                    **self.kwargs('read_app_credentials', 'store_app_credentials'))
            self.assertEqual(str(ctx.exception), 'KIS_CREDENTIALS_OPERATION_FAILED')
        self.assertEqual(self.backend.calls, [])

    def test_malformed_and_dependency_exception_redacted(self):
        self.backend.read = lambda target: bytearray(b'bad')
        with self.assertRaises(Blocked):
            self.store.read(BINDING, **self.kwargs('read_app_credentials'))
        def failing(target):
            raise ValueError(CREDS.app_secret)
        self.backend.read = failing
        with self.assertRaises(Blocked) as ctx:
            self.store.read(BINDING, **self.kwargs('read_app_credentials'))
        self.assertNotIn(CREDS.app_secret, str(ctx.exception))

    def test_provider_injects_only_exact_scope_and_permissions(self):
        grant = permission('read_app_credentials', 'issue_token')
        class Issuer:
            def issue(inner, binding, credentials, **kwargs):
                self.assertIs(kwargs['permission'], grant)
                self.assertEqual(credentials, CREDS)
                now = datetime.now(timezone.utc)
                return CredentialLease(binding, credentials.app_key, credentials.app_secret,
                                       'SYNTHETIC_BEARER', now, now+timedelta(hours=1))
        provider = WindowsCredentialProvider(self.store, Issuer(), grant)
        result = provider.acquire(BINDING, minimum_validity_seconds=30,
                   deadline_monotonic=time.monotonic()+2, cancellation=Cancellation())
        self.assertEqual(result.binding, BINDING)


class NativeBufferTests(unittest.TestCase):
    def test_synthetic_read_scrubs_before_free(self):
        blob = _encode(CREDS)
        native = (ctypes.c_ubyte * len(blob)).from_buffer(blob)
        target = credential_target(BINDING)
        item = _Credential(Type=1, TargetName=target, CredentialBlobSize=len(blob),
                           CredentialBlob=native, Persist=2, UserName='oneil-kis-app-v1')
        freed = []
        class Api:
            def CredReadW(self, target_arg, kind, flags, out):
                self_pointer = ctypes.cast(out, ctypes.POINTER(ctypes.POINTER(_Credential)))
                self_pointer[0] = ctypes.pointer(item)
                return 1
            def CredFree(self, pointer):
                freed.append(bytes(blob))
        backend = _Win32Backend()
        with patch.object(backend, '_api', return_value=Api()):
            result = backend.read(target)
        self.assertEqual(result, _encode(CREDS))
        self.assertEqual(freed, [b'\0'*len(blob)])

    def test_synthetic_wrong_target_still_scrubs_and_frees(self):
        blob = _encode(CREDS)
        native = (ctypes.c_ubyte * len(blob)).from_buffer(blob)
        item = _Credential(Type=1, TargetName='wrong-target', CredentialBlobSize=len(blob),
                           CredentialBlob=native, Persist=2, UserName='oneil-kis-app-v1')
        freed = []
        class Api:
            def CredReadW(self, target, kind, flags, out):
                ctypes.cast(out, ctypes.POINTER(ctypes.POINTER(_Credential)))[0] = ctypes.pointer(item)
                return 1
            def CredFree(self, pointer):
                freed.append(bytes(blob))
        backend = _Win32Backend()
        with patch.object(backend, '_api', return_value=Api()):
            with self.assertRaises(Exception):
                backend.read(credential_target(BINDING))
        self.assertEqual(freed, [b'\0'*len(blob)])

    def test_synthetic_write_scrubs_on_failure(self):
        blob = _encode(CREDS)
        class Api:
            def CredWriteW(self, pointer, flags):
                return 0
        backend = _Win32Backend()
        with patch.object(backend, '_api', return_value=Api()):
            with self.assertRaises(Exception):
                backend.write(credential_target(BINDING), blob)
        self.assertEqual(set(blob), {0})

    def test_construction_does_not_load_dll(self):
        with patch.object(_Win32Backend, '_api', side_effect=AssertionError('no calls')):
            WindowsCredentialStore()


if __name__ == '__main__':
    unittest.main()
