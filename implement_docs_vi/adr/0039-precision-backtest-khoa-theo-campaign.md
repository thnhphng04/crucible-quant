# ADR-0039 — Precision backtest khóa theo campaign (float64 | float32)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-30
- **Task:** P3-35 (config), P3-45 (fp32), P3-47 (holdout) · **Kiến trúc:** §3.5, §10 D23, §10.1

## Bối cảnh

Engine kernel (ADR-0038) có thể tính feature và tín hiệu bằng float32. Trên GPU này fp32 nhanh gấp 32 lần fp64, nhưng indicator float32 có thể làm lật điểm vào lệnh ngay tại ngưỡng, nên verdict của gate có thể khác float64. User muốn có cả hai precision, ngang hàng. Ngoài ra, thêm bất kỳ khóa `research.*` nào cũng khiến mọi lock hiện có fail `assert_lock_matches`, vì hàm này đòi khớp tuyệt đối.

## Quyết định

1. **Khóa Nhóm B** `research.backtest.precision: float64 | float32`, mặc định float64, được chép vào lock. Đi kèm `derived.backtest_numerics` (`fp64_oracle_v1` | `fp32_signals_v1`); tag lạ, hoặc tag mâu thuẫn với khóa, đều bị từ chối.
2. **Lock cũ.** `assert_lock_matches` đọc lock thiếu `backtest` là `{precision: float64}`. Lock cũ gặp config float32 thì bị từ chối. Lock mới luôn ghi khóa này.
3. **Phạm vi fp32.** Chỉ feature và tín hiệu chạy float32; tiền mặt, khối lượng và equity luôn float64. Campaign fp32 ngang hàng hoàn toàn, kể cả freeze và holdout.
4. **fp32 fail closed.**
   - Campaign fp32 chỉ nhận genome grammar. Chiến lược khác bị từ chối và không ghi trial (ADR-0022).
   - Không bao giờ fallback sang sandbox float64.
   - Audit của nó so CUDA với bản njit float32.
5. **Holdout.** Evaluator chạy engine theo precision của lock. Nó tự kiểm trước `claim()`, và nếu bị từ chối thì holdout chưa bị claim. GPU lỗi giữa chừng thì chạy tiếp trên CPU kernel cùng precision.
6. **E6.** Độ lệch fp32/fp64 (tín hiệu và lệnh lệch, ΔSharpe, verdict gate bị lật, mức tăng tốc) chỉ đo trên dữ liệu tổng hợp (P2) và được bổ sung vào đây ở P3-45.

## Hệ quả

- Kết quả của một campaign tái lập được chỉ từ lock của nó. Không thể đổi precision giữa campaign để cứu một candidate.
- Các campaign OPEN hiện có vẫn chạy float64 mà không cần lock mới.
- Campaign fp32 không nhận chiến lược viết tay hay do LLM viết. Nó cần host chạy được numba, ít nhất là CPU kernel.
- Test: `tests/config/test_lock_precision.py` (INV-108) và test router/holdout (INV-110). Studio có thêm ô precision (P3-35).
- **E6 (P3-45, `scripts/experiments/e6_fp32_drift.py`; 90 genome lấy mẫu trên ba chuỗi 1h tổng hợp, 67k bar, lưới 200 cấu hình):**
  - Cờ vào lệnh khác nhau ở trung bình 0,001% ô (bar, cấu hình) (genome tệ nhất 0,03%). Khoảng stop khác ở các bit thấp gần như ở mọi lệnh.
  - Một lệnh bị lật làm đổi mọi lệnh sau đó trên đường đi ấy, nên 12% cấu hình kết thúc với số lệnh khác (genome tệ nhất 94%).
  - |ΔSharpe| của cấu hình gate ③: trung vị 1e-7, trung bình 0,003, tệ nhất 0,26. Các hàng lưới: tệ nhất 1,09.
  - Không verdict gate ③ hay gate ④ nào bị lật; chênh PBO lớn nhất là 0,016.
  - Feature + tín hiệu trên CUDA: 0,065 s ở fp32 so với 0,111 s ở fp64 (1,7×); trên CPU hai precision bằng nhau (0,52 s). Vì vậy `auto` giữ CUDA cho cả hai precision. Replay, luôn fp64, không đổi.
  - Tóm lại fp32 mua được mức tăng tốc vừa phải, đổi lại là candidate có con số không phải con số float64. Đó là đánh đổi user đã chấp nhận, và lock ghi lại nó.

## Các phương án đã cân nhắc

- **Chỉ float64:** đơn giản nhất, nhưng user muốn tốc độ của fp32.
- **Search bằng fp32 rồi xác minh lại bằng fp64 cho candidate đã qua:** an toàn hơn, nhưng user chọn ngang hàng.
- **Cấm fp32 mở holdout:** user từ chối cùng lý do.
- **Chạy chiến lược không có genome bằng sandbox fp64 trong campaign fp32:** trộn hai precision trong một campaign.
