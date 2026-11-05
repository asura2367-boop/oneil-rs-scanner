# Offline server-owned policy command coordination

This is a socket-free simulation integration, not a deployment or a live trading
path. No account, credential, production provider, Windows service or database
connection is created by this package. `Worker` still accepts only the exact
`SimulatedBroker` type. No financial action was performed during verification.

## Supported composition

Use one explicitly initialized private `ControlState` journal, an existing isolated
`PostgresStore`, `CoreSimulationBackend(store)`, and a strict
`Worker(store, simulated_broker, policy_source=SimulationPolicySource())`. Bind them
with `SimulationPolicyCoordinator(state=state, backend=backend, worker=worker)` and
pass that same state/backend to `ControlAPI`. All objects using the core connection
must remain on its owning thread. A host must route dispatch to the worker whose
source it installs. Separate processes with independent in-memory policy sources
are not a supported dispatch topology; a missing registration fails closed.

A trusted host supplies `AuthenticatedOwner` and a complete explicit
`PolicyContext` to `coordinator.propose(...)`. The owner object identifies the
intended principal/account but does not approve an order. The coordinator runs the
pure planner and persists immutable original intent, including the full proposal,
original evidence, policy, mode and manual intent. It returns an opaque proposal ID.
For manual additions supply the trusted, exact `ManualAdd` with `mode='MANUAL_ADD'`.
There is no HTTP endpoint that accepts context, policy, ownership, manual IDs,
approval flags or provider evidence from the browser.

The HTTP flow is:

1. POST `/api/v2/previews` with exactly `action='policy_order'`,
   `expected_revision`, and the server-issued `proposal_id`.
2. Render all returned display fields, warning, simulation mode and expiry.
   Display includes exact ticker/side/venue/quantity/price/order expiry, policy
   version/mode, reason, fee-buffered cost and original-intent digest. A manual
   proposal also displays its exact holding expectation, quantity, maximum price,
   validity and reference. Nothing here is an order or an approval.
3. Only after the owner's explicit confirmation, POST `/api/v2/commands` with
   exactly `action='policy_order'`, `confirmation_id` and `content_digest`.
   Existing authenticated cookie, same-origin, CSRF, timestamp, nonce, request-body
   idempotency and session-bound one-use confirmation requirements still apply.
4. The coordinator acquires the same account advisory lock as Worker, then the
   account revision row lock. It revalidates the original/current policy and exact
   economics, rechecks live session revocation and confirmation expiry, enqueues
   the exact confirmed order, installs explicit simulator evidence and registers
   `PolicyAdmission` in the worker's current policy source before releasing locks.
5. Normal `Worker.tick` independently reconciles broker history and uses the typed
   strict gate at final admission. Queue acceptance is not broker ACK or a fill.

The core bridge now refuses the old raw `action='order'` path at both preview and
execution. Its generic API shape remains only for existing boundary test doubles;
it is not a supported core command path. Hosts must use the new proposal flow.

## Idempotency and crash boundaries

Automatic core keys derive from the full immutable intent digest. Manual keys are
exactly `policy-manual-<ManualAdd.request_id>`, as required by the strict gate.
Changing any original manual intent under the same account/manual ID is a conflict,
even if the resulting order economics are identical. Recreating an identical
proposal returns its existing ID, never a fresh execution key.

Command reservation atomically consumes both the session confirmation and a
proposal-wide durable dispatch tombstone in the same SQLite transaction as the
STARTED command. Multiple previews can exist, but only one can dispatch. A new HTTP
key, session, confirmation, process or coordinator cannot retry that proposal.
Completed identical retries return the original receipt without needing current
policy evidence. Unknown results remain unknown across restarts.

After enqueue begins, any enqueue, simulator installation, policy registration,
outer commit or lock-release error is reported as `COMMAND_OUTCOME_UNKNOWN` and
leaves STARTED intact. A process exit also leaves it intact. A subsequent command
journal completion failure has the same behavior. An unknown result may correspond
to a committed, explicitly approved queue row and must not be described as a safe
rejection. No retry, reset, deletion, evidence-based automatic resend or guessed
outcome recovery endpoint is added.

This policy deliberately consumes the proposal even for a known post-reservation
failure such as ACCOUNT_BUSY or changed controls. The same manual request ID cannot
simply be retried. A reviewed operator must establish the prior outcome before a
new manual intent and new exact owner confirmation are created. The coordinator
never creates that replacement itself.

