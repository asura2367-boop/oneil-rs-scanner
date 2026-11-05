# Windows-local KIS authentication implementation: inactive source only

This change extends `1132dc1` with optional Windows Credential Manager storage,
a token issuer and an authenticated HTTPS GET client. These are callable source
components, **not a configured app, consent UI, service or live data feed**. No
runtime, dashboard, worker or policy factory imports or activates them. All tests
use synthetic credentials, mocked Windows APIs and mocked network primitives.
No real credential was retrieved, created, stored or transmitted; no broker API,
Windows PC, installation, order or deployment was used. Public source review used
only the official KIS example on GitHub, not a broker endpoint.

## Permission and identity boundary

`LocalPermission` defaults to zero actions and binds the exact account
fingerprint, CANO, product and environment. A trusted local host must obtain the
user's actual action-time consent before constructing it. It is an in-process
misuse guard, **not proof of human consent**, an OS isolation boundary or a saved
permission rule. There is no UI, command-line secret input, environment flag,
configuration auto-approval or persisted consent here.

- `read_app_credentials`: retrieve only this app credential target.
- `store_app_credentials`: one attempt to persist the user-supplied app pair in
  the current Windows user's Credential Manager, local-machine persistence.
- `issue_token`: one attempt to transmit that pair to the exact official KIS
  token endpoint for the selected environment and create an access token.
- `authenticated_get`: the seven existing exact read recipes only.

Permissions last at most 15 minutes and expire by both UTC and monotonic clocks.
Rollback, expiry and revocation block; consuming a store/issuance permission
cannot be undone after failure or an uncertain outcome. A consumed operation can
only check its continuing validity, not authorize another attempt. Each later
issuance requires a newly obtained permission. Changing Python objects is not a
security boundary against code already executing inside the process.

No order request is available. A KIS token may nevertheless carry broker-side
rights beyond these local GET capabilities. Local account binding does **not**
prove or narrow the token's actual KIS account/product permissions. Do not call
these tokens broker-enforced read-only tokens. The policy source still refuses
`policy_evidence()` and `submit()` unconditionally.

## Windows Credential Manager

`WindowsCredentialStore` uses only a lazy `advapi32.dll` loaded from the Windows
system directory, with explicit `CredReadW`, `CredWriteW` and `CredFree` signatures.
Non-Windows calls fail before OS access. Generic-credential targets include a
SHA-256 digest of the complete local binding; there is no enumeration, implicit
account selection, environment/file fallback, deletion or automatic migration.
The structured app-key/app-secret blob contains no bearer. Reads validate target,
credential type, persistence, fixed username, size and encoding before use.

Windows protects this storage for the logged-in Windows identity. It does not
protect secrets from other same-user processes, administrators, process-memory
inspection or a compromised host. Intended Windows/service identity, profile
availability and actual native ABI/security behavior need local acceptance tests.
No change to DACLs or other security settings is made.

Native and mutable temporary buffers are wiped, and read allocations are freed.
The app-pair object has no dataclass/dictionary serialization surface, redacts
repr and rejects pickle. Python immutable strings, temporary interpreter copies,
traceback-local inspection, debugger memory and crash dumps cannot be reliably
zeroized or sanitized. Do not log these objects or enable local-variable capture.

Win32 calls execute in a single-flight daemon worker per store. The caller checks
cancellation, deadline and permission while waiting. A late read result is wiped.
Windows credential syscalls cannot be interrupted: a cancelled/timed-out write
may have completed and returns `KIS_CREDENTIALS_WRITE_OUTCOME_UNKNOWN`; it must
be checked locally rather than blindly retried. The single-flight lock remains
occupied until the outstanding syscall finishes. Multiple separately constructed
stores are not a machine-wide concurrency or security boundary.

## Token issuance is a separate, exact POST

`KisTokenIssuer.issue` has no caller URL/path/header/method input. Only these two
HTTPS destinations exist, selected by the validated binding:

- Real: `https://openapi.koreainvestment.com:9443/oauth2/tokenP`
- Demo: `https://openapivts.koreainvestment.com:29443/oauth2/tokenP`

