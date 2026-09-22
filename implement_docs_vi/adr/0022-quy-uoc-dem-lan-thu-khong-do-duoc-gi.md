# ADR-0022 — Quy ước đếm: các lần thử không đo được gì

- **Trạng thái:** Đã chấp nhận (người dùng quyết định)
- **Ngày:** 2026-09-22
- **Task:** sau lần chạy dữ liệu thật đầu tiên · **Kiến trúc:** §4.1 (trial = cấu hình đã đo), §3.2.1 5b · **Vấn đề mở:** đóng O17

## Bối cảnh

Ba trong 50 lần thử calibration của RSI momentum lỗi trong sandbox trước khi đo được gì (sửa đổi ADR-0017). Nếu đếm chúng vào `N`, mỗi lần một cụm riêng, gate ⑤ của SMA sẽ đổi từ 0,963 thành 0,936 — dưới `dsr_min`. Câu hỏi là những lần thử như vậy có thuộc về `N` không.

## Quyết định

1. **Không phải trial:** một lần thử lỗi trước khi tạo ra bất kỳ đầu ra hiệu suất nào — không returns, không Sharpe, không gì để người dùng hay optimizer lựa chọn — không phải trial thống kê. Nó không nằm trong `N`, không nằm trong V[SR], và không bao giờ được gán Sharpe bằng 0 hay giá trị bịa nào khác.
2. **Đã đo rồi bị loại vẫn tính:** một cấu hình đã đo được hiệu suất là trial dù gate nào loại nó sau đó (không đổi).
3. **Lỗi sau khi có đầu ra được phân loại riêng:** một lần thử đã lộ ra một con số hiệu suất nhưng không ghi được phép đo là `error_after_output`. Nó không bao giờ được tự động miễn tính: calibration dừng lại và người dùng quyết định cách đếm.
4. **Luôn truy vết được:** mọi lần thử đều có trong `gate_results` và trong sự kiện audit `CALIBRATION_FINISHED`.
5. **Độ nhạy, có báo cáo, không quyết định:** gate ⑤ và ⑥′ còn báo DSR mà chúng sẽ cho nếu mỗi lần chạy ③ không đo được gì (trên toàn ledger) là thêm một trial trong cụm riêng. Không có căn cứ để coi ba lỗi warm-up là ba thử nghiệm độc lập, nên đây vẫn là phép kiểm tra, không phải quy tắc.
6. **Một quy ước, không phải chứng minh:** Optuna nhận `FAILED_SCORE` cho lần thử lỗi, nên lỗi có ảnh hưởng tới các đề xuất tiếp theo. Loại nó khỏi `N` là quy ước đếm của dự án; nó không khẳng định lỗi hoàn toàn không ảnh hưởng quá trình lựa chọn.

## Hệ quả

- Lần chạy thật giữ nguyên như đã ghi: N_raw = 52 trial; ba lỗi warm-up không được cộng thêm.
- Test: `tests/validation/test_portfolio_dsr.py::test_unmeasured_attempts_are_a_sensitivity_not_a_count`, `tests/validation/test_calibration.py::test_an_error_after_output_is_not_silently_exempt`, `::test_every_attempt_is_accounted_for`.

## Phương án đã cân nhắc

- **Đếm mọi lần thử vào `N`:** loại — một lần thử không có đầu ra không đưa ra gì để chọn, và coi mỗi lần là một thử nghiệm độc lập không có căn cứ.
- **Miễn tính mọi lỗi:** loại — một lỗi phát sinh sau khi đã hiện một con số có thể che giấu một bước chọn.
