# ADR-0045 — Distance chia độ lệch chuẩn của chính spread (grammar v4)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-04
- **Task:** P3-58 · **Kiến trúc:** §3.1.11, §3.3.1 luật 3, ADR-0038 · **Vấn đề mở:** —

## Bối cảnh

`Distance` chia `a − b` cho ATR(14), biên độ của một bar, trong khi toán hạng có period tới 300 bar. P3-54 đo được độ lệch thang theo cả hai chiều: với period dài, tỷ số nằm ngoài `k ∈ [−3, 3]` trên 38% số bar; với period ngắn, `|k| ≥ 2` không bao giờ đạt. Trong bốn mẫu số đo trên bar in-sample, độ lệch chuẩn của chính spread để lại 1,6% clause `Distance` suy biến, so với 6% của ATR(14) (16% khi period lấy theo log), 18% của `stdev(close, n)` và 65% của `ATR(14)·√n`. Người dùng chọn phương án này ngày 2026-10-04.

## Quyết định

- Chỉ báo mới trong registry `spread_stdev(x, y, n)`: độ lệch chuẩn tổng thể của `x − y` trên n bar; NaN khi bằng 0 hoặc cửa sổ có NaN. Loại `pair`, đơn vị giá; nhân quả như mọi chỉ báo khác.
- `Distance.scale ∈ {atr, spread}`, mặc định `atr`, bỏ khỏi dạng JSON khi là mặc định. `Distance` dạng `spread` render `(a − b) / x["fK"]` với `"fK": ind.spread_stdev(<a>, <b>, self.p.<n của b>)` — n của a khi b là close; không thêm TUNABLE. Parser đọc lại được; `k = 0` vẫn nghĩa là `a > b`.
- Grammar v4 (lock mới): mọi `Distance` lấy mẫu đều là `spread`, period toán hạng ≤ 200 để warm-up `max(n_a, n_b) + n − 1` vừa trong lookback 400 bar đã khoá.
- Kernel: instance `OP_SPREAD` mang toán hạng trong `Program.inst_aux`; mỗi lane dựng lại cả hai toán hạng trên cửa sổ của nó (ema seed ở đầu cửa sổ), giữ n + 1 spread cuối trong bộ đệm của lane và lấy trung bình, độ lệch chuẩn theo tổng pairwise của numpy. Dòng clause có thêm cột thứ sáu, slot mẫu số của `Distance`.

## Hệ quả

- `Distance` sống ở mọi period (INV-119): tỷ lệ suy biến 17,5% → 0% trên random walk có seed; CPU, giả lập CUDA và GPU RTX khớp registry và `generate_signals` từng bit.
- Thay đổi từng bar của mẫu số tương quan ≤ 0,54 với mọi feature khác trên bar in-sample, dưới xa `max_indicator_corr` 0,9.
- Cổng ①a không cần sửa: `spread_stdev` có trong whitelist, lời gọi lồng được phép, đơn vị là giá chia giá.
- Không sửa kiến trúc: §3.3.1 luật 3 cho phép mọi `ind.*` trong whitelist; whitelist nằm ở registry.

## Các phương án đã cân nhắc

- `stdev(close, n)` — spread giữa hai MA nhỏ hơn nhiều độ phân tán của giá, nên `|k| ≥ 2` không thể đạt.
- `ATR(14)·√n` — cần luỹ thừa không nguyên trong DSL và tệ hơn khi có xu hướng.
- Kéo dài lookback thay vì giới hạn toán hạng ≤ 200 — đổi cửa sổ của mọi chỉ báo và tốn cho mọi lane.
