# 03 — Bản đồ bất biến → test

Mọi quy tắc mà kiến trúc coi là không thể thương lượng, kèm cơ chế cưỡng chế và test chứng minh. Tên test là **dự kiến** cho đến khi task hoàn thành; một dòng chỉ được đánh ✅ khi test được nêu tên tồn tại và chạy đạt.

Độ mạnh của cơ chế, mạnh nhất trước: **DB** (ràng buộc/trigger SQL) › **OS** (quyền tiến trình/file, container) › **Lint** (import-linter / test AST) › **Code** (kiểm tra lúc chạy) › **Review**.

## Nguyên tắc

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-01 | Mọi backtest đều đi qua ledger; không có đường vòng | P2, §4.1 | Code: chỉ tới được backtest qua `GatePipeline`, nơi ghi ledger; contract import cho `agent` ([ADR-0006](adr/0006-cong-minbtl-va-backtest-in-sample.md)) | `tests/e2e/test_phase0.py::test_every_candidate_has_ledger_rows` | P0-12 | ✅ |
| INV-02 | Dòng ledger không bao giờ bị xóa hay sửa (chỉ `campaigns.status` được đi tiếp) | P2, §4.1, ADR-0002 | Trigger DB | `tests/ledger/test_db.py::test_every_table_rejects_delete_at_sql_level`, `::test_replace_rejected_by_sql`, `::test_update_rejected_by_sql` | P0-02 | ✅ |
| INV-03 | Strategy nói bằng `Signal`, không bao giờ bằng khối lượng | P3, §3.3 | Code (kiểm tra `Signal`) + Lint | `tests/core/strategy/test_base.py::test_signal_validation` | P0-04 | ✅ |
| INV-04 | Strategy không bao giờ import tầng adapter/execution | §3.3 | Lint: import-linter "core is the bottom layer" | `lint-imports` | P0-01 | ✅ |
| INV-05 | Volatility đi vào size đúng một lần; stop là trần `min` | P4, §3.4 | Code | `tests/core/sizing/test_position_sizer.py::test_half_size_when_vol_doubles`, `::test_stop_cap_is_min_not_multiplier`, `tests/execution/test_risk.py::test_half_size_through_the_risk_sizer` | P1-06 | ✅ |
| INV-06 | Backtest ≡ live: execution chỉ phụ thuộc core | P5, §3.5 | Lint | `lint-imports` ("execution depends on core only") | P0-01 | ✅ |
| INV-07 | Holdout chỉ mở một lần mỗi campaign | P6, §4.2 L1 | DB: `PRIMARY KEY (campaign_id)` | `tests/ledger/test_db.py::test_second_holdout_access_raises` | P0-02 | ✅ |
| INV-08 | Dữ liệu holdout phát hiện được việc sửa đổi và chỉ-đọc | P6, §4.2 L2 | OS: chỉ-đọc + hash `holdout.lock` | `tests/data/test_holdout_split.py::test_lock_hash_matches`, `tests/holdout/test_evaluator.py::test_tampered_holdout_refused` | P0-09, P1-10 | ✅ |
| INV-09 | Holdout được đánh giá trong tiến trình riêng; chỉ trả PASS/FAIL | P6, §4.2 L3 | OS (tiến trình riêng) + Lint (không ai import `holdout`) | `tests/holdout/test_evaluator.py::test_output_is_single_token`, `lint-imports` | P1-10 | ✅ |
| INV-10 | Bộ nạp dữ liệu nghiên cứu không bao giờ trả bar trong khoảng holdout | P6, §4.2 | Code | `tests/data/test_store.py::test_holdout_range_refused` | P0-09 | ✅ |
| INV-11 | Coding agent không thể đọc dữ liệu holdout hay ghi file lock | P6, §10.1 | Hook Claude Code | `tests/tooling/test_guard_paths.py` | P0-01 | ✅ |
| INV-12 | Không có LLM lúc chạy: SDK LLM chỉ nằm dưới `agent/` | A4 | Lint (test AST) | `tests/test_architecture_boundaries.py::test_llm_sdks_only_in_agent_layer` | P0-01 | ✅ |

