# ADR-0020 — D4: `holdout_pass` đo cái gì, và giá trị của nó

- **Trạng thái:** Đã chấp nhận (người dùng quyết định)
- **Ngày:** 2026-09-21
- **Task:** trước campaign kế tiếp · **Kiến trúc:** §10 D4, §4.2 bước 3, §10.1 · **Vấn đề mở:** đóng O1 (phần holdout của D4)

## Bối cảnh

Bộ đánh giá holdout so một con số với `research.holdout_pass` và in ra PASS hoặc FAIL (ADR-0016). Tới nay D4 vẫn mở và ADR-0016 chỉ *giả định* con số đó là tỷ số Sharpe. Ngưỡng thuộc Nhóm B, nên được khóa cùng campaign và phải cố định trước khi phần nghiên cứu của campaign đó bắt đầu.

## Quyết định

1. **Định nghĩa:** `holdout_pass` là **tỷ số Sharpe ngoài mẫu quy năm tối thiểu của toàn bộ danh mục đã đóng băng**, đo trên holdout, **sau phí và trượt giá**, với **lợi nhuận tham chiếu bằng 0**. PASS khi và chỉ khi Sharpe đó ≥ `holdout_pass`.
2. **Cách tính** (`holdout/evaluator_proc.py`, không đổi): mỗi thành viên chạy lại trong sandbox với `derived.costs` đã khóa của campaign; gộp theo trọng số và quy tắc rebalance đã đóng băng; chỉ các bar đóng trong khoảng holdout; `mean / std(ddof=1) · √(số kỳ mỗi năm)` (365 cho crypto 1d).
3. **Giá trị: 1,3** cho campaign kế tiếp, đặt trong `config/user.yaml`. Người dùng chọn nó làm ngưỡng chấp nhận ban đầu; nó **không** phải giá trị đã được test tổng hợp chứng minh là phù hợp (0,5 trong test chỉ là dữ liệu mẫu). Mặc định của schema vẫn là `None`, nên một cấu hình bỏ trống khóa này không bao giờ mở được campaign.
4. **Khóa:** ghi vào lock của campaign khi campaign mở; đổi giữa campaign bị từ chối, và bộ đánh giá đọc lock, không đọc `user.yaml`. Không bao giờ điều chỉnh sau khi xem kết quả holdout — holdout đã dùng không bao giờ được dùng lại (sửa đổi ADR-0016), nên một ngưỡng khác chỉ có thể áp dụng cho campaign sau, trên dữ liệu mới.
5. **Ý nghĩa của PASS:** danh mục đã đóng băng vượt ngưỡng này trên holdout — không hơn. Nó **không** phải là chấp thuận giao dịch tiền thật; tiêu chí cấp vốn cho live vẫn mở trong D4 và cần quyết định riêng trước khi live.

## Hệ quả

- Test: `tests/holdout/test_evaluator.py::test_d4_is_the_frozen_portfolios_annualized_sharpe` (Sharpe tính tay từ returns thành viên đã biết; các job mang chi phí đã khóa, khác 0), `::test_d4_pass_means_at_least_the_threshold`, `::test_d4_comes_from_the_campaign_lock_not_user_yaml`; `tests/config/test_lock.py::test_holdout_pass_is_locked_for_the_whole_campaign`.
- Lock của campaign thật mở ở phase 0 có `holdout_pass: null`, nên với 1,3 trong `user.yaml` mọi lệnh trên nó bị từ chối vì đổi Nhóm B — hãy bỏ nó (ADR-0019) và lệnh kế tiếp sẽ mở campaign mới với 1,3 được khóa.
- Holdout 12 tháng là khoảng 365 lợi nhuận ngày: sai số chuẩn của Sharpe quy năm xấp xỉ 1, nên một lợi thế quanh 1,3 qua được nhờ may mắn cũng thường như trượt. Ngưỡng này để lọc; nó không chứng minh.

## Phương án đã cân nhắc

- **Để D4 mở:** không được — campaign mới giờ đòi `holdout_pass` (ADR-0019).
- **Đo trước chi phí, hoặc so với lãi suất phi rủi ro:** bị loại theo định nghĩa của người dùng — sau chi phí, tham chiếu 0.
