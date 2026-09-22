# ADR-0021 — Đề xuất: đánh giá lại một biến thể danh mục cũ trên ledger hiện tại

- **Trạng thái:** Đề xuất — để review, **chưa triển khai**
- **Ngày:** 2026-09-22
- **Task:** sau lần chạy dữ liệu thật đầu tiên · **Kiến trúc:** §3.2.1, §4.1, §4.2 bước 2 · **Vấn đề mở:** —

## Bối cảnh

Trong campaign `c-20260921-171247`, biến thể danh mục đầu tiên (`778e…`, chỉ có RSI momentum) đã qua ⑤ và ⑥′. Sau đó calibration thêm 48 trial; RSI đã calibrate trượt ④ (PBO 0,549), nên theo quy tắc "lần đo mới nhất quyết định, không quay về" (sửa đổi ADR-0013) RSI rời danh mục, và biến thể dựng lại (chỉ có SMA) trượt ⑥′. Kết quả của `778e…` đã lỗi thời (sửa đổi ADR-0016, INV-52) và không gì chạy lại được ⑤ → ⑥′ trên một biến thể mà quy tắc hiện tại không còn dựng ra. Người dùng giữ campaign OPEN (phương án a) và yêu cầu việc này thành một đề xuất riêng trước mọi thay đổi.

## Quyết định (đề xuất)

1. Một lệnh `portfolio-reevaluate HASH` chạy lại ⑤ → ⑥′ trên một biến thể **đã ghi** của campaign OPEN, với `trial_stats` và số biến thể hiện tại, và ghi kết quả kèm `ledger_snapshot` hiện tại. Nó không thêm dòng biến thể và không đổi thành viên, trọng số hay quy tắc nào.
2. Đóng băng giữ nguyên: chỉ kết quả ⑤/⑥′ mới nhất tính trên ledger hiện tại mới có giá trị, nên một biến thể được đánh giá lại chỉ đóng băng được nếu nó qua với `N` của hôm nay.
3. **Chặn vòng chọn lọc:** mỗi lần đánh giá lại được ghi log (`VARIANT_REEVALUATED`) và tính thêm một lần chọn vào `N` (như một biến thể danh mục), nên việc thử nhiều biến thể cũ tới khi có cái qua phải trả giá bằng DSR. Tùy chọn: tối đa một biến thể được đánh giá lại mỗi campaign.

## Hệ quả

- Biến thể RSI sẽ được xét trên mọi thứ đã thử từ đó tới nay, đúng là việc DSR làm: với ledger hôm nay, DSR của nó là 0,999 tại N_eff = 4 và 0,986 tại N_raw = 54 (kiểm tra chỉ đọc, 2026-09-22) — ⑥′ cũng phải chạy lại.
- Nó làm yếu quy tắc "lần đo mới nhất quyết định" ở cấp danh mục: một biến thể cũ có thể quay lại sau khi biến thể mới hơn trượt. Đó đúng là mối lo của reviewer ở lỗi 2, lên một cấp; điểm 3 là thứ buộc nó phải trả giá.

## Phương án đã cân nhắc

- **Giữ quy tắc hiện tại (đang chọn):** campaign vẫn OPEN; sự đa dạng phải đến từ chiến lược mới, ít tương quan hơn.
- **Đánh giá lại mà không tính vào N:** loại — chọn giữa các biến thể sau khi đã thấy kết quả sẽ thành miễn phí, một trial ẩn.
