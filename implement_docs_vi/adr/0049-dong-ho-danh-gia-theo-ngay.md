# ADR-0049 — Đồng hồ đánh giá theo ngày

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-10-05
- **Task:** P3-60 · **Kiến trúc:** §3.1.6, §3.2 dòng ⑤ ⑥′, §4.1, §4.2, §10 D4, D26 · **Vấn đề mở:** —

## Bối cảnh

Mọi thống kê đều đọc lợi nhuận theo từng bar của backtest và annual hoá bằng `√periods_per_year`: trên bar 1h đó là √8760 và `T ≈ 50 800` quan sát cho 5,8 năm. Lợi nhuận theo giờ không độc lập — một vị thế giữ nhiều giờ làm các bar liền nhau tương quan — nên Sharpe annual bị lệch (Lo 2002), còn PSR/DSR, vốn coi mỗi bar là một quan sát độc lập, thì quá tự tin. Một campaign 1h cũng sẽ bị chấm trên thang khác với các campaign 1d mà kết quả của chúng đã đặt ra D4 (`holdout_pass: 1.3`) và D17 (MinBTL 1,5). Campaign chuyển sang 1h (2026-10-04).

## Quyết định

- Lock mới ghi `derived.evaluation_clock: daily_v1` (`validation/clock.py`). Lock không có khoá giữ đồng hồ theo bar; tag lạ bị từ chối.
- Dưới `daily_v1`, lợi nhuận theo bar được **gộp kép** theo ngày UTC, `Π(1 + r) − 1` — đúng bằng tỷ lệ equity cuối ngày, chính xác ở chỗ phép cộng thì không. Một bar thuộc ngày chứa khoảng thời gian của nó: `Bars.ts` là thời điểm đóng, nên nhãn là `ts.ceil("D")` và một ngày giờ khớp với một bar ngày. Ngày chỉ có một bar trả nguyên lợi nhuận của nó, nên chuỗi 1d giữ nguyên từng bit.
- Trên đồng hồ đó, annual hoá bằng 365: `sharpe_is`, `sortino_is` và các moment của ranking (`sr_obs`, `skew_is`, `kurtosis_is`, `n_obs`) ở gate ③ — kéo theo `trials.sharpe_is`, `V[SR]`, feature map, archive, calibration; điểm chọn và `sharpe_is` của danh mục; gate ⑤ (DSR với `V[SR]/365`, `T` = số ngày); gate ⑥′ (chi phí × 2, nguồn thứ hai trên các bar chung gộp theo ngày, 60 ngày chung); Sharpe holdout so với `holdout_pass`, đọc từ lock đã kiểm trước khi claim; `RankContext` của ranking GP.
- Gate ③ giữ Sharpe theo bar thành chỉ số mô tả `sharpe_bar`; không gate nào đọc nó.
- `N_eff` tính tương quan mọi trial trên lợi nhuận ngày, bất kể lock (`onc-v2`): nó trải trên toàn ledger, nơi phép ghép theo bar của một chuỗi giờ với một chuỗi ngày chỉ gặp nhau lúc nửa đêm. Trên ledger ngày 2026-10-05 (2.525 trial, đều 1d) nó ra đúng con số cũ.
- Giữ theo bar, không đổi: backtest, artifact `returns_path` (`reproduce.py` so từng byte), `min_trades`, gate ④ (PBO, CPCV, `spp_median_sharpe`), MinBTL (theo năm), kiểm kernel ≡ sandbox.

## Hệ quả

- Campaign 1h và 1d được chấm trên cùng một thang; D4 và D17 giữ nguyên ý nghĩa (INV-124).
- Ranh giới IS/holdout là thời điểm đóng ≥ 00:00 của ngày holdout đầu tiên, nên bar đóng lúc đó — giờ cuối của ngày trước — mở đầu holdout thành một ngày một bar, còn IS kết thúc bằng một ngày 23 bar. Mỗi phía chỉ gộp bar của mình: không gì vượt qua ranh giới.
- `spp_median_sharpe` (số hạng `tanh` của ranking) và trung vị CPCV của `monitor.record_holder` vẫn theo bar, đứng cạnh `sharpe_is` theo ngày: chênh thang ở một số hạng phụ và một chẩn đoán, chấp nhận.
- Kiến trúc: §3.1.6, §3.2 ⑤, §4.1 và §10 (sửa D4, thêm D26) nói rõ thống kê đọc đồng hồ nào. Đơn vị "mỗi quan sát" của ADR-0008 giờ là mỗi ngày dưới `daily_v1`; Sharpe của ADR-0020 là Sharpe theo ngày.

## Các phương án đã cân nhắc

- Cộng lợi nhuận theo ngày — chỉ là xấp xỉ của lợi nhuận gộp kép, không được gì thêm.
- Lợi nhuận tháng — khoảng 70 quan sát trong 5,8 năm: quá ít cho skew, kurtosis và tương quan `N_eff`.
- Lợi nhuận theo bar với hiệu chỉnh Newey-West hoặc Lo — sửa được Sharpe nhưng `N_eff`, PSR và thang 1h/1d vẫn lệch nhau, lại thêm một lựa chọn độ trễ phải khoá.
