# ADR-0047 — Clause biến động và một thước ATR (grammar v5)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-04
- **Task:** P3-61 · **Kiến trúc:** §3.1.3, §3.1.11, §3.3.1 luật 4, ADR-0041 · **Vấn đề mở:** —

## Bối cảnh

Grammar chưa có clause nào cho chế độ biến động. P3-54 đã đo các ứng viên: độ rộng band thô `4·std/sma` tăng theo period (trung vị 0,145 ở n = 10, 0,575 ở n = 100), nên một khoảng `k` cố định sẽ suy biến; chia cho √n thì mức của nó phụ thuộc timeframe và instrument (trung vị 0,05 ở 1d, 0,008 ở 1h) và đi theo ATR/close (ρ 0,74–0,81). Tỷ lệ nhanh/chậm của độ rộng tính trên √bar và `atr(fast)/atr(slow)` giữ cùng một khoảng ở 1d và 1h (q05–q95 ≈ 0,3–2,7 và 0,4–2,4); người dùng chọn chúng ngày 2026-10-04.

Cổng ③ (§3.3.1 luật 4) loại hai feature có thay đổi từng bar tương quan trên `max_indicator_corr` 0,9. Trên bar IS, hai ATR của cùng một chuỗi tương quan 0,80–0,99, nên một tỷ lệ viết thành hai feature sẽ trượt cổng. Cùng phép đo lộ ra một lỗi của ADR-0041: ATR(14) cho guard đứng cạnh ATR(n_stop) cho stop (0,94–0,996) làm 39/40 genome của lock `stop_period` trượt, ở cả 1d và 1h. Chưa trial nào chạy với gen này — ADR-0041 vào code sau khi campaign cuối đã mở. Người dùng chọn một thước ATR.

## Quyết định

- Registry: `bandwidth(x, n) = 4·std / (sma·√n)`; `band_ratio(x, fast, slow)` và `atr_ratio(bars, fast, slow)`, nhanh chia chậm, NaN trừ khi phía chậm > 0. Loại `series_ratio` và `bars_ratio`; warm-up của một tỷ lệ là warm-up của period chậm.
- `VolRatio(fast, slow, op, k)` và `Bandwidth(fast, slow, op, k)`: mỗi clause một feature, render `x["fK"] op k` với `"fK": ind.atr_ratio(bars, n_fast, n_slow)` hoặc `ind.band_ratio(bars.close, …)`. Nhanh ∈ [5, 50] và chậm ∈ [60, 300], lấy mẫu log-uniform và rời nhau; `k` ∈ [0,6; 1,6] và [0,3; 2,5]. Tỷ lệ suy biến ở 1d / 1h: 2,7% / 7,9% và 4,1% / 3,5%.
- Grammar v5 (lock mới): chín loại clause; `volatility` là danh mục thứ năm ở cả hai phía, có đảo riêng (i4; đảo mở thành i5); `FEATURE_MAP_V2` liệt kê nó (trước đó chưa campaign nào mở dưới v2). Phiên bản 1–4 giữ bảy loại clause và năm đảo.
- Một thước ATR: stop ATR có gen `n_stop` render `"atr": ind.atr(bars, self.p.n_stop)` làm ATR duy nhất, được guard, stop và `k_tp` cùng đọc (`Genome.one_atr`). ADR-0041 giữ ATR(14) để việc tinh chỉnh stop không đổi thang `k` của `Distance`; từ v4 `Distance` chia cho spread của nó, và genome có `Distance` dạng ATR vẫn giữ cả hai ATR. Quy tắc suy ra từ chính genome, cho mọi phiên bản.
- Kernel: `OP_BANDWIDTH`, cùng `OP_ATR_RATIO` / `OP_BAND_RATIO` mang period chậm trong `inst_aux`; clause tỷ lệ biên dịch thành `CL_THRESHOLD`; `atr_slot` là ATR(n_stop) khi chỉ có một ATR.

## Hệ quả

- Genome v5 trượt phép đo tương quan 6% số lần (12/200 ở 1d, 11/200 ở 1h), luôn do hai feature vào lệnh; không còn cặp ATR nào (INV-121).
- Thay đổi của `atr_ratio` vẫn tương quan 0,57–0,89 với một thước ATR: cửa sổ chậm dài đưa một số genome `VolRatio` sát 0,9.
- CPU, giả lập CUDA và GPU RTX khớp registry và `generate_signals` từng bit; báo cáo của router bằng báo cáo của sandbox.
- Phải build lại image sandbox: registry nó chạy có thêm ba chỉ báo.
- Không đổi cổng hay kiến trúc: §3.3.1 luật 4 vẫn đếm mọi feature, và §3.1.3 chỉ nêu danh mục làm ví dụ.

## Các phương án đã cân nhắc

- Squeeze `bw / sma(bw, m)` — tương quan 0,80–0,84 với tỷ lệ, và cần chuỗi lồng trong kernel.
- Hai feature cho mỗi tỷ lệ — cổng ③ loại phần lớn.
- Bỏ ATR của guard và stop khỏi phép đo tương quan — nới một cổng cho mọi chiến lược.
- Một cờ trên genome cho guard — mơ hồ khi một bước point đổi stop thành stop Bollinger.