## Cấu hình & campaign

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-20 | `dsr_min < 0.95`, `pbo_max > 0.5` hoặc `minbtl_target_sharpe > 1.5` bị từ chối khi nạp | §10.1 | Code | `tests/config/test_loader.py::test_hard_floors` | P0-03 | ✅ |
| INV-21 | Sửa Nhóm B giữa campaign ⇒ từ chối chạy | §10.1 | Code (`assert_lock_matches`) | `tests/config/test_lock.py::test_group_b_change_refused` | P0-03 | ✅ |
| INV-22 | `evaluation.lock.yaml` được sinh tự động, chỉ-đọc, hash lưu trong `campaigns` | §10.1 | OS + DB | `tests/config/test_lock.py::test_lock_readonly_and_hashed` | P0-03 | ✅ |
| INV-23 | Trạng thái campaign chỉ đi `OPEN → FROZEN → BURNED` | §4.2 | Trigger DB | `tests/ledger/test_db.py::test_campaign_transitions` | P0-02 | ✅ |
| INV-24 | Không mở được holdout khi `holdout_pass` (D4) chưa đặt | §10.1, D4 | Code | `tests/holdout/test_evaluator.py::test_refuses_without_threshold`, `::test_d4_is_the_frozen_portfolios_annualized_sharpe`, `::test_d4_pass_means_at_least_the_threshold`, `::test_d4_comes_from_the_campaign_lock_not_user_yaml`, `tests/config/test_lock.py::test_holdout_pass_is_locked_for_the_whole_campaign` | P1-10 | ✅ |

## Code được sinh & các cổng

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-30 | Vùng cố định không đổi (hash) | §3.3.1 r1 | Code (cổng ①a) | `tests/core/strategy/test_template.py::test_byte_flip_changes_hash`, `tests/validation/test_guardrail.py::test_template_tamper_rejected` | P0-05, P0-07 | ✅ |
| INV-31 | ≤ 6 TUNABLE, bounds hữu hạn; hằng số chưa khai báo bị loại | §3.3.1 r2 | Code (cổng ①a) | `tests/core/strategy/test_tunable.py`, `tests/validation/test_guardrail.py::test_undeclared_constant_rejected` | P0-05, P0-07 | ✅ |
| INV-32 | Chỉ phép toán `ind.*`/DSL trong whitelist; không import/exec/dunder | §3.3.1 r3, §3.1.6 | Code (cổng ①a) | `tests/validation/test_guardrail.py::test_malicious_snippets` | P0-07 | ✅ |
| INV-33 | Mọi indicator đều nhân quả | §3.1.6, 07 §9 | Code | `tests/core/strategy/test_registry.py::test_all_indicators_causal` | P0-04 | ✅ |
| INV-34 | Cả 4 cấp leaky oracle đều bị loại | 07 §9, §7 GĐ 0 | Cổng ①a/①b | `tests/validation/test_oracles.py::test_oracle_rejected_at_static_gate`, `::test_oracle_level_rejected_by_dynamic_gate_alone`, `::test_full_guardrail_rejects_oracles_and_passes_ema` | P0-11 | ✅ |
| INV-35 | Tín hiệu ở bar *t* khớp ở *t+1*; lệnh limit chỉ khớp khi giá đi xuyên qua | §3.5 | Code | `tests/execution/test_engine.py::test_next_bar_execution_entry_and_exit`, `::test_gap_in_data_is_not_a_look_ahead`, `::test_signal_on_close_fills_at_next_open`, `::test_limit_touch_not_filled` | P0-10 | ✅ |
| INV-36 | Code được sinh chạy trong sandbox: không mạng, env rỗng, không thấy `holdout/` `config/` `ledger/` | §3.3.3 | OS (Docker) | `tests/validation/test_sandbox.py` (marker `docker`) | P0-08 | ✅ |
| INV-37 | Cổng fail closed; mọi `GateResult` đều vào ledger | §3.2 | Code | `tests/validation/test_gates.py::test_exception_rejects_and_logs`, `::test_passes_are_logged` | P0-06 | ✅ |
| INV-38 | Cổng ③ loại khi `< min_trades`, `< min_holding_bars`, indicator có abs(ρ) > max | §3.2 ③, D15 | Code | `tests/validation/test_is_gates.py::test_g3_constraints` | P0-12 | ✅ |

