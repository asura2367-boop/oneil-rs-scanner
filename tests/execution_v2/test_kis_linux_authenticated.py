"""Linux adapters: actual synthetic sealed input, fake DNS/TCP/TLS only."""
from dataclasses import replace
from datetime import timedelta
import os
import pickle
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_kis_authenticated_https as https_tests
import test_kis_token_issuer as token_tests
import test_kis_linux_credentials as credential_tests
from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import HistoryQuery, prepare_history_query
from oneil_execution_v2.kis_authenticated import RetryPolicy
from oneil_execution_v2.kis_local_authorization import LocalPermission
from oneil_execution_v2.kis_transport import ReadRateLimiter
from oneil_execution_v2 import kis_linux_authenticated as linux


class LinuxHttpsTests(https_tests.AuthenticatedHttpsTests):
    """The complete transport contract runs on the Linux-specific entrypoint."""
    client_type = linux.LinuxKisAuthenticatedHttpsGetClient
    platform = 'linux'
    blocked_platform = 'win32'
    platform_error = 'KIS_AUTH_HTTPS_LINUX_REQUIRED'
    connect = staticmethod(linux._connect)

    def test_permission_clock_mismatch_rejected_without_io(self):
        with self.assertRaisesRegex(Blocked, 'PERMISSION_CLOCK_REQUIRED'):
            self.client_type(https_tests.BINDING, permission=self.permission)
        self.dns.assert_not_called(); self.tcp.assert_not_called()

    def test_suspend_during_tls_prevents_get_send(self):
        self.tls.hook = lambda: setattr(self, 'tick', 700.)
        self.error(); self.assertEqual(self.tls.sent, [])

    def test_default_clock_is_suspend_inclusive(self):
        now = https_tests.NOW
        with patch.object(linux.linux_credential_elapsed.__globals__['time'],
                          'clock_gettime_ns', return_value=100_000_000_000):
            permission = LocalPermission(https_tests.BINDING, actions=('authenticated_get',),
                issued_at=now, expires_at=now+timedelta(seconds=120),
                monotonic=linux.linux_credential_elapsed, utc_now=lambda: now)
            client = self.client_type(https_tests.BINDING, permission=permission)
            self.assertIs(client._clock, linux.linux_credential_elapsed)
        self.dns.assert_not_called(); self.tcp.assert_not_called()


