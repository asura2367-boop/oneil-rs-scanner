# Oracle Linux 9, 1 GB: offline preparation only

This is a small preparation kit for a future Oracle Linux 9 x86_64 VM check. It
is **not an installer or trading service**. No Oracle VM has been accessed or
validated. A separate [Linux simulation dashboard foundation](../../oneil_runtime_v2/LINUX_DASHBOARD.md)
is now available in source with native Linux clock and private-storage checks.
The preparation command below still never starts it. The separate [Linux sealed credential input](../../oneil_execution_v2/KIS_LINUX_AUTH.md)
is inactive and disconnected from this kit and dashboard. Token issuance and
authenticated HTTPS remain Windows-gated; no platform check may be bypassed.

## Minimal future operator workflow

Once authorized server access and a verified source transfer are available, the
operator can run one grouped preparation command from the source directory:

```sh
python3.11 -I -B deploy/oracle/prepare_offline.py --rehearse
```

Python 3.11 or newer is required for this kit's rehearsal. Use the actual installed
interpreter name, such as `python3.12`, if different. The default checker also
parses on Python 3.9: `python3 -I -B deploy/oracle/prepare_offline.py` reports an
older interpreter as a blocker without attempting upgrades. A missing interpreter
must be handled separately using the OS vendor's supported packages. Do not
replace Oracle's system Python, compile Python on the 1 GB VM, install pip/Node/
PostgreSQL/Docker just for this check, or run an unreviewed curl-to-shell installer.
No package download or installation is performed by these scripts.

The command checks Linux, reports whether `/etc/os-release` identifies Oracle
Linux 9, checks Python, SQLite and Korea timezone data, and reports host available
memory and `/tmp` space. It needs at least 384 MiB host-available RAM and 128 MiB
free `/tmp` space before launching the small rehearsal. These are conservative
preparation thresholds, **not measured capacity for a scanner or always-on
service**. `/proc/meminfo` describes host memory and does not verify a container's
separate memory quota. An Oracle flag is detection only, never an acceptance pass.
The same smoke check may run on another Linux distribution for source validation.

Without `--rehearse`, the checker is read-only and does not start a process or
create test state. With it, a single sequential child:

- Uses isolated Python with no inherited credential/proxy/PYTHONPATH variables.
- Caps its address space at 256 MiB, CPU time at 30 seconds and individual file
  size at 4 MiB. The parent allows 45 seconds total. It does not change host limits,
  swap, users, firewall, permissions on existing files, or security settings.
- Creates only a private temporary synthetic SQLite journal, verifies lock
  exclusion, performs one simulation reconciliation-only tick and one halted tick,
  and reopens the journal to verify that it remains halted with no pending work.
- Never resumes the core, activates the runtime, creates an order, opens a socket,
  starts a dashboard/service/scheduler, loads credentials or calls a real broker.
  Socket/process audit events are rejected as an extra accidental-use guard;
  this is not a sandbox for malicious source or an OS network policy.
- Removes its temporary state on normal completion/failure and prints a small
  sanitized result. An OS kill/power loss can leave an inert temporary directory;
  it is never discovered, restored or auto-started on a later run.

`rehearsal.status = PASSED` means only this temporary halted SQLite smoke check
passed. `installed`, `deployed`, `live_capable`, and `ready_to_trade` remain false.
Exit code 0 covers only the requested preparation step, 2 means blocked/failed.
Detailed stdout/stderr from a failed child is deliberately not relayed. Review
source locally if diagnosis is needed; never paste keys or broker secrets.

## Transfer and verification boundary

Use the existing deterministic source-only bundle builder and verifier. The
historical `windows` tool names are retained, but the required allowlist now also
contains this preparation kit. The bundle has no dependencies or executable
installer. Verify its SHA-256 against a trusted message and its complete manifest
before any authorized transfer/execution. Hash integrity alone is not publisher
authenticity. Do not transfer the whole legacy repository, private state, `.env`,
SSH keys or production configuration. No upload, SSH connection, firewall change,
certificate trust change or automatic transfer is included here.

## Still required before any operational installation

- Confirm the actual Oracle host, resources, time sync and storage behavior.
- Accept the new Linux-native runtime/clock/private-storage/dashboard path on
  real ext4/XFS target storage and a trusted local HTTPS/browser flow. Source
  checks do not replace target acceptance; Windows-specific checks stay intact.
- Establish separately authorized access, authentication, trusted HTTPS and the
  intended access restrictions. Do not expose an unauthenticated public port.
- Validate crash/restart/backup semantics, resource use over time and coexistence
  with the legacy system. No old account/DB is migrated by this kit.
- Complete broker data provenance and production identity/exclusion work.
  Real credentials and all real trading remain outside this offline preparation.

This kit does not require the owner to type a sequence of phone SSH commands now.
It is prepared source for a future authorized operator session, not a workaround
for the current lack of server access.
