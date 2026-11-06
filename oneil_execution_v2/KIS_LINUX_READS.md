# Inactive Linux authenticated KIS reads

This is source-only Linux implementation with offline validation, **not an
activated broker connection, deployed service or trading release**. No actual
credential/account access, broker token, network request or order was used.

`kis_linux_authenticated.py` explicitly composes the existing sealed descriptor
input, a Linux-only token issuer, the seven canonical authenticated GET recipes,
and the bounded read-source lifecycle. Import and construction perform no
descriptor read, DNS, socket, TLS, token issuance or background work. No dashboard,
Oracle preparation command, runtime factory, worker or browser route imports or
starts it. There is no CLI credential argument, secret-entry/provisioning UI,
environment/file fallback, credential writer, bearer persistence or order path.

## Separate explicit permissions

A future trusted Linux host must separately obtain the user's actual permission
and supply the exact account/environment-bound sealed descriptor and ephemeral
`LocalPermission`. Merely creating a permission object does not establish human
consent and is not a security boundary against code inside this Python process.

- `read_app_credentials` allows only the exact sealed input read.
- `issue_token` is single-use per attempted token issuance. A cancelled, failed or
  uncertain attempt consumes that authorization before connection. No retry,
  token cache file, background renewal or new permission is manufactured.
- `authenticated_get` covers only the seven canonical read recipes. The complete
  source checks it before any credential read/token work and the HTTPS client
  rechecks it through DNS, TLS, send and streaming.
- No storage permission is needed or used. Read permission never implies token
  issuance or transmission. A missing action, scope mismatch, expired/revoked
  grant, cancellation or invalid input prevents the next privileged step.

The broker's app-issued token may have rights beyond these local GET restrictions.
It is **not a broker-enforced read-only token** and local binding is not proven
broker-side account/product provenance. Real token issuance remains an independently
authorized persistent-access operation, outside this source publication.

## Clock, lifecycle and resource boundaries

Linux classes default to `linux_credential_elapsed`, backed only by suspend-inclusive
`CLOCK_BOOTTIME`. Permissions supplied to these classes must use that **same
callable**, not `time.monotonic`, which excludes Linux suspension. The source,
credential reader, issuer, GET client and operation deadlines share that clock.
An explicitly injected test clock must also match the permission by identity.
The default shared Linux GET limiter uses BOOTTIME too. Other-platform constructors
remain inert and their operation entrypoints reject before native input/network.

`LinuxKisAuthenticatedReadSource` accepts one explicitly provided descriptor and
permission. The caller owns the original descriptor and must keep it stable/open
through the read. Kernel seals, ownership, bounds and scope digest are checked by
`LinuxCredentialInput`; see [its limits](KIS_LINUX_AUTH.md). Successful app values
exist only in process memory. Python copies, swap/dumps and same-user/root access
are not made safe by sealing or redacted repr.

The issuer sends only `/oauth2/tokenP` at the exact real/demo origin. It inherits
single-flight/one-minute process-local issuance throttling, one-use permission,
bounded strict JSON, absolute expiry, UTC/elapsed clock checks and conservative
transit handling. The client supports only the seven reviewed GET paths and
parameters from [the read contract](KIS_AUTHENTICATED.md). Windows entrypoints
retain their Windows-only gates; only private nonblocking socket mechanics are
shared. There is no public generic HTTP, order, cancel or revoke method.

TLS verification, official-host SNI, global numeric address selection, TLS 1.2+
and HTTP/1.1 remain mandatory. Trust overrides, proxies, cookies, key logs,
redirects and alternate-address fallback remain blocked. Success requires bounded
Content-Length JSON; real broker framing compatibility is unverified. The shared
DNS slot bounds abandoned lookups, whose workers never connect. Network waits
poll permission/cancellation/deadline at 100 ms. DNS, local trust loading, close
and kernel scheduling are not hard-interruptible or hard-real-time guarantees.

The complete source defaults to **one read attempt**, supports bounded existing
pagination and keeps a granted bearer in memory only. A cached valid bearer can
serve later authorized GETs, but expiry/401 cannot obtain another token with the
already-consumed permission. `close()` drops its cached lease. An optional bounded
retry policy cannot create permission or bypass quota/cancellation. Each Linux
source shares the module's read budget, cooldown and daily holiday reservation.
This budget does not coordinate other generic sources, processes, machines or
legacy engines; deployment must solve that exclusion/quota gate separately.

Only `OfflineRead` captures return. `policy_evidence()` and `submit()` remain
unconditionally blocked. There is no runtime trading admission and no activation
switch. A trusted future diagnostic caller would have to use matching BOOTTIME
and UTC domains; nothing connects or starts a diagnostic run automatically.

## Offline validation and remaining acceptance

The Linux suite reruns the complete transport and token contracts through the
Linux entrypoints with fake DNS/TCP/TLS. Native temporary memfd integration uses
visibly synthetic values only. It tests scope, missing actions, consumed grants,
clock mismatch, suspend with frozen UTC, cached bearer/revocation, non-Linux
refusal, exact real/demo requests, all seven recipes, order-path rejection,
cleanup, unsupported framing, sanitized errors and nonpermitting captures.
Portable refusal/inert-import tests also run on non-Linux; native Linux tests are
not selected there and no skips are used. Native Windows testing is not claimed.

The source-package inventory and network-free aggregate include this module,
document and test suite. Actual Oracle host/kernel/identity, trusted secret entry,
swap/dump policy, real TLS/broker schemas and provenance, quota/legacy exclusion,
semantic trading evidence, PostgreSQL durability, browser/TLS integration and
separately authorized deployment/activation all remain open. Green mocks do not
satisfy those gates. Do not supply production credentials or run trading workflows
as part of source review.
