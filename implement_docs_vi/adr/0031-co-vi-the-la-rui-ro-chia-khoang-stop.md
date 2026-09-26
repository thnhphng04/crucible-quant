# ADR-0031 — Cỡ vị thế là rủi ro chia khoảng stop

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-25
- **Task:** P3-02 · **Kiến trúc:** §3.4, §7, §10 (D7, D12, D18) · **Rút lại:** P4, INV-05

## Bối cảnh

`PLAN-PER-INSTRUMENT-STRATEGIES.en.md`, người dùng chốt ngày 25/9/2026, quy định mỗi vị thế nhắm lỗ biến động giá đến stop bằng 1% equity tài khoản lúc vào, và tổng ngân sách rủi ro của vị thế mở, lệnh chờ và entry mới không vượt 10% equity.

Công thức đó đã nằm sẵn trong code. `PositionSizer.size` tính `qty_cap = equity × max_risk_pct / (stop_distance × unit_value)` rồi trả `round_to_lot(min(qty_vol, qty_cap), instrument)`. Yêu cầu khác hành vi hiện tại đúng một token: cái `min` bao quanh. Giữ nó thì mỗi vị thế rủi ro **tối đa** 1%; bỏ nó thì **đúng** 1%.

Người dùng đã được trình bày phân biệt đó, kèm ghi chú rằng giữ `min` sẽ bảo toàn hard invariant P4 và không cần đổi kiến trúc, và đã chọn bỏ nó.

Đây không phải quyết định triển khai. P4 nằm trong bảng nguyên tắc của kiến trúc (§1) và trong bảng hard-invariant của `CLAUDE.md`; INV-05 canh nó bằng ba test có tên; D7 (`target_vol` = 10%/năm) tồn tại chỉ để nuôi chân vol targeting. Rút nó là quyết định của người dùng, ghi lại ở đây.

Campaign đầu tiên dưới luật mới cũng là campaign đầu trên Binance USDT-M perpetual, nên D18 cũng dịch. Spike coverage perpetual (P3-01, `scripts/perp_coverage.py`, chạy 25/9/2026) cho thấy ràng buộc là SOLUSDT niêm yết 2020-09-14, tức **5,03 năm** dữ liệu in-sample sau khi cắt holdout 12 tháng — so với 7,72 năm của spot. Ở `minbtl_target_sharpe = 1.5`, MinBTL do đó chặn ở **1.475** trial, so với 36.724 của cửa sổ spot, trong khi `trial_stats.n_eff` đã là 290.

## Quyết định

1. **Cỡ vị thế là `Q = R / d`**, với `R = max_risk_pct × equity` và `d = Signal.stop_distance`, quy đổi qua `instrument.multiplier × fx_rate` và làm tròn **xuống** theo lot step. Volatility vẫn đi vào size đúng một lần, qua `d` (thường k × ATR), không bao giờ qua một chân vol targeting.
2. **Làm tròn lệch một phía.** `round_to_lot` lấy sàn, nên rủi ro thực rơi vào `[R − ε, R]` với ε là phần mất do làm tròn lot và tick. Chữ "±1%" của spec sai dấu: sai số chỉ có thể đi xuống. Entry nào sau làm tròn có rủi ro thấp hơn `R` quá một dung sai đã khai thì **bị từ chối kèm lý do**, không bao giờ được báo là đã cấp đủ.
3. **`target_vol`, `idm` và `portfolio_scale` bị xoá, không phải đánh dấu lỗi thời.** `instrument_diversification_multiplier` và `portfolio_scale` rời `core/sizing/vol_target.py`; `target_vol`, `idm_cap` và `max_leverage` rời `RiskSettings`. Code chết mà trước đây từng gánh việc là loại nguy hiểm nhất: nó vẫn compile cho tới khi ai đó nối lại.
4. **`Signal.strength` bị ghim ở 1.0** cho campaign perpetual, cưỡng chế bằng một luật guardrail ở gate ①a. Field vẫn ở trên contract (P3, INV-03), nhưng nó không được nhân vào `R`: trần danh mục định nghĩa trên `R`, và một `R` bị scale làm trần đó mất tính ràng buộc theo cách không ai theo dõi. Grammar vốn đã render `1.0` dưới dạng literal.
5. **D7 đổi nghĩa chứ không biến mất.** Nó thành `max_portfolio_risk_pct` = 10% equity — tổng cam kết tại stop của vị thế mở, lệnh chờ và entry mới, tối đa 10 vị thế. Vì `q` và `d` đều đóng băng lúc vào lệnh, cam kết của mỗi vị thế là một hằng số tính bằng USDT, nên trần là số học chứ không phải một luật vi phạm viết tay riêng.
6. **Bước danh mục thành luật kết nạp.** Tại mỗi timestamp: xử lý exit, stop, thanh lý và funding; chụp equity một lần; rồi xét entry theo thứ tự canonical, mọi entry trong batch dùng cùng `E` và `R`. Không nới stop, không tự thêm margin, không đóng vị thế cũ để lấy chỗ. Thứ tự được hash vào protocol của campaign.
7. **INV-05 bị thay, không bị xoá.** INV-90 khẳng định rủi ro tại stop rơi vào `[R − ε, R]`; các test đi kèm khẳng định nhân đôi `vol_estimate` **không** làm đổi size — phủ định trực tiếp, thứ biến việc rút P4 thành chứng minh được thay vì giả định — và nhân đôi `d` làm size giảm nửa.
8. **D18 ghi lại cửa sổ đã đo.** Binance USDT-M perpetual, năm contract, `timeframe` cấu hình từ lock, IS từ 2020-09-14, holdout là 12 tháng cuối dưới khoá write-once riêng.

