# Simulation runtime orchestration contract

**Offline development only. Not installed, deployed, live-capable, or a Windows
service. No real account, broker transport, credential or legacy executor changed.**

## What now works in the isolated model

`SimulationRuntime` composes an existing strict `Worker`, a separately created
`RuntimeState`, a trusted clock and explicit trusted health observations. Its
`run` method is an in-process bounded polling loop. Construction does not connect,
launch, resume, provision or trade. There is no CLI that can enable execution.

1. Explicit initial setup creates a new SQLite file and registers a synthetic
   account fingerprint. Opening an existing state uses SQLite `mode=rw`; a missing,
   corrupt, version-mismatched or already initialized file cannot reset to healthy.
2. `start` commits a unique owner/epoch and reconciliation phase, discards old
   in-memory policy registrations, binds the runtime guard to the worker/core
   instance and asks the core to halt. No request is claimed by startup.
3. Every step durably evaluates a trusted health sample against the preceding
   dual-clock sample. Any unhealthy/unknown result clears activation and recovery
   evidence, retains a coded incident and asks the core to halt. If journal failure
   prevents that write, core halt is still attempted. Uncertain persistence poisons
   this guard in memory; it cannot admit again even if later observations improve.
4. Reconciliation-only ticks query history and preserve UNKNOWN, ACKNOWLEDGED and
   partial states without new claims. No absence-of-history retry exists. Healthy
   status does not clear halt or activation requirements.
5. `restore_pending` reconstructs only QUEUED/VALIDATING zero-attempt intent from an
   immutable archive and requires explicit current contexts for every pending ID.
   Reconstruction failure installs an empty policy source and halts. Restoring
   old contexts as though fresh never bypasses the final strict policy checks.
6. After successful health, reconstruction and reconciliation, the runtime waits
   for the owner. The existing separate authenticated **simulation** core resume
   must have occurred. A trusted `AuthenticatedOwner` for this exact account plus
   an exact control revision can call `activate_simulation`. That revision is bound
   to the runtime activation; a later core revision change invalidates it. This internal fixture
   type is not real identity verification and cannot be accepted from browser JSON.
7. Each guarded tick holds one SQLite `BEGIN IMMEDIATE` ownership lock from before
   the core account lock until after simulated broker submission/observation.
   `PolicyAdmissionGate` checks the exact guard at the final core admission
   boundary, before and after policy validation. The bound PostgresStore rejects
   omission/substitution of its guard. A fresh clock is compared with core time,
   health/dependency/heartbeat age, suspend/loop continuity and reconciliation age.
8. The guard is checked again after the durable attempt but before simulated
   submit. Failure then preserves SUBMISSION_UNKNOWN with one attempted slot and
   never sends/retries. It does not relabel a committed unknown as safely rejected.
   An already admitted in-flight effect cannot be retracted by a later halt.

Successful active history reconciliation refreshes its own timestamp; arbitrary
heartbeat success does not. Once a guard has halted, refreshing health alone never
reactivates it. Per-order owner confirmation, original expiry, policy checks,
core control revision and the at-most-one-attempt ledger remain independent gates.

## Intent archive and restart

`register_intent` verifies the exact pending core order before archiving and
registering it. It is a trusted server-side simulation method, not an HTTP action.
A crash after core enqueue but before archive persistence leaves missing intent;
that request cannot execute through this runtime until safely resolved.

The versioned codec has an explicit allowlist of data classes, fields and container
kinds. No pickle, executable code, dynamic import, credential loader or inferred
policy is used. Canonical encoding plus SHA-256 detects accidental corruption;
SQL triggers refuse replacement/deletion of an archived intent. This is not a
signature or protection from a hostile host or database owner.

Only original proposal/policy/evidence is retained. Current context must be supplied
anew; original quantity, prices, expiry, manual ID, reason and immutable provenance
cannot be rewritten. The archive contains no owner-approval receipt and cannot
recreate a request, alter an attempt/fill, reset a simulator balance or resume core
controls. Startup core halt/resume may invalidate prior queued control revisions;
reconstruction never upgrades their old approval to the new revision. Such requests
remain blocked/expired and need a new explicit exact order confirmation.

## Ownership and lifecycle limits

The same state file coordinates the named `V2_SIMULATION` and `LEGACY_SIMULATION`
participants. Tests prove contention and stale-epoch rejection for that cooperative
model. **The actual legacy engine does not participate.** Separate files, restored
copies, other hosts and unguarded existing workers are not fenced. No actual account
handover or legacy shutdown has been performed. A production fence must cover every
real execution path and hold through the external effect; a browser flag, local PID,
heartbeat timeout or code-version label cannot establish that guarantee.

A stale heartbeat never releases ownership. `record_exit` may only be called by a
trusted supervisor that has actually observed the exact owner's process exit.
There is no Windows supervisor implementation or OS exit verifier in this package.
After hard kill, the old owner remains claimed until that externally verified
recovery exists. Do not fabricate exit confirmation, clear the owner or delete the
journal to start another worker.

Confirmed abnormal exit persists a 5/10/20-second restart plan and consumes one of
three restart slots. Reopening the file, reconstructing a runtime or manual stop/
start does not reset the budget. Exhaustion fails closed with no reset API. The
wall-clock restart deadline is only a rehearsal backoff, not an OS monotonic timing
claim across reboot. Every permitted new owner starts reconciliation-only; no
process is launched here. Explicit stop, Ctrl+C and clean exit do not auto-restart.

The runtime/core/command journals are separate databases without a distributed
transaction. The in-process lock and scripted-core tests do not prove PostgreSQL
concurrency, cross-process power-loss durability, actual Windows ACLs or immunity
to independent database restore. Keep active files off network/cloud-sync folders.
The SQLite lock serializes all accounts in its file and may block a second process;
that is conservative coordination, not a measured throughput design.

## Required verification before any operational claim

Run `python tools/run_monitor_v2_tests.py` for network-free regressions. Then, in a
separately authorized suitable environment, real PostgreSQL admission/commit failure
and concurrency tests, Windows process-kill/reboot/clock/file-lock tests and evidence
provider tests are still mandatory. This workspace's prior denied socket operation
was not retried or worked around.

Production identity, complete verified broker facts, actual shared legacy fence,
OS supervision, backup consistency, policy shadow comparisons and explicit
user-controlled live activation remain open. A sleeping/offline PC cannot alert
while down; no external heartbeat monitor or paid service was created. No amount of
passing mock tests authorizes or proves real financial operation.

## Separate recovery and handover rehearsal

`ownership.py` adds an independent same-file fixture coordinator with atomic
recovery batches, unique native identities, non-clearing halts, causal fill proof
and signed synthetic both-engine handover/exit evidence. See
[the recovery contract](../oneil_execution_v2/RECOVERY.md). It does not wire the real
legacy engine or existing Worker to a shared production fence. The existing
runtime, core and actual legacy execution remain unchanged.
