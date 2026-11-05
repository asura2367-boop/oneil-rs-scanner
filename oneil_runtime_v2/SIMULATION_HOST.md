# One-process SQLite simulation dashboard host

This is a real application composition using durable local SQLite and an actual
supervised Python process. It is not a PostgreSQL mock, production account adapter,
network listener, login service, installed Windows service or live trading path.
All account access is restricted to the fixed synthetic supervisor account. Only
the exact SimulatedBroker type is used; production identity stays unavailable.

## Explicit initialization and process ownership

`initialize_simulation_host(directory, trading_policy, origin=..., cash_krw=...,
positions=...)` creates a new private directory/profile. The TradingPolicy must
explicitly name the fixed simulation account. Initial simulation cash/holdings
and exact HTTPS origin are required; nothing is inferred from live settings or
credentials. Existing directories/journals are never overwritten or migrated.
The normal reconciliation-only profile is unchanged.

The profile contains separate supervisor, runtime, command/session, simulation
queue and simulator-checkpoint journals. The normal fixed worker child recognizes
only this explicitly initialized profile and constructs `SupervisedSimulationHost`:

Supervisor -> one child instance lock -> SimulationRuntime + strict Worker +
SQLiteSimulationStore + SimulatedBroker + ControlAPI/PolicyCoordinator + dashboard.

The child verifies its durable STARTING/RUNNING launch receipt and exact lease.
The same owning thread handles worker ticks and WSGI requests. No background
request thread shares a SQLite/core connection. Requests cannot choose an account,
process, runtime epoch, broker class or database. The supervisor alone records
actual child exit and releases ownership; host close only quiesces and revokes.

An authorized embedding host can use the WSGI callable and same-thread `run`
dispatch callback, or the worker child's explicit in-process setup/dispatch hooks.
There is no CLI module loader, credential file, environment-token fallback, request
mailbox, proxy or IPC substitute for denied network access. The default supervisor
starts no listener and supplies no identity. Its host remains inaccessible to a
browser and cannot receive commands until a permitted HTTPS embedding host and
simulation identity issuer are provided. This is a real, tested composition but
not an installed/reachable dashboard. No socket restriction was bypassed.

## Session injection and simulation-only controls

No session exists by default. A trusted in-process issuer calls
`inject_simulation_session(SessionRecord(...), csrf_token)` using an exact fixed
account, `simulation:` principal, validated short lifetime, independently generated
session/CSRF values and their matching digests. The raw session cookie is not
provided to or persisted by this method. The issuer must separately deliver it as
`__Host-oneil_session; Secure; HttpOnly; SameSite=Strict; Path=/` through its reviewed
HTTPS flow. Do not place tokens in URLs, command lines, environment or disk files.

Only digests are stored in the private command journal. Matching CSRF bootstrap is
kept in memory and returned only after authentication in the same process. All
old sessions are revoked when a new host attaches and on orderly close. Revoked,
expired, absent, other-account and non-simulation identities fail closed. Reads
recheck identity at completion. Read-only injection does not enable commands.
This mechanism authenticates a local simulation session only; it does not verify
any production owner or financial authority.

The dashboard has a permanent SIMULATION ONLY label and no editable order/policy
form. It lists server-owned proposal references and displays the complete returned
exact preview. Confirmation is a separate click. CSRF, exact origin/host, nonce,
request timestamp, session-bound confirmation and idempotency requirements remain.
Delayed previews cannot resurrect a dismissed/backgrounded confirmation. Failures
clear old controls; no request or unknown outcome is automatically retried.

The explicit lifecycle is:

1. Start halted, discard source registrations, reconcile the actual local journals
   and explicit simulator history. No prior activation is restored.
2. Finish exact pending-intent reconstruction using newly supplied current context,
   or explicitly cancel a pending zero-attempt request through authenticated control.
   A pending request does not prevent the host from opening for these operations.
3. Preview and confirm simulation core resume at its exact control revision.
4. Separately preview and confirm `activate_runtime`, bound to the exact runtime
   epoch/lease digest and current core revision. No order confirmation implicitly
   activates runtime. No endpoint activates live operation.
5. A trusted host/provider can call the coordinator's pure proposer with explicit
   current simulation policy/account/market/calendar evidence. The user previews
   and confirms that exact server-owned proposal. The same process archives intent,
   enqueues and runs the strict Worker using the actual SQLite admission ledger.

A stop request prevents further command dispatch and quiesces the same runtime.
Halt blocks new admission; it is not cancellation of an already simulated attempt.
Queue acceptance is not a fill. The existing same-day risk, ownership, evidence,
exact economics, runtime-health and unknown-outcome gates remain mandatory.

## Durability and uncertain outcomes

SQLiteSimulationStore uses a real OS account lock separate from SQLite write
transactions. The strict typed admission gate and bound runtime guard run under
a short durable transaction. SUBMISSION_UNKNOWN and attempts=1 commit before the
simulator is invoked. Independent-connection tests observe that committed state
inside the pre-submit hook. The original pending order/approval cannot be rewritten
or retried after admission. Manual intent keys retain their exact policy contract.

Original intent, runtime lifecycle, command receipts, simulation queue and simulator
checkpoints are separate journals, not an atomic distributed transaction. Any
uncertain dispatch, receipt, dependency or checkpoint write poisons/halts rather
than clearing UNKNOWN or generating a replacement key. The simulator checkpoint
records actual in-process simulated specs, orders, balances and fill watermarks.
A crash leaving a committed attempt without matching checkpoint evidence makes
history incomplete; accepted history is never invented from the core receipt.
Restart remains halted. No absence-of-history resend or financial recovery claim
is made. Restoring mismatched files requires explicit review, not a reset button.

## Verification and remaining boundaries

Tests use actual SQLite transactions, OS lock contention, real strict runtime/gate/
Worker and exact simulator. A real OfflineSupervisor spawns a Python child that
injects explicit public test sessions, runs same-process WSGI resume/activation/order
confirmation, commits one simulated attempt, checkpoints it and stops through the
supervisor. Socket creation is denied in that test. These are synthetic market and
identity inputs to real application/storage/process code, not real broker proof.
Node tests cover dashboard dismissal/background/auth races; no real browser/TLS
login is claimed. Native Windows clocks/ACLs/sleep/kill/power-loss, actual permitted
HTTPS hosting and issuer/session delivery, real PostgreSQL, authenticated KIS
policy authority, production legacy fencing and live activation remain open.
No network, credential, production account, install, deployment or real order was
used in this work. See the current aggregate in deploy/windows/VALIDATION.md.
