# 03 — Invariant → test map

Every rule the architecture calls non-negotiable, with the mechanism that enforces it and the test that proves it. Test names are **planned** until the task lands; a row turns ✅ only when the named test exists and passes.

Mechanism strength, strongest first: **DB** (SQL constraint/trigger) › **OS** (process/file permissions, container) › **Lint** (import-linter / AST test) › **Code** (runtime check) › **Review**.

## Principles

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-01 | Every backtest goes through the ledger; no bypass path | P2, §4.1 | Code: backtests are reached only through `GatePipeline`, which writes the ledger; import contract for `agent` ([ADR-0006](adr/0006-minbtl-and-in-sample-gates.md)) | `tests/e2e/test_phase0.py::test_every_candidate_has_ledger_rows` | P0-12 | ✅ |
| INV-02 | Ledger rows are never deleted or updated (only `campaigns.status` moves forward) | P2, §4.1, ADR-0002 | DB triggers | `tests/ledger/test_db.py::test_every_table_rejects_delete_at_sql_level`, `::test_replace_rejected_by_sql`, `::test_update_rejected_by_sql` | P0-02 | ✅ |
| INV-03 | Strategies speak `Signal`, never quantities | P3, §3.3 | Code (`Signal` validation) + Lint | `tests/core/strategy/test_base.py::test_signal_validation` | P0-04 | ✅ |
| INV-04 | Strategies never import the adapter/execution layer | §3.3 | Lint: import-linter "core is the bottom layer" | `lint-imports` | P0-01 | ✅ |
| INV-05 | Volatility enters size exactly once; stop is a `min` cap | P4, §3.4 | Code | `tests/core/sizing/test_position_sizer.py::test_half_size_when_vol_doubles`, `::test_stop_cap_is_min_not_multiplier`, `tests/execution/test_risk.py::test_half_size_through_the_risk_sizer` | P1-06 | ✅ |
| INV-06 | Backtest ≡ live: execution depends on core only | P5, §3.5 | Lint | `lint-imports` ("execution depends on core only") | P0-01 | ✅ |
| INV-07 | Holdout opened once per campaign | P6, §4.2 L1 | DB: `PRIMARY KEY (campaign_id)` | `tests/ledger/test_db.py::test_second_holdout_access_raises` | P0-02 | ✅ |
| INV-08 | Holdout data tamper-evident and read-only | P6, §4.2 L2 | OS: read-only + `holdout.lock` hash | `tests/data/test_holdout_split.py::test_lock_hash_matches`, `tests/holdout/test_evaluator.py::test_tampered_holdout_refused` | P0-09, P1-10 | ✅ |
| INV-09 | Holdout evaluated in its own process; returns PASS/FAIL only | P6, §4.2 L3 | OS (separate process) + Lint (nothing imports `holdout`) | `tests/holdout/test_evaluator.py::test_output_is_single_token`, `lint-imports` | P1-10 | ✅ |
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
| INV-24 | Holdout cannot be opened while `holdout_pass` (D4) is unset | §10.1, D4 | Code | `tests/holdout/test_evaluator.py::test_refuses_without_threshold`, `::test_d4_is_the_frozen_portfolios_annualized_sharpe`, `::test_d4_pass_means_at_least_the_threshold`, `::test_d4_comes_from_the_campaign_lock_not_user_yaml`, `tests/config/test_lock.py::test_holdout_pass_is_locked_for_the_whole_campaign` | P1-10 | ✅ |

