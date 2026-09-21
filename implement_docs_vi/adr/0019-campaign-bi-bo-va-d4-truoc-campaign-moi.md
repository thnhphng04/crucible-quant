# ADR-0019 — Campaign bị bỏ, và D4 trước một campaign mới

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** review P1-12 · **Kiến trúc:** §4.2 (bước 5 của quy trình, thêm mới), §10 D4, §10.1 · **Vấn đề mở:** đóng O16

## Bối cảnh

Campaign dữ liệu thật mở ở phase 0 có trước `derived.sizing`, nên gate ③ từ chối lock của nó. Nó đang OPEN, và §4.2 chỉ biết OPEN → FROZEN → BURNED: lối ra duy nhất là mở holdout, một cách vô ích. Người dùng chọn một trạng thái `ABANDONED` có dấu vết audit, giữ nguyên lock cũ. Cùng lần review đó chỉ ra rằng `holdout_pass` (D4) được khóa cùng campaign, nên phải quyết định trước khi mở campaign mới, không phải lúc mở holdout.

## Quyết định

1. **`ABANDONED`** = một dòng trong bảng append-only `campaign_abandonments` (schema v3) với lý do không rỗng, cộng một sự kiện audit `CAMPAIGN_ABANDONED` (`validation/freeze.py::abandon`, CLI `campaign-abandon --reason`). `Ledger.campaign()` báo trạng thái là `ABANDONED`. CHECK của `campaigns.status` giữ nguyên: đổi nó nghĩa là phải dựng lại một bảng mà các bảng khác tham chiếu tới.
2. **Chỉ OPEN, một lần, là cuối cùng:** trigger từ chối bỏ một campaign không OPEN, và sau khi bỏ thì từ chối mọi chuyển trạng thái cùng mọi dòng `trials`, `gate_results` hay `portfolio_variants` mới của nó.
3. **Không reset gì:** trial của campaign vẫn nằm trong `N` và `V[SR]` (`trial_stats` trên toàn ledger). Holdout của nó chưa từng bị claim, nên vẫn chưa bị dùng và có thể phục vụ campaign kế tiếp.
4. **Lock cũ giữ nguyên:** không bị sửa. Khi campaign kế tiếp mở, `config.lock.open_campaign` chuyển nó nguyên từng byte sang `config/locks/<campaign_id>.lock.yaml`; SHA256 của nó vẫn khớp ledger.
5. **D4 trước:** `current_campaign` mở campaign mới — chưa có lock, hoặc campaign của lock đã BURNED hay ABANDONED — chỉ khi `research.holdout_pass` đã đặt (nếu không thì `CampaignNotOpened`). Id campaign có hậu tố `-N` khi hai campaign mở trong cùng một giây.

## Hệ quả

- Campaign thật có thể được đóng bằng `uv run python -m quantcrucible.cli campaign-abandon --reason "..."` — một lần ghi ledger không đảo ngược được, để người dùng tự chạy.
- Campaign thật kế tiếp vẫn chờ D4: ý nghĩa của nó (ADR-0016 hiểu là Sharpe OOS quy năm tối thiểu) và giá trị của nó. Giá trị 0,5 dùng trong test không phải căn cứ để chọn.
- Test: `tests/ledger/test_db.py::test_abandoned_campaign_is_final_and_keeps_its_trials`, `::test_only_an_open_campaign_can_be_abandoned`; `tests/validation/test_run.py::test_abandoned_campaign_is_replaced`, `::test_a_new_campaign_needs_d4`; `tests/config/test_lock.py::test_new_campaign_after_an_abandoned_one`.

## Phương án đã cân nhắc

- **Cắt lại một holdout mới:** loại ở đây — nó vứt bỏ một holdout chưa dùng trong khi lịch sử dữ liệu miễn phí ngắn (§4.2 "tài nguyên tiêu hao").
- **Dựng lại `campaigns` với CHECK bốn trạng thái:** loại — cần tắt khóa ngoại trong lúc migrate một ledger append-only; một bảng riêng cho cùng bảo đảm bằng trigger.
