# ADR-0016 — Holdout evaluator and campaign freeze

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-10 · **Arch:** §4.2 (procedure, 3 layers), P6, D4, §3.3.3 · **Open item:** O1 (D4) stays open

## Context

§4.2 fixes the procedure (OPEN → FROZEN → one opening → BURNED), the one-row-per-campaign `holdout_access`, the read-only + hashed files, and a separate evaluator process returning one bit. Open: where the frozen `portfolio_hash` lives before the opening (the schema has no freeze table), how the evaluator runs member code on holdout data when generated code may run only in the sandbox and the sandbox never sees `holdout/`, what `holdout_pass` (D4) is compared with, and how the CLI freezes a campaign when nothing may import `quantcrucible.holdout`.

## Decision

1. **Freeze = research side** (`validation/freeze.py`, callable from the CLI): refused unless the campaign is OPEN, the hash is a `portfolio_variants` row of this campaign and its latest ⑤ and ⑥′ results are passes computed on the current ledger (amended below). It moves the campaign to FROZEN and writes a `CAMPAIGN_FROZEN` audit event carrying `portfolio_hash` + `frozen_at`. From then on `GatePipeline.run` and `record_variant` refuse the campaign (nothing added, removed or re-weighted).
2. **Evaluator = holdout side** (`holdout/evaluator_proc.py`, own `-m` entry point, `holdout/campaign.py`): input is only `--portfolio <hash>`. Preflight, before any holdout byte is read: campaign FROZEN on this hash; no `holdout_access` row yet; `frozen_at < now`; the evaluation lock matches the hash recorded at campaign open; `holdout_pass` set (D4); `holdout.lock` matches the hash recorded at campaign open; every holdout file matches `holdout.lock`.
3. **Member code runs in the sandbox** on a copy of the verified holdout slice, prefixed with the last `lookback` in-sample bars as warm-up, written into that one job's input folder — never a mount of `holdout/`. Only returns at bars closing inside the holdout range count. The sandbox image is built lazily, after preflight.
4. **Verdict:** the frozen weights and rebalance rule combine the members; `PASS` iff the annualized OOS Sharpe ≥ `research.holdout_pass`. D4 is now decided exactly so — see [ADR-0020](0020-d4-holdout-pass-definition-and-value.md) (value 1.3, net of costs, reference 0).
5. **Burn:** insert `holdout_access` (with `sharpe_oos`), log `HOLDOUT_OPENED` (no numbers), FROZEN → BURNED; then print exactly `PASS` or `FAIL`. Refusals go to stderr with exit 2; any other error prints only its type (exit 3) — no traceback that might carry holdout values. ~~A crash after reading leaves the campaign FROZEN~~ — superseded below: the holdout is claimed before it is read, and any error after the claim burns the campaign.

## Consequences

- INV-08/09/24 enforced: `tests/holdout/test_evaluator.py::test_tampered_holdout_refused`, `::test_output_is_single_token`, `::test_refuses_without_threshold`; plus second opening, unfrozen/unknown portfolio, freeze rules, a real subprocess run. All on a synthetic holdout in a tmp dir.
- The evaluator is the only code that reads the root `holdout/`; import-linter still forbids every other package from importing `quantcrucible.holdout`.
- Starting a new campaign after BURNED already works (`config.lock.open_campaign` archives the old lock); abandoning an OPEN campaign is ADR-0019.

## Alternatives considered

- **Run members on the host inside the evaluator:** rejected — generated code outside the sandbox (§3.3.3).
- **Mount `holdout/` read-only into the container:** rejected — a copy of just the verified slice keeps the container's view minimal.
- **A `freezes` table:** rejected for now — a schema change; the append-only audit log already records the freeze immutably.

## Amendment — review of d765668

- **Freeze on current results only** (finding 3): the latest ⑤ and ⑥′ results must carry the ledger's current `ledger_snapshot` (ADR-0014 amendment); a trial or variant added since makes them stale and the freeze is refused until ⑤ → ⑥′ run again.
- **Claim before use** (finding 5): after preflight and before any holdout byte is read, the evaluator inserts a `holdout_claims` row (schema v3) in one SQLite transaction and logs `HOLDOUT_CLAIMED`. Of two concurrent evaluators only one claim succeeds. `holdout_access` can only be written for a claimed campaign.
- **Errors after the claim consume the holdout**: the evaluator logs `HOLDOUT_EVALUATION_FAILED` (error type only), writes `holdout_access` with verdict FAIL and no Sharpe, and burns the campaign; a retry is refused. A hard kill leaves the claim, which preflight also refuses.
- **No reuse across campaigns** (finding 4, §4.2 step 4): a claim is refused — by a trigger and by preflight — if an earlier claim has the same `holdout.lock` hash or an overlapping period (ranges end-exclusive, as in the store); opening a campaign on such a holdout is refused too (`config.lock.open_campaign`).
- Tests: `tests/holdout/test_evaluator.py::test_freeze_refuses_gate_results_from_an_older_ledger`, `::test_an_error_after_the_claim_still_consumes_the_holdout`, `::test_a_concurrent_claim_wins_once`, `::test_a_used_holdout_is_never_reopened_by_a_new_campaign`; `tests/ledger/test_db.py::test_a_holdout_is_claimed_once_across_campaigns`; `tests/config/test_lock.py::test_a_used_holdout_cannot_open_a_new_campaign`.
