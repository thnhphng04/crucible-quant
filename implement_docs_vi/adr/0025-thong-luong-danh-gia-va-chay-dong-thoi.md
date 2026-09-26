# ADR-0025 — Thông lượng đánh giá: chi phí đo được, cửa sổ rẻ hơn, slot chạy đồng thời

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-22
- **Task:** P2-08 · **Kiến trúc:** §3.1.8, §3.2 ④, P5 · **Vấn đề mở:** thu hẹp O14, cập nhật O9

## Bối cảnh

Cổng ④ backtest mọi cấu hình trong lưới PBO (`M` ≤ 200). Engine C đưa hàng trăm đến hàng nghìn ứng viên qua cổng này, nên chi phí của nó quyết định một campaign GĐ 2 mất vài ngày hay vài tuần. Đo trên máy phát triển (i7-12700H, 20 luồng, Docker Desktop 8 GB), dữ liệu tổng hợp cùng hình dạng với campaign (5 symbol × 2.450 bar ngày), mỗi container một CPU: cổng ③ ≈ 12,7 s; ≈ 9 s cho mỗi cấu hình trong lưới; PBO + CPCV trên ma trận 200 cấu hình < 1 s. Profile cho thấy ~45% thời gian một backtest dùng để dựng lại cửa sổ bar từ tuple Python và tạo đối tượng pandas trong tầng Risk ở mỗi bar.

## Quyết định

1. **Cửa sổ rẻ hơn, kết quả không đổi.** Bridge giữ OHLCV và thời điểm đóng cửa trong buffer numpy tăng dần; cửa sổ của một bar là view của `lookback` dòng cuối. Tầng Risk lưu mảng thô và chỉ dựng pandas khi tái cân bằng; khóa kỳ tháng/quý tính bằng numpy. Đường equity trùng khớp từng bit với trước (tái cân bằng tháng và tuần); một backtest giảm từ 8,9 s xuống 5,4 s trên máy host.
2. **Các slot đánh giá chạy đồng thời.** Pipeline chạy `workers` slot; mỗi slot có kết nối ledger riêng (WAL + `busy_timeout = 30 s`). Thông lượng đo với job lưới 10 cấu hình: 6 slot ≈ 0,89 backtest/s, 12 slot ≈ 1,08 backtest/s — máy bão hòa quanh một backtest mỗi giây. RAM đỉnh ≈ 270 MB mỗi container, nên 12 slot vừa trong 8 GB. Mặc định: `workers = 8`.
3. **Container gắn nhãn, bị kill khi hủy.** Mọi container sandbox mang nhãn `quantcrucible.run=<label>`; nhánh hủy của pipeline gọi `SandboxRunner.kill_all()` (INV-69).
4. **Lập kế hoạch ngân sách:** ở `M = 200`, một ứng viên tới được ④ tốn ≈ 200 s thời gian máy. P2-15 định cỡ `trial_budget` theo con số này.

## Hệ quả

- O14 được thu hẹp: thông lượng đã biết và tốt hơn ~40%; đòn bẩy còn lại là `pbo_grid.max_configs` (D13, do người dùng quyết) — không dùng backtester vector hóa (P5).
- O9 vẫn mở: không có lỗi khóa với 6 luồng ghi đồng thời trong test; xem lại nếu lần chạy thật cho thấy tranh chấp.
- Indicator vẫn được tính lại trên cả cửa sổ ở mỗi bar (≈ 20% một backtest). Đó là đường live (P5); cache chúng cần một ADR riêng.

## Các phương án đã cân nhắc

- **Backtester vector hóa cho lưới:** không chọn — một engine backtest thứ hai phá P5.
- **Container worker sống lâu:** chưa cần — thời gian khởi động container nhỏ so với backtest ≈ 5–9 s.
