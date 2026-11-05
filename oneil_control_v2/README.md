# Authenticated simulation control boundary

**Development foundation, not a production release. No deployment, login,
credentials, account access, broker transport, or live activation was performed.**

This additive package bridges the existing `oneil_execution_v2` core to the
versioned `oneil_monitor_v2.read_model` projector.
It is a WSGI callable exercised in process; it does not open a socket. Python's
standard library is sufficient for the API tests. The actual core adapter still
requires its existing isolated PostgreSQL store and `psycopg`.

## What is enforced

- Authenticated opaque-cookie sessions, SHA-256 token/CSRF digests at rest,
  server-mapped principal and account, separate read/command scopes, expiry and
  persisted revocation. There is no request field for owner/account/approval.
- Exact HTTPS origin/Host and same-origin Fetch Metadata for commands; explicit
  session-bound CSRF header; no credential CORS; no query tokens or Bearer fallback.
  Forwarded headers never bypass the HTTPS/Host checks.
- Bounded JSON body, required framing, UTF-8 only, duplicate/extra fields rejected,
  integers only, no compressed/transfer-encoded body, strict action allowlist.
- Durable per-session nonces and server timestamp windows; exact wire-content
  idempotency; single-use, session-bound confirmation references with immutable
  server-side exact content. No browser `approved: true` shortcut.
- Durable reservation before a core effect. A crash or uncertain completion leaves
  `COMMAND_OUTCOME_UNKNOWN`; an unchanged key cannot execute again, and its
  confirmation cannot be reused. Completed receipts survive restart and expire
  independently of the original preview, but require a currently valid session.
- The core adapter locks the account row across revision checks and core commands,
  closing the gap between preview and execution. Simulation resume rechecks unknown
  orders inside that lock. This choreography has unit tests; actual PostgreSQL
  concurrency is still an open acceptance gate.
- Read polling has no queue, confirmation, nonce or command side effect. Original
  evidence timestamps are retained. ACK is never presented as a fill.
- Commands are disabled by default. Enabling the configuration permits simulation
  commands only; it does not resume an account. Resume requires an authenticated
  owner's separate preview and confirmation. There is no live-mode endpoint.

The durable journal uses a private local SQLite file in a private directory,
FULL synchronous durability and WAL. A missing database never creates a fallback
journal on an HTTP request. Use one journal for all API processes on one host.
Do not place it on a network filesystem or give separate replicas separate files.

## Trust and identity boundary

The code verifies sessions; it does **not** implement an identity provider or prove
who owns an account. A reviewed external login issuer must authenticate the owner,
map authorized accounts on the server, issue high-entropy independent session and
CSRF tokens, and install their digests with `install_session_record`. That trusted
method is not exposed over HTTP. No default session exists. Sessions last at most
one hour, cannot be overwritten/revived, and are looked up again on admission.
Admission samples the trusted clock after each journal lock acquisition, then
rechecks session, request timestamp and confirmation expiry. A slow body read or
queued request cannot keep an earlier authorization time alive. Reads recheck
session validity and use completion-time evidence freshness before disclosure.

The issuer must set `__Host-oneil_session` as `Secure; HttpOnly; SameSite=Strict;
Path=/`, with no Domain attribute. Deliver the CSRF token in a same-origin protected
bootstrap response, never a URL or log. Do not store login or CSRF tokens in local
storage. Require fresh owner authentication before creating command-capable
sessions. The API creates only short-lived confirmation references, not login
credentials. Test tokens are conspicuous public fixtures and are never installed
outside temporary test databases.

The application trusts its host, Python dependencies, issuer and private database.
SHA-256 digests do not protect a compromised host or a stolen bearer cookie. TLS
protects transport integrity; database-bound confirmation content prevents browser
edits, while idempotency hashes detect changed request bodies. Neither constitutes
an end-user digital signature. XSS, browser compromise, issuer compromise and
malicious local Python code require additional controls outside this boundary.

An authenticated request is not proof that a human clicked. A reviewed UI must
render every exact preview field, mode, warning and expiry and send the returned
reference only after that user's explicit confirmation. Financial/live activation
is outside this package and requires a separately reviewed user-controlled flow.

