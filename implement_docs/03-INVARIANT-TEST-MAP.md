# 03 — Invariant → test map

Every rule the architecture calls non-negotiable, with the mechanism that enforces it and the test that proves it. Test names are **planned** until the task lands; a row turns ✅ only when the named test exists and passes.

Mechanism strength, strongest first: **DB** (SQL constraint/trigger) › **OS** (process/file permissions, container) › **Lint** (import-linter / AST test) › **Code** (runtime check) › **Review**.

## Principles

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-01 | Every backtest goes through the ledger; no bypass path | P2, §4.1 | Code: the backtest runner requires a ledger handle and writes before returning | `tests/e2e/test_phase0.py::test_every_candidate_has_ledger_rows` | P0-12 | ☐ |
| INV-02 | Ledger rows are never deleted or updated (only `campaigns.status` moves forward) | P2, §4.1, ADR-0002 | DB triggers | `tests/ledger/test_db.py::test_every_table_rejects_delete_at_sql_level`, `::test_update_rejected_by_sql` | P0-02 | ✅ |
| INV-03 | Strategies speak `Signal`, never quantities | P3, §3.3 | Code (`Signal` validation) + Lint | `tests/core/strategy/test_base.py::test_signal_validation` | P0-04 | ✅ |
| INV-04 | Strategies never import the adapter/execution layer | §3.3 | Lint: import-linter "core is the bottom layer" | `lint-imports` | P0-01 | ✅ |
| INV-05 | Volatility enters size exactly once; stop is a `min` cap | P4, §3.4 | Code | `tests/core/sizing/test_position_sizer.py::test_half_size_when_vol_doubles`, `::test_stop_cap_is_min_not_multiplier` | P1-06 | ☐ |
| INV-06 | Backtest ≡ live: execution depends on core only | P5, §3.5 | Lint | `lint-imports` ("execution depends on core only") | P0-01 | ✅ |
| INV-07 | Holdout opened once per campaign | P6, §4.2 L1 | DB: `PRIMARY KEY (campaign_id)` | `tests/ledger/test_db.py::test_second_holdout_access_raises` | P0-02 | ✅ |
| INV-08 | Holdout data tamper-evident and read-only | P6, §4.2 L2 | OS: read-only + `holdout.lock` hash | `tests/data/test_holdout_split.py::test_lock_hash_matches`, `tests/holdout/test_evaluator.py::test_tampered_holdout_refused` | P0-09, P1-10 | ◐ |
| INV-09 | Holdout evaluated in its own process; returns PASS/FAIL only | P6, §4.2 L3 | OS (separate process) + Lint (nothing imports `holdout`) | `tests/holdout/test_evaluator.py::test_output_is_single_token`, `lint-imports` | P1-10 | ◐ |
| INV-10 | Research data loader never returns holdout-range bars | P6, §4.2 | Code | `tests/data/test_store.py::test_holdout_range_refused` | P0-09 | ✅ |
| INV-11 | The coding agent cannot read holdout data or write the lock | P6, §10.1 | Claude Code hook | `tests/tooling/test_guard_paths.py` | P0-01 | ✅ |
| INV-12 | No LLM at runtime: LLM SDKs only under `agent/` | A4 | Lint (AST test) | `tests/test_architecture_boundaries.py::test_llm_sdks_only_in_agent_layer` | P0-01 | ✅ |

## Configuration & campaign

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-20 | `dsr_min < 0.95`, `pbo_max > 0.5` or `minbtl_target_sharpe > 1.5` rejected at load | §10.1 | Code | `tests/config/test_loader.py::test_hard_floors` | P0-03 | ✅ |
| INV-21 | Group B edited mid-campaign ⇒ refuse to run | §10.1 | Code (`assert_lock_matches`) | `tests/config/test_lock.py::test_group_b_change_refused` | P0-03 | ✅ |
| INV-22 | `evaluation.lock.yaml` is generated, read-only, hashed in `campaigns` | §10.1 | OS + DB | `tests/config/test_lock.py::test_lock_readonly_and_hashed` | P0-03 | ✅ |
| INV-23 | Campaign status only `OPEN → FROZEN → BURNED` | §4.2 | DB trigger | `tests/ledger/test_db.py::test_campaign_transitions` | P0-02 | ✅ |
| INV-24 | Holdout cannot be opened while `holdout_pass` (D4) is unset | §10.1, D4 | Code | `tests/holdout/test_evaluator.py::test_refuses_without_threshold` | P1-10 | ☐ |