## Thống kê & danh mục

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-40 | DSR khớp ví dụ của Bailey & López de Prado (2014) | §6, 07 §4 | Code | `tests/validation/test_statistical.py::test_dsr_reference_example`, `::test_psr_and_dsr_match_purgedcv_on_a_series` | P1-02 | ✅ |
| INV-41 | PBO ≈ 0.5 trên nhiễu, → 0 khi có edge cài sẵn; fixture tính tay theo định nghĩa CSCV và khớp một cài đặt độc lập (`purgedcv`, ghim phiên bản; ADR-0023) | §3.2 | Code | `tests/validation/test_pbo.py::test_pbo_noise`, `::test_pbo_planted_edge`, `::test_pbo_reference_example`, `::test_pbo_is_independent_of_purgedcv`, `::test_matches_purgedcv`; cổng: `tests/validation/test_pbo_gate.py` | P1-03 | ✅ |
| INV-42 | DSR chỉ trên danh mục hợp nhất; đầu vào từ `trial_stats` + `portfolio_variants` | §3.1.6, §4.1 | Code (không có API theo ô) | `tests/validation/test_portfolio_dsr.py::test_more_trials_lower_dsr`, `::test_inputs_are_trial_stats_plus_variants`, `::test_no_per_cell_dsr_api`, `::test_both_counts_and_the_clusters_are_reported` | P1-08 | ✅ |
| INV-43 | Chỉ cấu hình đã tới ③ mới tính là trial; sự kiện audit thì không | §4.1 | Code | `tests/ledger/test_db.py::test_trial_stats_ignores_audit_log`, `tests/e2e/test_phase1.py::test_ledger_separates_audit_from_trials` | P0-02 | ✅ |
| INV-44 | Các lần đánh giá calibration là trial (`source='param_opt'`) | §3.2.1 5b, §4.1 | Code | `tests/validation/test_calibration.py::test_every_eval_is_a_trial`, `::test_rejected_evaluations_still_count`, `::test_every_attempt_is_accounted_for` | P1-11 | ✅ |
| INV-45 | Mọi thay đổi quy tắc danh mục ⇒ một dòng `portfolio_variants` mới, được tính vào `N` | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_rule_change_is_new_variant` | P1-07 | ✅ |
| INV-46 | Metric `private` không bao giờ vào prompt; `feedback` chỉ phụ thuộc `public` | §3.3.2 | Code (+ quét log prompt ở GĐ 2) | `tests/validation/test_report.py::test_feedback_ignores_private` | P0-06 | ✅ |
| INV-47 | Đường cong IS→OOS dùng các path CPCV, không bao giờ dùng holdout | §3.2 | Code + Lint | `tests/validation/test_cpcv.py::test_oos_curve_is_private`, `::test_purge_removes_rows_whose_label_overlaps_the_test_block`, `::test_embargo_drops_rows_after_each_test_block` | P1-04 | ✅ |
| INV-48 | `N_eff` không bao giờ gộp các trial không liên quan về thống kê (chỉ hạ ngưỡng khi có bằng chứng) | §4.1 | Code (ONC + bước bảo vệ ý nghĩa) | `tests/validation/test_n_eff.py::test_onc_recovers_the_number_of_independent_sources`, `::test_onc_on_independent_series_keeps_them_apart`, `::test_update_n_eff_appends_a_clustering_run` | P1-05 | ✅ |
| INV-49 | Optimizer tham số chỉ chạy trong calibration (một lần, trước khi đóng băng) — không bao giờ trong vòng tiến hoá | §3.3.1, §3.2.1 5b | Lint (test AST) | `tests/test_architecture_boundaries.py::test_parameter_optimizer_only_in_calibration` | P1-11 | ✅ |
| INV-50 | Artifact của một lần đo (returns gate ③, ma trận gate ④) được ghi một lần và không bao giờ bị ghi lại | §4.1 | Code (đường dẫn mới, tạo độc quyền) | `tests/validation/test_is_gates.py::test_g3_artifacts_are_immutable_per_measurement`, `tests/validation/test_pbo_gate.py::test_g4_matrix_is_immutable_per_measurement` | review P1-12 | ✅ |
| INV-51 | Một trial chỉ vào danh mục nếu chính trial đó đã qua ④; lần đo mới nhất của ứng viên quyết định | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_a_later_rejected_trial_is_not_eligible`, `::test_the_4_pass_must_belong_to_the_selected_trial` | review P1-12 | ✅ |
| INV-52 | Chỉ đóng băng trên kết quả ⑤/⑥′ tính trên ledger hiện tại (không có trial hay biến thể nào thêm vào sau đó) | §4.2, §4.1 | Code (`ledger_snapshot`) | `tests/holdout/test_evaluator.py::test_freeze_refuses_gate_results_from_an_older_ledger` | review P1-12 | ✅ |
| INV-53 | Holdout được claim trước khi dùng, một lần mỗi campaign, và holdout đã dùng (cùng manifest hoặc khoảng thời gian chồng lấn) không bao giờ được campaign nào dùng lại; lỗi sau claim vẫn đốt nó | P6, §4.2 | DB (`holdout_claims` + trigger) + Code | `tests/ledger/test_db.py::test_a_holdout_is_claimed_once_across_campaigns`, `tests/holdout/test_evaluator.py::test_a_concurrent_claim_wins_once`, `::test_an_error_after_the_claim_still_consumes_the_holdout`, `::test_a_used_holdout_is_never_reopened_by_a_new_campaign`, `tests/config/test_lock.py::test_a_used_holdout_cannot_open_a_new_campaign` | review P1-12 | ✅ |
| INV-54 | Campaign bị bỏ là trạng thái cuối và không reset gì; campaign mới cần D4 đã đặt | §4.2, D4 | DB (`campaign_abandonments` + trigger) + Code | `tests/ledger/test_db.py::test_abandoned_campaign_is_final_and_keeps_its_trials`, `tests/validation/test_run.py::test_a_new_campaign_needs_d4`, `::test_abandoned_campaign_is_replaced` | review P1-12 | ✅ |
| INV-55 | Một lần thử không có đầu ra hiệu suất không phải trial (không Sharpe bịa) nhưng được truy vết; lần thử đã lộ một con số mà không ghi được phép đo không bao giờ được tự động miễn tính; DSR còn báo độ nhạy của các lần thử không đo được | §4.1, §3.2.1 5b | Code | `tests/validation/test_calibration.py::test_every_attempt_is_accounted_for`, `::test_an_error_after_output_is_not_silently_exempt`, `tests/validation/test_portfolio_dsr.py::test_unmeasured_attempts_are_a_sensitivity_not_a_count` | O17 | ✅ |
| INV-56 | Trọng số và returns của danh mục chỉ phụ thuộc thành viên (ứng viên bị loại không thay đổi gì); một hash đã ghi luôn mang returns của artifact của nó | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_a_dropped_candidate_does_not_change_the_portfolio`, `::test_member_weights_use_the_members_window_only`, `::test_a_recorded_hash_must_match_its_artifact` | review 2 | ✅ |
| INV-57 | Calibration chạy một lần cho mỗi chiến lược trong mỗi campaign với ngân sách đã khóa; chỉ lượt chưa đo được gì mới được retry | §3.2.1 5b, §3.3.1 | Code | `tests/validation/test_calibration.py::test_a_second_calibration_is_refused`, `::test_a_technical_retry_is_allowed_only_if_nothing_was_measured`, `::test_an_interrupted_search_is_not_resumed`, `::test_a_crashed_confirmation_is_not_reported_as_success`; `tests/ledger/test_db.py::test_calibration_runs_once_per_strategy` | review 2 | ✅ |

## GĐ 2 — engine C

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-60 | Mọi ứng viên của engine đều được đánh giá qua `submit` → `GatePipeline`; không engine nào gọi thẳng backtester | P2, §3.1.11 | Lint (agent không được import execution/sandbox) + Code | `lint-imports`, `tests/e2e/test_phase2_random.py::test_every_candidate_has_ledger_rows` | P2-09 | ✅ |
| INV-61 | Hạn mức trial của mỗi (engine, seed) không bao giờ bị vượt | §3.1.11 | Code | `tests/agent/test_scheduler.py::test_quota_never_exceeded_concurrently`, `::test_existing_trials_count_against_the_quota` | P2-06 | ✅ |
| INV-62 | Campaign thử harness không bao giờ bị đóng băng, nên không bao giờ claim hay mở holdout | §3.1.11, P6 | DB (`campaign_purposes` + trigger) | `tests/validation/test_run.py::test_harness_test_campaign_cannot_freeze`, `tests/ledger/test_db.py::test_harness_test_campaign_is_never_frozen` | P2-06 | ✅ |
| INV-63 | Biên bin của feature map chỉ lấy từ lock của campaign | §3.1.3 | Code | `tests/agent/evolution/test_feature_map.py::test_bounds_only_from_lock`, `::test_out_of_range_lands_in_the_edge_bin` | P2-07 | ✅ |
| INV-64 | Không chiến lược nào so một chuỗi theo thang giá với hằng số (bất biến theo thang giá) | §3.1.6, §3.1.11 | Code (cổng ①a) | `tests/validation/test_guardrail.py::test_price_vs_constant_rejected`, `::test_scale_invariant_rules_pass`, `::test_stop_must_be_in_price_units` | P2-04 | ✅ |
| INV-65 | C-random không đọc kết quả nào | §3.1.11 | Code | `tests/agent/engines/test_random_search.py::test_random_engine_has_no_result_input` | P2-05 | ✅ |
| INV-66 | Con chỉ đổi tham số ≤ `param_only_max`; mỗi con được đánh giá là một trial | §3.1.11, D20, §3.3.1 | Code | `tests/agent/evolution/test_operators.py::test_param_only_cap`, `::test_a_parameter_only_child_keeps_the_parent_hash` | P2-11 | ✅ |
| INV-67 | Tích hợp đảo: cha từ đảo đang xử lý, migrant không nhân bản, con của migrant ở đảo đích | §3.1.5 | Code | `tests/agent/evolution/test_islands.py::test_island_integration` | P2-12 | ☐ |
| INV-68 | Điểm xếp hạng của engine chỉ đọc metric `public` | §3.3.2 | Code | `tests/agent/evolution/test_operators.py::test_ranking_ignores_private` | P2-11 | ✅ |
| INV-69 | Run bị ngắt không để lại container sandbox nào | §3.3.3 | OS + Code | `tests/agent/test_pipeline.py::test_interrupt_kills_containers` (marker `docker`) | P2-08 | ✅ |
| INV-70 | Giao thức so sánh được khóa trước trial đầu tiên của lần chạy | §3.1.11 | Code | `tests/agent/test_compare.py::test_protocol_predates_first_trial` | P2-15 | ☐ |

Hoãn cùng engine A/B (D19): không key `private` nào xuất hiện trong prompt đã log (§3.3.2); ngưỡng của cổng drift ⓪ được áp dụng đúng như đã khóa (§3.1.7).
