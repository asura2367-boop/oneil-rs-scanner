"""Permission checks need no OS, credentials or networking."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pickle
import unittest

from oneil_execution_v2.domain import Blocked
from oneil_execution_v2.kis_adapter import AccountBinding
from oneil_execution_v2.kis_local_authorization import LocalPermission

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
BINDING = AccountBinding('a'*64, 'real', '12345678', '01')


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.tick = 100.; self.now = NOW

    def grant(self, **kwargs):
        return LocalPermission(BINDING, issued_at=NOW, expires_at=NOW+timedelta(minutes=5),
                               monotonic=lambda: self.tick, utc_now=lambda: self.now, **kwargs)

    def test_default_grants_nothing(self):
        permission = self.grant()
        with self.assertRaises(Blocked): permission.check(BINDING, 'authenticated_get')

    def test_scope_and_action_are_exact(self):
        permission = self.grant(actions=('authenticated_get',))
        permission.check(BINDING, 'authenticated_get')
        for binding in (replace(BINDING, cano='87654321'), replace(BINDING, environment='demo'),
                        replace(BINDING, product_code='02'), replace(BINDING, account='b'*64)):
            with self.assertRaises(Blocked): permission.check(binding, 'authenticated_get')
        with self.assertRaises(Blocked): permission.check(BINDING, 'issue_token')

    def test_issuance_and_write_are_single_use(self):
        permission = self.grant(actions=('issue_token', 'store_app_credentials'))
        for action in ('issue_token', 'store_app_credentials'):
            permission.check(BINDING, action); permission.consume(BINDING, action)
            with self.assertRaises(Blocked): permission.consume(BINDING, action)
            with self.assertRaises(Blocked): permission.check(BINDING, action)

    def test_consumed_use_can_finish_but_remains_revocable(self):
        permission = self.grant(actions=('issue_token',))
        use = permission.consume(BINDING, 'issue_token')
        use.check()
        with self.assertRaises(Blocked): permission.consume(BINDING, 'issue_token')
        with self.assertRaises(TypeError): pickle.dumps(use)
        permission.revoke()
        with self.assertRaises(Blocked): use.check()

    def test_revoke_stops_reads(self):
        permission = self.grant(actions=('authenticated_get',))
        permission.revoke()
        with self.assertRaises(Blocked): permission.check(BINDING, 'authenticated_get')

    def test_frozen_utc_cannot_extend_monotonic_grant(self):
        permission = self.grant(actions=('authenticated_get',))
        self.tick += 300
        with self.assertRaises(Blocked): permission.check(BINDING, 'authenticated_get')

    def test_wall_expiry_and_clock_rollback_fail(self):
        for clock in ('expiry', 'wall_rollback', 'monotonic_rollback', 'nan'):
            self.setUp(); permission = self.grant(actions=('authenticated_get',))
            if clock == 'expiry': self.now += timedelta(minutes=5)
            elif clock == 'wall_rollback': self.now -= timedelta(seconds=1)
            elif clock == 'monotonic_rollback': self.tick -= 1
            else: self.tick = float('nan')
            with self.assertRaises(Blocked): permission.check(BINDING, 'authenticated_get')

    def test_bad_grants_are_rejected(self):
        for actions in (['issue_token'], ('issue_token', 'issue_token'), ('order',), (1,)):
            with self.assertRaises(Blocked): self.grant(actions=actions)
        for expires in (NOW, NOW+timedelta(minutes=16), NOW.replace(tzinfo=None)):
            with self.assertRaises(Blocked):
                LocalPermission(BINDING, issued_at=NOW, expires_at=expires, utc_now=lambda: NOW)

    def test_repr_and_pickle_hide_scope(self):
        permission = self.grant()
        self.assertNotIn(BINDING.cano, repr(permission)); self.assertNotIn(BINDING.account, repr(permission))
        with self.assertRaises(TypeError): pickle.dumps(permission)


if __name__ == '__main__': unittest.main()