## Generated code & gates

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-30 | Fixed region unchanged (hash) | §3.3.1 r1 | Code (gate ①a) | `tests/core/strategy/test_template.py::test_byte_flip_changes_hash`, `tests/validation/test_guardrail.py::test_template_tamper_rejected` | P0-05, P0-07 | ✅ |
| INV-31 | ≤ 6 TUNABLE, finite bounds; undeclared constants rejected | §3.3.1 r2 | Code (gate ①a) | `tests/core/strategy/test_tunable.py`, `tests/validation/test_guardrail.py::test_undeclared_constant_rejected` | P0-05, P0-07 | ✅ |
| INV-32 | Only whitelisted `ind.*`/DSL ops; no imports/exec/dunder | §3.3.1 r3, §3.1.6 | Code (gate ①a) | `tests/validation/test_guardrail.py::test_malicious_snippets` | P0-07 | ✅ |
| INV-33 | Every indicator is causal | §3.1.6, 07 §9 | Code | `tests/core/strategy/test_registry.py::test_all_indicators_causal` | P0-04 | ✅ |
| INV-34 | All 4 leaky-oracle levels rejected | 07 §9, §7 phase 0 | Gates ①a/①b | `tests/validation/test_oracles.py::test_oracle_rejected_at_static_gate`, `::test_oracle_level_rejected_by_dynamic_gate_alone`, `::test_full_guardrail_rejects_oracles_and_passes_ema` | P0-11 | ✅ |
| INV-35 | Signal on bar *t* fills at *t+1*; limit fills only on trade-through | §3.5 | Code | `tests/execution/test_engine.py::test_next_bar_execution_entry_and_exit`, `::test_gap_in_data_is_not_a_look_ahead`, `::test_signal_on_close_fills_at_next_open`, `::test_limit_touch_not_filled` | P0-10 | ✅ |
| INV-36 | Generated code runs sandboxed: no network, empty env, no view of `holdout/` `config/` `ledger/` | §3.3.3 | OS (Docker) | `tests/validation/test_sandbox.py` (marker `docker`) | P0-08 | ✅ |
| INV-37 | Gates fail closed; every `GateResult` reaches the ledger | §3.2 | Code | `tests/validation/test_gates.py::test_exception_rejects_and_logs`, `::test_passes_are_logged` | P0-06 | ✅ |
| INV-38 | Gate ③ rejects `< min_trades`, `< min_holding_bars`, indicator abs(ρ) > max | §3.2 ③, D15 | Code | `tests/validation/test_is_gates.py::test_g3_constraints` | P0-12 | ✅ |

