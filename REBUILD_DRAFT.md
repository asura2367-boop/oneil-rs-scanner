# Draft offline rebuild v2

This branch preserves isolated source for offline review and simulation. It is
not a release, deployment, activation, account migration, or authorization to
place orders. Live `policy_evidence()` and `submit()` remain blocked.

## Verified recovery and new source

This update starts from published draft
`0f1512df0d95a2ab09a0e10a50dbede8f717d910`, which preserved 128 source/test/document
files from source `d01a181ec1baf969c95b523f56a291bfcadd80da`. Its 905 Python and 33
Node tests were rerun after recovery. Later unpublished work was unavailable
following workspace replacement. The following changes are newly implemented
against the verified publication, not claimed recovery of missing commits:

- An inactive, explicit finite seven-stream KIS read poller with shared absolute
  deadlines, pacing, cancellation-safe progress, once-per-run holiday reads and
  orderly source cleanup. Its sanitized output never becomes trading evidence.
- Explicit Windows-only simulation `init`, `check` and `run-dashboard` commands;
  bounded single-threaded HTTPS on literal 127.0.0.1; one-use 120-second console
  pairing; 15-minute Secure/HttpOnly sessions and CSRF-protected logout.
- Read-only local fixed-volume and private DACL/ancestor checks, clean explicit
  restart, fail-closed failure reporting and browser interruption/repeat fences.

Credentials/token/GET implementations remain inactive and separately permissioned.
No runtime, dashboard or worker activates KIS access. Optional observation
attachment does not start or authorize a source. A broker token can have
rights beyond local GET restrictions; it is not a broker-enforced read-only token.
No actual credential access, storage, token issuance or transmission occurred.
The new simulation host requires explicit local trusted TLS inputs and does not
install certificates, alter trust, set ACLs, create services or schedule itself.

## Read-to-runtime diagnostic integration

Built on verified remote `49fa9205b73e42d7a99fa7c8de1d2ae767497c38`, whose tree
`ff6136cb2f42fe4d3ca8d39e7ec32cce69b5727b` exactly matches local `a6efb1e`.
The new explicit diagnostic runner connects actual supplied read captures to the
existing seven-stream parsers and the authenticated simulation dashboard. It
reports malformed, inconsistent, incomplete, stale, cancelled and failed cycles
instead of treating a completed read counter as parsed evidence. Old reports are
invalidated before refresh, and a cycle cannot appear before the poller's commit.
The host can observe/cancel an explicitly attached runner but never constructs
credentials, starts broker reads or promotes its result into trading evidence.

See [runtime diagnostics](oneil_runtime_v2/KIS_DIAGNOSTICS.md). The default console
still has no broker connection. All real broker, native Windows and PostgreSQL
acceptance gates below remain open.

## Bounded dashboard status recovery

Built on verified remote `a0467e8470ecd0a885f19a03d7ead0820415a3b5`. A stale
browser view could previously retain its last status indefinitely. The console
now clears old status before refresh and expires snapshots within 15 seconds of
request start, shortened by the diagnostic's remaining UTC/monotonic validity.
Five-second fetch/body timeouts release hung waits without retrying. Clock
changes, hidden/navigation/restored pages, expired confirmations and late
responses cannot revive old controls or status. Internal action checks remain
fail-closed even when a browser timer has not run. POST timeout/abort does not
claim rollback or automatic recovery of an unknown host outcome.

## Verification

- Exact network-free aggregate: **968 Python and 69 Node tests passed**, zero
  failures or skips. The package requires all 142 public rebuild files.
- Independent review checked diagnostic publication, stale/cancel/deadline
  handling, host projection and packaging. Browser review covers delayed reads,
  old authentication failures, request/body timeouts, hidden/restored pages,
  command completion after expiry and malformed confirmation expiry. All checks
  use offline doubles; native browser behavior is not claimed as tested.
- Tests include actual isolated SQLite, OS locks and Linux subprocess simulation.
  DNS/TCP/TLS, Windows APIs, broker responses and browser DOM are mocked.
- Common secret/private-artifact patterns were checked before publication. Test
  identities and app-key/app-secret/bearer values are synthetic.
- All 89 inherited GitHub Actions workflows remain unchanged. Publication uses
  `[skip ci]` and no pull request to avoid legacy workflow execution. Remote CI
  is not claimed as run or passing; local aggregate results are the evidence.

## Open acceptance gates

Native Windows identity/DACL/fixed-volume behavior, trusted certificate and real
browser flow, sleep/reboot/power-loss, PostgreSQL durability/concurrency, trusted
human consent and secret entry, actual authenticated broker schema/provenance,
semantic evidence, machine-wide quota/legacy exclusion, production identity and
deployment review remain open. Storage preflight is a snapshot, not protection
against hostile same-user/admin changes or every filesystem TOCTOU race.

Repository main, production workflows, dashboard state, account data and live
settings were not changed by this update. Legacy files remain inherited context.
No real KIS request, credential configuration, installation, deployment,
activation or real order was performed. Green offline tests and source
publication do not establish production readiness.

Do not run legacy trading workflows from this branch, merge it, deploy it, or
connect production credentials as part of review. No live order transport is
supplied by this rebuild.

Start with [the local simulation console](oneil_runtime_v2/LOCAL_DASHBOARD.md),
[bounded inactive polling](oneil_execution_v2/KIS_POLLING.md),
[the integration guide](deploy/windows/INTEGRATION.md), and
[the validation record](deploy/windows/VALIDATION.md).
