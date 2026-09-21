# ADR-0003 — Ngữ nghĩa khớp lệnh NautilusTrader cho backtest theo bar

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-09-21
- **Task:** P0-10 · **Kiến trúc:** §3.5, §3.3, §3.4 · **Vấn đề mở:** O3

## Bối cảnh

§3.5 yêu cầu mô hình khớp lệnh bi quan: tín hiệu tính trên bar **đã đóng** được thực thi ở giá mở của bar **kế tiếp**, lệnh limit chỉ khớp khi giá đi xuyên qua, và có tính trượt giá. Các đoạn mã NautilusTrader trong kiến trúc chưa được kiểm chứng (§10). Một spike trên `nautilus_trader` 1.231.0 đã cài (có wheel cho `cp312-win_amd64` và manylinux) cho thấy:

- Lệnh market gửi trong `on_bar` khớp ở **giá đóng của chính bar đó** — đúng bar đã sinh ra tín hiệu. Chỉ thêm `LatencyModel(base_latency_nanos=1)` thì khớp ở **giá đóng** của bar t+1. Cả hai đều không phải giá mở kế tiếp.
- `FillModel(prob_fill_on_limit=0)` cho limit khớp khi đi xuyên qua: limit mua 115 với giá thấp nhất đúng 115 không khớp; limit 116 khớp ở 116.
- `FillModel` không có trượt giá cố định theo tick cho dữ liệu bar. Bước giá của crypto (0.01 với BTC/USDT) khiến "N tick" vốn vô nghĩa.
- Với tài khoản `CASH`, một lệnh làm âm số dư sẽ dừng cả backtest với `AccountBalanceNegative` — **âm thầm**: `engine.run()` vẫn trả về bình thường.

## Quyết định

1. **Khớp ở giá mở kế tiếp:** trước mỗi bar t+1 có một `TradeTick` tổng hợp ở giá mở của nó, gắn thời điểm 1 ns sau khi bar t đóng, và sàn có độ trễ 1 ns. Lệnh gửi lúc bar t đóng đến matching engine đúng tick đó và khớp ở giá mở của t+1. Bar được gắn thời điểm đóng (P0-09).
2. **Limit:** `FillModel(prob_fill_on_limit=0, prob_slippage=0)`, có seed.
3. **Trượt giá = phí taker cộng thêm:** `CostModel(fee_rate=0.001, slippage_bps=5)`; phí maker và taker của instrument đều bằng `fee_rate + slippage_bps/10⁴`. Gate ⑥′ nhân cả hai qua `CostModel.scaled`.
4. **Spot, long hoặc flat:** tài khoản `CASH`, `NETTING`. Tín hiệu `short` nghĩa là flat; được đếm trong `BacktestResult.signals` và không bao giờ giao dịch. Short cần sàn margin (GĐ 2+).
5. **Một stop bảo vệ cho mỗi vị thế:** stop-market `reduce_only` ở `close − stop_distance`, huỷ và đặt lại mỗi bar. Giá nhảy khoảng trống qua stop thì khớp ở giá mở của khoảng trống, không phải ở giá kích hoạt.
6. **Dải tái cân bằng:** vị thế đang mở chỉ được đổi kích thước khi khối lượng mục tiêu lệch hơn 25%, để mục tiêu notional cố định không giao dịch mỗi bar.
7. **`PlaceholderSizer`** (đến P1-06): `50% tiền mặt / số_mã × strength / close`. Nửa còn lại trả cho khoảng trống giá mở kế tiếp cao hơn giá đóng lúc có tín hiệu.
8. **Bị cắt cụt là lỗi:** `run_backtest` đếm số bar strategy đã thấy; ít hơn số bar đưa vào thì ném `BacktestAbortedError`.
9. **Equity** được dựng lại từ các lần khớp và định giá theo giá đóng mỗi bar; lợi nhuận mỗi bar được quy năm với 365 kỳ/năm (crypto giao dịch mọi ngày).

## Hệ quả

- Oracle mức 2 (hành động trên chính bar sinh tín hiệu) không thể kiếm lời từ giá đóng của bar đó: lần khớp sớm nhất là giá mở kế tiếp.
- Chi phí là một con số cho mỗi campaign, ghi vào lock cùng phần còn lại của Nhóm B.
- Test: `tests/execution/test_engine.py` (INV-35) cố định mọi quy tắc trên bằng giá trị kỳ vọng tính tay.
- Nâng cấp Nautilus sau này phải chạy lại các test spike; bản ghim là `>=1.231,<1.232`.

## Phương án đã cân nhắc

- **Hậu xử lý lần khớp** (để Nautilus khớp ở giá đóng rồi định giá lại theo giá mở kế tiếp): bác bỏ — đường live sẽ khác đường backtest (P5).
- **Lớp con `FillModel` tuỳ biến có trượt giá theo tick:** bác bỏ — không thay đổi thời điểm lệnh do bar kích hoạt đến sổ lệnh, và tick ít ý nghĩa với crypto.
- **Tài khoản margin để cho phép short ngay:** hoãn — GĐ 0 chỉ cần harness chạy một strategy tham chiếu chỉ-long.

## Sửa đổi (2026-09-21, review GĐ 0)

- **Khoảng trống trong dữ liệu:** tick mở cửa của bar t+1 được gắn 1 ns sau thời điểm mở của chính bar đó (`close − timeframe`), không phải 1 ns sau khi bar t đóng. Với bar liên tục thì không gì thay đổi; khi thiếu bar, lệnh giờ chờ bar kế tiếp mở cửa, thay vì khớp lúc bar t đóng ở một giá được in ra sau đó (look-ahead). Bar chồng lấn (khoảng cách < timeframe) bị từ chối. Test: `tests/execution/test_engine.py::test_gap_in_data_is_not_a_look_ahead`.
- **Trade được đếm từ các lần khớp,** theo thứ tự thời gian (flat → long → flat), không từ vị thế tại giá đóng các bar: một lệnh vào bị stop ngay trong cùng bar vẫn là một trade giữ 0 bar, điều mà `min_trades` và `min_holding_bars` của gate ③ phải thấy. Test: `::test_round_trips_inside_one_bar_are_counted`.
- **Độ chính xác khối lượng lệnh = của đồng base** (2026-09-22, lần chạy dữ liệu thật đầu tiên): instrument dùng 8 chữ số thập phân, nhưng Nautilus giữ số dư XRP ở 6; bán hết một vị thế để lại −0.000001 XRP và engine dừng (`AccountBalanceNegative`), nên gate ③ loại EMA crossover vì một lỗi của harness. Nay mỗi instrument lấy `min(8, precision của đồng base)`, và tầng Risk làm tròn theo bước lot đó cho từng symbol. Test: `::test_base_currency_precision_below_the_size_precision`.