## Statistics & portfolio

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-40 | DSR matches Bailey & López de Prado (2014) example | §6, 07 §4 | Code | `tests/validation/test_statistical.py::test_dsr_reference_example`, `::test_psr_and_dsr_match_purgedcv_on_a_series` | P1-02 | ✅ |
| INV-41 | PBO ≈ 0.5 on noise, → 0 with a planted edge; hand-computed fixtures from the CSCV definition and agreement with an independent implementation (`purgedcv`, version pinned; ADR-0023) | §3.2 | Code | `tests/validation/test_pbo.py::test_pbo_noise`, `::test_pbo_planted_edge`, `::test_pbo_reference_example`, `::test_pbo_is_independent_of_purgedcv`, `::test_matches_purgedcv`; gate: `tests/validation/test_pbo_gate.py` | P1-03 | ✅ |
| INV-42 | DSR only on the consolidated portfolio; inputs from `trial_stats` + `portfolio_variants` | §3.1.6, §4.1 | Code (no per-cell API) | `tests/validation/test_portfolio_dsr.py::test_more_trials_lower_dsr`, `::test_inputs_are_trial_stats_plus_variants`, `::test_no_per_cell_dsr_api`, `::test_both_counts_and_the_clusters_are_reported` | P1-08 | ✅ |
| INV-43 | Only configurations that reached ③ count as trials; audit events don't | §4.1 | Code | `tests/ledger/test_db.py::test_trial_stats_ignores_audit_log`, `tests/e2e/test_phase1.py::test_ledger_separates_audit_from_trials` | P0-02 | ✅ |
| INV-44 | Calibration evaluations are trials (`source='param_opt'`) | §3.2.1 5b, §4.1 | Code | `tests/validation/test_calibration.py::test_every_eval_is_a_trial`, `::test_rejected_evaluations_still_count`, `::test_every_attempt_is_accounted_for` | P1-11 | ✅ |
| INV-45 | Any portfolio-rule change ⇒ a new `portfolio_variants` row, counted in `N` | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_rule_change_is_new_variant` | P1-07 | ✅ |
| INV-46 | `private` metrics never reach prompts; `feedback` depends on `public` only | §3.3.2 | Code (+ prompt-log scan in phase 2) | `tests/validation/test_report.py::test_feedback_ignores_private` | P0-06 | ✅ |
| INV-47 | IS→OOS curve uses CPCV paths, never the holdout | §3.2 | Code + Lint | `tests/validation/test_cpcv.py::test_oos_curve_is_private`, `::test_purge_removes_rows_whose_label_overlaps_the_test_block`, `::test_embargo_drops_rows_after_each_test_block` | P1-04 | ✅ |
| INV-48 | `N_eff` never merges statistically unrelated trials (only lowers the bar on evidence) | §4.1 | Code (ONC + significance guard) | `tests/validation/test_n_eff.py::test_onc_recovers_the_number_of_independent_sources`, `::test_onc_on_independent_series_keeps_them_apart`, `::test_update_n_eff_appends_a_clustering_run` | P1-05 | ✅ |
| INV-49 | A parameter optimizer runs only in calibration (once, pre-freeze) — never in the evolution loop | §3.3.1, §3.2.1 5b | Lint (AST test) | `tests/test_architecture_boundaries.py::test_parameter_optimizer_only_in_calibration` | P1-11 | ✅ |
| INV-50 | A measurement's artifacts (gate-③ returns, gate-④ matrix) are written once and never rewritten | §4.1 | Code (fresh path, exclusive create) | `tests/validation/test_is_gates.py::test_g3_artifacts_are_immutable_per_measurement`, `tests/validation/test_pbo_gate.py::test_g4_matrix_is_immutable_per_measurement` | P1-12 review | ✅ |
| INV-51 | A trial enters a portfolio only if that trial itself passed ④; a candidate's latest measurement decides | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_a_later_rejected_trial_is_not_eligible`, `::test_the_4_pass_must_belong_to_the_selected_trial` | P1-12 review | ✅ |
| INV-52 | Freeze only on ⑤/⑥′ results computed on the current ledger (no trial or variant added since) | §4.2, §4.1 | Code (`ledger_snapshot`) | `tests/holdout/test_evaluator.py::test_freeze_refuses_gate_results_from_an_older_ledger` | P1-12 review | ✅ |
| INV-53 | The holdout is claimed before use, once per campaign, and a used holdout (same manifest or overlapping period) is never used again by any campaign; an error after the claim still burns it | P6, §4.2 | DB (`holdout_claims` + triggers) + Code | `tests/ledger/test_db.py::test_a_holdout_is_claimed_once_across_campaigns`, `tests/holdout/test_evaluator.py::test_a_concurrent_claim_wins_once`, `::test_an_error_after_the_claim_still_consumes_the_holdout`, `::test_a_used_holdout_is_never_reopened_by_a_new_campaign`, `tests/config/test_lock.py::test_a_used_holdout_cannot_open_a_new_campaign` | P1-12 review | ✅ |
| INV-54 | An abandoned campaign is final and resets nothing; a new campaign needs D4 set | §4.2, D4 | DB (`campaign_abandonments` + triggers) + Code | `tests/ledger/test_db.py::test_abandoned_campaign_is_final_and_keeps_its_trials`, `tests/validation/test_run.py::test_a_new_campaign_needs_d4`, `::test_abandoned_campaign_is_replaced` | P1-12 review | ✅ |
| INV-55 | An attempt with no performance output is not a trial (no made-up Sharpe) but is traced; one that exposed a number without a measurement is never exempted automatically; DSR also reports the unmeasured-attempts sensitivity | §4.1, §3.2.1 5b | Code | `tests/validation/test_calibration.py::test_every_attempt_is_accounted_for`, `::test_an_error_after_output_is_not_silently_exempt`, `tests/validation/test_portfolio_dsr.py::test_unmeasured_attempts_are_a_sensitivity_not_a_count` | O17 | ✅ |
| INV-56 | A portfolio's weights and returns depend on its members only (a dropped candidate changes nothing); a recorded hash always carries its artifact's returns | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_a_dropped_candidate_does_not_change_the_portfolio`, `::test_member_weights_use_the_members_window_only`, `::test_a_recorded_hash_must_match_its_artifact` | review 2 | ✅ |
| INV-57 | Calibration runs once per strategy per campaign with the locked budget; only a run that measured nothing may be retried | §3.2.1 5b, §3.3.1 | Code | `tests/validation/test_calibration.py::test_a_second_calibration_is_refused`, `::test_a_technical_retry_is_allowed_only_if_nothing_was_measured`, `::test_an_interrupted_search_is_not_resumed`, `::test_a_crashed_confirmation_is_not_reported_as_success`; `tests/ledger/test_db.py::test_calibration_runs_once_per_strategy` | review 2 | ✅ |

## Phase 2 — engine C

| ID | Invariant | Arch | Mechanism | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-60 | Every engine candidate is evaluated through `submit` → `GatePipeline`; no engine reaches the backtester directly | P2, §3.1.11 | Lint (agent may not import execution/sandbox) + Code | `lint-imports`, `tests/e2e/test_phase2_random.py::test_every_candidate_has_ledger_rows` | P2-09 | ☐ |
| INV-61 | The trial quota of each (engine, seed) is never exceeded | §3.1.11 | Code | `tests/agent/test_scheduler.py::test_quota_never_exceeded_concurrently` | P2-06 | ☐ |
| INV-62 | A harness-test campaign never builds a portfolio, freezes or claims a holdout | §3.1.11, P6 | Code | `tests/validation/test_run.py::test_harness_test_campaign_cannot_freeze` | P2-06 | ☐ |
| INV-63 | Feature-map bin bounds come only from the campaign lock | §3.1.3 | Code | `tests/agent/evolution/test_feature_map.py::test_bounds_only_from_lock` | P2-07 | ☐ |
| INV-64 | No strategy compares a price-scale series with a constant (scale invariance) | §3.1.6, §3.1.11 | Code (gate ①a) | `tests/validation/test_guardrail.py::test_price_vs_constant_rejected` | P2-04 | ☐ |
| INV-65 | C-random reads no results | §3.1.11 | Code | `tests/agent/engines/test_random_search.py::test_random_engine_has_no_result_input` | P2-05 | ☐ |
| INV-66 | Parameter-only children ≤ `param_only_max`; each evaluated child is one trial | §3.1.11, D20, §3.3.1 | Code | `tests/agent/evolution/test_operators.py::test_param_only_cap` | P2-11 | ☐ |
| INV-67 | Island integration: parent from the processed island, no duplicated migrant, a migrant's children on the destination island | §3.1.5 | Code | `tests/agent/evolution/test_islands.py::test_island_integration` | P2-12 | ☐ |
| INV-68 | Engine ranking reads `public` metrics only | §3.3.2 | Code | `tests/agent/evolution/test_ranking.py::test_ranking_ignores_private` | P2-11 | ☐ |
| INV-69 | An interrupted run leaves no sandbox container | §3.3.3 | OS + Code | `tests/agent/test_pipeline.py::test_interrupt_kills_containers` (marker `docker`) | P2-08 | ☐ |
| INV-70 | The comparison protocol is locked before the run's first trial | §3.1.11 | Code | `tests/agent/test_compare.py::test_protocol_predates_first_trial` | P2-15 | ☐ |

Deferred with engines A/B (D19): `private` keys absent from every logged prompt (§3.3.2); drift gate ⓪ thresholds applied as locked (§3.1.7).
