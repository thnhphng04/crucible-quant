# ADR-0042 — Loại stop khoá theo campaign; Bollinger tắt mặc định

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-04
- **Task:** P3-55 · **Kiến trúc:** §3.3.1, §10 D21, D24, D25 · **Vấn đề mở:** —

## Bối cảnh

Dưới `bracket_timeout_v1`, engine C lấy mẫu stop Bollinger với xác suất một nửa. P3-54 đo được stop này `<= 0` — nên guard `ready` từ chối tín hiệu — đúng lúc các clause mean reversion kích hoạt: long, band 20, `zscore(20) < −2` ⇒ 100% số bar đó (hai điều kiện là cùng một bất đẳng thức); band 14 ⇒ 67%; `rsi(14) < 30` ⇒ 35–49%. Người dùng quyết định (D25) tắt stop Bollinger cho campaign mới, qua một khoá config để bật lại được mà không sửa code.

## Quyết định

- Khoá nhóm B `research.exit.stop_kinds: [atr | bollinger, …]`, mặc định `[atr]`; danh sách rỗng, loại không biết hoặc lặp lại bị từ chối khi nạp config.
- `lock_stop_kinds(lock)` (`core/strategy/tunable.py`): giá trị đã khoá, hoặc với lock viết trước khi có khoá, các loại stop mà exit của nó đã dùng — `[atr, bollinger]` dưới `bracket_timeout_v1`, `[atr]` trước đó.
- `grammar_config` chỉ đặt `boll_stop_probability` = 0.5 khi lock cho phép `bollinger` (và exit là bracket). Với 0, `point` không bao giờ đổi kiểu stop và crossover chỉ chép stop của chính quần thể.
- Cổng ①a từ chối mọi lời gọi `ind.boll_upper/boll_lower` khi lock không có `bollinger`: grammar chỉ dùng band làm stop.
- Campaign đang mở có lock thiếu khoá sẽ khớp `user.yaml` ở mặc định mới `[atr]` hoặc ở đúng các loại stop nó đang chạy, ngoài ra không khớp; stop của nó vẫn theo lock (`config/lock.py::_comparable`).

## Hệ quả

- Campaign mới chỉ tìm stop ATR (INV-116); gene `n_stop` (ADR-0041) vẫn tune chu kỳ ATR.
- Tám campaign đang mở ngày 2026-10-04 chạy tiếp không đổi; genome của chúng vẫn có cả hai kiểu stop.
- `config` không được import `core`, nên `_comparable` lặp lại quy tắc legacy của `lock_stop_kinds`; một test ghim cả hai.
- Sửa kiến trúc (làm cùng ADR này): §3.3.1 (`exit` nêu các loại stop), D25, thay đổi 0.11 → 0.12.

## Các phương án đã cân nhắc

- Viết cứng chỉ-ATR vào lock mới — bật lại phải sửa code và có ADR; người dùng chọn khoá config.
- Giữ Bollinger nhưng xử lý bar `stop <= 0` cách khác — đổi ngữ nghĩa exit của ADR-0036, không được yêu cầu.