## Hệ quả

- **Vol danh mục thành kết quả, không còn là mục tiêu.** Cảnh báo ở §3.4 của kiến trúc — "BTC vol ~60% nuốt hết rủi ro danh mục và VN30F1M vol ~20% coi như không tồn tại" — không còn áp dụng theo cách cũ, vì rủi ro nay được cân bằng tại stop chứ không tại ước lượng volatility. Thứ mất thật sự là phần bù tương quan của IDM: năm perpetual crypto tương quan cao, mỗi cái rủi ro 1%, không phải năm cược 1% độc lập, và không gì trong luật mới bù lại. Trần danh mục chặn tổng số học, không chặn tổng có tương quan. Ai xét lại chuyện này nên bắt đầu từ đó.
- **Lock viết dưới vol targeting không được trộn với lock viết dưới `Q = R/d`.** `is_gates.risk_settings` vốn đã từ chối lock có trước tầng Risk thay vì trộn hai luật sizing trong trial của cùng một campaign; phép từ chối đó nay phủ luôn lock mang `derived.sizing.max_leverage`. Năm campaign đang OPEN giữ nguyên giá trị hồ sơ; không cái nào chạy tiếp được.
- **Thêm field vào `Research` làm hỏng `assert_lock_matches` cho các campaign đó.** `research_to_dict` dump cả dataclass và guard so dict chính xác, nên một key mới làm `current != locked`. Đường `review` chỉ đọc nên không đi qua guard này, campaign cũ vẫn xem được; thứ mất đi là khả năng chạy lại.
- **Headroom MinBTL nay hữu hạn và đếm được.** 1.475 − 290 = 1.185 trial trên bộ dữ liệu này, vĩnh viễn. Theo nhịp đã đo — 600 trial thô của campaign v4 đẩy `n_eff` từ 145 lên 290 — đó là khoảng năm campaign 600-trial nữa trước khi gate ② đóng. `_check_trial_budget` còn chặt hơn, vì nó cộng budget thô vào `n_eff`. Cửa sổ 3 năm đã được xét và bất khả thi về số học: nó chặn N ở 121, dưới 290 đã ghi.
- **Timeframe là knob thật và để ngỏ.** `periods_per_year` được dẫn xuất ở cả `execution/engine.py` lẫn `validation/pbo_gate.py`; `span_years` và `_years` của archive dùng ngày lịch, nên gate ② độc lập với timeframe theo cấu tạo. Thứ chưa an toàn: `Member.from_dict` mặc định timeframe thiếu thành `"1d"` thay vì từ chối, `DEFAULT_LOOKBACK = 400` đếm theo nến nên đổi nghĩa theo timeframe, và `min_trades` / `min_holding_bars` của D15 cũng đếm theo nến. P3-14 xử lý những chỗ đó.
- **`RANKING_CTX` trong `compare.py` giữ nguyên `periods_per_year=365.0` viết cứng.** Đó là scenario đóng băng cho ranking fingerprint (INV-82) và không được trôi theo cấu hình.
- Phép thử ½-size mà §3.4 gọi là bắt buộc ở giai đoạn 1, và từng bắt được một lỗi đếm hai lần thật ở v0.2, nay không còn. Thứ thay nó phải ít nhất cũng đối kháng bằng, nên INV-90 gồm cả dạng phủ định chứ không chỉ dạng khẳng định.

## Phương án đã cân nhắc

- **Giữ `min(qty_vol, qty_cap)` — rủi ro ≤ 1%.** Đã được khuyến nghị và bị từ chối. Nó bảo toàn P4, INV-05, D7 và giữ kiến trúc nguyên vẹn, và thoả ý định của spec nếu đọc là một cận trên thay vì một mục tiêu. Người dùng chọn cách đọc đúng-1%.
- **Giữ `min` nhưng từ chối entry có chân vol thấp hơn nhiều so với trần rủi ro.** Bảo toàn P4 đồng thời loại bỏ vị thế quá nhỏ, đổi lại thêm một ngưỡng phải hiệu chỉnh và hash vào protocol. Bị từ chối cùng phương án trên.
- **Rút P4 nhưng giữ `portfolio_scale` làm chẩn đoán cố vấn.** Bác bỏ theo quyết định 3: một hệ số scale ngủ đông từng chi phối sizing là lời mời bật lại nó mà không cần ADR.
- **Nhân `R` với `Signal.strength`.** Bác bỏ theo quyết định 4: nó tái lập một đòn bẩy rủi ro thứ hai mà trần danh mục 10% không tính tới.
