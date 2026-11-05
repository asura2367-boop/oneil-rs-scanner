# Draft offline rebuild v2

This branch preserves an isolated, simulation-only rebuild for review. It is not a release, deployment, activation, account migration, or authorization to place orders.

## Preserved source

The 116 rebuild source, test, and documentation files are byte-identical to local source commit `73d069397328093849a29ee5576f7d15d4938012`, including `4f3fa28` and `e5c4c24`. The update preserves the earlier `b2661f0` rebuild and adds a supervised SQLite simulation host, dashboard/control lifecycle, policy coordinator, strict supervised Worker composition, inactive credential-free HTTPS GET client, bounded read-only evidence pipeline, and fail-closed public KIS schema profiles.

The source commit also fixes the required package inventory to include `KIS_PUBLIC_SPEC.md`. Public-source schema conflicts remain explicit blockers unless the required verified profile is supplied; this draft does not guess live broker fields.

The branch remains based on repository main at `880b48859aa351a1a20202cbe8fd25c1a61d28d3`. No existing production code, workflow, dashboard state, account data, or live settings were changed. Existing legacy files remain only as inherited repository context.

## Verification performed on the preserved source

- Network-free aggregate: 757 Python tests and 33 Node tests passed, zero failures and zero skips.
- An independent review reran the same aggregate and checked the inventory fix and validation record.
- New source files checked for common credential patterns and private-data artifacts. Account/session fixtures and broker-like responses are synthetic.
- All 89 inherited GitHub Actions workflow definitions are unchanged from the previously inspected draft. This update uses a `[skip ci]` commit and no pull request was opened, so publication does not request legacy workflow execution.
- Tests include real isolated SQLite and a Linux simulation subprocess. DNS/TCP/TLS operations in the HTTPS tests are replaced by offline doubles.

## Open acceptance gates

Real PostgreSQL durability/isolation, native Windows and Task Scheduler behavior, reboot/power-loss testing, trusted evidence acquisition, authenticated broker responses, production HTTPS/session issuance, production database roles, and actual legacy/new-engine account exclusion remain unverified or unimplemented. The source's detailed validation documents distinguish simulation tests from those gates.

The concrete HTTPS GET client is inactive, has no credentials or live runtime registration, and does not support order submission. No real KIS request, broker authentication, financial action, deployment, or installation was performed for publication.

Do not run legacy trading workflows from this branch, merge it, deploy it, or connect production credentials as part of this review. Live order transport is not supplied by this rebuild.

Start with [the simulation host](oneil_runtime_v2/SIMULATION_HOST.md), [the integration guide](deploy/windows/INTEGRATION.md), [public KIS schema limitations](oneil_execution_v2/KIS_PUBLIC_SPEC.md), and [the validation record](deploy/windows/VALIDATION.md).
