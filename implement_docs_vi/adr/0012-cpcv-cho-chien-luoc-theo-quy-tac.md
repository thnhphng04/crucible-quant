# ADR-0012 — CPCV cho chiến lược theo quy tắc

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-04 · **Kiến trúc:** §3.2 (④, đường cong suy giảm IS→OOS), 07-VALIDATION-LAYER §3, §4.1, §3.3.2 · **Vấn đề mở:** —

## Bối cảnh

CPCV (López de Prado, AFML ch. 12) fit một mô hình trên tập train đã purge của từng fold rồi dự đoán tập test. Chiến lược của ta là quy tắc tất định: không có gì được fit, nên CPCV trên một cấu hình cố định chỉ là cắt lát chuỗi lợi nhuận IS của nó. Thứ *thực sự* được fit là việc chọn tham số. Kiến trúc muốn Sharpe trung vị CPCV-OOS là metric private và là đường OOS của đường cong suy giảm, mà không dùng holdout.

## Quyết định

1. **Việc chọn chính là "fit":** trong mỗi fold, cấu hình có Sharpe tốt nhất trên tập train được chọn từ ma trận `(M × T)` của gate ④ (đúng quy tắc của vòng lặp, như PBO) và lợi nhuận của nó trên các block test được giữ lại. Không backtest thêm.
2. **Cách chia** từ `purgedcv.CombinatorialPurgedCV` (N = 6 nhóm, k = 2 ⇒ 15 fold, 5 path; 07 §4.1): quan sát *i* = lợi nhuận của bar *i*; nhãn của nó trải `horizon_bars` = ⌈thời gian giữ lệnh trung bình của ứng viên⌉ (≥ 1); các dòng train có khoảng trải chồng lên khoảng của test bị purge (cả hai phía); embargo = ⌊1% · T⌋ dòng sau mỗi block test. Các path được dựng bằng `purgedcv.reconstruct_paths`.
3. **Metric private** từ gate ④: `cpcv_oos_median_sharpe`, `cpcv_oos_negative_share`, `cpcv_n_paths` (Sharpe annualize); Sharpe từng path và lựa chọn từng fold nằm trong `gate_results.detail`.
4. **Đường cong suy giảm:** `is_oos_diverging(points)` báo hiệu khi kỷ lục IS tăng (hệ số OLS > 0) trong khi trung vị CPCV-OOS của cùng các chiến lược đi ngang hoặc giảm, trên ≥ 3 mốc. Việc ghi mốc theo từng engine và dừng engine là việc của vòng lặp GĐ 2.
5. Một fold không còn dữ liệu train sau purge/embargo sẽ báo lỗi ⇒ gate ④ fail closed.

## Hệ quả

- INV-47: `test_oos_curve_is_private`; purge/embargo được ghim trên một fixture 12 bar dựng tay (`test_purge_removes_rows_whose_label_overlaps_the_test_block`, `test_embargo_drops_rows_after_each_test_block`).
- CPCV không tốn thêm thời gian sandbox; nó dùng lại ma trận lưới. Chất lượng của nó phụ thuộc tập cấu hình (O15).

## Phương án đã cân nhắc

- **CPCV trên cấu hình duy nhất của ứng viên:** loại — bằng với cắt lát lợi nhuận IS; không đo được gì về việc chọn.
- **Chỉ walk-forward:** loại — một path OOS, đúng vấn đề mà CPCV sinh ra để giải (07 §4.1).
- **Chỉ purge phía trước block test:** loại — nhãn chồng lấn phía sau block cũng rò rỉ (AFML §7.4.1).