The original intent and dispatch tombstones have no update/delete path. They must
be retained with the command journal. Restoring mismatched core/command journals or
losing a journal is not safe recovery. SQL immutability and SHA-256 are accidental
change guards, not signatures or protection from a hostile database/host owner.

## Current evidence and lifecycle limits

Current evidence is explicit in-memory provider input. `refresh(owner, proposal_id,
context)` checks a supplied current context against immutable original intent; it
cannot change original economics or extend the approved lifetime. Original archived
evidence is never loaded as current context after restart. Missing current context
blocks new previews/dispatch; current simulator balances are never reset by install.
The strict worker still independently checks history, positions, cash, fills,
calendar/capability, daily exposure and fresh evidence at final admission.

The coordinator supports either the standalone strict Worker or an explicitly
supplied, already-started `SimulationRuntime` for that exact worker/store/account.
Pass `runtime=runtime` after its `start()`. An implicit runtime guard, unstarted
runtime, different worker/store/state/lease or later guard replacement is refused.
Starting the runtime still halts the core and discards source registrations. The
host must finish explicit reconstruction and reconciliation, separately resume the
core as the authenticated simulated owner, and separately activate the runtime at
that exact control revision. Propose, preview, refresh and order confirmation do
not perform any of these activation steps and require an active runtime.

Runtime-aware operations hold the runtime ownership scope. Confirmation follows
runtime SQLite ownership -> shared core account advisory lock -> account row lock.
Under those locks it rechecks runtime activation/health, enqueues, writes immutable
original intent inside the held runtime transaction, and registers strict admission.
It resamples health, control revision, policy/order validity and owner-session/
confirmation validity after archive work. It never captures an obsolete policy
source when reconstruction replaces that source.

The core transaction commits before the runtime ownership transaction. These are
separate journals, not an atomic distributed commit. An exception after enqueue,
including runtime COMMIT, poisons the originally bound guard before independent
best-effort runtime/core halt writes. A scope-exit failure is poisoned even when
the body rejected before enqueue. A failed command-receipt write after successful
dispatch also poisons the guard. Failed cleanup never converts UNKNOWN to rejection
or clears its tombstones. An orphan runtime archive after core rollback grants no
approval and cannot recreate a queue row. A committed queue row missing its archive
cannot be permissively restored.

Runtime recovery still requires explicit current contexts and exact original
intent; it cannot restore activation, upgrade an old control revision or extend
owner approval. Cross-process kill, disk-loss and independently restored journals
remain acceptance gates. Supervisor/Worker hosting and production activation are
unchanged and are not supplied by this offline composition.

## Validation and remaining gates

`python -m unittest discover -s tests/control_v2 -v` exercises real ControlAPI,
ControlState SQLite, coordinator, bridge, SimulationAPI, planners, strict Worker,
PolicyAdmissionGate and SimulatedBroker, with an explicitly named non-durable core
SQL double. Tests cover automatic/manual success, lock ordering/contention,
owner/content tampering, no browser evidence injection, duplicate confirmations,
manual intent conflict, revisions, revocation/expiry after lock wait, registration
failure, uncertain commits, scripted process exit, restart receipts and durable
unknown fencing. These are in-process end-to-end rehearsals, not PostgreSQL proof.

The original standalone change passed 95 control tests, including 26 coordinator
flows. Runtime-aware tests additionally cover exact bindings, activation isolation,
lock/archive ordering, late health/session changes, uncertain core/runtime/command
commits, failed halt writes, stale ownership, process exit and explicit restore.
See `../deploy/windows/VALIDATION.md` for current aggregate counts and limits.
Actual PostgreSQL transaction/lock/concurrency and process-kill durability tests,
reviewed production evidence and identity providers, browser login/TLS/UI tests,
supervisor/Worker hosting, shared legacy fencing and user-controlled
live activation remain separate gates. No denied DB socket operation was retried
or worked around.

## Concrete supervised SQLite host

The exact `SQLiteSimulationStore` is now additionally supported by the core bridge.
`SupervisedSimulationHost` composes it with this coordinator, runtime and dashboard
on one owning thread and one verified supervisor lease. Activation can use a
separate authenticated `activate_runtime` preview/confirmation bound to the lease
digest; order confirmation never performs that activation. Default session/issuer
and network listener remain absent. See `../oneil_runtime_v2/SIMULATION_HOST.md`.