## Generated code & gates

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-30 | Fixed region unchanged (hash) | §3.3.1 r1 | Code (gate ①a) | `tests/core/strategy/test_template.py::test_byte_flip_changes_hash`, `tests/validation/test_guardrail.py::test_template_tamper_rejected` | P0-05, P0-07 | ✅ |
| INV-31 | ≤ 6 TUNABLE, finite bounds; undeclared constants rejected | §3.3.1 r2 | Code (gate ①a) | `tests/core/strategy/test_tunable.py`, `tests/validation/test_guardrail.py::test_undeclared_constant_rejected` | P0-05, P0-07 | ✅ |
| INV-32 | Only whitelisted `ind.*`/DSL ops; no imports/exec/dunder | §3.3.1 r3, §3.1.6 | Code (gate ①a) | `tests/validation/test_guardrail.py::test_malicious_snippets` | P0-07 | ✅ |
| INV-33 | Every indicator is causal | §3.1.6, 07 §9 | Code | `tests/core/strategy/test_registry.py::test_all_indicators_causal` | P0-04 | ✅ |
| INV-34 | All 4 leaky-oracle levels rejected | 07 §9, §7 phase 0 | Gates ①a/①b | `tests/validation/test_oracles.py::test_oracle_level_{1,2,3,4}_rejected` | P0-11 | ☐ |
| INV-35 | Signal on bar *t* fills at *t+1*; limit fills only on trade-through | §3.5 | Code | `tests/execution/test_engine.py::test_next_bar_execution`, `::test_limit_touch_not_filled` | P0-10 | ☐ |
| INV-36 | Generated code runs sandboxed: no network, empty env, no view of `holdout/` `config/` `ledger/` | §3.3.3 | OS (Docker) | `tests/validation/test_sandbox.py` (marker `docker`) | P0-08 | ☐ |
| INV-37 | Gates fail closed; every `GateResult` reaches the ledger | §3.2 | Code | `tests/validation/test_gates.py::test_exception_rejects_and_logs`, `::test_passes_are_logged` | P0-06 | ✅ |
| INV-38 | Gate ③ rejects `< min_trades`, `< min_holding_bars`, indicator abs(ρ) > max | §3.2 ③, D15 | Code | `tests/validation/test_gates.py::test_g3_constraints` | P0-12 | ☐ |

## Statistics & portfolio

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-40 | DSR matches Bailey & López de Prado (2014) example | §6, 07 §4 | Code | `tests/validation/test_statistical.py::test_dsr_reference_example` | P1-02 | ☐ |
| INV-41 | PBO ≈ 0.5 on noise, → 0 with a planted edge; matches Bailey et al. (2017) | §3.2 | Code | `tests/validation/test_statistical.py::test_pbo_noise`, `::test_pbo_planted_edge`, `::test_pbo_reference_example` | P1-03 | ☐ |
| INV-42 | DSR only on the consolidated portfolio; inputs from `trial_stats` + `portfolio_variants` | §3.1.6, §4.1 | Code (no per-cell API) | `tests/validation/test_portfolio_dsr.py::test_more_trials_lower_dsr` | P1-08 | ☐ |
| INV-43 | Only configurations that reached ③ count as trials; audit events don't | §4.1 | Code | `tests/ledger/test_db.py::test_trial_stats_ignores_audit_log` | P0-02 | ✅ |
| INV-44 | Calibration evaluations are trials (`source='param_opt'`) | §3.2.1 5b, §4.1 | Code | `tests/validation/test_calibration.py::test_every_eval_is_a_trial` | P1-11 | ☐ |
| INV-45 | Any portfolio-rule change ⇒ a new `portfolio_variants` row, counted in `N` | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_rule_change_is_new_variant` | P1-07 | ☐ |
| INV-46 | `private` metrics never reach prompts; `feedback` depends on `public` only | §3.3.2 | Code (+ prompt-log scan in phase 2) | `tests/validation/test_report.py::test_feedback_ignores_private` | P0-06 | ✅ |
| INV-47 | IS→OOS curve uses CPCV paths, never the holdout | §3.2 | Code + Lint | `tests/validation/test_cpcv.py::test_oos_curve_is_private` | P1-04 | ☐ |

## Phase 2+ (fill in when the phase is broken into tasks)

- `private` keys absent from every logged prompt (string scan of the prompt log) — §3.3.2.
- No optimizer call inside the evolution loop — §3.3.1.
- Engine budget shares enforced over statistical trials — §3.1.11.
- Feature-map bin bounds fixed per campaign — §3.1.10.
- Drift gate ⓪ thresholds applied as locked — §3.1.7.
