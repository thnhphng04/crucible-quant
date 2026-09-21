# ADR-0016 — Holdout evaluator and campaign freeze

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-10 · **Arch:** §4.2 (procedure, 3 layers), P6, D4, §3.3.3 · **Open item:** O1 (D4) stays open

## Context

§4.2 fixes the procedure (OPEN → FROZEN → one opening → BURNED), the one-row-per-campaign `holdout_access`, the read-only + hashed files, and a separate evaluator process returning one bit. Open: where the frozen `portfolio_hash` lives before the opening (the schema has no freeze table), how the evaluator runs member code on holdout data when generated code may run only in the sandbox and the sandbox never sees `holdout/`, what `holdout_pass` (D4) is compared with, and how the CLI freezes a campaign when nothing may import `quantcrucible.holdout`.

## Decision

1. **Freeze = research side** (`validation/freeze.py`, callable from the CLI): refused unless the campaign is OPEN, the hash is a `portfolio_variants` row of this campaign and its latest ⑤ and ⑥′ results are passes. It moves the campaign to FROZEN and writes a `CAMPAIGN_FROZEN` audit event carrying `portfolio_hash` + `frozen_at`. From then on `GatePipeline.run` and `record_variant` refuse the campaign (nothing added, removed or re-weighted).
2. **Evaluator = holdout side** (`holdout/evaluator_proc.py`, own `-m` entry point, `holdout/campaign.py`): input is only `--portfolio <hash>`. Preflight, before any holdout byte is read: campaign FROZEN on this hash; no `holdout_access` row yet; `frozen_at < now`; the evaluation lock matches the hash recorded at campaign open; `holdout_pass` set (D4); `holdout.lock` matches the hash recorded at campaign open; every holdout file matches `holdout.lock`.
3. **Member code runs in the sandbox** on a copy of the verified holdout slice, prefixed with the last `lookback` in-sample bars as warm-up, written into that one job's input folder — never a mount of `holdout/`. Only returns at bars closing inside the holdout range count. The sandbox image is built lazily, after preflight.
4. **Verdict:** the frozen weights and rebalance rule combine the members; `PASS` iff the annualized OOS Sharpe ≥ `research.holdout_pass`. **This interprets D4 as a minimum Sharpe — confirm or replace it when D4 is decided.**
5. **Burn:** insert `holdout_access` (with `sharpe_oos`), log `HOLDOUT_OPENED` (no numbers), FROZEN → BURNED; then print exactly `PASS` or `FAIL`. Refusals go to stderr with exit 2; any other error prints only its type (exit 3) — no traceback that might carry holdout values. A crash after reading but before the insert leaves the campaign FROZEN with nothing printed.

## Consequences

- INV-08/09/24 enforced: `tests/holdout/test_evaluator.py::test_tampered_holdout_refused`, `::test_output_is_single_token`, `::test_refuses_without_threshold`; plus second opening, unfrozen/unknown portfolio, freeze rules, a real subprocess run. All on a synthetic holdout in a tmp dir.
- The evaluator is the only code that reads the root `holdout/`; import-linter still forbids every other package from importing `quantcrucible.holdout`.
- Starting a new campaign after BURNED already works (`config.lock.open_campaign` archives the old lock); there is no command to abandon an OPEN campaign.

## Alternatives considered

- **Run members on the host inside the evaluator:** rejected — generated code outside the sandbox (§3.3.3).
- **Mount `holdout/` read-only into the container:** rejected — a copy of just the verified slice keeps the container's view minimal.
- **A `freezes` table:** rejected for now — a schema change; the append-only audit log already records the freeze immutably.
