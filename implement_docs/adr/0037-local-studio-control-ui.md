# ADR-0037 — Local Studio control UI

- **Status:** Accepted; implemented 2026-09-28
- **Date:** 2026-09-27
- **Task:** P3-26 · **Arch:** §4.1 (ledger), §4.2 (campaign lifecycle), §5 (directory structure), §10 D22 · **Succeeds:** ADR-0029

## Context

ADR-0029 deliberately made `cli review` a read-only UI over the ledger. That remains the right boundary for reviewing campaign history, but it does not let a user safely configure a new campaign, prepare data, preview locks and quotas, create a campaign, run/resume search, or enqueue the research portfolio step from the browser.

The current write path is split across CLI adapters, `validation/run.py`, campaign locks, data carving, comparison protocol setup and portfolio commands. Several of those paths can implicitly open a campaign, depend on the active lock, or assume fixed dataset locations. A browser control surface would turn those assumptions into safety problems unless campaign creation, dataset identity, run ownership and job state are made explicit.

Studio now implements this contract through `cli studio`, with the remaining real-source perpetual preflight tracked by P3-24.

## Decision

1. **Keep review and control separate.** `quantcrucible.review` remains read-only and GET-only. A new `quantcrucible.studio` package owns mutating routes, local job state and Studio-specific security. `cli studio` may compose the review router for read screens, but `cli review` must not import or expose Studio write paths.
2. **Use one business path for CLI and UI.** Campaign creation, run/resume, comparison setup and portfolio evaluation move behind services that accept an explicit campaign, draft or dataset identity. CLI commands become adapters over those services. `run_campaign(id)` never creates a campaign implicitly.
3. **Introduce immutable dataset v1.** New campaigns use `data/datasets/<dataset_id>/` plus `holdout/datasets/<dataset_id>/`, with a manifest of canonical spec, relative paths, coverage and SHA256 hashes. The campaign lock records `dataset_id`, manifest hash, holdout lock hash and resolved data end. Legacy locks without `dataset_id` keep their existing resolver and are not rewritten.
4. **Make Create idempotent and recoverable.** Drafts are versioned and do not write config, locks or ledger. Preview reuses the same validator as Create but has no side effect. Create requires a preview token and request key, takes the project writer lock, rechecks inputs, writes the canonical lock, records the ledger request row and updates active compatibility files in a recovery-safe order.
5. **Store jobs outside the ledger.** Studio keeps `studio/jobs.sqlite` for queued/running/stopping/succeeded/failed/interrupted/unknown jobs, process identity, heartbeat, log path and idempotency key. The ledger remains the statistical record; job state is operational state. A single writer job may run per project root.
6. **Constrain the local browser boundary.** Studio binds to `127.0.0.1`, rejects foreign Host/Origin, requires a same-origin token/header on mutations, accepts only JSON allowlists, limits body size, uses fixed subprocess argv with `shell=False`, and never accepts executable code, arbitrary command, URL or filesystem path from the browser.
7. **Keep irreversible research actions out of Studio Run.** Studio does not automatically freeze, burn, open holdout or place live orders. `harness_test` locks its comparison protocol before trial 1; `research` may enqueue portfolio evaluation after search.

## Consequences

- The UI can become a real local operating surface without weakening ADR-0029's read-only guarantee.
- Dataset identity becomes part of the campaign contract. This is extra implementation work, but it prevents changing timeframe, market, date range or source bytes under an existing campaign.
- Job truth and research truth are intentionally separate. After restart, Studio must reconcile process identity, heartbeat and locks instead of inferring that an `OPEN` campaign means a job is still running.
- The crash surface around Create grows, so tests must cover pending lock, lock-only, ledger-only and active-complete recovery states, plus double-click/idempotency-key retries.
- The command, API, supervisor and UI flow were verified with 993 Python tests (excluding docker/network/slow), 28 component tests, 18 Playwright tests, lint/typecheck/build and the EN/VI document mirror on 2026-09-28.

## Alternatives considered

- **Add POST routes to `quantcrucible.review`.** Rejected because it would erase the ADR-0029 boundary and make the read model import write-layer concerns.
- **Let the browser edit `config/user.yaml` and call existing CLI code.** Rejected because drafts would have side effects and `run` could still create or target the wrong campaign through active-lock assumptions.
- **Use the ledger as the job queue.** Rejected because the ledger is the append-only statistical record, while job state includes process identity, heartbeat, stop/restart reconciliation and log paths.
- **Serve Studio beyond localhost.** Rejected for this phase; remote access needs authentication and a separate review of what can be shown or mutated.
