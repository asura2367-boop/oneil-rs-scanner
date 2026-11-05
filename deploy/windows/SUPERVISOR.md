# Manual Windows offline supervisor rehearsal

**Source implementation, not an installed or Windows-validated service. No live
orders, broker connection, account credentials, network listener, scheduled task,
software installation, security setting or existing engine is changed.**

The fixed reconciliation child now composes the actual strict `Worker`, `SimulationRuntime`,
`SimulatedBroker` and a durable isolated SQLite reconciliation adapter. It can
reconcile explicitly synthetic history, but cannot enqueue, claim, admit, submit
or resume orders. The local core is permanently halted. Real DB/broker readiness
remains false. See [COMPOSITION.md](../../oneil_runtime_v2/COMPOSITION.md) for the
exact class composition, stop/recovery contract and test-only synthetic scenario.

## Manual interface (not executed on the user's PC)

Use already trusted Python 3.10+ and the IANA Asia/Seoul timezone database required
by v2 imports. No dependency installation is performed. Use a verified extracted
source checkout and an entirely new private local state directory. Do not use an
existing operational folder, OneDrive/Dropbox, network share or removable drive.
The launcher rejects obvious symlinks/reparse points and UNC paths, but it cannot
prove Windows ACLs, filesystem durability or that all sync software is absent.

The PowerShell wrapper requires fully qualified local drive paths (for example
C:\Folder\file), rejects drive-relative/root-relative/UNC inputs, and captures
canonical paths before changing its working directory. It requires a trusted
python.exe path and defaults
to **status**, not startup. It does not change execution policy. If local policy
blocks the script, stop and follow that policy; do not bypass it. These example
paths are placeholders, not verified paths on the user's computer:

```powershell
# From the verified source root. Choose a new state directory yourself.
.\deploy\windows\Start-OfflineRehearsal.ps1 -PythonExe 'C:\Python312\python.exe' -StateDirectory 'C:\OneilOffline\rehearsal-01' -Action init
.\deploy\windows\Start-OfflineRehearsal.ps1 -PythonExe 'C:\Python312\python.exe' -StateDirectory 'C:\OneilOffline\rehearsal-01' -Action run
# In a second console: this requests a stop; status confirms the later outcome.
.\deploy\windows\Start-OfflineRehearsal.ps1 -PythonExe 'C:\Python312\python.exe' -StateDirectory 'C:\OneilOffline\rehearsal-01' -Action stop
.\deploy\windows\Start-OfflineRehearsal.ps1 -PythonExe 'C:\Python312\python.exe' -StateDirectory 'C:\OneilOffline\rehearsal-01' -Action status
# Only after STOPPED, an explicit new offline start preserves the crash budget.
.\deploy\windows\Start-OfflineRehearsal.ps1 -PythonExe 'C:\Python312\python.exe' -StateDirectory 'C:\OneilOffline\rehearsal-01' -Action run -StartStopped
```

The parent of the new state directory must already exist. `init` refuses any
existing target directory, including an empty one. It never replaces a missing or
corrupt journal. A partial failed initialization is not silently cleaned up.
The direct module CLI has the same `init/run/stop/status` interface. All CLI
operations reject non-Windows hosts; Linux unit tests use explicit injected
adapters, not an operational override. `--help` is read-only everywhere.

## Safety properties and limitations

- One OS-backed file lock is held throughout a supervisor run. Windows uses
  `msvcrt.locking`; POSIX `flock` is a portable test adapter only. Two cooperating
  supervisors using the **same state directory** cannot run together. Different
  directories, other hosts, the legacy engine and existing workers are not fenced.
- The child command is fixed to the repository's reconciliation-only worker child with the current
  absolute Python interpreter, `-I -B`, no shell, no arbitrary command/module,
  no inherited credential environment, and no stdin. No account or endpoint option
  exists. The PowerShell wrapper also uses an isolated fixed Python entrypoint.
- STARTING is durable before taking the runtime lease or spawning. Each child
  gets a new epoch in RECONCILING with no activation, health or previous recovery
  evidence. Startup is blocked; verified local synthetic reconciliation can reach
  AWAITING_OWNER, but no activation or core resume exists. A new process is never
  authority to trade.
