# ADR-0017 — Calibration: mỗi lần đánh giá là một trial

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-11 · **Kiến trúc:** §3.2.1 bước 5b, §3.3.1 (tắt optimizer trong vòng lặp), §4.1 ("trial ẩn") · **Vấn đề mở:** —

## Bối cảnh

Bước 5b chạy Optuna một lần, trước khi đóng băng, cho mỗi chiến lược được chọn với ngân sách cố định; mọi lần đánh giá phải là một dòng `trials` với `source='param_opt'`, và PBO cùng DSR phải được tính lại sau đó. Còn mở: một lần đánh giá đi qua những gate nào, chiến lược đã calibrate thay chiến lược gốc trong danh mục ra sao, và quy tắc "không optimizer trong vòng lặp" được cưỡng chế thế nào.

## Quyết định

1. **`optuna>=4.5,<5`** (MIT; các dependency của nó là MIT/BSD, tqdm MPL-2.0 + MIT). Sampler TPE có seed; TUNABLE kiểu nguyên được lấy mẫu là số nguyên, trong bounds đã khai báo.
2. **Mỗi lần đánh giá** chạy `GatePipeline([InSampleGate()])` — thẳng tới gate ③ — với `candidate_id = <gốc>-optNNN` và `trial_source='param_opt'`. Bỏ qua ①a/①b/② là có chủ ý: code là của bản gốc, đã được kiểm; chạy ② cho từng lần đánh giá có thể loại một lần *trước khi* nó được đo và như vậy làm ẩn một trial. Các lần đánh giá bị loại ở ③ vẫn là trial và nhận điểm −10.
3. **Xác nhận:** bộ tham số tốt nhất đã qua ③ đi qua toàn bộ pipeline ứng viên ①a → ④ **dưới `candidate_id` gốc** (thêm một trial `param_opt`). Gate ④ tính lại PBO với các dòng calibration nằm trong biến thể ledger của nó (cùng `strategy_hash`). Vì điều kiện vào danh mục lấy trial mới nhất của mỗi ứng viên và đòi *chính* trial đó đã qua ④ (sửa đổi ADR-0013), trial đã calibrate thay bản gốc ở lần xây kế tiếp — hoặc ứng viên bị loại nếu bước xác nhận trượt ③ hoặc ④; không quay về trial gốc. Gate ⑤ của lần xây đó tính lại DSR.
4. **Cưỡng chế:** `tests/test_architecture_boundaries.py::test_parameter_optimizer_only_in_calibration` — optuna (và hyperopt, skopt, nevergrad, ray) chỉ được import bởi `validation/calibration.py`.

## Hệ quả

- INV-44: `tests/validation/test_calibration.py::test_every_eval_is_a_trial` (ngân sách B ⇒ B + 1 dòng `param_opt`).
- Calibration tăng `N` thêm B + 1 cho mỗi chiến lược; với ngân sách mặc định 50 và ~20 thành viên là ~1 000 trial — DSR trả giá cho điều đó, đúng ý §3.2.1.
- Phần điều phối (calibrate từng thành viên, xây lại, chạy lại ⑤–⑥′) được nối ở P1-12.

## Phương án đã cân nhắc

- **Các lần đánh giá đi qua toàn bộ pipeline:** loại — ② có thể làm ẩn một cấu hình đã đo; ④ cho mỗi lần đánh giá tốn M lần backtest mỗi lần.
- **Chiến lược đã calibrate dưới một candidate id mới:** loại — bản gốc và bản đã calibrate sẽ cạnh tranh trong danh mục như hai chiến lược.

## Sửa đổi — review commit d765668

- Bước xác nhận dùng lại `candidate_id` gốc, nên trước đây nó ghi đè file returns của trial gốc (lỗi 1). Nay artifact được ghi một lần cho mỗi lần đo (sửa đổi ADR-0013); returns của trial gốc giữ nguyên như lúc đo.
- **Chính sách khi bước xác nhận thất bại:** ứng viên rời danh mục ở lần xây kế tiếp. Quay về trial trước calibration sẽ là chọn, sau khi đã biết kết quả, lần đo nào trông tốt hơn trong hai lần — một bước chọn ẩn.
