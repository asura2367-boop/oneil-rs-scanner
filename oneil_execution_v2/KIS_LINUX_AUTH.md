# Linux credential input foundation: inactive source only

`LinuxCredentialInput` is a Linux-native **read-only app-credential input**. It is
not a credential store, token provider, installer, consent UI or broker-connected
runtime. Its import and constructor perform no I/O. No dashboard, worker, runtime
factory or preparation command imports or activates it. No actual credential,
account access, token issuance, persistent access or transmission was used to
implement or test this source.

The Windows Credential Manager implementation remains Windows-gated. Its
`AppCredentials` value type has moved to the inert `kis_app_credentials` module;
the old Windows import is retained as the same class for compatibility. The original token
issuer uses that shared type and retains its Windows gate, as does the original
authenticated HTTPS client. Separate explicit Linux implementations now exist in
[KIS_LINUX_READS.md](KIS_LINUX_READS.md); this input alone neither starts them nor
changes the original Windows gates or enables orders.

## Explicit descriptor boundary

The future trusted Linux host must obtain separately authorized secret entry and
provide an already-populated, sealed anonymous memory descriptor. This reader
does not create, populate, discover, inherit automatically, cache or persist one.
There is no path argument, environment variable, plaintext file, CLI secret,
standard-input reader, browser-state fallback, keyring lookup, account discovery,
service setup or credential migration. Do not put real secrets into shell
arguments, command history, source, chat, test fixtures or a repository.

Each read requires all of the following:

- An explicit integer descriptor above standard input/output/error, retained
  open and stable by its trusted owner until the read returns.
- A valid exact account fingerprint, CANO, product and environment binding.
- An unexpired, unrevoked `LocalPermission` for `read_app_credentials` at that
  exact scope. Read permission never grants storage, token issuance or GETs.
  `LocalPermission` is not proof of actual human consent or a security boundary
  against code already running in the process.
- Linux, a regular unlinked object owned by the current effective user, and the
  immutable `F_SEAL_WRITE`, `F_SEAL_GROW`, `F_SEAL_SHRINK`, `F_SEAL_SEAL` set.
  Seals are checked through the kernel. Python builds without exported seal
  names use the stable Linux UAPI values; there is no unsealed fallback.
- A total size from 54 through 2086 bytes and a strict versioned envelope. Its
  SHA-256 binding digest must match the requested scope before app values return.
- Valid cancellation and a deadline, with at most 120 seconds remaining.
  The default reader uses suspend-inclusive Linux `CLOCK_BOOTTIME`; construct
  its `deadline_monotonic` value from `linux_credential_elapsed()`, not
  `time.monotonic()` (which excludes Linux suspension). An explicitly injected
  clock and its deadline must use the same domain. No fallback clock is used.
  Deadline, clock rollback, permission expiry/revocation and cancellation
  are rechecked before, during and after the native read and before return.
  Any failed check is terminal for that operation even if a dependency recovers.

The intended source is Linux `memfd`. A close-on-exec duplicate pins the input
synchronously before the cancellable worker starts. Only this owned duplicate
reaches the worker, so closing or reusing the original after a failed read returns
cannot redirect a late worker to another object. The duplicate closes before a
successful return, or on worker cleanup after an abandoned read. The original
descriptor and its offset remain owned and unchanged by the caller. The read
uses a bounded position-independent mutable buffer. Partial or malformed reads,
unsupported sealing, foreign ownership, ordinary files, pipes and mismatched
bindings fail closed. There is no write, retry, token acquisition or networking
method on this class.

The binary envelope is `ONL1`, followed by the 32-byte SHA-256 of the ASCII
NUL-joined binding fields in this order: account fingerprint, environment, CANO,
product code. A two-byte unsigned big-endian app-key length follows, then the
ASCII app key and app secret. Each value is 8 through 1024 characters drawn from
`A-Z a-z 0-9 . _ ~ + / = -`. The digest is a local scope-misrouting guard, not
broker-side identity proof or cryptographic attestation. No production encoder,
credential provisioning workflow or real account fixture is supplied.

## Resource and disclosure limits

Only one native operation may run per reader. After the initial pin, a waiting
caller can cancel or time out without waiting for stalled worker I/O. Initial
descriptor duplication is synchronous and cannot be hard-interrupted. The outstanding worker retains
the single-flight slot until it finishes, then wipes any abandoned result. The
reader does not retry or launch replacement workers for a stuck input. This is
not a process-wide concurrency control or a hard syscall-interruption guarantee.
KeyboardInterrupt/SystemExit during the wait or thread startup abandon work and
clean up ownership while preserving the caller's control-flow exception.
Only fixed sanitized error codes escape; no descriptor path, account, payload or
arbitrary dependency exception is logged or returned.

Read buffers are wiped on success and failure. `AppCredentials` has a redacted
representation, no dictionary/dataclass serialization surface and rejects
pickle. Python strings, interpreter copies, traceback-local capture and crash
dumps cannot be reliably zeroized. Sealing is **not encryption**. Memory-backed
pages can be swapped or dumped, and the original input remains until its owner
closes all references. This does not protect against same-user processes, root,
debuggers or a compromised host. No memory-lock, core-dump, swap, permissions or
other host security setting is changed by this source.

## Verification and remaining gates

The tests use synthetic values and actual temporary Linux sealed descriptors,
plus controlled fault doubles. They cover boundary sizes, all required seals,
scope mismatch, foreign ownership, no file/pipe fallback, malformed data, original
offset preservation, descriptor cleanup, redaction, cancellation/deadline/revoke,
late-result wiping, suspend-inclusive default deadlines, single-flight and
unchanged Linux network blocks. Fresh-process import checks confirm that the
Oracle/dashboard modules never load credential/token/network implementations. No actual
secret or broker endpoint is accessed. Native Linux descriptor checks require
Linux and are not a Windows native-acceptance test.

Before any operational credential integration, still needed: trusted secret
entry and actual user authorization, reviewed provisioning and lifecycle rules,
actual Oracle kernel/Python/identity acceptance, explicit swap/dump/memory threat
model, real Linux token/HTTPS acceptance beyond the offline implementation,
broker response/provenance acceptance, machine-wide identity/exclusion, and separately authorized deployment.
The offline runtime remains nonpermitting and cannot submit live orders.
