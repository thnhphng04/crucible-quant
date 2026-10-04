# ADR-0046 — Feature map không phụ thuộc timeframe (v2)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-04
- **Task:** P3-59 · **Kiến trúc:** §3.1.3 · **Vấn đề mở:** —

## Bối cảnh

Bound của feature map được đặt cho nến ngày: `trades_per_year ∈ [0, 150]` chia tuyến tính, nên ở nến 1h mọi chiến lược giao dịch quá 150 lần/năm sẽ dồn vào bin trên cùng, và `total_return ∈ [−1, 4]` lớn dần theo độ dài IS. Đối chiếu với 2.473 trial gate ③ của các campaign 1d, thêm hai bound quá rộng so với sizing theo rủi ro tại stop: 98% drawdown nằm dưới 0,36 trong `[0, 1]`, và tổng lợi nhuận chỉ phủ khoảng ba trong mười sáu bin. Campaign chuyển sang 1h (2026-10-04), sau này có thể ngắn hơn; grammar giữ period theo bar, nên chỉ các thước đo của map cần không phụ thuộc timeframe.

## Quyết định

- Lock mới ghi `FEATURE_MAP_V2`: nó nêu `dimensions` và các chiều `log` của mình. Lock không có `dimensions` là v1 và được đọc đúng như trước.
- `trades_per_year ∈ [1, 10 000]` theo thang log: mỗi bin trong 16 bin trải một hệ số 1,78; không giao dịch rơi vào bin 0.
- `annual_return` — lãi kép `(1 + total)^(1/years) − 1` — thay `total_return`, bound `[−0,1; 0,3]`.
- `max_drawdown ∈ [0, 0,5]`; `sharpe_is` và `sortino_is` giữ `[−1, 3]` và `[−1,5; 4,5]`.

## Hệ quả

- Một map dùng cho cả campaign 1d và 1h và mọi độ dài IS (INV-120); bound vẫn cố định theo campaign (INV-63).
- Bound là tạm thời: trial của campaign 1h đầu tiên là lần hiệu chỉnh kế tiếp, cho campaign sau nó.
- Không sửa kiến trúc: §3.1.3 liệt kê các chiều (tần suất, drawdown, Sharpe, Sortino, lợi nhuận) mà không cố định đơn vị.

## Các phương án đã cân nhắc

- Bound theo từng timeframe — phải duy trì một bảng cho mỗi timeframe, và cùng một ô mang nghĩa khác nhau giữa các campaign.
- Bin theo phân vị học từ trial — giãn theo giá trị quan sát, điều §3.1.3 cấm.
