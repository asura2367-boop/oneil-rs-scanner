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
No runtime, dashboard or worker activates KIS access. A broker token can have
rights beyond local GET restrictions; it is not a broker-enforced read-only token.
No actual credential access, storage, token issuance or transmission occurred.
The new simulation host requires explicit local trusted TLS inputs and does not
install certificates, alter trust, set ACLs, create services or schedule itself.

## Verification

- Exact network-free aggregate: **945 Python and 44 Node tests passed**, zero
  failures or skips. The package requires all 139 public rebuild files.
- Independent review checked polling deadlines/cancellation and local host
  lifecycle/security boundaries. Findings were fixed and covered by regressions.
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
