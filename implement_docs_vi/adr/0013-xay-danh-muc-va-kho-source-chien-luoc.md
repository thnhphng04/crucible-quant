# ADR-0013 — Xây danh mục và kho source chiến lược

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-07 · **Kiến trúc:** §3.2.1 bước 1–6, D9, §3.1.6, §4.1 (`portfolio_variants`) · **Vấn đề mở:** O15 (một phần)

## Bối cảnh

§3.2.1 cố định quy tắc (đại diện ô theo DSR-rank, lọc |ρ|, giới hạn K, risk parity đơn giản, rebalance theo lịch, hash + dòng biến thể) nhưng để ngỏ: "DSR-rank" là gì khi DSR chỉ được tính trên danh mục (§3.1.6), trial nào đại diện cho một ứng viên, lợi nhuận được gộp ra sao, cái gì đi vào hash, và khi nào xây lại là một biến thể mới. Các bước sau (⑥′, holdout) còn phải chạy lại các thành viên, mà ledger chỉ giữ `strategy_hash`.

## Quyết định

1. **Đủ điều kiện** = trial mới nhất của mọi ứng viên có kết quả gate ④ mới nhất trong campaign là pass.
2. **Điểm chọn** ("DSR-rank") = PSR của lợi nhuận IS của trial so với benchmark đã khử lạm phát *chung của campaign* SR₀(`N_eff`, `V[SR]`) — một benchmark cho tất cả, nên đây là một cách xếp hạng phạt các chuỗi ngắn, lệch hoặc đuôi dày; nó không bao giờ được so với `dsr_min` (không tồn tại gate DSR theo từng chiến lược).
3. **Các bước:** một đại diện cho mỗi `cell_id` (không có ô ⇒ ô riêng); |ρ| của lợi nhuận IS trên các ngày chung so với mọi thành viên đã nhận, theo thứ tự điểm (NaN ⇒ bị loại); giới hạn `max_strategies`; trọng số w ∝ 1/σ (tổng 1, không đòn bẩy); vị thế đặt lại về trọng số ở mỗi mốc `rebalance` và trôi tự do ở giữa.
4. **`portfolio_hash`** = SHA256 của JSON chuẩn tắc: các thành viên sắp theo `strategy_hash` kèm params và trọng số (làm tròn 1e-10) + quy tắc (`max_corr`, `max_strategies`, `rebalance`, cách chọn, cách đặt trọng số).
5. **Biến thể:** mỗi hash khác nhau là một dòng `portfolio_variants` kèm lợi nhuận IS dưới `results/portfolios/`; xây lại một danh mục giống hệt không phải là một lần chọn mới và không được ghi lần nữa.
6. **Kho source chiến lược** (`validation/archive.py`): `GatePipeline.run` lưu mọi source được nộp tại `results/strategies/<strategy_hash>.py`; khi đọc sẽ kiểm tra lại hash. Việc này cũng gỡ vướng mắc về source của O15 cho vòng lặp GĐ 2.

## Hệ quả

- INV-45: `test_rule_change_is_new_variant`; mỗi bước có test riêng trong `tests/validation/test_portfolio.py`.
- Hàm đọc ledger mới: `portfolio_variants()`, `holdout_access()`, `gate_result_details()`.
- Điểm chọn phụ thuộc `trial_stats` tại thời điểm xây; xây lại sau khi có thêm trial có thể đổi thứ tự — và khi đó là một biến thể mới, được đếm vào `N`.

## Phương án đã cân nhắc

- **Xếp hạng theo Sharpe IS thô:** loại — bỏ qua độ dài chuỗi và các moment bậc cao mà DSR hiệu chỉnh.
- **Dùng DSR từng chiến lược so với `dsr_min` làm bộ lọc:** loại — §3.1.6 cấm DSR theo ô/chiến lược.
- **Lưu source trong ledger:** tạm loại — là thay đổi schema; kho source giữ nguyên schema ledger và kiểm tra nội dung bằng hash.
