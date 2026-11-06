"""Explicit inactive Linux KIS read composition, never registered with a runtime.

Import/construction does not read a descriptor or open a connection. An explicit
capture may issue one separately permissioned tokenP POST followed by allowlisted
GETs. No orders, automatic consent, credential provisioning or persistence exists.
Only synthetic inputs and DNS/TCP/TLS doubles are used in the offline tests.
"""
from datetime import datetime, timezone
import math
import sys

from .domain import Blocked
from .kis_authenticated import (Cancellation, CredentialLease,
                                KisAuthenticatedReadSource, RetryPolicy)
from .kis_linux_credentials import LinuxCredentialInput, linux_credential_elapsed
from .kis_local_authorization import LocalPermission
from .kis_transport import ReadLimits, ReadRateLimiter
from . import kis_authenticated_https as secure
from . import kis_token_issuer as token


# Separate clock-domain budget. Every instance of this Linux composition shares
# it. Other sources/processes/legacy engines are not coordinated by this object.
_LINUX_READ_LIMITER = ReadRateLimiter(monotonic=linux_credential_elapsed)


def _connect(host, port, deadline, connect_seconds, read_seconds):
    secure._check(sys.platform == 'linux', 'KIS_AUTH_HTTPS_LINUX_REQUIRED')
    return secure._connect_official(host, port, deadline, connect_seconds, read_seconds)


def _scope(permission, clock):
    # Identity, not a sample comparison: clocks can agree before suspension but
    # disagree afterwards. Injected test clocks must also be the same callable.
    if type(permission) is not LocalPermission or permission._clock is not clock:
        raise Blocked('KIS_LINUX_AUTH_PERMISSION_CLOCK_REQUIRED')


class LinuxKisTokenIssuer(token.KisTokenIssuer):
    """Exact tokenP request only; a consumed issue_token grant per attempt."""
    def __init__(self, *, monotonic=linux_credential_elapsed,
                 utc_now=lambda: datetime.now(timezone.utc)):
        super().__init__(monotonic=monotonic, utc_now=utc_now)

    def __repr__(self):
        return 'LinuxKisTokenIssuer(inactive=True, one_permission_per_attempt=True)'

    def _check_platform(self):
        token._check(sys.platform == 'linux', 'KIS_TOKEN_PLATFORM_BLOCKED')

    def _connect(self, host, port, deadline, connect_seconds, read_seconds):
        return _connect(host, port, deadline, connect_seconds, read_seconds)

    def issue(self, binding, credentials, *, permission, minimum_validity_seconds,
              deadline_monotonic, cancellation):
        _scope(permission, self._clock)
        return super().issue(binding, credentials, permission=permission,
            minimum_validity_seconds=minimum_validity_seconds,
            deadline_monotonic=deadline_monotonic, cancellation=cancellation)


class LinuxKisAuthenticatedHttpsGetClient(secure.KisAuthenticatedHttpsGetClient):
    """Canonical seven GET recipes, independent request and grant validation."""
    def __init__(self, binding, *, permission, monotonic=linux_credential_elapsed):
        _scope(permission, monotonic)
        super().__init__(binding, permission=permission, monotonic=monotonic)

    def __repr__(self):
        return 'LinuxKisAuthenticatedHttpsGetClient(GET_only=True, inactive=True)'

    def _check_platform(self):
        secure._check(sys.platform == 'linux', 'KIS_AUTH_HTTPS_LINUX_REQUIRED')

    def _connect(self, host, port, deadline, connect_seconds, read_seconds):
        return _connect(host, port, deadline, connect_seconds, read_seconds)


