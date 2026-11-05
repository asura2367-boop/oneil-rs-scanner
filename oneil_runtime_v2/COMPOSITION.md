# Supervised simulation profiles

The original reconciliation-only profile below is unchanged. A separately and
explicitly initialized **SQLite simulation control profile** now composes the real
strict Worker/runtime, durable simulation admission store, authenticated ControlAPI
and dashboard in the same supervised process. It has no default identity, listener
or live capability. See [SIMULATION_HOST.md](SIMULATION_HOST.md) for the exact
initialization, session injection, lifecycle, durability and hosting boundaries.

# Supervised reconciliation-only simulation composition

This adds an actual composition, not a mock store imported from tests:

`OfflineSupervisor -> worker_child -> SimulationRuntime -> strict Worker ->
RehearsalStore (isolated SQLite) + exact SimulatedBroker`.

**No production network, PostgreSQL connection, broker transport, credentials,
live order, simulated order submission, service install or automatic start.**
The source remains unverified on real Windows. The existing denial of a socket
operation was not retried, proxied or bypassed.

## What the process actually runs

`init` creates a new `rehearsal.sqlite` and `worker.lock` alongside the existing
supervisor/runtime journals. Existing folders are never overwritten or migrated.
Older observation-only state directories do not silently gain the new dependency;
missing files fail closed. Rehearsal state is separate from every real account.

The supervisor still creates the durable STARTING receipt and RuntimeLease before
launching one fixed isolated Python child. The child verifies the exact receipt,
account, engine, owner and epoch, takes its own lifetime OS-backed worker lock, and
attaches only to a fresh RECONCILING lease. A stale or previously observed lease
cannot be attached as though new. A durable stop present before the loop prevents
observations. A stop after a loop's check can allow that observation to finish.

The child builds actual `Worker`, `SimulationPolicySource`, `SimulatedBroker` and
`SimulationRuntime` instances. Fixture-only Worker mode remains false. SQLite
provides durable local control, incidents, heartbeat, request recovery and explicit
synthetic history. It is not a substitute PostgreSQL implementation or production
admission store. The local core is permanently halted with a database constraint;
there is no enqueue, claim, attempt reservation or resume implementation.

Each iteration reads the real local SQLite dependency and persisted synthetic
history, refreshes the exact simulator's history, and runs the strict Worker's
reconciliation path. Database/simulator health is therefore observed for these
**synthetic dependencies**, never implied for the real DB or KIS account. The
report explicitly labels the dependency `ISOLATED_SQLITE_RECONCILIATION_ONLY` and
keeps real broker connection and trade readiness false. `simulation_worker_composed`
means the journal has seen this composition, not that an OS process is currently
alive. The stored heartbeat is evidence with a timestamp, not proof of liveness.

First startup health is blocked; successful subsequent empty/history
reconciliation and empty intent reconstruction can reach AWAITING_OWNER. That name
is inherited from the runtime lifecycle, **not** an invitation to activate this
host: there is no activation UI/CLI and its core cannot resume. The child also
rejects ACTIVE_SIMULATION. No trading policy defaults, quotes, current contexts,
calendar, owner receipt or approval are fabricated for an order.

## Recovery and stop behavior

- SQLite is opened in existing-file mode, with integrity/schema checks. Corrupt,
  missing or wrong-mode state is never replaced by a clean journal.
- UNKNOWN with empty history stays UNKNOWN. ACKNOWLEDGED and partial orders stay
  unresolved until exact matching synthetic history proves a transition. History
  completeness, identity, monotonic cumulative fills and allowed transitions are
  checked through the real Worker and local store.
- Original attempted rows and cumulative fills are persisted. Nothing rebuilds a
  queue request, consumes another attempted slot or replays a submit. This path
  never submits an order, so simulator balance mutation is not part of the claim.
- Pending zero-attempt intent cannot be reconstructed from guessed current
  context. With no explicit context provider in this host, such pending rows block
  startup. Existing in-process runtime tests still cover separate exact-intent
  reconstruction; this CLI does not pretend to supply its missing evidence.
- Child stop/failure calls `quiesce`, which halts its core/runtime and retains its
  lease. Only the supervisor observes the exact process exit, records it, and
  decides restart/STOPPED. Child code never debits the restart budget or releases
  ownership itself. A killed supervisor still leaves uncertain child outcome
  blocked, with no PID-based recovery shortcut.
- Three bounded restarts, durable stop, explicit stopped restart and no restart
  budget reset keep their existing semantics. Separate SQLite files are not one
  transaction; a storage mismatch blocks rather than being guessed repaired.

## Explicit test-only synthetic scenario

`tests/runtime_v2/test_worker_composition.py` contains direct fixture seeding of
one preexisting synthetic UNKNOWN request and synthetic broker observations. It
walks PART_FILLED to FILLED through the actual Worker and durable SQLite adapter,
then verifies one original attempted slot and the persisted cumulative fill.
Other tests prove empty history across restart cannot retry an UNKNOWN, invalid
history cannot resolve it, and incomplete history remains blocked. The source
contains no CLI or store API for seeding an order. `SimulatedBroker.submit` is
patched to fail if these scenarios attempt any submission.

A separate integration test runs the real supervisor and a real Python child
against the actual SQLite files, reaches AWAITING_OWNER, persists a stop request
and verifies STOPPED with released ownership. It injects a synthetic clock from
the test subprocess. That is a Linux/process composition test, not a Windows
clock, PowerShell, reboot, power-loss, ACL or scheduling pass.

## Remaining gates

Real Windows execution/locking/kill/sleep/power-loss tests, real isolated PostgreSQL
transactions/concurrency, production identity/TLS, actual broker reads, complete
account/cash/fill evidence, current policy-context integration, authoritative
legacy/v2 fencing and explicit real-operation activation remain open. Passing the
new local composition tests does not mark these stages complete. No production
launch or real financial operation is enabled by this work.
