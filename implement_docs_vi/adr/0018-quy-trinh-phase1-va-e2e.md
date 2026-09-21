# ADR-0018 — Quy trình phase 1, CLI và test end-to-end

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-12 · **Kiến trúc:** §3.2, §3.2.1, §4.2, §7 phase 1 · **Vấn đề mở:** O16 (mở mới)

## Bối cảnh

P1-01…P1-11 xây từng gate và từng bước riêng lẻ. Cổng phase (§7) đòi toàn bộ đường đi: một chiến lược viết tay qua mọi gate, một danh mục qua ⑤ → ⑥′, calibration, đóng băng và một lần mở holdout, với ledger tách audit khỏi trial và tính được `N_eff` + `V[SR]`. Còn mở: phần điều phối nằm ở đâu, CLI đưa ra những gì, và test end-to-end chạy trên dữ liệu nào.

## Quyết định

1. **`validation/research_run.py`** chứa quy trình trước đóng băng: `ResearchSession` (ledger, lock, dữ liệu IS của cả hai nguồn, sandbox, thư mục kết quả → `GateContext`), `submit` (①a → ④), `evaluate_portfolio` (xây theo quy tắc đã khóa, thêm dòng biến thể nếu mới, rồi ⑤ → ⑥′), `calibrate_members` (5b cho mỗi thành viên từ source đã lưu, chỉ khi lock bật) và `run_phase1` (nộp tất cả → xây → calibrate → xây lại). Nó không thêm logic gate và không có đường vòng: mọi backtest vẫn đi qua một pipeline và ledger (P2).
2. **Zoo:** `core/zoo/sma_trend.py` (giá trên SMA) và `core/zoo/rsi_momentum.py` (RSI trên một ngưỡng) đứng cạnh `ema_crossover.py` — các thể hiện template hợp lệ với cùng vùng cố định (test INV-30 phủ cả ba).
3. **CLI:** `validate FILE` giờ chạy ①a → ④ (`candidate_pipeline`); `portfolio [--calibrate]` xây và chạy ⑤ → ⑥′ (từ chối nếu thiếu dữ liệu nguồn thứ hai từ `data-fetch-second`); `freeze HASH` chuyển OPEN → FROZEN. CLI không bao giờ import `quantcrucible.holdout`; holdout chỉ được mở bởi `python -m quantcrucible.holdout.evaluator_proc --portfolio HASH` (P6).
4. **Test end-to-end** `tests/e2e/test_phase1.py` (docker, slow) chạy **chỉ trên dữ liệu tổng hợp**: một thị trường có xu hướng đổi chế độ (`make_trending_bars`, để một chiến lược theo xu hướng *có thể* qua), một "nhà cung cấp" thứ hai in cùng thị trường với nhiễu giá 0,1 % (`second_vendor`), và một holdout tổng hợp cắt trong thư mục tạm. Lưới được giữ nhỏ (3 giá trị mỗi tham số, 10 cấu hình) và ngân sách calibration là 3, chỉ vì thời gian chạy; mọi ngưỡng là mặc định đã khóa.

## Hệ quả

- Test cổng phase 1: `test_ema_crossover_clears_every_candidate_gate`, `test_portfolio_clears_5_and_6p` (DSR tại `N_eff` ≥ 0,95, có báo `N_raw`), `test_ledger_separates_audit_from_trials`, `test_calibration_rows_and_new_variant`, `test_freeze_then_one_holdout_opening` (một token, campaign BURNED).
- Qua được trên dữ liệu tổng hợp cho thấy harness *để lọt một lợi thế thật*; nó không nói gì về thị trường thật. Chưa có lần chạy phase 1 nào trên dữ liệu thật.
- **O16:** campaign mở trên dữ liệu thật ở phase 0 có trước `derived.sizing`, nên gate ③ từ chối lock của nó; không thể đóng nó mà không mở holdout, và chưa có chuyển trạng thái "bỏ" (abandon). Một lần chạy phase 1 trên dữ liệu thật cần người dùng quyết định (xem 05).

## Phương án đã cân nhắc

- **Điều phối nằm trong CLI:** loại — test e2e khi đó sẽ kiểm việc phân tích tham số dòng lệnh thay vì quy trình, và vòng lặp agent của phase 2 cần cùng các hàm này.
- **E2E trên dữ liệu nghiên cứu thật:** loại — chậm, cần mạng, và việc một chiến lược viết tay có qua trên thị trường thật hay không là một kết quả nghiên cứu, không phải một tính chất của harness.
