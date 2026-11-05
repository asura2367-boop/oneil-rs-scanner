# Combined offline validation record

Date: 2026-11-05. Linux cloud workspace, Python 3.12.14, Node 24.22.1,
Git 2.52.0; no Windows PC accessed.

## Combined source provenance

Common baseline: `5fd6730`. Original commits preserved without cherry-pick
duplicates: policy import/planning `eebd0b8`, strict worker policy admission
`74ca1369`, Windows operations foundation `904c216d`. The combined branch adds
aggregate/runtime coverage, complete required package inventory, timezone
preflight and explicit UTF-8/ASCII transport corrections. The exact packaged
commit is embedded in MANIFEST.json; do not infer it from an earlier component ZIP.

## Verified offline on Linux

- `python tools/run_monitor_v2_tests.py`: **368 Python + 29 Node = 397 tests
  passed without skips**. This includes core, control, monitor, policy, runtime,
  archive-integrity and Windows-locale regression tests.
- Independent read-only review found missing Windows IANA timezone prerequisites
  and locale-default Korean HTML/JSON handling. Both were corrected. UTF-8 mock
  generation is tested with CP949 defaults; the API fixture emits ASCII-safe JSON.
- Preflight returns intentional exit 2, `ready_to_trade: false`, and explicitly
  reports Windows/suspend-clock unverified and psycopg absent.
- With system timezone lookup disabled and Python site packages excluded,
  preflight also reports `KST_TIMEZONE_DATA_MISSING`, without importing the core.
- Archive verifier requires the complete fixed implementation/test inventory,
  including policy admission and both separate PostgreSQL test tools. Missing
  components are rejected even when the archive's own hashes match.
- Added 33 network-free tests for runtime lifecycle, durable backoff/stop, immutable
  original-intent restoration, explicit owner-only simulation activation, unknown
  order preservation, healthy active reconciliation beyond the freshness window,
  core/host clock disagreement, last-boundary staleness, sleep after committed
  admission, state-write failure, cooperative fence contention and bound-store
  callback omission. These use SQLite plus a scripted core, not PostgreSQL proof.
- Independent final runtime review reran **368 Python + 29 Node**, zero skips,
  and found no blocking issue within the documented simulation-only boundary.
  The active reconciliation timestamp defect found during review was fixed;
  control-revision changes and observed core halt invalidate runtime activation.
- Earlier component review also fixed unsafe Windows archive path aliases and
  repeated incident-row growth. Those regressions remain included.

The aggregate command does not discover the 37 `test_postgres.py` cases; this is
an explicit separate acceptance gate, not a skip hidden in the count. No
PostgreSQL listener, single-user validator or database workaround was run for
this combined release.

## Not verified or not implemented

Native Windows/PowerShell, actual Task Scheduler, installed service/supervisor,
native sleep/reboot/power-loss, Internet/KIS probes, real PostgreSQL, ACL/security
changes, credentials or authentication, live broker, financial actions and
deployment. The in-process SimulationRuntime now persists lifecycle/restart
budget, restores original archived intent only with explicit current contexts, and
holds a cooperative SQLite ownership lock through guarded simulation admission
and submit. It is not an OS supervisor or automatic process recovery service.
Health integration is exercised with a scripted core and optional bound-store
hooks, not real PostgreSQL. Actual legacy/new-engine shared account exclusion,
trusted runtime evidence providers, HTTPS/session issuer and production database
roles remain open acceptance gates.

Scripted-store tests cover policy admission logic, not PostgreSQL durability,
transaction isolation or actual Windows process recovery. They do not prove
production readiness. No dependency or paid service was installed or provisioned.

## Archive verification

The source archive exports its embedded exact Git commit. Dependencies are not
bundled. Verify MANIFEST.json and compare the overall SHA-256 with a separately
trusted release record before manual extraction. Integrity does not authenticate
the publisher or establish code safety.

The build fixes timestamps, ordering and modes. Byte reproducibility is checked
with the same Python/zlib toolchain; cross-version compression identity is not
claimed. Rebuilding from extracted files alone requires recovering the exact Git
commit because the builder exports Git objects and refuses a dirty worktree.
