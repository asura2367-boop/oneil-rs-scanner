# Combined Windows rehearsal source: readiness and boundaries

This is an integrated **offline development source**, not an operational trading
application. No host was installed, no Windows machine was used, no credential or
production database was read, and no broker network request or real order was sent.

## Recovered publication and source history

The current work starts from the verified published draft
`0f1512df0d95a2ab09a0e10a50dbede8f717d910`, whose 128 rebuild files preserve
source `d01a181ec1baf969c95b523f56a291bfcadd80da` byte for byte. A clean recovery
reran 905 Python and 33 Node tests, with zero failures or skips. Later unpublished
work was unavailable after the workspace was replaced. The subsequent polling
and local-dashboard changes in this source are newly implemented and reviewed;
they are not claimed to be a recovery of the missing commits or their results.

The identifiers below describe earlier source provenance, not additional Git
ancestors of this published draft. The published history preserves the reviewed
file content separately from those original local commits.

Current assembly baseline: `e107bae`, preserving all prior evidence, control,
transport and supervisor histories described by that commit. This assembly adds:

- Runtime-bound policy coordinator: `b10eb02`.
- Supervised strict Worker/reconciliation composition: `13e130f`.
- Concrete inactive credential-free HTTPS GET client: `3787323`.

The earlier assembly retained fresh supervised lease attach/quiesce and scoped
intent registration. Its independent review checked their combined semantics.
Aggregate and inventory regressions caught missing HTTPS test/source entries;
those entries are required. The ZIP manifest identifies the final exact source
commit of each new package.

## What has been demonstrated offline

- Authenticated simulation control confirmation can register exact server-owned
  proposals for the strict Worker and exact SimulatedBroker, either standalone or
  with its explicitly bound, independently activated simulation runtime. The
  runtime path enforces runtime-first ownership, original-intent archival and
  conservative cross-journal failure halts. Order confirmation never activates it;
  runtime activation requires its own exact lease-bound confirmation.
- Seven supplied, paced captures pass through the concrete HTTPS client (all
  DNS/TCP/TLS replaced with offline doubles), transport and KIS diagnostic pipeline. They remain unauthenticated and cannot authorize a
  policy context. Installing them for an explicitly runtime-confirmed request
  blocks its attempt; a successful HTTP-shaped response is not trading authority.
- Malformed KIS refresh removes an older synthetic registration before parsing.
  A block during control dispatch remains an unknown command with a durable
  proposal tombstone; new HTTP keys/confirmations cannot resend it.
- The offline supervisor now runs the actual strict Worker/SimulationRuntime with
  isolated SQLite and SimulatedBroker reconciliation dependencies. New-order
  admission and resume are disabled. A real Linux subprocess test with test-only
  clocks verifies owner-wait and durable stop; native Windows is still untested.
  See [COMPOSITION.md](../../oneil_runtime_v2/COMPOSITION.md).
- All current public source and test files are required in the archive. Discovery
  tests ensure every Python test module except the explicit PostgreSQL suite is
  included once; passing a smaller component suite is not a full integration pass.

These include real local storage/process and mocked/network-free tests, not recorded authenticated KIS,
native Windows or real PostgreSQL execution evidence.

## Genuine operational gaps

1. **Local simulation composition is implemented; hosting is not installed.** An
   explicitly initialized fixed-account SQLite profile now connects the actual
   supervised process, runtime, strict Worker, durable command store, coordinator
   and WSGI dashboard. Real SQLite/OS-lock and actual child-process tests cover
   exact authenticated simulation commands. No PostgreSQL mock is used in these
   tests. Production identity stays unavailable and no session exists by default.
2. **Optional local browser hosting source exists; native acceptance is open.**
   The explicit Windows-only `init`, `check` and `run-dashboard` commands add a
   single-threaded HTTPS simulation host bound to literal `127.0.0.1`. It requires
   explicit local TLS files, private-storage checks and a console, and supplies a
   one-use 120-second pairing code, 15-minute Secure/HttpOnly session cookie and
   explicit logout. It never supplies production identity or activates broker
   access. The default worker child still starts no listener. No listener was
   opened, certificate installed, trust changed or socket restriction bypassed
   during development. WSGI/wire/Node doubles are not a native Windows or real
   browser/TLS pass. No service or scheduler was installed. Unknown ownership
   and cross-journal crashes still require fail-closed recovery. See
   [LOCAL_DASHBOARD.md](../../oneil_runtime_v2/LOCAL_DASHBOARD.md) and
   [SIMULATION_HOST.md](../../oneil_runtime_v2/SIMULATION_HOST.md).
3. **Credential and authenticated HTTPS implementations exist but are inactive.**
   The unchanged credential-free client and optional Windows Credential Manager,
   tokenP issuer and authenticated GET client have synthetic-only tests. There
   is no human-consent UI, real credential, runtime wiring, native validation or
   broker verification. Successful replies require bounded Content-Length JSON.
   See [KIS_WINDOWS_AUTH.md](../../oneil_execution_v2/KIS_WINDOWS_AUTH.md).
4. **Bounded authenticated pagination and polling remain inactive source.** The
   credential-free direct traversal remains fail-fast at rate limits. The
   authenticated source adds pacing/cancellation/bounded retries and history/
   balance pagination. A separately constructed, caller-started finite poller
   now sequences the seven fixed read streams, propagates one absolute deadline,
   captures holiday only once and suppresses success after cancellation. It
   exposes sanitized progress counters only, not policy evidence or account data.
   No runtime consumes it and a refreshed token requires new explicit local
   issuance permission. Mock traversal does not establish actual complete broker
   history. See [KIS_POLLING.md](../../oneil_execution_v2/KIS_POLLING.md).
5. **KIS-to-policy authority is missing.** Complete shapes do not establish
   authenticated origin, coherent snapshot/fill causality, settled exact-order
   buying power, managed ownership, daily account loss/commitments, executable
   quotes or authoritative session/calendar/eligibility. Pipeline output cannot
   become a PolicyContext. Synthetic fixture success does not close these gates.
6. **PostgreSQL acceptance remains unrun.** The 37 integration cases and updated
   SQL validation require a permitted test environment with PostgreSQL/psycopg.
   No listener, socket retry, proxy or alternate route was attempted here.
7. **Windows acceptance remains unrun.** Native PowerShell, lock behavior,
   sleep/hibernate, reboot/power-loss, full disk, ACLs, stop/launch races and parent
   crash behavior must be verified on the actual intended host.
8. **Production isolation and identity remain open.** Existing legacy executors
   do not cooperate with the new local ownership rehearsal. Complete shared
   account exclusion, coherent backup/restore, production DB roles, HTTPS/login,
   owner identity, monitor integration and outage alerts are not delivered by a
   source merge. No live activation path or financial authorization is added.

Do not describe this source ZIP as installed, live, Windows-validated, ready to
trade, or a completed always-on trading service. See [VALIDATION.md](VALIDATION.md)
for the exact offline checks and [SUPERVISOR.md](SUPERVISOR.md) for manual reconciliation-only
semantics. Existing production settings and execution are unchanged.
