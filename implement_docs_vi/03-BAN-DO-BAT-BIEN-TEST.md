# 03 — Bản đồ bất biến → test

Mọi quy tắc mà kiến trúc coi là không thể thương lượng, kèm cơ chế cưỡng chế và test chứng minh. Tên test là **dự kiến** cho đến khi task hoàn thành; một dòng chỉ được đánh ✅ khi test được nêu tên tồn tại và chạy đạt.

Độ mạnh của cơ chế, mạnh nhất trước: **DB** (ràng buộc/trigger SQL) › **OS** (quyền tiến trình/file, container) › **Lint** (import-linter / test AST) › **Code** (kiểm tra lúc chạy) › **Review**.

## Nguyên tắc

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-01 | Mọi backtest đều đi qua ledger; không có đường vòng | P2, §4.1 | Code: bộ chạy backtest bắt buộc có handle ledger và ghi trước khi trả kết quả | `tests/e2e/test_phase0.py::test_every_candidate_has_ledger_rows` | P0-12 | ☐ |
| INV-02 | Dòng ledger không bao giờ bị xóa hay sửa (chỉ `campaigns.status` được đi tiếp) | P2, §4.1, ADR-0002 | Trigger DB | `tests/ledger/test_db.py::test_every_table_rejects_delete_at_sql_level`, `::test_update_rejected_by_sql` | P0-02 | ✅ |
| INV-03 | Strategy nói bằng `Signal`, không bao giờ bằng khối lượng | P3, §3.3 | Code (kiểm tra `Signal`) + Lint | `tests/core/strategy/test_base.py::test_signal_validation` | P0-04 | ✅ |
| INV-04 | Strategy không bao giờ import tầng adapter/execution | §3.3 | Lint: import-linter "core is the bottom layer" | `lint-imports` | P0-01 | ✅ |
| INV-05 | Volatility đi vào size đúng một lần; stop là trần `min` | P4, §3.4 | Code | `tests/core/sizing/test_position_sizer.py::test_half_size_when_vol_doubles`, `::test_stop_cap_is_min_not_multiplier` | P1-06 | ☐ |
| INV-06 | Backtest ≡ live: execution chỉ phụ thuộc core | P5, §3.5 | Lint | `lint-imports` ("execution depends on core only") | P0-01 | ✅ |
| INV-07 | Holdout chỉ mở một lần mỗi campaign | P6, §4.2 L1 | DB: `PRIMARY KEY (campaign_id)` | `tests/ledger/test_db.py::test_second_holdout_access_raises` | P0-02 | ✅ |
| INV-08 | Dữ liệu holdout phát hiện được việc sửa đổi và chỉ-đọc | P6, §4.2 L2 | OS: chỉ-đọc + hash `holdout.lock` | `tests/data/test_holdout_split.py::test_lock_hash_matches`, `tests/holdout/test_evaluator.py::test_tampered_holdout_refused` | P0-09, P1-10 | ◐ |
| INV-09 | Holdout được đánh giá trong tiến trình riêng; chỉ trả PASS/FAIL | P6, §4.2 L3 | OS (tiến trình riêng) + Lint (không ai import `holdout`) | `tests/holdout/test_evaluator.py::test_output_is_single_token`, `lint-imports` | P1-10 | ◐ |
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
| INV-24 | Không mở được holdout khi `holdout_pass` (D4) chưa đặt | §10.1, D4 | Code | `tests/holdout/test_evaluator.py::test_refuses_without_threshold` | P1-10 | ☐ |

