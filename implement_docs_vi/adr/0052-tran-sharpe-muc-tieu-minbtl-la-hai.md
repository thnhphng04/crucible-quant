# ADR-0052 — Trần Sharpe mục tiêu của MinBTL là 2.0

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-06
- **Task:** P3-65 · **Kiến trúc:** §3.2 dòng ②, §10 D17, §10.1 · **Vấn đề mở:** —

## Bối cảnh

Gate ② từ chối ứng viên khi lịch sử IS ngắn hơn `MinBTL(N) = (E[max_N] / mục tiêu)²` năm (Bailey và cộng sự 2014). D17 đặt mục tiêu 1.5 năm hóa và lấy 1.5 làm trần cứng: mục tiêu cao hơn làm MinBTL ngắn đi, tức nới gate. Ở 1.5, ba năm IS chỉ cho `N ≤ 121` trial mỗi campaign (ADR-0050), còn 600 trial cần 4,29 năm. Người dùng muốn một campaign perpetual 1h với 600 trial trên ba năm IS và, sau khi được trình bày đánh đổi, đã quyết định ngày 2026-10-06 nâng trần lên 2.0.

## Quyết định

- `MINBTL_TARGET_SHARPE_CEILING` là 2.0: `minbtl_target_sharpe` phải nằm trong `(0, 2.0]`; trên 2.0 bị từ chối lúc nạp (INV-20).
- Bỏ trống mục tiêu vẫn có nghĩa là 1.5 (`MINBTL_TARGET_SHARPE_DEFAULT`): chỉ khi đặt tường minh mới đạt giá trị lỏng hơn.
- Mục tiêu vẫn khóa theo campaign như trước, nên lock đã có giữ mục tiêu nó mở với.
- `config/user.yaml` đặt 2.0 cùng `trial_budget: 600`; với ba năm IS, trần là 2 128 trial.

## Hệ quả

- Gate ② không còn bảo đảm rằng một Sharpe IS từ 1.5 đến 2.0 không thể là may mắn của trial tốt nhất trong `N`. Campaign ở 2.0 không so được ở gate ② với campaign ở 1.5; lock ghi rõ là cái nào.
- Thiên lệch chọn lọc vẫn bị tính ở gate ⑤: DSR khấu trừ Sharpe của danh mục gộp theo `N_eff` và `V[SR]` của campaign, nên càng nhiều trial thì ⑤ càng khó, không dễ hơn.
- `dsr_min`, `pbo_max` và D4 (`holdout_pass`) không đổi.
- Kiến trúc: D17 (nay đã chốt, mặc định 1.5, trần 2.0), chú thích config ở §10.1 và dòng sàn cứng; bảng bất biến trong CLAUDE.md; INV-20.

## Phương án đã cân nhắc

- Cửa sổ IS dài hơn (bắt đầu 2021-04-02, 4,5 năm, trần 775 ở 1.5) — giữ nguyên trần và không cần dữ liệu mới; người dùng chọn ba năm.
- Đặt 2.0 làm mặc định — sẽ nới mọi campaign bỏ trống khóa, không chỉ campaign được yêu cầu.