class LinuxTokenTests(token_tests.TokenIssuerTests):
    """The complete token boundary runs again on Linux, with a fake connector."""
    issuer_type = linux.LinuxKisTokenIssuer
    platform = 'linux'
    blocked_platform = 'win32'
    connector_module = linux

    def descriptor(self, binding=token_tests.BINDING, *, seals=credential_tests.REQUIRED):
        import fcntl
        fd = os.memfd_create('oneil-synthetic-integration', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        self.addCleanup(os.close, fd)
        os.write(fd, credential_tests.payload(binding, token_tests.KEY, token_tests.SECRET))
        if seals:
            fcntl.fcntl(fd, credential_tests.F_ADD_SEALS, seals)
        return fd

    def source(self, *, permission=None, descriptor=None, policy=RetryPolicy(max_attempts=1)):
        return linux.LinuxKisAuthenticatedReadSource(token_tests.BINDING,
            descriptor=self.descriptor() if descriptor is None else descriptor,
            permission=self.permission if permission is None else permission,
            monotonic=self.clock, utc_now=lambda: self.now, policy=policy)

    def setUp(self):
        super().setUp()
        p = patch.object(linux, '_LINUX_READ_LIMITER', ReadRateLimiter(monotonic=self.clock))
        p.start(); self.addCleanup(p.stop)
        self.recipe = prepare_history_query(HistoryQuery(token_tests.BINDING, '2026-11-05'))

    def get_stream(self, *, status='200 OK'):
        return token_tests.Stream(token_tests.response({'rt_cd':'0', 'output1':[]},
                                  status=status, headers='tr_cont: D\r\n'))

    def test_complete_provider_issuer_get_source_remains_nonpermitting(self):
        fd = self.descriptor(); offset = os.lseek(fd, 3, os.SEEK_SET)
        source = self.source(descriptor=fd); get = self.get_stream()
        self.connect.side_effect = [self.stream, get]
        self.assertEqual(source.capture(self.recipe).response.body['rt_cd'], '0')
        self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), offset)
        self.assertEqual(len(self.stream.sent), 1); self.assertEqual(len(get.sent), 1)
        self.assertTrue(self.stream.sent[0].startswith(b'POST /oauth2/tokenP '))
        self.assertTrue(get.sent[0].startswith(b'GET /uapi/'))
        with self.assertRaises(Blocked): source.policy_evidence()
        with self.assertRaises(Blocked): source.submit()
        source.close()
        with self.assertRaises(Blocked): source.capture(self.recipe)
        self.assertEqual(self.connect.call_count, 2)

    def test_read_permission_alone_never_issues_or_connects(self):
        for actions in ((), ('read_app_credentials',), ('issue_token',),
                        ('authenticated_get',), ('read_app_credentials', 'issue_token'),
                        ('authenticated_get', 'issue_token'),
                        ('authenticated_get', 'read_app_credentials')):
            with self.subTest(actions=actions):
                self.tick += 2
                source = self.source(permission=self.grant(actions=actions))
                with patch.object(linux.LinuxCredentialInput, 'read') as read:
                    with self.assertRaises(Blocked): source.capture(self.recipe)
                    read.assert_not_called()
        self.connect.assert_not_called()

    def test_wrong_scope_unsealed_input_and_cancel_never_connect(self):
        cases = [self.source(permission=self.grant(replace(token_tests.BINDING, cano='87654321'))),
                 self.source(descriptor=self.descriptor(seals=0))]
        for source in cases:
            with self.assertRaises(Blocked): source.capture(self.recipe)
        self.token.cancel()
        with self.assertRaises(Blocked): self.source().capture(self.recipe, cancellation=self.token)
        self.connect.assert_not_called()

    def test_session_constructor_no_descriptor_tls_or_network_io(self):
        with patch('os.fstat', side_effect=AssertionError), \
             patch.object(linux.LinuxCredentialInput, 'read', side_effect=AssertionError):
            source = self.source(descriptor=123)
            self.assertIn('inactive=True', repr(source))
            for value in (source, source._provider, source._client, source._provider._issuer):
                with self.assertRaises(TypeError): pickle.dumps(value)
        self.connect.assert_not_called()

    def test_session_rejects_permission_clock_mismatch(self):
        with self.assertRaisesRegex(Blocked, 'PERMISSION_CLOCK_REQUIRED'):
            linux.LinuxKisAuthenticatedReadSource(token_tests.BINDING,
                descriptor=123, permission=self.permission)
        self.connect.assert_not_called()

    def test_cached_token_never_reissues_and_revoked_get_never_connects(self):
        source = self.source()
        self.connect.side_effect = [self.stream, self.get_stream(), self.get_stream()]
        source.capture(self.recipe)
        self.tick += 2; source.capture(self.recipe)
        self.assertEqual(len(self.stream.sent), 1)
        self.permission.revoke(); self.tick += 2
        with self.assertRaises(Blocked): source.capture(self.recipe)
        self.assertEqual(self.connect.call_count, 3)
        source.close(); self.assertIsNone(source._lease)

    def test_401_cannot_automatically_create_renewal_permission(self):
        source = self.source(policy=RetryPolicy(max_attempts=3))
        self.connect.side_effect = [self.stream, self.get_stream(status='401 Unauthorized')]
        with patch.object(linux.Cancellation, 'wait',
                          side_effect=lambda seconds: setattr(self, 'tick', self.tick+seconds)):
            with self.assertRaises(Blocked): source.capture(self.recipe)
        self.assertEqual(self.connect.call_count, 2)
        self.assertEqual(len(self.stream.sent), 1)

    def test_suspend_frozen_wall_invalidates_permission_cached_lease(self):
        source = self.source(); self.connect.side_effect = [self.stream, self.get_stream()]
        source.capture(self.recipe); self.tick += 3601
        with self.assertRaises(Blocked): source.capture(self.recipe)
        self.assertEqual(self.connect.call_count, 2)

    def test_all_linux_sources_share_budget(self):
        one = self.source(); two = self.source()
        self.assertIs(one._limiter, two._limiter)
        self.assertIs(one._limiter, linux._LINUX_READ_LIMITER)

    def test_nonlinux_composition_never_reads_descriptor(self):
        source = self.source()
        with patch.object(linux.sys, 'platform', 'win32'), \
             patch.object(linux.LinuxCredentialInput, 'read') as read:
            with self.assertRaises(Blocked): source.capture(self.recipe)
            read.assert_not_called()
        self.connect.assert_not_called()

    def test_new_module_not_loaded_by_dashboard_or_oracle_import(self):
        code = '''import sys
import oneil_runtime_v2.linux_dashboard
import deploy.oracle.prepare_offline
assert "oneil_execution_v2.kis_linux_authenticated" not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_order_recipe_rejected_before_provider_or_network(self):
        source = self.source()
        with patch.object(linux.LinuxCredentialInput, 'read') as read:
            with self.assertRaises(Blocked):
                source.capture(replace(self.recipe, path='/uapi/domestic-stock/v1/trading/order-cash'))
            read.assert_not_called()
        self.connect.assert_not_called()


class PortablePlatformRefusalTests(unittest.TestCase):
    def test_nonlinux_entrypoints_reject_before_native_input_or_network(self):
        now = token_tests.NOW; clock = lambda: 100.
        permission = LocalPermission(token_tests.BINDING,
            actions=('read_app_credentials', 'issue_token', 'authenticated_get'),
            issued_at=now, expires_at=now+timedelta(minutes=5),
            monotonic=clock, utc_now=lambda: now)
        for platform in ('win32', 'darwin', 'freebsd'):
            with patch.object(linux.sys, 'platform', platform), \
                 patch.object(linux.secure, '_connect_official') as connect, \
                 patch.object(linux.LinuxCredentialInput, 'read') as read:
                source = linux.LinuxKisAuthenticatedReadSource(token_tests.BINDING,
                    descriptor=123, permission=permission, monotonic=clock, utc_now=lambda: now)
                with self.assertRaises(Blocked):
                    source.capture(prepare_history_query(HistoryQuery(token_tests.BINDING, '2026-11-05')))
                with self.assertRaisesRegex(Blocked, 'PLATFORM_BLOCKED'):
                    source._provider._issuer.issue(token_tests.BINDING, token_tests.CREDS,
                        permission=permission, minimum_validity_seconds=60,
                        deadline_monotonic=110., cancellation=linux.Cancellation())
                with self.assertRaisesRegex(Blocked, 'LINUX_REQUIRED'):
                    source._client.open_authenticated_get(None)
                read.assert_not_called(); connect.assert_not_called()

    def test_no_native_tests_selected_on_other_platform(self):
        loader = unittest.TestLoader()
        with patch.object(sys, 'platform', 'win32'):
            selected = load_tests(loader, None, None)
        self.assertEqual(selected.countTestCases(),
                         loader.loadTestsFromTestCase(PortablePlatformRefusalTests).countTestCases())

    def test_fresh_import_and_construction_perform_no_io(self):
        code = '''import sys, os, threading