class LinuxCredentialProvider:
    """Read only the supplied sealed descriptor, then explicitly granted issue.

    The caller owns the original descriptor and must hold it stable through the
    complete read. No hidden token renewal permission or bearer cache exists.
    """
    def __init__(self, *, descriptor, permission, monotonic=linux_credential_elapsed,
                 utc_now=lambda: datetime.now(timezone.utc)):
        _scope(permission, monotonic)
        if type(descriptor) is not int or not 3 <= descriptor < 1048576:
            raise Blocked('KIS_LINUX_AUTH_INPUT_INVALID')
        self._descriptor = descriptor; self._permission = permission
        self._clock = monotonic
        self._reader = LinuxCredentialInput(monotonic=monotonic)
        self._issuer = LinuxKisTokenIssuer(monotonic=monotonic, utc_now=utc_now)

    def __repr__(self):
        return 'LinuxCredentialProvider(inactive=True, persistent=False)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_AUTH_SERIALIZATION_BLOCKED')

    def acquire(self, binding, *, minimum_validity_seconds, deadline_monotonic,
                cancellation):
        try:
            if (type(minimum_validity_seconds) not in (int, float) or
                    not math.isfinite(minimum_validity_seconds) or
                    not 0 < minimum_validity_seconds <= 86400 or
                    type(cancellation) is not Cancellation):
                raise ValueError()
            self._permission.check(binding, 'read_app_credentials')
            self._permission.check(binding, 'issue_token')
            credentials = self._reader.read(binding, descriptor=self._descriptor,
                permission=self._permission, deadline_monotonic=deadline_monotonic,
                cancellation=cancellation)
            lease = self._issuer.issue(binding, credentials, permission=self._permission,
                minimum_validity_seconds=minimum_validity_seconds,
                deadline_monotonic=deadline_monotonic, cancellation=cancellation)
            # Token issuance has consumed its grant; only read permission is
            # reusable. Issuer checks the in-flight issue grant before returning.
            self._permission.check(binding, 'read_app_credentials')
            now = self._clock()
            if (type(lease) is not CredentialLease or lease.binding != binding or
                    lease.app_key != credentials.app_key or
                    lease.app_secret != credentials.app_secret or cancellation.cancelled or
                    not token._number(now, 0, 1e15) or now >= deadline_monotonic):
                raise ValueError()
            return lease
        except Exception:
            raise Blocked('KIS_LINUX_AUTH_PROVIDER_FAILED') from None


class LinuxKisAuthenticatedReadSource(KisAuthenticatedReadSource):
    """Explicit Linux composition, never a live execution/evidence provider.

    Permission must have the same suspend-aware elapsed clock as this source.
    Trusted provisioning and actual human authorization remain external. One
    session cannot automatically mint permission to replace an expired token.
    """
    def __init__(self, binding, *, descriptor, permission, policy=RetryPolicy(max_attempts=1),
                 limits=ReadLimits(), monotonic=linux_credential_elapsed,
                 utc_now=lambda: datetime.now(timezone.utc)):
        _scope(permission, monotonic)
        provider = LinuxCredentialProvider(descriptor=descriptor, permission=permission,
                                          monotonic=monotonic, utc_now=utc_now)
        client = LinuxKisAuthenticatedHttpsGetClient(binding, permission=permission,
                                                     monotonic=monotonic)
        super().__init__(binding, provider, client, policy=policy, limits=limits,
                         limiter=_LINUX_READ_LIMITER, monotonic=monotonic, utc_now=utc_now)
        self._permission = permission

    def _run(self, operation, cancellation, deadline_monotonic=None, deadline_clock=None):
        if sys.platform != 'linux':
            raise Blocked('KIS_LINUX_AUTH_PLATFORM_REQUIRED')
        # A missing GET grant must not cause even a credential read/token POST.
        self._permission.check(self._binding, 'authenticated_get')
        return super()._run(operation, cancellation, deadline_monotonic, deadline_clock)

    def __repr__(self):
        return 'LinuxKisAuthenticatedReadSource(inactive=True, live_eligible=False)'

    def __reduce_ex__(self, protocol):
        raise TypeError('KIS_AUTH_SERIALIZATION_BLOCKED')
