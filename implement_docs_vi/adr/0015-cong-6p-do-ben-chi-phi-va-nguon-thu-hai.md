# ADR-0015 — Gate ⑥′: stress chi phí và nguồn dữ liệu thứ hai

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-09 · **Kiến trúc:** §3.2 dòng ⑥′, §6.1 ("dữ liệu hai tầng", quy tắc 30%), §10.1 · **Vấn đề mở:** —

## Bối cảnh

⑥′ chạy lại danh mục với phí × 2 và trượt giá × 2 ("Sharpe > 0 và DSR không được giảm quá một ngưỡng khoá theo campaign") và trên một nguồn dữ liệu khác ("Sharpe không được giảm quá 30%"). Với crypto, nguồn research (Binance qua ccxt) *chính là* broker, nên "dữ liệu broker" không cho ý kiến thứ hai; ngưỡng cho DSR khi stress cũng không được nêu.

## Quyết định

1. **Người dùng quyết (21/9/2026):** nguồn thứ hai là **một sàn khác qua ccxt**; ngưỡng stress chi phí là **DSR stress tại `N_eff` ≥ `dsr_min`** (không thêm knob) cộng Sharpe > 0.
2. **`research.data.second_exchange`** (Group B, mặc định `gate` = Gate.io). Một lần dò bằng ccxt ngày 21/9/2026 cho thấy Gate.io là sàn miễn phí duy nhất có đủ năm symbol từ khoảng mốc bắt đầu của Binance (BTC, ETH, XRP 2018; BNB 2019; SOL 2020); OKX, KuCoin, HTX, MEXC, Bitfinex, Kraken và Bybit đều thiếu một symbol hoặc thiếu nhiều năm lịch sử. Phải khác `research.data.exchange`.
3. **Tải về:** `cli data-fetch-second` → `data/is-<exchange>/`, các yêu cầu kết thúc trước khi holdout bắt đầu, bar nào nằm trong một khoảng holdout bị bỏ trước khi ghi, đọc lại qua `ResearchStore` (vốn lại từ chối holdout lần nữa). Giá thời kỳ holdout của sàn thứ hai không bao giờ được lưu.
4. **Chạy lại:** mọi thành viên được chạy lại trong sandbox từ kho source (ADR-0013), với sizing của campaign; lợi nhuận khi stress và lợi nhuận trên nguồn thứ hai được gộp theo trọng số và quy tắc rebalance của danh mục. DSR khi stress dùng cùng `trial_stats` + biến thể như ⑤ (kiểm tra độ bền không phải một lần chọn mới).
5. **Quy tắc nguồn thứ hai:** Sharpe trên nguồn thứ hai so với lợi nhuận IS của các thành viên trên nguồn chính **trên cùng các ngày** (≥ 60 bar chung), `drop = 1 − S₂/S₁ ≤ 0.30`. Thiếu dữ liệu nguồn thứ hai ⇒ loại (fail closed).
6. Hệ số 2 và mức giảm 30% được ghi vào `derived.robustness` của lock.

## Hệ quả

- Test: `tests/validation/test_robustness.py` (bền ⇒ qua; edge bị phí × 2 ăn hết ⇒ trượt; edge còn một nửa trên nguồn thứ hai ⇒ trượt; không có dữ liệu thứ hai ⇒ trượt; kho source bị sửa ⇒ lỗi); `tests/data/test_second_source.py` (không có gì từ holdout trên đĩa).
- Chi phí: 2 lần backtest sandbox cho mỗi thành viên.
- **Việc cần làm ở kiến trúc (người dùng quyết):** ví dụ `user.yaml` ở §10.1 và D18 chưa liệt kê `second_exchange`.

## Phương án đã cân nhắc

- **Cùng sàn, endpoint khác (ví dụ futures):** loại — là một công cụ khác, không phải một nhà cung cấp thứ hai của cùng công cụ.
- **Knob mới `cost_dsr_max_drop`:** người dùng đã loại, chọn dùng lại `dsr_min`.
- **So với toàn bộ Sharpe IS của danh mục:** loại — lịch sử của nguồn thứ hai ngắn hơn; chỉ các ngày chung mới so sánh tương đương.