from datetime import datetime, timedelta, timezone
def denied(*args, **kwargs): raise AssertionError('INACTIVE_IO')
def audit(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system'): denied()
sys.addaudithook(audit)
for name in ('preadv', 'memfd_create'):
    if hasattr(os, name): setattr(os, name, denied)
threading.Thread.start = denied
from oneil_execution_v2.kis_adapter import AccountBinding
from oneil_execution_v2.kis_local_authorization import LocalPermission
from oneil_execution_v2.kis_linux_authenticated import LinuxKisAuthenticatedReadSource
binding = AccountBinding('a'*64, 'demo', '12345678', '01')
now = datetime.now(timezone.utc); clock = lambda: 100.
permission = LocalPermission(binding, issued_at=now, expires_at=now+timedelta(seconds=60), monotonic=clock)
source = LinuxKisAuthenticatedReadSource(binding, descriptor=123, permission=permission, monotonic=clock)
assert 'inactive=True' in repr(source)
assert 'oneil_execution_v2.kis_windows_credentials' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


def load_tests(loader, tests, pattern):
    # Native sealed-descriptor/BOOTTIME acceptance runs only on real Linux.
    # Other platforms test refusal without falsely passing mocks or adding skips.
    suite = loader.loadTestsFromTestCase(PortablePlatformRefusalTests)
    if sys.platform == 'linux':
        suite.addTests(loader.loadTestsFromTestCase(LinuxHttpsTests))
        suite.addTests(loader.loadTestsFromTestCase(LinuxTokenTests))
    return suite


if __name__ == '__main__':
    unittest.main()
