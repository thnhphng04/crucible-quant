# ADR-0027 — So sánh giai đoạn 2: giao thức khóa trong ledger, ngân sách, quyết định

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-23
- **Task:** P2-15 · **Kiến trúc:** §3.1.11 (so sánh, quy tắc quyết định), §7 GĐ 2, D14 · **Vấn đề mở:** —

## Bối cảnh

§3.1.11 (v0.6) so sánh C-gp với C-random ở chế độ `isolated` — cùng dữ liệu, cổng và hạn mức trial, ≥ 3 seed — với quy tắc quyết định khóa trước khi chạy. Nó nêu năm chỉ số nhưng chưa nói chỉ số nào quyết định, "thắng … với cách biệt vượt độ dao động giữa các seed" nghĩa là gì bằng số, "khóa trước khi chạy" được cưỡng chế ra sao, và ngân sách trial là bao nhiêu.

## Quyết định

1. **Giao thức** (`agent/compare.py::PROTOCOL`, có phiên bản, có hash). Chỉ số chính: **hiệu suất trial** = số chiến lược qua cổng ④ trên 100 trial, theo (engine, seed). C-gp *thắng* C-random khi `mean(gp) − mean(random) > max(sd(gp), sd(random))` (độ lệch chuẩn mẫu giữa các seed). Cả hai dưới 1 trên 100 ⇒ *không bên nào đáng kể*: dừng lại tìm nguyên nhân. Còn lại là *hòa* ⇒ C-random thành engine chính (§3.1.11). Chỉ số phụ, không quyết định: độ phủ của archive và của tìm kiếm, số đề xuất trên mỗi trial, số backtest cổng ④ cho mỗi chiến lược qua cổng, phân kỳ IS→OOS, và DSR của một danh mục §3.2.1 dựng riêng từ mỗi engine ở cùng `N` (ghi thành một biến thể, như mọi danh mục được dựng). Không bao giờ so Sharpe IS của chiến lược tốt nhất.
2. **Khóa trong ledger:** `cli compare --lock` ghi `PROTOCOL_LOCKED` (giao thức + SHA-256) vào audit log của campaign, chỉ khi campaign chưa có trial nào. `evolve` từ chối campaign thử harness khi chưa có nó; báo cáo từ chối campaign có bất kỳ lần submit nào xảy ra trước nó (INV-70).
3. **Ngân sách** (`config/user.yaml` cho lần chạy): `campaign: {purpose: harness_test, trial_budget: 600}`, engine gp 0,5 / random 0,5, 3 seed ⇒ 100 trial mỗi (engine, seed). MinBTL cho phép ~37.000 trial trên 7,7 năm IS ở Sharpe mục tiêu 1,5; thời gian máy mới là ràng buộc: ≈ 1 backtest/s (ADR-0025) ⇒ ≈ 10–15 giờ nếu ~30–40% trial tới ④ với `M` ≤ 200.
4. Chiến lược của campaign GĐ 2 không bao giờ vào một danh mục được đóng băng (campaign này không đóng băng được, INV-62).

## Hệ quả

- Phép so sánh thêm 600 trial và hai biến thể danh mục vào `N` của mọi campaign sau — cái giá của một đối chứng trung thực (§3.1.11: chạy song song không cho trial miễn phí).
- Đổi chỉ số hay độ dao động sau khi thấy kết quả sẽ cần một phiên bản giao thức mới và một campaign mới.

## Các phương án đã cân nhắc

- **Kiểm định t trên trung bình các seed:** tạm không chọn — với 3 seed nó có độ mạnh thấp và che mất quy tắc "vượt độ dao động" đơn giản của kiến trúc.
- **Chỉ ghi giao thức trong file ADR:** không chọn — một file không chứng minh được nó có trước lần chạy; thứ tự append của ledger thì chứng minh được.
