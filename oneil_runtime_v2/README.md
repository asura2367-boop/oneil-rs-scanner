# Windows offline operations foundation

**준비용 소스 패키지입니다. PC에 설치하거나 자동 시작을 등록하지 않았고,
사용자의 Windows PC 연결 여부도 확인하지 않았습니다. 실거래는 불가능합니다.**

Combined supervised-runtime source based on `e107bae`, preserving runtime
coordinator `b10eb02`, reconciliation Worker composition `13e130f`, and concrete
inactive HTTPS client `3787323`. The original component histories remain intact.
No legacy trading code is imported. These components are **not an end-to-end
operational host**: the supervised SQLite core is permanently halted, while the
control coordinator requires the separate PostgreSQL core. The original HTTPS client is
credential-free and unwired. Optional Windows-local credential/token/authenticated
GET components now exist separately, also unwired and synthetic-tested only; see
[KIS_WINDOWS_AUTH.md](../oneil_execution_v2/KIS_WINDOWS_AUTH.md). See [INTEGRATION.md](../deploy/windows/INTEGRATION.md)
for exact verification and gaps. Never relax policy/runtime gates or use fixture-
only mode as an operational fallback. ZIP MANIFEST.json identifies the exact
final source commit.

## Included

- New fixed-child offline process supervisor and manual Windows launcher. Its
  [real simulation Worker composition](COMPOSITION.md) reconciles isolated SQLite
  and explicitly synthetic broker history, with no order admission or submission.
  It is not Windows-validated or installed. See
  [SUPERVISOR.md](../deploy/windows/SUPERVISOR.md) for exact launch/stop semantics.

- Pure health evaluator: stale/future heartbeat and dependency observations,
  sleep/hibernation, long loop pauses, backward/discontinuous clocks, missing
  worker, low disk space and unresolved-order uncertainty all fail unhealthy.
- Native Windows read-only clocks: GetTickCount64 includes suspend time;
  QueryUnbiasedInterruptTime excludes it. Their difference detects sleep after
  resume. Missing clock support fails unknown rather than silently passing.
- SQLite incident latch for offline rehearsal: startup begins blocked, faults
  persist across reopen, healthy samples never clear incidents, missing/corrupt
  files never create a clean replacement. Repeated faults keep one indexed
  first-seen latch per reason, not an unbounded sample log. There is deliberately
  no reset/resume.
- Pure bounded restart planner: 5/10/20 second delays, then operator review;
  stopped/clean-running processes are not restarted. Every suggested restart is
  reconciliation-only with commands off. **It does not launch processes.**
- In-process simulation orchestration, durable lifecycle/restart budget, strict
  original-intent restoration and a cooperative account ownership model. Health
  is revalidated at guarded policy admission and before simulated submission.
  See [ORCHESTRATION.md](ORCHESTRATION.md) for exact scope and remaining gates.
- Non-mutating preflight and deterministic source ZIP builder/verifier.
- Windows operating and recovery plan in [RUNBOOK.md](../deploy/windows/RUNBOOK.md).

## Run locally without account data or network

Run preflight first from the extracted source folder, with an already available
trusted Python 3.10+. Preflight itself does not need the timezone database:

```powershell
python -m oneil_runtime_v2.preflight
```

Both runtime tests and the complete network-free suite require the IANA
`Asia/Seoul` timezone database. The complete suite additionally requires Node
with its built-in test runner/fetch/Response APIs (Node 24.22.1 tested). Windows usually needs the official `tzdata` Python package because it
does not ship that database. Preflight detects its absence without importing the
core. No dependency is bundled or installed here. Use separately authorized,
trusted dependency setup before running the complete suite:

```powershell
python -m unittest discover -s tests/runtime_v2 -v
python tools/run_monitor_v2_tests.py
```

The second command covers core, control, monitor, policy and runtime plus Node contract
tests, and rejects skipped tests. It never runs the real PostgreSQL suite. The
Unix-socket PostgreSQL harness is included only as source for a separately
authorized compatible test environment, not as a Windows-compatible DB test.

Preflight intentionally exits **2** and always reports `ready_to_trade: false`.
It reads local source availability, Python, free space and Windows clocks. It
never opens a network socket, imports broker code, logs in, loads credentials,
starts a worker, changes settings, installs software or creates a scheduled task.
Do not put this preflight in a restart loop or Task Scheduler: it is not a service.
It reports source presence, not source integrity; verify the archive separately.

## Limits that must not be hidden

This is **not an installed Windows service or deployment**. The new process
supervisor composes an isolated reconciliation-only simulation Worker, not a
production Worker host.
`SimulationRuntime` is a callable in-process loop, not a process launcher. Its
optional guard integrates with strict Worker/PolicyAdmissionGate and the bound
PostgresStore's final admission path. An unguarded existing Worker is unchanged;
all production/legacy entrypoints are still unmodified and are not fenced here.

The original incident journal remains observation-only. The separate runtime
state implements a same-file cooperative simulation fence held through the
simulated effect, not a distributed transaction with the core or command journal.
No claim is made that the real execution engine is halted. Actual PostgreSQL,
Windows crashes, hostile file edits/restores and a legacy participant remain
acceptance gates. Production admission must enforce one authoritative cross-engine
ownership boundary in every execution entrypoint before any live transport exists.

`Evidence` must be supplied by trusted read adapters, not by browser JSON. The
package consumes database/broker availability observations; it does not itself
connect to KIS or test the internet. Network-adapter or generic ping success is
not proof that broker authentication, API, DB or the required account is healthy.
A future adapter must keep those checks separate, with fixed verified endpoints,
TLS validation, bounded read-only timeouts, no credential logging and no order POST.

A sleeping/offline PC cannot send an alert. Detection here occurs **after wake**;
external heartbeat monitoring is needed to notice an outage while it happens.
No external monitor, notification channel or paid service was created.

The SQLite latch is an operational prototype, not a tamper-proof security log.
It neither verifies Windows ACLs nor survives hostile edits/restores. Never use a
cloud-sync or network-share folder for active journals. Multi-process durability,
Windows power-loss behavior, disk-full failure and real file permissions remain
acceptance gates. Do not delete or reset a journal to make a screen look healthy.
