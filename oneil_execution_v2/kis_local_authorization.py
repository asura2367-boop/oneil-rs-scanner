"""Explicit, ephemeral local permission records; not a human-consent authority.

The trusted Windows host must obtain the user's action-time permission before
constructing these records. No UI, persisted grant, auto-approval, network or
credential access is supplied. A Python caller controlling this process can
construct/modify objects; this is a misuse guard, not a hostile-code boundary.
"""
from datetime import datetime, timezone
import math
import threading
import time

from .domain import Blocked
from .kis_adapter import AccountBinding

_ACTIONS = frozenset(('read_app_credentials', 'store_app_credentials',
                     'issue_token', 'authenticated_get'))
_SINGLE_USE = frozenset(('store_app_credentials', 'issue_token'))


def _valid_binding(binding):
    if type(binding) is not AccountBinding or any(type(value) is not str for value in
            (binding.account, binding.environment, binding.cano, binding.product_code)):
        raise ValueError()
    binding.validate()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e15


class LocalPermission:
    """Exact account/environment grant, at most 15 minutes, never renewed.

    No action is granted by default. Store and token issuance are single-use;
    consumed attempts remain consumed on cancellation, error or uncertain result.
    GET/read consent expires by BOTH UTC and monotonic time. Scope contains
    account identifiers and therefore must not be serialized or logged.
    """
    def __init__(self, binding, *, actions=(), issued_at, expires_at,
                 monotonic=time.monotonic, utc_now=lambda: datetime.now(timezone.utc)):
        try:
            _valid_binding(binding)
            if (type(actions) is not tuple or len(set(actions)) != len(actions) or
                    any(type(action) is not str or action not in _ACTIONS for action in actions) or
                    not callable(monotonic) or not callable(utc_now) or
                    any(type(value) is not datetime or value.utcoffset() is None
                        for value in (issued_at, expires_at))):
                raise ValueError()
            now = utc_now(); tick = monotonic()
            if (type(now) is not datetime or now.utcoffset() is None or not _number(tick) or
                    not issued_at <= now < expires_at or
                    not 0 < (expires_at - issued_at).total_seconds() <= 900):
                raise ValueError()
        except Exception:
            raise Blocked('KIS_LOCAL_PERMISSION_INVALID') from None
        self._binding = binding; self._actions = frozenset(actions)
        self._expires_at = expires_at; self._end = tick + (expires_at - now).total_seconds()
        self._clock = monotonic; self._utc = utc_now
        self._previous = tick; self._previous_utc = now
        self._used = set(); self._revoked = False; self._lock = threading.Lock()

    def __repr__(self):
        return 'LocalPermission(ephemeral=True, automatic_consent=False)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_LOCAL_PERMISSION_SERIALIZATION_BLOCKED')

    def _check(self, binding, action, *, consume, in_flight=False):
        try:
            _valid_binding(binding)
            now = self._utc(); tick = self._clock()
            if (self._revoked or binding != self._binding or type(action) is not str or
                    action not in self._actions or (action in self._used and not in_flight) or
                    type(now) is not datetime or now.utcoffset() is None or
                    not _number(tick) or not self._previous <= tick < self._end or
                    not self._previous_utc <= now < self._expires_at):
                raise ValueError()
            self._previous = tick; self._previous_utc = now
            if consume:
                if action not in _SINGLE_USE:
                    raise ValueError()
                self._used.add(action)
        except Exception:
            raise Blocked('KIS_LOCAL_PERMISSION_REQUIRED') from None

    def check(self, binding, action):
        with self._lock:
            self._check(binding, action, consume=False)

    def consume(self, binding, action):
        with self._lock:
            self._check(binding, action, consume=True)
            return _PermissionUse(self, binding, action)

    def revoke(self):
        """Stop further operations. Does not revoke an already-issued broker token."""
        with self._lock:
            self._revoked = True


class _PermissionUse:
    """Continuation check for one consumed operation, not a new-operation grant."""
    def __init__(self, permission, binding, action):
        self._permission = permission; self._binding = binding; self._action = action

    def check(self):
        with self._permission._lock:
            self._permission._check(self._binding, self._action, consume=False, in_flight=True)

    def __repr__(self):
        return 'LocalPermissionUse(single_operation=True)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_LOCAL_PERMISSION_SERIALIZATION_BLOCKED')
