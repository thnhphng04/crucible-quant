# ADR-0043 — Danh mục chiến lược theo hướng (grammar v2)

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-04
- **Task:** P3-56 · **Kiến trúc:** §3.1.3, §3.1.5, §3.1.11 · **Vấn đề mở:** —

## Bối cảnh

`clause_category` gán nhãn cho clause tách riêng. Từ P3-12 mỗi scope có một hướng, và ở phía short các nhãn bị lật: bán khi `rsi > 70` là mean reversion, bán khi `Breakout(down)` là breakout. Một số nhãn phía long vốn đã thô: `close < sma(200)` là "trend", và `Distance(a, b, >, k)` với `Distance(b, a, <, −k)` — cùng một điều kiện — có hai nhãn khác nhau. Bit danh mục là một chiều của feature map và các đảo được seed theo danh mục, nên cả hai cùng mang sai sót này.

## Quyết định

- Lock mới ghi `derived.grammar_version: 2`; lock thiếu khoá là version 1 (`lock_grammar_version`).
- v2: mỗi clause có một họ — trend (`Compare`, `Cross`, `Distance`, `Slope` trên giá), momentum (`Threshold`, `CrossLevel`, `Slope` trên oscillator), breakout (`Breakout`) — và một cực, +1 khi nó đúng lúc giá đi lên. Clause hai chuỗi được đọc nhanh − chậm (close, rồi period ngắn hơn, cùng period thì `ema` trước `sma`). Danh mục = họ nếu cực × hướng > 0, ngược lại là mean reversion.
- `clause_category(c, direction, version)` và `categories(genome, direction, version)`; version 1 trả nhãn cũ.
- Pipeline ghi `descriptors.categories` theo hướng của scope (long với key legacy) và version của lock; archive vẫn đọc nhãn đã ghi, nên trial đã có trong ledger giữ nguyên nhãn.
- Dưới v2, đảo có danh mục chỉ seed clause thuộc danh mục đó: `CATEGORY_CLAUSES_V2` (mọi loại với mean reversion), bốc lại tối đa 50 lần. C-random và đảo mở không đổi.

## Hệ quả

- Đảo và bit feature map của scope short đúng với tên của chúng (INV-117).
- Render golden, lấy mẫu dưới v1 và hash protocol so sánh không đổi; các campaign đang mở chạy tiếp.
- Các thay đổi grammar sau (P3-57 …) tăng `GRAMMAR_VERSION` và rẽ nhánh theo `>= N`.
- Không sửa kiến trúc: §3.1.3 nêu tên các danh mục nhưng không quy định cách gán cho clause.

## Các phương án đã cân nhắc

- Đổi tên danh mục thành `long` / `short` — hướng đã là một chiều riêng (scope); danh mục là lý do vào lệnh.
- Chỉ lật nhãn phía short và giữ luật v1 — giữ lại các nhãn long thô và hai nhãn của cùng một điều kiện `Distance`.
