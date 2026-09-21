# ADR-0006 — Gate ② MinBTL và ③ backtest in-sample

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P0-12 · **Kiến trúc:** §3.2 (②, ③), §3.3.1 quy tắc 4, §4.1, D15, D17 · **Vấn đề mở:** —

## Bối cảnh

Gate ② và ③ khép lại GĐ 0. Kiến trúc cố định những gì chúng kiểm tra (MinBTL từ `N` đang chạy; số lệnh tối thiểu, thời gian giữ tối thiểu và tương quan chỉ báo tối đa ở ③; một dòng `trials` từ ③ trở đi) nhưng không nói `N` nào, độ dài lịch sử nào, "tương quan chỉ báo" đo thế nào, chi phí lấy từ đâu, hay pipeline GĐ 0 được khởi động ra sao ngoài test.

## Quyết định

1. **MinBTL** (`validation/statistical.py`): Bailey, Borwein, López de Prado & Zhu (2014), `E[max_N] = (1−γ)Z⁻¹[1−1/N] + γZ⁻¹[1−1/(Ne)]`, `MinBTL = (E[max_N] / target)²` năm; bằng 0 khi `N ≤ 1`. Test cố định các ví dụ của bài báo (N = 7 → ≈ 2 năm, N = 45 → ≈ 5 năm với target 1).
2. **Gate ②** dùng `N = trial_stats.n_eff + 1` (mọi trial đến nay, mọi campaign, là N_raw cho đến khi chạy phân cụm, cộng ứng viên này) và Sharpe mục tiêu từ lock (`minbtl_target_sharpe`, D17). Độ dài khả dụng là khoảng thời gian của chuỗi **ngắn nhất** trong universe của ứng viên.
3. **Gate ③** chạy một job `backtest` trong sandbox với chi phí và lookback ghi trong phần `derived` của lock khi mở campaign (mặc định `CostModel`, lookback 400). Các metric public của nó thành `EvaluationReport`; lợi nhuận từng bar vào `results/returns/<campaign>/<candidate>.parquet` và đường dẫn vào dòng `trials`. Dòng được ghi bất kể kết quả; sandbox bị crash thì không đo được gì và không ghi dòng (dòng `gate_results` của nó ghi lại việc đó).
4. **Tương quan chỉ báo được đo trên thay đổi giữa các bar**, không phải mức giá, trên mọi cặp chuỗi do `indicators()` trả về, theo từng mã, và |ρ| lớn nhất được so với `max_indicator_corr`. Mức của hai chỉ báo bám giá bất kỳ tương quan gần 1 (EMA20 và EMA100 trên BTC: 0,99), điều này sẽ bác bỏ mọi strategy nhiều chỉ báo mà chẳng nói gì về sự trùng lặp; còn thay đổi của chúng thì không (0,75).
5. **P2 bằng cấu trúc:** một contract import-linter cấm `quantcrucible.agent` import trực tiếp `execution`, sandbox hay leak check, nên agent chỉ có thể tới backtest qua `GatePipeline`, nơi ghi ledger. `execution` không dính đến ledger để đường live bằng đường backtest (P5).
6. **`quantcrucible.cli validate FILE`** chạy một strategy qua ①a → ①b → ② → ③. Nó tiếp tục campaign đứng sau `config/evaluation.lock.yaml` (sau `assert_lock_matches`), hoặc mở mới: khoảng và hash holdout lấy từ `holdout.lock`, và bar IS từ `ResearchStore`, vốn từ chối khoảng holdout.

## Hệ quả

- Cổng GĐ 0 được phủ đầu-cuối bởi `tests/e2e/test_phase0.py` (docker, slow): bốn oracle bị bác bỏ ở ①a (và ở ①b khi bỏ qua ①a) mà không thành trial; EMA crossover qua cả bốn gate và là trial #1.
- Lần chạy thật đầu tiên (2026-09-21, dữ liệu IS ngày của Binance, 2018-01 → 2025-09) mở campaign `c-20260921-104445`; EMA crossover đạt với Sharpe IS 1,07 qua 206 lệnh. Lợi nhuận không phải tiêu chí của GĐ 0.
- Cách thực thi INV-01 giờ là "pipeline + contract import", không phải "runner yêu cầu ledger handle".

## Phương án đã cân nhắc

- **Truyền ledger handle vào `run_backtest`:** bác bỏ — sẽ buộc đường thực thi live vào ledger nghiên cứu (P5).
- **Tương quan trên mức:** bác bỏ (xem 4). **Spearman trên mức:** cùng vấn đề.
- **Chuỗi dài nhất hoặc khoảng hợp cho ②:** bác bỏ — chuỗi ngắn nhất là lựa chọn thận trọng.
