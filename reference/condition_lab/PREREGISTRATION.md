# Condition lab — đăng ký trước (27/10/2026, viết trước khi chạy)

Mục tiêu: thử các **điều kiện vào lệnh mới** chồng lên hai chiến thuật short phá dải Bollinger hiện có, với luật
đạt/không đạt chốt trước, để không chọn điều kiện theo kết quả.

## Hai baseline (mỗi baseline 9 bộ tham số quanh bộ "điển hình", điều kiện mới phải giúp đa số bộ)

| | A — vol filter (current_strategy) | B — notebook GPU short trend |
|---|---|---|
| Timeframe | 15min | 5min |
| Vào lệnh | close[t-1] > lower > close[t], không RSI | như A, không RSI |
| Bộ lọc gốc | vol_ratio(288 nến, 90 ngày) < 0.9 | band_ratio_max 0.75 (độ rộng dải ≤ 0.75 × trung vị 30 ngày) |
| Thoát | trailing 1.5R, không TP, giữ tối đa 384 nến | TP 1.1R, giữ tối đa 288 nến |
| Lưới 9 bộ | boll {150, 200, 300} × dev 2.0 × sl {0.5, 0.6, 0.8} | boll {200, 300, 400} × dev 2.0 × sl {0.5, 0.8, 1.1} |

Phí như engine (0.1% vào / R, 0.01% mỗi 8 giờ), một lệnh mỗi lúc. Điều kiện mới được AND với tín hiệu gốc.

## Dữ liệu, coin, giai đoạn

- 46 coin của `data_5m` trừ BTC (BTC dùng làm chỉ báo thị trường). Chia cố định theo tên (sắp xếp, xen kẽ):
  chỉ số chẵn = **dev**, lẻ = **xác nhận**. Coin xác nhận chỉ được xem sau khi danh sách điều kiện đạt ở dev đã chốt.
- Tính R của các lệnh **đóng** từ 2021-08-01 đến 2026-09-30 (đủ khởi động SMA 200 ngày; funding hết 2026-09-30).
- Mọi chỉ báo chỉ dùng dữ liệu đến close nến t (khung lớn: nến đã đóng; funding: lần thanh toán đã xảy ra).

## Điều kiện (~50), mỗi cái là một mặt nạ cho phép vào lệnh

1. Chế độ BTC: BTC < SMA 20/50/100/200 ngày; lợi nhuận BTC 7/30 ngày < 0; lợi nhuận BTC 1 ngày < 0; vol ratio BTC < 1.
2. Yếu tương đối: (lợi nhuận coin − lợi nhuận BTC) 7/30 ngày < 0.
3. Xu hướng riêng coin (đối chứng §9d): coin < SMA 50/200 ngày.
4. Độ rộng thị trường: tỉ lệ coin có close ngày < SMA 50 ngày (tính tới ngày trước) ≥ 0.5 / ≥ 0.65.
5. Funding: lần gần nhất > 0; > 0.01% (trên mức trung tính); > trung vị 30 ngày; trung bình 7 ngày > 0.
6. RSI chuẩn (thử lại): RSI14 < 30/40/50; RSI5 < 20/30. RSI ngược (không bán khi quá bán): RSI14 > 25/30/35; RSI5 > 10/20.
7. Nến phá: volume / trung vị volume 1 ngày ≥ 1.5 / ≥ 2 / < 1; vị trí close trong nến ≤ 0.25 / ≥ 0.5;
   |lợi nhuận nến| ≥ 2σ / < 1σ (σ = độ lệch chuẩn 1 ngày).
8. Khung lớn: close 1h < SMA20 1h; close 4h < SMA20 4h; RSI14 4h < 50; close ngày < SMA20 ngày.
9. Thời điểm: phiên Á (0–8 UTC), Âu (8–16), Mỹ (16–24) theo giờ đóng nến; ngày thường; cuối tuần.
10. Vị trí giá: close ≥ 0.9 × đỉnh 30 ngày (gần đỉnh); ≤ 0.8 × đỉnh 30 ngày (đã sập); nửa trên / nửa dưới biên độ 30 ngày.

## Thước đo

Danh mục chia đều các coin có dữ liệu trong tháng; R danh mục mỗi tháng = trung bình R tháng của các coin.
- **Chính:** Sharpe năm hoá của R danh mục theo tháng (bộ lọc bớt lệnh, nên không so tổng R).
- Phụ: R/năm/coin, maxDD danh mục, lệnh/năm/coin, R trung bình mỗi lệnh theo năm.

## Luật đạt (riêng cho từng baseline), so với "không điều kiện" của cùng bộ tham số

- **D1 (dev):** ΔSharpe > 0 ở ≥ 7/9 bộ và trung vị ΔSharpe > 0.
- **D2 (dev):** R/lệnh (trung vị qua 9 bộ) cao hơn không điều kiện ở ≥ 4/6 năm 2021–2026.
- **D3 (dev):** giữ lại ≥ 25% số lệnh.
- **C1 (xác nhận, chỉ cho điều kiện qua D1–D3):** ΔSharpe > 0 ở ≥ 6/9 bộ và trung vị ΔSharpe > 0.
- Đạt = D1 ∧ D2 ∧ D3 ∧ C1. Với ~50 điều kiện, vài cái có thể đạt nhờ may; báo cả số đạt kỳ vọng do ngẫu nhiên.
- Bước sau (chỉ khi có điều kiện đạt): ghép tối đa 2 điều kiện đạt vào walk-forward 24/6 (baseline A: grid của
  current_strategy; baseline B: grid notebook) và so với không điều kiện trên mọi coin.

## Không làm

Không thêm/bớt điều kiện, đổi ngưỡng hay đổi luật sau khi đã thấy kết quả dev; nếu có thì ghi rõ là khám phá thêm.
