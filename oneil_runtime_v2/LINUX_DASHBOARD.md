# Linux local simulation dashboard foundation

This is a separate Linux-native entry point for the existing simulation console,
not a live-trading port, cloud deployment, remote-access setup or service. It
requires Python 3.11+, an unprivileged Linux user, and explicit private local
inputs. It has no new dependencies. The Windows entry point and checks remain
Windows-only; no platform flag is overridden to bypass them.

## Explicit commands

For a future authorized operator on the target host, use the isolated wrapper:

```sh
python3.11 -I -B deploy/oracle/run_dashboard.py init \
  --state-dir /absolute/private-parent/new-simulation \
  --policy-json /absolute/private-parent/synthetic-policy.json \
  --positions-json /absolute/private-parent/synthetic-positions.json \
  --cash-krw 0 --port 9443

python3.11 -I -B deploy/oracle/run_dashboard.py check \
  --state-dir /absolute/private-parent/new-simulation \
  --cert /absolute/private-parent/loopback-cert.pem \
  --key /absolute/private-parent/loopback-key.pem --port 9443
```

These are placeholders, not existing paths. Inputs must be deliberately prepared
synthetic simulation data using the `TradingPolicy` shape and fixed synthetic
`supervisor.ACCOUNT` documented in [the console guide](LOCAL_DASHBOARD.md). No
existing account settings or balance is loaded. Initialization never overwrites
an existing directory. A partial initialization stays inert and must not be
reused. The command does not create users, install packages or repair permissions.

`run-dashboard` takes the same arguments as `check`. It additionally requires a
private terminal and explicitly starts a single-threaded HTTPS listener on
**127.0.0.1 only**. It cannot take a public address or proxy settings. Do not run
it as root, daemonize it, forward it to an untrusted network, or put its pairing
challenge in a transcript or log. No service unit, timer, autostart, remote browser
or tunnel is provided. Check never opens a listener or loads the TLS key.

Certificates and unencrypted TLS keys must already be provided and trusted for
the literal loopback IP. Neither command creates credentials, installs trust,
bypasses warnings or validates browser trust. Authentication is the existing
one-use 120-second local-console pairing and 15-minute Secure/HttpOnly session,
with exact origin and CSRF checks. This is only dashboard simulation
identity, **not KIS authentication**. The separate
[Linux sealed credential input](../oneil_execution_v2/KIS_LINUX_AUTH.md) remains
inactive and is never imported or invoked by this dashboard. Linux token issuance
and authenticated HTTPS remain blocked; no credential-to-runtime path is supplied.

## Linux-specific boundaries

- Runtime state, JSON inputs, certificate and TLS key paths must be absolute,
  canonical and privately owned by the current non-root user. Files may have only
  owner read/write bits, must be regular and single-linked; directories may have
  only owner permissions. New state uses a restrictive `077` creation mask,
  retained while running so SQLite journals are private. Existing permissions
  are not changed. Each input JSON is limited to 64 KiB.
- Every ancestor must be a directory owned by this user or root, without group or
  other write permission or special mode bits. Symlinks, special files, hard
  links, extended POSIX access/default ACLs and inspection errors fail closed.
  `/tmp` is intentionally not an eligible profile parent even with its sticky bit.
  SELinux labels are left alone; a denial is not a reason to disable SELinux.
- Mountinfo must identify one unambiguous writable ext4 or XFS filesystem, with
  root `/`. Network, FUSE, overlay, tmpfs, read-only, subtree and unknown
  filesystems are unsupported. A bind mount of an entire filesystem can also
  report root `/`; this check does not establish bind-mount or block-device
  provenance. Matching a filesystem type is not a durability proof. Snapshot
  preflight does not defend against a hostile owner/root, later permission/mount changes, or all path TOCTOU races.
- Native `CLOCK_BOOTTIME` includes suspend while `CLOCK_MONOTONIC` excludes it.
  Sampling pauses over 250 ms and reversed/inconsistent samples are rejected.
  Existing health checks halt on suspend, time disagreement and long loop gaps.
  Pairing expiry and HTTP total deadlines also use suspend-inclusive BOOTTIME,
  so suspended time cannot extend either window. This does not verify NTP,
  actual host suspend/resume or power-loss behavior.
- The shared lifecycle retains the exclusive supervisor and worker locks,
  halted startup, session revocation on restart and explicit simulation control
  confirmations. A clean console interrupt stops it. A later explicit launch can
  restart only READY or cleanly STOPPED state. FAILED/uncertain state cannot
  auto-recover. SIGTERM/kill/power loss is not promised to be a clean stop and can
  leave the profile blocked for operator inspection. Never clear state to bypass
  an unknown outcome.

Command summaries still say `live_capable: false` and `ready_to_trade: false`.
A launch never resumes or activates simulation automatically, and no real order
transport, broker identity, secret retrieval or external market data is added.

## Verification and remaining gates

On unprivileged Linux, the offline suite exercises actual clocks, ordinary POSIX metadata/xattrs,
private synthetic file creation and SQLite initialization/start/clean-stop/restart
and failure paths. Mountinfo parsing, accepted filesystem selection and TLS/socket
handling use test doubles. A socket guard prevents the new tests from listening
or connecting. On non-Linux or root-run test hosts, native tests assert that
the Linux entry point rejects the host, rather than claiming native acceptance.
Existing protocol, pairing, control and browser-model tests cover
the shared code; no UI design or JavaScript behavior was changed.

The development container uses overlay storage, which this launcher correctly
rejects. A successful real ext4/XFS target preflight is therefore **not claimed**.
No real TLS handshake, trusted browser flow, Oracle VM, certificate/key, broker
connection, credential store, account or real order was exercised. Sustained
memory use on the 1 GB Oracle VM, filesystem durability, reboot/suspend/recovery,
production identity, legacy-system exclusion and host access still need separate
acceptance. The earlier approximately 16 MiB smoke measurement is not a dashboard
or scanner memory budget. Green tests establish a source foundation only.
