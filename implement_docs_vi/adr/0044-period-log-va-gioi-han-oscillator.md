# ADR-0044 — Period lấy mẫu theo log và giới hạn period oscillator (grammar v3)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-04
- **Task:** P3-57 · **Kiến trúc:** §3.1.11, §3.3.1 luật 2 · **Vấn đề mở:** —

## Bối cảnh

P3-54 đo được 18,8% clause lấy mẫu bị suy biến — đúng trên < 1% hoặc > 99% số bar in-sample — chủ yếu do ba nguyên nhân. Period lấy đều trên [2, 300] rơi trên 100 hai lần trong ba, nơi hai đường trung bình hiếm khi cắt nhau. RSI period dài bám quanh 50, nên các mức trong [50, 90] hay [10, 50] hiếm khi chạm tới. Z-score của n giá trị không bao giờ vượt (n − 1)/√n — 1,79 khi n = 5 — nên các mức ±2,25 và ±3 không thể đạt. Period theo log cùng RSI ≤ 30 và z-score ≥ 10 đưa tỷ lệ xuống 10,9% trên bar in-sample, tương tự trên sàn thứ hai.

## Quyết định

- Lock mới ghi `derived.grammar_version: 3`.
- v3 lấy mọi period của clause theo log-uniform trong bound của nó: `round(exp(U(ln lo, ln hi)))`. Bound không đổi, nên toán tử `param` và lưới gate ④ vẫn phủ hết.
- v3 giới hạn period oscillator: `rsi` ∈ [2, 30], `zscore` ∈ [10, 300] (`PERIOD_CAPS_V3`); giới hạn trở thành bound của TUNABLE.
- Dưới v3, `point` đổi `rsi` ↔ `zscore` sẽ đưa period vào khoảng và bound của oscillator mới.
- `n_stop` giữ phân bố đều [5, 50]; level và `k` giữ khoảng cũ. Dưới v3 không có gì đổi: render golden và luồng lấy mẫu v1/v2 giữ nguyên.
- Không giới hạn period của `Cross` và `CrossLevel`: chúng là sự kiện, hiếm tính theo bar nhưng nhiều tính theo số lần ở timeframe ngắn, và campaign chuyển sang 1h.

## Hệ quả

- Ít clause chết và ít trial phí hơn (INV-118): trên random walk có seed 17,6% → 12,0%, `Cross` 42% → 20%, `CrossLevel` 47% → 22%.
- `Distance` tệ hơn (7,5% → 17,5%): period theo log ưu tiên period ngắn, nơi ATR(14) là thước đo quá rộng. P3-58 đổi mẫu số của nó; không campaign nào mở ở giữa.
- Không sửa kiến trúc: các khoảng là hằng triển khai, khai báo làm bound TUNABLE (§3.3.1 luật 2).

## Các phương án đã cân nhắc

- Thu hẹp bound thay vì lấy mẫu theo log — `param` và lưới PBO sẽ mất hẳn các period dài.
- Khoảng level phụ thuộc period RSI — ràng buộc hai gene; period đổi thì level phải lấy lại.
