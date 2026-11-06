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

## Oracle Linux offline preparation

Built on verified remote `100be3887bc9a300abf5785684cf1f65213876a2`. The
[Oracle preparation kit](deploy/oracle/README.md) groups host checks and one
explicit temporary SQLite rehearsal into a single command for a future authorized
operator session. No server access, upload, installation or deployment occurred.
The read-only checker reports Oracle Linux 9, Python, SQLite, Korea timezone data,
host available memory and temporary disk space. The fixed smoke child has a
256 MiB address-space limit, a 30-second CPU cap and a 45-second parent deadline.
It never resumes or activates anything, opens a listener or calls a broker.
Actual Linux smoke checks verified halted state before/after, zero simulated
order calls, lock exclusion, journal reopening and temporary-state cleanup.
Measured peak RSS was about 16 MiB for this check, not an operating-service budget.

At this preparation baseline, the actual dashboard launcher, storage validation
and credential integration remained Windows-specific. The Linux simulation
foundation below now adds a separate launcher/storage/clock path. The inactive
Linux credential input described below remains disconnected from that launcher. This kit is not a Linux trading port,
installer or Oracle VM acceptance result. It downloads no dependencies and changes no existing
host settings. An independent review's temporary-storage error finding was fixed
and regression-tested so failures remain sanitized and fail closed.

## Linux simulation dashboard foundation

Built on verified Oracle preparation commit
`3e248754ca51cd18b640f835793a3ea5db24a4fe`. A separate explicit Linux entry point
adds private POSIX ownership/mode/ACL and ext4/XFS mount inspection, native
suspend-aware clocks, restrictive creation of synthetic state and the shared
simulation-only loopback HTTPS/pairing lifecycle. Existing Windows gates remain
intact. See [Linux dashboard boundaries](oneil_runtime_v2/LINUX_DASHBOARD.md).

No Oracle server, secrets, TLS keys, broker or real account were accessed. No
listener, installation, service, network exposure or real trading was activated.
Mount acceptance and TLS use offline doubles; the actual development overlay
filesystem is rejected. Independent review found and fixed Linux suspend
extending pairing and request deadlines; both now use BOOTTIME with regression
tests. Native Oracle/ext4/XFS, trusted browser/TLS, resource capacity,
suspend/power-loss and broker authentication remain open gates.

## Combined Linux source-only draft

This integration starts from published Linux dashboard commit
`d9586a632f42ee87b49a9885c4b606711e64539c` and incorporates credential source
commits `4d2cb24cabfbfd6d29d8268712513d247332206f` and
`6974e049bf64c0b5fcb99e1c6ecdb77ca6ceae25`. The required bundle inventory and
aggregate now include both foundations together, with no workflow changes.

The [Linux credential input](oneil_execution_v2/KIS_LINUX_AUTH.md) reads only an
explicitly supplied, current-user-owned, unlinked sealed descriptor after an
exact-scope ephemeral permission check. Native synthetic memfd tests cover
seals, bounded parsing, original-offset preservation, cancellation, revocation,
late-result wiping, descriptor ownership and cleanup. The default credential
deadline now uses suspend-inclusive BOOTTIME, with no fallback to awake time. The shared app-pair type
retains the Windows compatibility alias and redaction/serialization guards.
It supplies no writer, secret-entry UI, persistence, token or network method.

Neither the Linux dashboard nor Oracle preparation imports or activates this
credential input. The original Windows token issuer and authenticated HTTPS retain their
platform gates. Separate inactive Linux implementations are described below. No real credential, account, token, persistent access,
server, listener, workflow or deployment was used. Sealing is not encryption;
trusted provisioning, swap/dump policy, native target acceptance and all real
broker/trading acceptance gates remain unresolved.

## Inactive Linux broker-read foundation

Built on `884d1e7a084242a318be9421c54b39836d7dc4e7`, this source adds a separate
[Linux authenticated-read composition](oneil_execution_v2/KIS_LINUX_READS.md).
Only explicitly supplied sealed input and separately granted read/token/GET
permissions can reach its operation entrypoints. The token grant is consumed once;
no automatic renewal permission, persistence, generic HTTP or order method exists.
All default Linux deadlines and permission clocks are suspend-inclusive BOOTTIME.
The seven canonical GET recipes, strict framing/TLS controls and nonpermitting
capture contract are retained. Original Windows entrypoints remain Windows-gated.

No dashboard, Oracle command, runtime or worker imports or starts this source.
Tests use only synthetic memfd input and fake DNS/TCP/TLS. No real credentials,
account, token, broker request, server, listener, service, deployment or live
trading activation occurred. Broker tokens are not broker-enforced read-only;
actual user consent, trusted provisioning, real broker acceptance and all existing
production gates remain open. This source publication does not meet those gates.

## Verification

- Exact network-free aggregate: **1121 Python and 69 Node tests passed**, zero
  failures or skips. The package requires all 159 public rebuild files.
- Independent Linux-read review found and fixed native-test selection on non-Linux
  platforms. The 75-test Linux suite and three portable refusal tests pass;
  actual Windows execution is not claimed.
- Independent review checked diagnostic publication, stale/cancel/deadline
  handling, host projection and packaging. Browser review covers delayed reads,
  old authentication failures, request/body timeouts, hidden/restored pages,
  command completion after expiry and malformed confirmation expiry. All checks
  use offline doubles; native browser behavior is not claimed as tested.
- Tests include actual isolated SQLite, OS locks and Linux subprocess simulation.
  Actual Linux sealed descriptors use synthetic app values. DNS/TCP/TLS,
  Windows APIs, broker responses and browser DOM are mocked.
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
