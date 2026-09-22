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

## Sửa đổi — lần chạy dữ liệu thật đầu tiên (2026-09-22)

- **Lần thử, lần lỗi, trial.** Calibration của RSI momentum có 50 lần thử Optuna: 47 lần đo được ở ③ (47 trial `param_opt`) và 3 lần lỗi trong sandbox trước khi đo được gì (`stop_distance = NaN` trong giai đoạn khởi động chỉ báo). Vậy "ngân sách B ⇒ B + 1 dòng" chỉ đúng khi không có lần thử nào lỗi: B lần thử = trial đo được + lần lỗi, cộng một trial xác nhận. `CalibrationResult` nay liệt kê mọi `Attempt` (id, tham số, kết cục `passed` / `rejected` / `error`, trial id, Sharpe, lý do), và một sự kiện audit `CALIBRATION_FINISHED` ghi đủ cả B lần kèm ba con số đếm.
- **Không Sharpe giả.** Một lần lỗi không có returns và không có Sharpe, nên không có dòng `trials` (§4.1 đòi cả hai). Optuna nhận `FAILED_SCORE` để định hướng tìm kiếm; con số đó không bao giờ được lưu như một Sharpe.
- **Truy vết lần chạy thật** (chạy trước khi có sự kiện này): cả 50 id lần thử `…-opt000` tới `…-opt049` đều có trong `gate_results` ở ③ — 47 có trial id, 3 không có và kèm lỗi — và trong 50 sự kiện audit `CANDIDATE_SUBMITTED`.
- **Ảnh hưởng lên N và V[SR], đo chứ không giả định.** V[SR] không thể chứa một lần lỗi (không có giá trị). N hiện không tính nó (§4.1). Nếu đếm 3 lần lỗi vào N, mỗi lần một cụm riêng, hai biến thể của campaign đó sẽ đổi như sau: RSI `778e…` DSR 0,999 → 0,998 tại N_eff (4 → 7), 0,986 → 0,985 tại N_raw; SMA `d7bd…` **0,963 → 0,936** tại N_eff — dưới `dsr_min` — và 0,792 → 0,788 tại N_raw. Quy tắc đếm vì vậy có thể đảo kết quả gate ⑤; quy ước đếm đã được chốt ở [ADR-0022](0022-quy-uoc-dem-lan-thu-khong-do-duoc-gi.md) (O17): không tính, báo cáo như một phép kiểm độ nhạy.
- Test: `tests/validation/test_calibration.py::test_every_attempt_is_accounted_for`.

## Sửa đổi 2 — review commit b8dc07a

- **Một lần, với ngân sách đã khóa, do ledger cưỡng chế** (MEDIUM). Không gì ngăn một lần `portfolio --calibrate` thứ hai: với ngân sách 1, chạy lại đưa N_raw từ 2 lên 4 và dùng lại id lần thử `x-opt000` — tìm thêm sau khi đã thấy kết quả. Schema v4 thêm `calibration_runs` (campaign, ứng viên, strategy hash, ngân sách; mỗi ứng viên và mỗi code chiến lược một lần trong một campaign; chỉ campaign OPEN) và `calibration_finishes` (kết cục `finished` hoặc `stopped`, số lần thử ≤ ngân sách, trial, lỗi), cả hai append-only. Một lượt được đăng ký trước lần thử đầu tiên, kèm sự kiện audit `CALIBRATION_STARTED`; `calibrate_members` kiểm mọi thành viên trước khi chạy bất kỳ ai.
- **Retry kỹ thuật và tối ưu thêm — một quy tắc.** Một lượt đã đăng ký chỉ được chạy lại nếu nó chưa từng kết thúc *và* chưa ghi kết quả lần thử nào (chưa đo gì, nên chưa thấy gì): cùng ứng viên, code và ngân sách, được đánh dấu `technical_retry` trong sự kiện audit. Mọi trường hợp khác bị từ chối (`CalibrationRefused`): lượt đã kết thúc hoặc đã dừng, ngân sách khác, hoặc lượt bị ngắt khi đã ghi kết quả lần thử — chạy tiếp sẽ tìm vượt ngân sách sau khi đã thấy kết quả; người quyết định.
- **Chuyển các lượt cũ sang.** Migration đăng ký các lượt ghi trước v4 từ sự kiện `CALIBRATION_FINISHED` hoặc, cũ hơn, từ các dòng của chúng (trial của run `calib-<id>`, mỗi lần thử một kết quả ③). Lượt calibration RSI của campaign thật thành một lượt đã kết thúc với 50 lần thử (47 trial + 3 lỗi); N không đổi (đã kiểm trên bản sao ledger).
- **Tìm xong ≠ calibration thành công** (người dùng quyết định). Ngân sách bị khóa ngay khi việc tìm kết thúc: Optuna không bao giờ chạy lại cho chiến lược đó. Bước xác nhận được theo dõi riêng (`CALIBRATION_CONFIRMATION`: `passed`, `rejected`, `not_run` khi không lần thử nào qua ③, `error`; đã bắt đầu mà không có sự kiện này nghĩa là `incomplete`), và lời từ chối hay lỗi nói rõ "đã dùng hết ngân sách; xác nhận lỗi hoặc chưa hoàn tất — calibration KHÔNG thành công" thay vì "đã calibrate". Chạy lại riêng bước xác nhận hiện bị từ chối; nếu sau này hỗ trợ, nó chỉ đo bộ tham số đã lưu và mọi lần đo đều là trial theo quy tắc thường. Một bước xác nhận chết sau ③ vẫn giữ trial ③ của nó trong `N`.
- Test: `tests/validation/test_calibration.py::test_a_rerun_reports_the_confirmation`, `::test_a_crashed_confirmation_is_not_reported_as_success`, `::test_a_failed_confirmation_says_so`, `::test_a_second_calibration_is_refused`, `::test_the_same_code_under_another_id_is_refused`, `::test_a_technical_retry_is_allowed_only_if_nothing_was_measured`, `::test_an_interrupted_search_is_not_resumed`, `::test_a_stopped_calibration_is_not_rerun`; `tests/ledger/test_db.py::test_calibration_runs_once_per_strategy`, `::test_v3_calibrations_are_carried_over`, `::test_older_calibrations_are_carried_over_from_their_rows`.