Only `grant_type=client_credentials`, `appkey` and `appsecret` are sent in JSON.
No account number, bearer, order, cancellation, hashkey, websocket approval or
revocation POST is supported. The exact request and absolute-expiry format were
cross-checked against the official
[KIS authentication example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/kis_auth.py)
on 2026-11-05. Its plaintext YAML/token files, logging and automatic refresh were
**not** copied. That example notes KIS may return the same token and send an
issuance notification; consent must disclose these effects before real use.

The conservative response profile requires exactly `access_token`, `token_type`,
`expires_in` and `access_token_token_expired`. Duplicate keys, extra fields,
noninteger/nonpositive/over-24-hour lifetime, unsafe token strings and malformed
JSON fail closed. The timezone-less expiry string is interpreted using an
explicit KST profile. These strict field/timezone assumptions remain an actual
recorded-response acceptance gate, not a claim of empirical KIS compatibility.

Expiry is the earlier of the absolute profile timestamp and request-start plus
`expires_in`, further reduced when monotonic transit exceeds UTC elapsed time.
The token must retain the requested minimum validity after transit. `issued_at`
records local acquisition observation, not independently proven KIS issuance.
The existing read source retains its own monotonic expiry/history checks and
rejects a previously unauthorized bearer. No token is saved or logged. A single
process guard permits one in-flight issuance and at least 60 seconds between
starts, including failures. It is not a broker quota statement or cross-process
rate coordinator. There are no POST retries or automatic reauthorization.

## Authenticated GET transport and cancellation

`KisAuthenticatedHttpsGetClient` independently reuses the unchanged credential-
free client's exact request validator before adding exactly Bearer/appkey/
appsecret headers. The original `kis_https.py` is unchanged and still rejects the
authenticated envelope. Only the seven recipes in
[KIS_AUTHENTICATED.md](KIS_AUTHENTICATED.md) are accepted. It must be composed
behind `KisAuthenticatedReadSource` for read rate/retry/pagination semantics;
direct client use does not supply those higher-level limits.

Both new network operations are Windows-gated and use only the two fixed HTTPS
host/port pairs. TLS certificate/hostname verification, SNI, TLS 1.2 minimum and
HTTP/1.1 ALPN are required. Environment trust overrides are refused. There are no
proxies, redirects, cookies, ambient authentication, TLS key logging, alternate
addresses, connection reuse or internal retries. Successful responses require
bounded Content-Length JSON; chunked or close-delimited success is unsupported.
Token responses are capped at 16 KiB with 8 KiB/32-header limits. GET retains the
existing body/header bounds. This may reject actual valid server framing and
must not be loosened without separate evidence/review.

Direct connections, TLS handshake, partial writes and reads are nonblocking and
poll cancellation/permission/deadline at most every 100 ms while waiting. DNS
uses one process-wide daemon slot; cancellation abandons the lookup result, and
that DNS worker never opens a socket. OS DNS, trust-store loading and local close
cannot be forcibly interrupted; no hard-real-time promise is made. Cleanup
closes descriptors without TLS unwrap/shutdown requests. Unknown transport errors
fail closed rather than being guessed transient. Public exception text is fixed
and contains no server body, credential, URL or account identifier.

## Demonstrated and remaining

Synthetic tests cover scoped permissions, single-use/revoked grants, native
buffer cleanup, late reads and uncertain writes, exact real/demo requests,
TLS/cancellation/timeout/partial-write failures, token expiry including frozen
UTC, redaction, strict framing and complete provider→issuer→GET-source
composition. Successful captures remain nonpermitting `OfflineRead` values.

Before any real local use, still required:

1. Trusted local human-consent/secret-entry UI and separately authorized credential
   storage/issuance/transmission. No real secret should be put in a chat or repo.
2. Windows identity, Credential Manager ABI and worker cancellation/late-write
   acceptance testing on the intended PC, without automated installation.
3. Authorized TLS/broker response and clock-profile verification, including
   account/product provenance, actual token/GET schemas and framing.
4. Machine-wide credential rotation/revocation and token/GET quota coordination
   with every other process, especially legacy executors.
5. Full authenticated semantic evidence, causal fills/reservations/P&L,
   PostgreSQL concurrency, production identity/isolation and deployment review.

None is satisfied by mock success, source publication or a green offline suite.
