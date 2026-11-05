# Supervised runtime assembly: offline validation record

Date: 2026-11-05. Linux cloud workspace, Python 3.12.14, Node 24.22.1,
Git 2.52.0. No native Windows machine, production PostgreSQL or real KIS account
was accessed. No actual network/DNS/TCP/TLS requests were made.

## Source and current aggregate

Base `e107bae`; preserved source tips `b10eb02`, `13e130f`, `3787323`,
`e5c4c24`, `4f3fa28` and `73d0693`. Public-spec and inactive authenticated
read-source documentation are included in the required package inventory.
`python tools/run_monitor_v2_tests.py`: **905 Python + 33 Node = 938 tests**,
zero failures and zero skips. Every execution, control, monitor, policy and runtime
offline module is discovered once. The 37 PostgreSQL cases remain a separate,
unrun acceptance gate; they are not counted as passes or hidden skips.

Independent integration review verified preservation and combined orchestrator
semantics, and passed 93 focused cases: runtime coordinator 24, supervised Worker
composition 17, mocked concrete HTTPS 52. No merge-blocking safety defect was found.

Cross-component regressions also cover concrete HTTPS code with all network
primitives mocked, seven parsed GET captures through transport and policy
pipeline, and an explicitly runtime-confirmed request remaining blocked without
an attempt. Older malformed refresh/UNKNOWN tombstone/discovery/inventory cases
remain included. The test inventory caught the initially omitted HTTPS source
and test entries, which are now required in both aggregate and package.

The new simulation host adds 12 actual SQLite store tests, 25 host/lifecycle tests
and four Node dashboard race tests. An actual supervisor/child process completes
explicit simulation session injection, WSGI core resume, separate runtime activation,
exact proposal confirmation, one strict simulated ACK, checkpoint and durable stop.
Core transactions are real SQLite, not SQL doubles. Synthetic market/identity and
clock inputs are explicit; no broker or network capability is inferred.

The earlier supervised composition suite includes one real Linux Python subprocess with
synthetic clocks and isolated SQLite, verifying reconciliation and durable stop.
It is not a Windows/PowerShell, broker, PostgreSQL or power-loss test. Other paths
use named SQL-core, HTTP/process and provider doubles with local SQLite journals.

## Inactive authenticated GET foundation

The source adds 61 synthetic, socket-forbidden credential lifecycle, cancellable
GET retry, rate/cooldown, pagination, redaction and fail-closed admission tests.
That baseline supplied injected provider/client contracts. The later optional
Windows-local implementations are described below. The existing
credential-free HTTPS client and all production/runtime wiring remain unchanged.
Independent review reran all 271 KIS tests and an adversarial frozen-clock bearer
reuse probe; token resurrection, cleanup and persistent-auth-failure findings
were fixed before the final pass. No remaining blocker was found for this
inactive scope. These checks do not establish authenticated provenance or live
semantic evidence. See [KIS_AUTHENTICATED.md](../../oneil_execution_v2/KIS_AUTHENTICATED.md).

## Optional Windows-local credential and token implementation

Separate, inactive source extends `1132dc1` with exact-scope expiring local
permissions, Windows Credential Manager app-pair storage, a one-permission tokenP
issuer and an independently validated authenticated GET client. Native API and
all network operations are replaced by synthetic test doubles. No consent UI or
runtime is connected. Independent review found permission polling and frozen-UTC
transit expiry gaps; regression fixes are verified before the final package.
See [KIS_WINDOWS_AUTH.md](../../oneil_execution_v2/KIS_WINDOWS_AUTH.md) for exact
limits and the separate acceptance gates. No real secret or broker operation was
performed. The new implementation adds 87 tests: permission 9, store/provider 20,
authenticated HTTPS 38 and token/complete composition 20.
Independent review reran the 904-case aggregate before the final cleanup-time
regression, then reviewed that final change and passed all 20 token tests. The
final 905-case aggregate passed again after that change, with all 33 Node tests.

## Separate acceptance gates, not run

- Real PostgreSQL/psycopg transactions, locking, concurrency, DDL and crash tests.
  Required binaries/driver are absent; no installation, listener, socket retry,
  proxy or prior-denial workaround was used.
- Native Windows/PowerShell locks, process/sleep/reboot/power-loss and ACL tests.
- Actual DNS/TCP/TLS, broker authentication, recorded responses and current policy
  evidence. Concrete authenticated HTTPS is inactive with no real credentials or production wiring.
- Actual browser HTTPS hosting/login/session delivery. Same-process WSGI dashboard
  control now reaches the real supervised SQLite simulation runtime, but no
  listener was opened and no production identity provider is available.
- Legacy execution exclusion, production identity, installation, deployment and
  user-controlled real operation. No real order or financial action was taken.

Read [INTEGRATION.md](INTEGRATION.md), [COMPOSITION.md](../../oneil_runtime_v2/COMPOSITION.md)
and [KIS_HTTPS.md](../../oneil_execution_v2/KIS_HTTPS.md) for exact scope, including
Content-Length-only success framing. The credential-free transport remains
fail-fast on multipage pacing; the inactive injected authenticated source adds
bounded pacing without activating an authenticated client.

## Package verification

The source builder exports a clean exact Git commit. All current public source,
tests and documentation are required. Two builds with the same Python/zlib
must be byte-identical; the external verification record supplies final commit,
SHA-256, size and extracted-test results. Dependencies are not bundled. Integrity
is not publisher authentication or operational readiness. Cross-version
compression identity is not promised.

Preflight runs without opening a network connection, reports Windows and psycopg
unverified/missing, and always exits 2 with ready_to_trade false. With system
IANA timezone lookup and site packages disabled, it reports missing KST data
without crashing. A Windows environment usually needs trusted tzdata setup.
