# ADR-0016 — Bộ đánh giá holdout và đóng băng campaign

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-10 · **Kiến trúc:** §4.2 (quy trình, 3 lớp), P6, D4, §3.3.3 · **Vấn đề mở:** O1 (D4) vẫn mở

## Bối cảnh

§4.2 cố định quy trình (OPEN → FROZEN → một lần mở → BURNED), `holdout_access` một dòng mỗi campaign, file chỉ-đọc + có hash, và một tiến trình đánh giá riêng trả về một bit. Còn mở: `portfolio_hash` đã đóng băng nằm ở đâu trước lần mở (schema không có bảng freeze), bộ đánh giá chạy code thành viên trên dữ liệu holdout thế nào khi code sinh ra chỉ được chạy trong sandbox và sandbox không bao giờ thấy `holdout/`, `holdout_pass` (D4) được so với cái gì, và CLI đóng băng campaign thế nào khi không gì được import `quantcrucible.holdout`.

## Quyết định

1. **Đóng băng = phía research** (`validation/freeze.py`, gọi được từ CLI): từ chối trừ khi campaign đang OPEN, hash là một dòng `portfolio_variants` của campaign này và kết quả ⑤, ⑥′ mới nhất của nó là pass. Nó chuyển campaign sang FROZEN và ghi một sự kiện audit `CAMPAIGN_FROZEN` mang `portfolio_hash` + `frozen_at`. Từ đó `GatePipeline.run` và `record_variant` từ chối campaign (không thêm, bớt hay đổi trọng số gì).
2. **Bộ đánh giá = phía holdout** (`holdout/evaluator_proc.py`, entry point `-m` riêng, `holdout/campaign.py`): đầu vào chỉ là `--portfolio <hash>`. Kiểm tra trước, trước khi đọc bất kỳ byte holdout nào: campaign FROZEN trên hash này; chưa có dòng `holdout_access`; `frozen_at < now`; evaluation lock khớp hash đã ghi khi mở campaign; `holdout_pass` đã đặt (D4); `holdout.lock` khớp hash đã ghi khi mở campaign; mọi file holdout khớp `holdout.lock`.
3. **Code thành viên chạy trong sandbox** trên một bản sao đoạn holdout đã kiểm, có thêm `lookback` bar in-sample cuối cùng ở phía trước để khởi động chỉ báo, ghi vào thư mục đầu vào của đúng job đó — không bao giờ mount `holdout/`. Chỉ lợi nhuận tại các bar đóng trong khoảng holdout được tính. Image sandbox được build lười, sau bước kiểm tra.
4. **Kết luận:** trọng số và quy tắc rebalance đã đóng băng gộp các thành viên; `PASS` khi và chỉ khi Sharpe OOS theo năm ≥ `research.holdout_pass`. **Đây là cách hiểu D4 là một Sharpe tối thiểu — xác nhận hoặc thay khi D4 được quyết.**
5. **Burn:** chèn `holdout_access` (kèm `sharpe_oos`), ghi `HOLDOUT_OPENED` (không có con số), FROZEN → BURNED; rồi in đúng `PASS` hoặc `FAIL`. Từ chối đi ra stderr với exit 2; lỗi khác chỉ in tên loại lỗi (exit 3) — không traceback nào có thể mang giá trị holdout. Nếu crash sau khi đọc nhưng trước khi chèn, campaign vẫn FROZEN và không in gì.

## Hệ quả

- Cưỡng chế INV-08/09/24: `tests/holdout/test_evaluator.py::test_tampered_holdout_refused`, `::test_output_is_single_token`, `::test_refuses_without_threshold`; cùng mở lần hai, danh mục chưa đóng băng/không rõ, quy tắc đóng băng, một lần chạy subprocess thật. Tất cả trên holdout tổng hợp trong thư mục tạm.
- Bộ đánh giá là code duy nhất đọc `holdout/` ở gốc; import-linter vẫn cấm mọi package khác import `quantcrucible.holdout`.
- Mở campaign mới sau BURNED đã hoạt động (`config.lock.open_campaign` lưu trữ lock cũ); chưa có lệnh huỷ một campaign đang OPEN.

## Phương án đã cân nhắc

- **Chạy thành viên trên host bên trong bộ đánh giá:** loại — code sinh ra chạy ngoài sandbox (§3.3.3).
- **Mount `holdout/` chỉ-đọc vào container:** loại — một bản sao chỉ đoạn đã kiểm giữ cho container thấy tối thiểu.
- **Bảng `freezes`:** tạm loại — thay đổi schema; audit log chỉ-thêm đã ghi việc đóng băng một cách bất biến.