## Strict policy command path

The core bridge now requires the server-owned `SimulationPolicyCoordinator`.
Its `policy_order` preview/confirm path installs the exact `PolicyAdmission` before
releasing the shared Worker account lock. Manual keys use `policy-manual-<request_id>`.
The raw-order path described by the original boundary fixture contract is rejected
by the core bridge. See [the coordinator contract](POLICY_COORDINATOR.md) for host
composition, immutable intent, proposal-wide crash fencing and remaining gates.
Runtime-aware composition requires the explicit, already-started exact runtime;
its separate owner activation, health, archive and runtime-first locks remain
mandatory. HTTP confirmation never activates or resumes it.

## HTTP contract

All requests require a valid session cookie, trusted HTTPS and exact Host. No
query parameters are supported. Responses are JSON, no-store and nosniff; no
redirects, permissive CORS, raw exception text or raw account fingerprint.

- `GET /api/v2/status` requires `monitor.read`. Returns `monitor.v1` projection,
  `source=AUTHENTICATED_SIMULATION_CORE`, read-only and all UI controls disabled.
- `POST /api/v2/previews` requires `simulation.command`. Body has `action` and
  `expected_revision`. `action=policy_order` additionally requires a server-issued
  `proposal_id`; order, evidence and policy fields come only from the trusted host.
  `action=resume_simulation` has no order. Raw `action=order` is refused by the core bridge.
- `POST /api/v2/previews`, `activate_runtime`: exactly action and expected_revision.
  The server binds the preview to the runtime epoch/lease; a separate exact
  confirmation activates only that simulation runtime after core resume.
- `POST /api/v2/commands`, policy_order/resume/activate_runtime: exactly `action`, `confirmation_id` and
  `content_digest`, copied from the displayed preview.
- `POST /api/v2/commands`, halt: exactly `action=halt`, `expected_revision`.
- `POST /api/v2/commands`, pending cancellation: exactly `action=cancel_pending`,
  `expected_revision`, `request_id`. Cancellation after admission remains refused
  by the existing core. Halt stops new admission; it is not broker cancellation.

Each POST needs `Content-Type: application/json`, `Content-Length`, exact Origin,
`Sec-Fetch-Site: same-origin`, `X-CSRF-Token`, `Idempotency-Key`, `X-Request-Nonce`
and `X-Request-Timestamp` (UTC Unix seconds, maximum 30 seconds clock difference).
Keys/nonces contain 16–128 letters, digits, `_` or `-`. Use an independently random
nonce for each network attempt. Keep the original idempotency key and exact body
bytes on retries; never create a fresh key automatically after an unknown result.
The service never retries a core effect.

Previews expire within 60 seconds, and before session/order expiry. Order expiry
must be within the next 120 seconds. `display.notional_krw` is a decimal string to
preserve integer precision above JavaScript's safe integer limit. Quantity and
limit price retain the core's integer bounds. Preview validation does not replace
core policy, fresh broker/session evidence, or final admission checks.

A valid completed retry returns the original status/receipt, not the current
request state. Read status separately. Authentication/authorization is checked
again before returning that receipt. A reused nonce is rejected even for an
otherwise idempotent retry; make a new nonce, not a new idempotency key.

## Core and monitor integration

`CoreSimulationBackend(existing_store)` accepts the exact `PostgresStore` or the
fixed-account `SQLiteSimulationStore` from the explicitly initialized local
simulation host. The SQLite path uses real durable transactions and OS locks, not
a PostgreSQL substitute; see `../oneil_runtime_v2/SIMULATION_HOST.md`. PostgresStore
restricts databases to `oneil_v2_*` and SIMULATION. Bridge construction opens no
connection. Each serving thread must use its own core connection, created
on that thread. Do not share the bridge between threads or use transaction-pooling
proxies. A reviewed host/factory must compose `ControlAPI`, `ControlState`, that
thread's bridge and the real `project_status` callable.