- Only an exit observed on the exact owned `Popen` process handle can release its
  claim. Abnormal exits consume the persistent shared runtime budget: 5, 10, 20
  seconds and then FAILED, at most three restarts after the initial launch. Each
  delay uses monotonic waiting; reopening a RESTART_WAIT state waits its full
  delay again and also respects the runtime's UTC deadline. Manual stop/start
  never resets the budget. A clean exit is not restarted.
- `stop` persists a request. The child checks it every 0.5 seconds. The supervisor
  waits five seconds, then may terminate only its exact child handle and wait
  another five seconds. If termination is unconfirmed, it keeps ownership and
  blocks another launch. It does not kill processes by PID/name from disk. Ctrl+C
  takes the same conservative path. Another console can verify STOPPED; the stop
  command alone is not a stop-completion receipt. A stop racing an already
  admitted STARTING phase may briefly launch the fixed reconciliation child. If the request is
  durable before the child's first stop check, it exits before observing; a request
  arriving after a check is handled on the next loop. The child is not restarted.
  This race does not guarantee that spawning is prevented. A concurrent newer stop cannot
  be erased by a stopped-start request.
- A crashed supervisor may leave its child alive. A freed OS lock is **not** child
  exit proof. Durable STARTING/RUNNING on a new invocation therefore blocks with
  PRIOR_CHILD_OUTCOME_UNKNOWN_OPERATOR_REQUIRED, even if a PID is absent, reused
  or stale. Spawn API errors are conservatively uncertain and also block. There
  is deliberately no `--force`, stale-PID takeover, journal-delete recovery, reboot
  auto-reclaim or restart-budget-reset command.
- Unexpected journal errors trigger best-effort cleanup, never a claim of
  confirmed safe stop. An uncertain write keeps a conservative durable phase;
  subsequent mismatches fail closed. The runtime/supervisor files are separate
  databases, not one atomic distributed state. A manual restore is not supported.
- This supervisor does not terminate merely because a heartbeat is stale and does
  not send external alerts. A hung child remains non-ready. Its live child status
  is not a broker/DB health claim. A sleeping/offline PC cannot notify while down.
- Local state and locks are cooperative, not tamper-proof. Source edits, malicious
  state replacement, external termination and Windows power-loss behavior remain
  outside the verified model. No actual application identity is established by
  the synthetic local `AuthenticatedOwner` used for an explicit stopped restart.

## Verification and promotion gates

Run `python -m unittest discover -s tests/runtime_v2 -p test_supervisor.py -v`
and the complete network-free `python tools/run_monitor_v2_tests.py` in a suitable
source test environment. Tests cover fresh/missing/corrupt state, lock contention,
bounded restarts, clean/explicit stop, interrupted spawn, stale PID, unconfirmed
termination, strict fixed command, unknown dependency evidence and exact-child
exit observation. Mock times and subprocess results are not real Windows proof.

Before calling this an operational Windows host, separately verify:

1. Actual supported Windows/Python/PowerShell, Unicode/spaced paths, required
   timezone, native byte locks and the fixed isolated child launch.
2. Same-directory second process refusal, Ctrl+C/stop races, killed child, killed
   parent, parent failure during spawn/state write and no unauthorized takeover.
3. Sleep/hibernate, reboot/power loss, discontinuous clocks, full/read-only disk,
   inaccessible journals, permissions, and backup/restore consistency.
4. A production-compatible read-only evidence/Worker adapter, authenticated owner
   flow, authoritative PostgreSQL admission and a complete legacy/v2 shared
   execution fence. The new isolated SQLite composition does not supply these.
5. PostgreSQL failure/concurrency and complete broker evidence tests in an
   authorized test environment. The earlier denied socket operation was not
   retried, proxied, replaced with another network route or treated as a pass.
6. User-controlled real operation gating. This source has **no live activation
   path**. Passing every mock test still does not authorize financial activity.

No scheduled task, service, autostart, external monitoring or deployment is
provided or enabled. Automatic recovery after a supervisor crash is intentionally
an unresolved gate, not a green checkmark.