## Code được sinh & các cổng

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-30 | Vùng cố định không đổi (hash) | §3.3.1 r1 | Code (cổng ①a) | `tests/core/strategy/test_template.py::test_byte_flip_changes_hash`, `tests/validation/test_guardrail.py::test_template_tamper_rejected` | P0-05, P0-07 | ✅ |
| INV-31 | ≤ 6 TUNABLE, bounds hữu hạn; hằng số chưa khai báo bị loại | §3.3.1 r2 | Code (cổng ①a) | `tests/core/strategy/test_tunable.py`, `tests/validation/test_guardrail.py::test_undeclared_constant_rejected` | P0-05, P0-07 | ✅ |
| INV-32 | Chỉ phép toán `ind.*`/DSL trong whitelist; không import/exec/dunder | §3.3.1 r3, §3.1.6 | Code (cổng ①a) | `tests/validation/test_guardrail.py::test_malicious_snippets` | P0-07 | ✅ |
| INV-33 | Mọi indicator đều nhân quả | §3.1.6, 07 §9 | Code | `tests/core/strategy/test_registry.py::test_all_indicators_causal` | P0-04 | ✅ |
| INV-34 | Cả 4 cấp leaky oracle đều bị loại | 07 §9, §7 GĐ 0 | Cổng ①a/①b | `tests/validation/test_oracles.py::test_oracle_level_{1,2,3,4}_rejected` | P0-11 | ☐ |
| INV-35 | Tín hiệu ở bar *t* khớp ở *t+1*; lệnh limit chỉ khớp khi giá đi xuyên qua | §3.5 | Code | `tests/execution/test_engine.py::test_next_bar_execution`, `::test_limit_touch_not_filled` | P0-10 | ☐ |
| INV-36 | Code được sinh chạy trong sandbox: không mạng, env rỗng, không thấy `holdout/` `config/` `ledger/` | §3.3.3 | OS (Docker) | `tests/validation/test_sandbox.py` (marker `docker`) | P0-08 | ☐ |
| INV-37 | Cổng fail closed; mọi `GateResult` đều vào ledger | §3.2 | Code | `tests/validation/test_gates.py::test_exception_rejects_and_logs`, `::test_passes_are_logged` | P0-06 | ✅ |
| INV-38 | Cổng ③ loại khi `< min_trades`, `< min_holding_bars`, indicator có abs(ρ) > max | §3.2 ③, D15 | Code | `tests/validation/test_gates.py::test_g3_constraints` | P0-12 | ☐ |

## Thống kê & danh mục

| ID | Bất biến | Kiến trúc | Cơ chế | Test | Task | ✓ |
|---|---|---|---|---|---|---|
| INV-40 | DSR khớp ví dụ của Bailey & López de Prado (2014) | §6, 07 §4 | Code | `tests/validation/test_statistical.py::test_dsr_reference_example` | P1-02 | ☐ |
| INV-41 | PBO ≈ 0.5 trên nhiễu, → 0 khi cài sẵn edge; khớp Bailey et al. (2017) | §3.2 | Code | `tests/validation/test_statistical.py::test_pbo_noise`, `::test_pbo_planted_edge`, `::test_pbo_reference_example` | P1-03 | ☐ |
| INV-42 | DSR chỉ tính trên danh mục hợp nhất; đầu vào từ `trial_stats` + `portfolio_variants` | §3.1.6, §4.1 | Code (không có API theo ô) | `tests/validation/test_portfolio_dsr.py::test_more_trials_lower_dsr` | P1-08 | ☐ |
| INV-43 | Chỉ cấu hình đã tới ③ mới tính là trial; sự kiện audit thì không | §4.1 | Code | `tests/ledger/test_db.py::test_trial_stats_ignores_audit_log` | P0-02 | ✅ |
| INV-44 | Các lần đánh giá khi hiệu chỉnh là trial (`source='param_opt'`) | §3.2.1 5b, §4.1 | Code | `tests/validation/test_calibration.py::test_every_eval_is_a_trial` | P1-11 | ☐ |
| INV-45 | Mọi thay đổi quy tắc danh mục ⇒ một dòng `portfolio_variants` mới, được tính vào `N` | §3.2.1 | Code | `tests/validation/test_portfolio.py::test_rule_change_is_new_variant` | P1-07 | ☐ |
| INV-46 | Metric `private` không bao giờ vào prompt; `feedback` chỉ phụ thuộc `public` | §3.3.2 | Code (+ quét log prompt ở GĐ 2) | `tests/validation/test_report.py::test_feedback_ignores_private` | P0-06 | ✅ |
| INV-47 | Đường cong IS→OOS dùng các path CPCV, không bao giờ dùng holdout | §3.2 | Code + Lint | `tests/validation/test_cpcv.py::test_oos_curve_is_private` | P1-04 | ☐ |

## GĐ 2+ (điền khi giai đoạn được chia thành task)

- Không key `private` nào xuất hiện trong prompt đã log (quét chuỗi trên log prompt) — §3.3.2.
- Không gọi optimizer bên trong vòng tiến hóa — §3.3.1.
- Tỷ lệ ngân sách engine được cưỡng chế trên các trial thống kê — §3.1.11.
- Biên bin của feature map cố định theo campaign — §3.1.10.
- Ngưỡng của cổng drift ⓪ được áp dụng đúng như đã khóa — §3.1.7.