The integrated `oneil_monitor_v2` UI now accepts the authenticated source, uses
same-origin credentialed GET status reads, and retains permanently disabled
controls. Its `MonitorHost` rejects every non-GET request, requires simulation
commands disabled, and serves no fixture or login endpoints. Separate mock UI
responses are explicitly labeled `MOCK_AUTHENTICATED_SIMULATION`; real and mock
transports reject each other's source. See `../oneil_monitor_v2/README.md`.

The runtime package still receives the projector explicitly. Integration now pins
the versioned projector in this checkout, removing the old sibling-directory test
dependency. Actual browser login/TLS/cookie and PostgreSQL integration remain open;
mocked UI tests do not prove those production properties.

## Failure, persistence and recovery

The journal and core use separate databases and cannot commit atomically together.
The service does **not** claim exactly-once delivery or execution. A committed
STARTED command may mean the core was never called, did commit, or returned before
journal completion failed. It stays unknown without time-based reset or resend.
The coordinator's immutable proposal/manual intent key is the core's stable
idempotency key. A proposal-wide tombstone also fences new HTTP keys and previews.
Neither fence is used to guess/retry an ambiguous effect.

A reviewed operator recovery workflow must inspect both journals and correlate
exact identity before recording an outcome. No reset/delete/force-retry HTTP API is
provided. Preserve commands and confirmation tombstones across restart, backup,
restore and retention. Restoring one database to an older point can destroy the
safety correspondence; an externally enforced account halt and joint recovery
plan are required before resuming. Core reconciliation remains authoritative for
actual orders and ambiguous broker outcomes.

Revocation prevents later admission and receipt retrieval; it cannot undo a command
already admitted. Unknown command outcomes and expired/revoked sessions must not
be reported as an order rejection. Read-only poll failure must not clear stale or
unknown evidence.

The durable command limiter allows 60 authenticated valid-shape attempts per
session/minute, including rejected confirmations and completed receipt retries.
Invalid framing, unauthenticated requests and reads need a separate reverse-proxy
abuse limiter and bounded socket/read timeouts. Journal commands/audit are retained;
capacity monitoring, migration/versioning and an approved retention plan are still
required. The local audit is append-only by trigger, not a tamper-proof external log.

## Validation

From the execution-v2 checkout:

```sh
python -m unittest discover -s tests/control_v2 -v
python -m compileall -q oneil_control_v2
```

The original boundary suite has **69 network-free tests**; additional coordinator
flow tests are documented in [the coordinator contract](POLICY_COORDINATOR.md). They cover session expiry/revocation,
strict auth/CSRF/origin/body handling, exact order fields, single-use/expired/tampered
confirmation, action mismatch, stable idempotency, replay after restart, durable
SQLite concurrency, session revocation during admission, rate limiting, delayed
body reads, actual journal lock waits, expiry after either admission lock, delayed
receipt retries, slow status reads, subsecond evidence freshness, corrupt session
records, lossless money display, crash-after-effect and failed journal completion.
Six tests inspect bridge transaction ordering with doubles; those are **not**
real-PostgreSQL integration evidence. No browser login, actual TLS server, real
core database, broker, live account or financial behavior was exercised.

## Remaining release gates

1. Independent source/security review and actual multi-process SQLite durability,
   forced process-kill, corrupt/disk-full/restore tests. Review state file ownership,
   schema upgrades, retention and external security/audit telemetry.
2. Actual PostgreSQL end-to-end bridge tests, including revision/admission races,
   unresolved-order resume races, crashes on each cross-database commit boundary,
   and exactly the final code revision. Existing core concurrency gates still apply.
3. Reviewed identity provider, account access mapping, logout/revocation integration,
   fresh reauthentication, HTTPS proxy trust/framing, edge rate limits and timeouts.
4. Actual authenticated monitor deployment/login verification beyond the completed
   mocked read-only UI integration. Any command UI still needs an accessible exact-
   confirmation flow and browser tests for double clicks, changed drafts and expiry.
5. All core broker evidence, legacy/new ownership fencing, policy migration,
   deployment/cost, recovery and explicit user live-activation gates. No credentials
   or real financial actions are authorized by this development artifact.
