# Explicit local simulation console

`python -m oneil_runtime_v2.local_dashboard` adds explicit `init`, `check`, and
`run-dashboard` commands. The existing supervisor and default worker remain
listener-free. This entry point is Windows-only, simulation-only, single-process,
and single-threaded. It cannot select a production account, broker, arbitrary
module, or remote address. Nothing is installed or automatically started.

## Inputs and lifecycle

Initialize a **new** state directory using `init --state-dir PATH --policy-json
PATH --positions-json PATH --cash-krw INTEGER --port INTEGER`. Policy JSON is the
exact `TradingPolicy` dataclass shape, including nested `RiskPolicy` and ISO
`source_observed_at`, explicitly using the fixed synthetic `supervisor.ACCOUNT`.
Positions JSON is the existing explicit simulation holding map (or `{}`). It is
not a live settings import. No defaults are inferred and existing state is never
overwritten. Policy references are simulation provenance, not financial authority.

`check --state-dir PATH --cert PATH --key PATH --port INTEGER` inspects local
storage and Windows clock availability without starting a listener. It does not
load the TLS key or certify certificate identity, trust, browser reachability,
production readiness, or native Windows behavior. Successful output still says
`live_capable: false` and `ready_to_trade: false`.

`run-dashboard` accepts the same explicit arguments. It also requires a terminal,
checks the exact configured `https://127.0.0.1:PORT` origin, obtains exclusive
supervisor/worker locks, and starts a fresh lease halted. Only READY or cleanly
STOPPED profiles may start; STARTING/RUNNING/FAILED/uncertain profiles fail closed.
A deliberate later `run-dashboard` is the clean-stop restart action, never an
automatic restart or recovery. Keyboard interruption requests cooperative stop.
Startup/bind/tick failures record exit code 2 and FAILED, not a clean stop.

Certificates and unencrypted private keys must be supplied locally by the user.
The host never generates, prints, installs, trusts, exports, or persists TLS keys,
and never prompts for a key password. Supply a certificate already valid and
trusted for the literal IP address; do not bypass a browser security warning.
TLS load/bind failures close the server and fail closed. Only TLS >= 1.2, literal
IPv4 127.0.0.1, and explicit unprivileged ports 1024–65535 are allowed.

## Pairing and sessions

The private console prints one fresh 256-bit pairing challenge, valid for 120
seconds, once per launch. This is the only intentionally displayed bootstrap
secret, never a TLS key or session token. Enter it at `/pair.html`. Never copy it
to a URL, script argument, log, remote session, or shared transcript. Five failed
attempts exhaust pairing. An exact same-origin JSON POST consumes the challenge
before session installation. No automatic retries or replacement challenges.

The resulting cookie is `__Host-oneil_session; Secure; HttpOnly; SameSite=Strict;
Path=/; Max-Age=900`. The session lasts 15 minutes and permits only local simulated
monitor/control scopes. Only digests enter the simulation command journal; CSRF
bootstrap lives in process memory. `/logout.html` performs authenticated,
CSRF-protected revocation. Restart and host shutdown revoke all sessions. Pairing
does not resume the core, activate the runtime, authorize real trading, or create
market/policy proposals. Existing exact-preview/confirmation gates still apply.

## Browser snapshot recovery

The console is a manually refreshed snapshot, not a live feed. Every refresh
clears the previous lifecycle status, diagnostics, proposals and confirmation
before reading. A snapshot is usable for at most 15 seconds measured from the
start of the refresh on both browser wall and monotonic clocks. A current KIS
diagnostic may shorten that lifetime to its remaining server-projected validity;
missing, nonfinite or invalid diagnostic validity fails closed. This diagnostic
expiry is only a capture-age limit, not proof of broker as-of freshness.

Expiry, clock reversal/disagreement, hiding/leaving the page and back-forward
cache restoration clear both status displays and disable simulation actions.
The clock/age check also runs inside every action, so a suspended browser timer
cannot authorize an old click. Returning from a hidden page attempts a new read
only if no operation remains in flight; otherwise refresh explicitly. Expired or
interrupted previews and delayed command responses cannot restore current state.

Each HTTP operation has a five-second timeout including JSON decoding. Elapsed
wall/monotonic time is also checked after each response/body wait so a delayed
timer cannot accept an overdue result. It aborts
the request and releases the UI wait without retrying. Abort is not a rollback:
a POST may have completed on the host, and an unknown result requires inspecting
durable state rather than retrying with a new key. Superseded/late responses cannot
overwrite a newer snapshot, including late authentication failures. Exception and
server error text are not displayed. Browser timers are not hard-real-time and
cannot execute while the browser is frozen; synchronous action checks run again
when script execution resumes. Real Windows/browser sleep and restoration still
require separate acceptance tests.

## Transport and storage boundaries

The single-thread adapter owns HTTP framing: duplicate headers, absolute targets,
transfer encoding, content encoding, upgrades, overlong headers, oversized and
truncated bodies are rejected. Headers are bounded to 16 KiB, JSON bodies to 8 KiB,
backlog to four, and each connection has a two-second total deadline covering TLS,
headers, body, and response. Deadline is checked after application dispatch; it
cannot preempt synchronous SQLite/application work. SQLite busy timeouts and the
runtime health gates remain separate. There is no keep-alive, proxy trust,
request thread, access logging, or exception dump. A stop observed immediately
before response transmission suppresses the response. Bytes already handed to
the OS cannot be recalled if another process requests stop afterward.

Read-only Win32 inspection rejects reparse/remote/removable storage, missing/null
DACLs, unsupported granting ACEs, and grants on profile files or TLS inputs outside
the current user, SYSTEM, and Administrators. Ancestors may allow ordinary reads,
but not broad writes, deletion, child deletion, or ownership/DACL changes. Deny
ACEs are conservatively ignored rather than used to excuse broad grants. No ACL,
security setting, service, scheduled task, certificate trust, or credential store
is changed. This snapshot preflight is not a hostile same-user/admin boundary and
cannot eliminate filesystem TOCTOU. Native inspection must be validated on the
target Windows machine before operational use; no ACL repair shortcut exists.

## Verification status

`python -m unittest discover -s tests/runtime_v2 -p test_local_dashboard.py -v`
uses real isolated SQLite/control/runtime composition plus offline socket/TLS and
policy doubles. No listener, TLS handshake, certificate installation, native
Windows API, real broker, PostgreSQL socket, credential retrieval, live ledger,
account or real order was exercised. Native Windows DACL/fixed-volume behavior,
trusted certificate/browser flow, sleep/power-loss behavior and operational HTTPS
remain unverified gates, not passed tests. This implementation is new source atop
the recovered baseline, not recovery of the earlier unpublished changes.
