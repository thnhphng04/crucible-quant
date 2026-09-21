# ADR-0016 — Bộ đánh giá holdout và đóng băng campaign

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-10 · **Kiến trúc:** §4.2 (quy trình, 3 lớp), P6, D4, §3.3.3 · **Vấn đề mở:** O1 (D4) vẫn mở

## Bối cảnh

§4.2 cố định quy trình (OPEN → FROZEN → một lần mở → BURNED), `holdout_access` một dòng mỗi campaign, file chỉ-đọc + có hash, và một tiến trình đánh giá riêng trả về một bit. Còn mở: `portfolio_hash` đã đóng băng nằm ở đâu trước lần mở (schema không có bảng freeze), bộ đánh giá chạy code thành viên trên dữ liệu holdout thế nào khi code sinh ra chỉ được chạy trong sandbox và sandbox không bao giờ thấy `holdout/`, `holdout_pass` (D4) được so với cái gì, và CLI đóng băng campaign thế nào khi không gì được import `quantcrucible.holdout`.

## Quyết định

1. **Đóng băng = phía research** (`validation/freeze.py`, gọi được từ CLI): từ chối trừ khi campaign đang OPEN, hash là một dòng `portfolio_variants` của campaign này và kết quả ⑤, ⑥′ mới nhất của nó là pass được tính trên ledger hiện tại (sửa đổi bên dưới). Nó chuyển campaign sang FROZEN và ghi một sự kiện audit `CAMPAIGN_FROZEN` mang `portfolio_hash` + `frozen_at`. Từ đó `GatePipeline.run` và `record_variant` từ chối campaign (không thêm, bớt hay đổi trọng số gì).
2. **Bộ đánh giá = phía holdout** (`holdout/evaluator_proc.py`, entry point `-m` riêng, `holdout/campaign.py`): đầu vào chỉ là `--portfolio <hash>`. Kiểm tra trước, trước khi đọc bất kỳ byte holdout nào: campaign FROZEN trên hash này; chưa có dòng `holdout_access`; `frozen_at < now`; evaluation lock khớp hash đã ghi khi mở campaign; `holdout_pass` đã đặt (D4); `holdout.lock` khớp hash đã ghi khi mở campaign; mọi file holdout khớp `holdout.lock`.
3. **Code thành viên chạy trong sandbox** trên một bản sao đoạn holdout đã kiểm, có thêm `lookback` bar in-sample cuối cùng ở phía trước để khởi động chỉ báo, ghi vào thư mục đầu vào của đúng job đó — không bao giờ mount `holdout/`. Chỉ lợi nhuận tại các bar đóng trong khoảng holdout được tính. Image sandbox được build lười, sau bước kiểm tra.
4. **Kết luận:** trọng số và quy tắc rebalance đã đóng băng gộp các thành viên; `PASS` khi và chỉ khi Sharpe OOS theo năm ≥ `research.holdout_pass`. D4 nay đã được chốt đúng như vậy — xem [ADR-0020](0020-d4-y-nghia-va-gia-tri-holdout-pass.md) (giá trị 1,3, sau chi phí, tham chiếu 0).
5. **Burn:** chèn `holdout_access` (kèm `sharpe_oos`), ghi `HOLDOUT_OPENED` (không có con số), FROZEN → BURNED; rồi in đúng `PASS` hoặc `FAIL`. Từ chối đi ra stderr với exit 2; lỗi khác chỉ in tên loại lỗi (exit 3) — không traceback nào có thể mang giá trị holdout. ~~Crash sau khi đọc để campaign vẫn FROZEN~~ — bị thay thế bên dưới: holdout được claim trước khi đọc, và mọi lỗi sau claim đều đốt campaign.

## Hệ quả

- Cưỡng chế INV-08/09/24: `tests/holdout/test_evaluator.py::test_tampered_holdout_refused`, `::test_output_is_single_token`, `::test_refuses_without_threshold`; cùng mở lần hai, danh mục chưa đóng băng/không rõ, quy tắc đóng băng, một lần chạy subprocess thật. Tất cả trên holdout tổng hợp trong thư mục tạm.
- Bộ đánh giá là code duy nhất đọc `holdout/` ở gốc; import-linter vẫn cấm mọi package khác import `quantcrucible.holdout`.
- Mở campaign mới sau BURNED đã chạy được (`config.lock.open_campaign` lưu trữ lock cũ); bỏ (abandon) một campaign OPEN là ADR-0019.

## Phương án đã cân nhắc

- **Chạy thành viên trên host bên trong bộ đánh giá:** loại — code sinh ra chạy ngoài sandbox (§3.3.3).
- **Mount `holdout/` chỉ-đọc vào container:** loại — một bản sao chỉ đoạn đã kiểm giữ cho container thấy tối thiểu.
- **Bảng `freezes`:** tạm loại — thay đổi schema; audit log chỉ-thêm đã ghi việc đóng băng một cách bất biến.

## Sửa đổi — review commit d765668

- **Chỉ đóng băng trên kết quả hiện hành** (lỗi 3): kết quả ⑤ và ⑥′ mới nhất phải mang `ledger_snapshot` hiện tại của ledger (sửa đổi ADR-0014); một trial hay biến thể thêm vào sau đó làm chúng lỗi thời và việc đóng băng bị từ chối tới khi ⑤ → ⑥′ chạy lại.
- **Claim trước khi dùng** (lỗi 5): sau preflight và trước khi đọc bất kỳ byte holdout nào, bộ đánh giá chèn một dòng `holdout_claims` (schema v3) trong một transaction SQLite và ghi `HOLDOUT_CLAIMED`. Với hai bộ đánh giá chạy đồng thời, chỉ một claim thành công. `holdout_access` chỉ ghi được cho campaign đã claim.
- **Lỗi sau claim vẫn tiêu thụ holdout**: bộ đánh giá ghi `HOLDOUT_EVALUATION_FAILED` (chỉ tên loại lỗi), ghi `holdout_access` với verdict FAIL và không có Sharpe, rồi đốt campaign; chạy lại bị từ chối. Nếu process bị kill cứng, claim vẫn còn và preflight cũng từ chối.
- **Không dùng lại giữa các campaign** (lỗi 4, §4.2 bước 4): một claim bị từ chối — bởi trigger và bởi preflight — nếu một claim trước có cùng hash `holdout.lock` hoặc khoảng thời gian chồng lấn (khoảng không gồm điểm cuối, như trong store); mở campaign trên holdout như vậy cũng bị từ chối (`config.lock.open_campaign`).
- Test: `tests/holdout/test_evaluator.py::test_freeze_refuses_gate_results_from_an_older_ledger`, `::test_an_error_after_the_claim_still_consumes_the_holdout`, `::test_a_concurrent_claim_wins_once`, `::test_a_used_holdout_is_never_reopened_by_a_new_campaign`; `tests/ledger/test_db.py::test_a_holdout_is_claimed_once_across_campaigns`; `tests/config/test_lock.py::test_a_used_holdout_cannot_open_a_new_campaign`.
