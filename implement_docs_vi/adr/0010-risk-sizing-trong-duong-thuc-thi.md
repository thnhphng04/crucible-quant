# ADR-0010 — Risk & Sizing trong đường thực thi

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P1-06 · **Kiến trúc:** §3.4, D7, D12, P4, P5 · **Vấn đề mở:** O11 (đã đóng)

## Bối cảnh

§3.4 cố định công thức sizing (nhánh vol × strength, stop cap dạng `min`, làm tròn lot, FX) và một bước cấp danh mục (scale đồng đều về `target_vol`, leverage cap, ước lượng lại IDM khi rebalance), nhưng không nói bộ ước lượng volatility, giá trị leverage cap, bước danh mục chạy khi nào trong backtest từng bar, hay lock của campaign ghim các lựa chọn này ra sao. GĐ 0 dùng `PlaceholderSizer` notional cố định (O11).

## Quyết định

1. **`core/sizing/`** (thuần, không có code venue): `PositionSizer.size(signal, instrument, account, vol_estimate, n_active, idm, portfolio_scale=1)` đúng như §3.4 — `portfolio_scale` chỉ nhân vào nhánh vol, nên stop cap vẫn là giới hạn cứng; `round_to_lot` làm tròn xuống theo bước, bỏ nếu dưới `min_qty`, cắt ở `max_qty`; `InstrumentSpec.multiplier × fx_rate` đổi sang tiền tệ gốc. `ewma_vol` (span 25 bar, bình phương lợi nhuận đơn giản trung bình 0, annualize, ≥ 10 lợi nhuận nếu không thì không vào lệnh), `instrument_diversification_multiplier` (1/√(wᵀCw), trọng số bằng nhau, ρ < 0 kẹp về 0, trần 2.5), `portfolio_scale` (target / vol ước lượng, rồi gross ≤ `max_leverage`).
2. **`execution/risk.py` `RiskSizer`** là thứ bridge NautilusTrader gọi mỗi bar (protocol `TargetSizer`: symbol, signal, cửa sổ bar đã đóng, equity tài khoản). `n_active` = số instrument trong universe; IDM và scale danh mục được ước lượng lại ở mỗi mốc `rebalance` (Group B `portfolio.rebalance`) từ lợi nhuận 250 bar gần nhất; leverage cap gộp được cưỡng chế mỗi bar theo target hiện tại của các symbol khác. Equity = tiền mặt quote + vị thế theo giá đóng cửa gần nhất. Tài khoản spot tiền mặt ⇒ `max_leverage = 1.0`.
3. **Khoá theo campaign:** `target_vol`, `max_risk_pct`, `rebalance` lấy từ Group B; `vol_span`, `max_leverage`, `idm_cap` được ghi vào `derived.sizing` của lock khi mở campaign (như chi phí và lookback). Không thêm key `user.yaml` mới và không đổi §10.
4. **Lock không có `derived.sizing` bị từ chối ở gate ③** ("mở campaign mới"): các trial cũ của nó được size bằng placeholder, và một campaign không được trộn hai quy tắc sizing.
5. **Đã xoá `PlaceholderSizer`.** Test cơ chế thực thi (khớp ở giá mở bar sau, stop, chi phí; P0-10) dùng sizer `FixedNotional` chỉ có trong test, nên giá trị golden không đổi; sizing có test riêng.

## Hệ quả

- Cưỡng chế INV-05: `test_half_size_when_vol_doubles`, `test_stop_cap_is_min_not_multiplier` (core) và `test_half_size_through_the_risk_sizer` (execution).
- Campaign GĐ 0 `c-20260921-104445` không chạy được gate ③ nữa; cần một campaign mới (chưa có CLI cho việc đó — P1-10 thêm các lệnh campaign).
- Scale danh mục có thể tăng vị thế khi ít instrument đang nắm giữ (quy tắc kiến trúc "scale về target"); stop cap và leverage cap chặn nó lại.
- Sharpe IS thay đổi so với GĐ 0 (khối lượng vị thế khác); chỉ các lần chạy trong campaign mới là so sánh được.

## Phương án đã cân nhắc

- **Bước danh mục bên ngoài backtest (scale lợi nhuận sau khi chạy):** loại — backtest ≠ live (P5).
- **Key Group B mới cho `vol_span` / `max_leverage`:** hoãn — hiện là hằng số cài đặt; nâng chúng lên là thay đổi §10 của kiến trúc, do người dùng quyết.
- **Tiếp tục chạy campaign cũ với sizing mặc định:** loại — âm thầm trộn các quy tắc sizing trong cùng một tập trial.
