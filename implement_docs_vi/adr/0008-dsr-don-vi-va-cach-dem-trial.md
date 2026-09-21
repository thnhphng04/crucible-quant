# ADR-0008 — DSR: đơn vị, moment và những gì được đếm vào `N`

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-02 · **Kiến trúc:** §3.1.6, §3.2 dòng ⑤, §4.1, 07-VALIDATION-LAYER §4.3 · **Vấn đề mở:** —

## Bối cảnh

Kiến trúc cố định đầu vào của DSR (`N_eff` + `V[SR]` từ `trials`, cộng `portfolio_variants`, trên danh mục hợp nhất) nhưng không nói đơn vị hay quy ước. `trials.sharpe_is` được annualize (engine, ADR-0006) trong khi công thức DSR tính theo từng quan sát, và Sharpe mẫu, skew, kurtosis đều có hơn một định nghĩa trong sách.

## Quyết định

1. **Đơn vị theo từng quan sát bên trong công thức.** `portfolio_dsr` đổi `V[SR]` annualize của ledger bằng `var_sr / periods_per_year` (chính xác, vì SR năm = SR mỗi quan sát · √periods).
2. **Moment theo quy ước của `purgedcv`** (ADR-0007): SR dùng độ lệch chuẩn tổng thể (`ddof=0`), skew đã hiệu chỉnh bias, kurtosis không-excess đã hiệu chỉnh bias; một test ghim PSR và DSR bằng `purgedcv` tới 1e-12.
3. **`N` = trials + biến thể danh mục**, cho cả hai cách đếm: `n_eff = trial_stats.n_eff + n_variants`, `n_raw = trial_stats.n_raw + n_variants`. `trial_stats` bao mọi campaign, engine và verdict.
4. **Luôn báo cả hai cách đếm** (`DsrReport.dsr_n_eff`, `.dsr_n_raw`, kèm benchmark). Gate ⑤ so cái nào với `dsr_min` sẽ quyết ở P1-08.
5. **Không có điểm vào theo chiến lược/theo ô.** Các hàm public nhận moment hoặc một chuỗi lợi nhuận danh mục; `portfolio_dsr` là hàm duy nhất đọc `TrialStats` (INV-42).

## Hệ quả

- Cưỡng chế INV-40: `test_dsr_reference_example` tái lập Bailey & López de Prado (2014): SR₀ ≈ 1.7894 annualize, DSR ≈ 0.9004.
- `validation/statistical.py` giờ import `ledger.records` (được phép: validation nằm trên ledger).
- `var_sr` âm do nhiễu số thực của SQL được kẹp về 0.

## Phương án đã cân nhắc

- **Lưu Sharpe theo từng quan sát trong `trials`:** loại — sẽ đổi schema của GĐ 0 và metric public mà prompt nhìn thấy.
- **Độ lệch chuẩn mẫu (`ddof=1`) như Sharpe của engine:** loại — chênh lệch cỡ O(1/T), và khớp `purgedcv` giữ cho phép đối chiếu chính xác.
- **Chỉ cộng biến thể vào `N_raw`:** loại — một biến thể danh mục là một lần chọn, dù phân cụm nói gì.
