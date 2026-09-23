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

## Bổ sung — giao thức v2 (2026-09-23)

Lần chạy đầu của v1 để lộ hai lỗ hổng, cả hai đều được phát hiện trước khi đọc bất kỳ kết quả nào.

1. **Điều kiện "chạy xong" chỉ là ngầm định.** `decide` chạy trên bất cứ thứ gì ledger có, nên một campaign mới đánh giá 3 trong 600 trial — toàn bộ đều của GP — vẫn báo `gp_beats_random`. v2 thêm `complete`: mọi (engine, seed) của cả hai arm phải dùng hết hạn mức, với `>= 3` seed, nếu không báo cáo chỉ hiện tiến độ dưới kết quả `incomplete` và không phát quyết định. Arm bị monitor dừng vì phân kỳ IS→OOS cho kết quả `stopped_early`: chính sự phân kỳ là phát hiện, và campaign đó không sinh ra quyết định chọn engine.
2. **Hash đã khóa chưa ràng buộc gì.** Báo cáo tính lại hash từ code và không bao giờ đối chiếu với sự kiện `PROTOCOL_LOCKED` của campaign, nên sửa `PROTOCOL` là lặng lẽ diễn giải lại một campaign cũ. Giờ báo cáo và `evolve` từ chối campaign có hash đã khóa khác với hash trong code (`assert_protocol_is_the_locked_one`), còn `lock_protocol` từ chối khóa một giao thức thứ hai khác đi.

Cả hai sửa đổi chỉ làm luật chặt hơn, nhưng v2 đổi hash, nên campaign `c-20260922-181850` (khóa theo v1) không chạy tiếp dưới v2 được. 121 trial của nó vẫn nằm trong `N`, như mọi trial, và phép so sánh bắt đầu lại trong một campaign mới. Hai lỗi nữa sửa trong cùng đợt ảnh hưởng tới thứ mà lần chạy đo được: các slot đánh giá bị dồn hết cho một (engine, seed) đầu danh sách (INV-71), và DSR danh mục của hai arm được tính ở số biến thể khác nhau vì arm trước được ghi trước khi arm sau được dựng — giờ cả hai arm deflate theo cùng một ảnh chụp.
