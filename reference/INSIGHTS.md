# Tổng hợp insight — chiến thuật Bollinger AVAX (thư mục `reference/`)

> Ghi chép nghiên cứu cá nhân, ngoài lab `quantcrucible` (không ledger, không holdout). Mọi con số dưới
> đây đến từ file trong `reference/results/` (đường dẫn ghi ở từng mục). Trừ khi ghi khác: AVAX/USDT
> futures, khung **15min**, phí giữ lệnh 1 lần / 8 giờ (`fee_bars = 32`), đơn vị **R** (1R = rủi ro của
> một lệnh = khoảng cách tới stop), "R/tháng" = tổng R ròng (sau phí) chia số tháng.

## 1. Thiết lập

| Thành phần | Ở đâu |
|---|---|
| Dữ liệu | `long_bollinger+RSI/avaxusdt_futures_5min.csv` — nến 5 phút, 2022-06-21 → 2025-06-20 (CSV phía short giống hệt) |
| Chiến thuật gốc | `long_bollinger+RSI/`, `short_bollinger+RSI/` — script GPU + notebook (kernel "Python (trading)", anaconda) |
| Squeeze | `long|short_bollinger+atr_ratio/`, `long|short_bollinger+band_ratio/` |
| Logic long mới | `long_bollinger+meanrev/`, `+pullback/`, `+RSI+regime/`, `+RSI+volume/` — dùng chung `long_logic_engine.py` |
| Nhiều lệnh chồng nhau | `multi_position/long_bollinger+RSI/`, `multi_position/short_bollinger+RSI/` — dùng chung `multi_position/multi_position_engine.py` |
| Walk-forward | `walk_forward_15min.py` (9 tháng train / 3 tháng test, 9 fold, test nối liền 2023-04 → 2025-06) |
| Đối chứng vào lệnh ngẫu nhiên | `random_entry_benchmark.py` |
| Kết quả tóm tắt | `results/` |

Exit chung của mọi script: một vị thế mỗi lúc; stop ban đầu (long: `entry − sl × (entry − lower)`);
TP = `tp_sl_ratio × risk`; timeout `max_holding_bars = 288` nến (3 ngày); phí vào lệnh `0.001 / sl_ratio`
(bỏ lệnh nếu > 0,2R); phí giữ lệnh `0.0001 × (số nến / fee_bars) / sl_ratio`.

Mọi kết quả GPU (fp32) đã được đối chiếu với replay CPU float64: 0 lệnh lệch ở phía long. Phía short có
1/60 bộ lệch 1 lệnh do giá/RSI sát ngưỡng tới 1e-6 (fp32 vs fp64), không ảnh hưởng kết luận.

## 2. Bài học về phương pháp

1. **Bộ "best" in-sample không đáng tin.** Bộ đứng đầu trên train rơi về ~0 hoặc âm ngoài mẫu (long RSI
   18m/6m: +1,37 → +0,10 R/tháng; short RSI: +1,58 → −0,08). Đổi thước đo xếp hạng (Sharpe → Martin) là
   bộ đứng đầu đổi hẳn (long RSI best-1: +0,10 → −0,78).
2. **Thứ hạng train không dự báo test.** Spearman train↔test ≈ 0 ở mọi logic (9m/3m: −0,02…0,10; 18m/6m:
   ≤ 0,35). Cái bền là **vùng/họ tham số**, không phải một bộ cụ thể.
3. **Giữ nhóm an toàn hơn chọn một bộ.** Short RSI 18m/6m: cả grid +0,84 ±0,60, top 25% +0,69 ±0,64, bộ
   best −0,08 ±1,22 R/tháng (`results/ensemble_vs_best.py`). Tâm vùng phẳng đôi khi tốt (short atr_ratio
   +0,91, dương 3/3 fold) nhưng không ổn định giữa các logic.
4. **Công thức score có bình phương/tích làm mất dấu** (`performance**2/...`, `r_sharpe*r_martin`) ⇒ xếp
   bộ lỗ lên đầu. Dùng metric giữ dấu (`r_martin_monthly`, `r_sharpe_monthly`).
5. **Chọn exit hoặc grid sau khi nhìn dữ liệu là rò rỉ.** Grid long RSI được mở rộng sau khi thấy đoạn
   2024-07 → 2025-06 ⇒ các fold sau lạc quan. Cho walk-forward tự chọn exit trên train ("ALL exits") còn
   tệ hơn chốt exit trước (top 10 regime −0,02 so với +0,68).
6. **Đánh giá ở cấp cả họ** (trung vị toàn grid, % bộ có lãi, top 25%) ít bị nhiễu chọn lọc hơn top 10/best.
7. Mỗi quý test chỉ ~10–30 lệnh cho top 10 ⇒ từng fold rất nhiễu; cần nhiều fold.

## 3. Chỉ báo: ADX vs atr_ratio / band_ratio

- `atr_ratio` / `band_ratio` đo **biến động hiện tại so với nền dài** (quanh 1, tự chuẩn hoá theo lịch sử);
  ADX đo **mức độ một chiều** của chuyển động (0–100, chuẩn hoá theo ATR cùng period). Hai trục độc lập.
- `band_ratio` dùng độ lệch chuẩn của **mức giá** nên bị drift lẫn vào: xu hướng đều kéo `band_ratio` < 1
  (giả "squeeze"); `atr_ratio` không bị.
- ADX trên random walk không bằng 0 và mốc nền giảm theo period (~1/√n) ⇒ ngưỡng phải hiệu chỉnh theo n.

## 4. Kết quả chiến thuật (walk-forward, ngoài mẫu)

### 4.1 Squeeze (Bollinger breakout + atr_ratio / band_ratio thay RSI)
Thua RSI ở cả hai phía và mọi cách đánh giá. 9m/3m (`results/wf_9m3m/`): long atr −1,08, long band −1,71,
short atr −0,27, short band −0,48 R/tháng (trung vị toàn grid ở test); RSI tương ứng long −0,02, short +0,52.
Ở 1h in-sample squeeze có vẻ giúp short — không giữ được ngoài mẫu ở 15min.

### 4.2 Short RSI — logic bền nhất
9m/3m, xếp theo Martin (`results/wf_9m3m_martin/`): trung vị toàn grid **+0,52** (6/9 quý dương), 63% bộ có
lãi, **top 10 +0,74 (9/9 quý dương)**, top 25% +0,68 (7/9). Theo Martin, bộ best ổn định hơn Sharpe
(dao động −0,61…+1,12 thay vì −2,11…+6,45) và tham số lặp lại: `rsi_window 80` (7/9 fold),
`rsi_threshold` 41–44, `boll_window` nhỏ. Yếu ở quý AVAX tăng mạnh (2023-Q4, 2024-Q1).
Lưu ý: chọn Martin sau khi đã thấy kết quả Sharpe ⇒ có chọn lọc.

### 4.3 Long RSI — entry có lợi thế, nhưng kết quả quanh 0 với TP 1,1
- 9m/3m: trung vị toàn grid −0,02 (`fee_bars 8`) → **+0,08 (`fee_bars 32`)**, top 10 0,18 → 0,20
  (`results/wf_repro_fee8/`, `results/wf_newlong_fee32/`). Chênh phí long 8 / short 32 ở script gốc chỉ
  giải thích ~0,1 R/tháng.
- Mở rộng grid (`boll_window` tới 50, `dev` tới 2,8, threshold tới 70) nâng trung vị toàn grid 18m/6m từ
  −0,13 lên +0,06 nhưng không cải thiện việc chọn (best không đổi, top 10 0,28 → 0,23) — có rò rỉ (mục 2.5).
- Martin thay Sharpe không đổi kết luận phía long (top 10 ~0).

### 4.4 Logic long mới (fee 32, 9m/3m, `results/wf_newlong_fee32/`)

| Logic | Trung vị toàn grid | Top 10 | Top 10 dương |
|---|---|---|---|
| Long RSI (baseline) | +0,08 | 0,20 | 6/9 |
| RSI + regime (`close > SMA(trend)`) | +0,14 | 0,05 | 6/9 |
| RSI + volume (`vol > k × SMA(vol)`) | +0,09 | 0,20 | 6/9 |
| Mean reversion ở dải dưới + RSI quá bán | −0,27 | −0,61 | 1/9 |
| Pullback ở dải dưới + xu hướng tăng | −0,28 | −0,45 | 1/9 |
| (trigger "quay lại dải dưới" không lọc) | −1,80 | −0,72 | 1/9 |

Không logic nào đạt tiêu chí chốt trước (trung vị > 0, top 10 dương ≥ 7/9, hơn baseline). Mua ở dải dưới
thua rõ ở 15min.

## 5. Exit bất đối xứng (long breakout)

Walk-forward 9m/3m, 6 mức TP × 4 mức trailing theo R (`results/wf_exit_engine/`, `results/wf_exit_legacy/`):

| Logic | Exit | Trung vị toàn grid | Top 25% | Top 10 | Top 10 dương | Quý tệ nhất (top 10) |
|---|---|---|---|---|---|---|
| Long RSI | TP 1,1 | 0,01 | −0,08 | 0,19 | 6/9 | −1,21 |
| | **Không TP** (stop + timeout) | **0,35** | **0,71** | 0,77 | 4/9 | −2,81 |
| | TP 5 + trailing 3R | 0,33 | 0,36 | 0,88 | 5/9 | −1,25 |
| RSI + regime | TP 1,1 → **không TP** | 0,14 → **0,34** | 0,11 → **0,84** | 0,05 → 0,68 | 6/9 → 6/9 | −2,22 → −2,95 |
| RSI + volume | TP 1,1 → **không TP** | 0,09 → **0,49** | 0,06 → **0,87** | 0,20 → 0,72 | 6/9 → 6/9 | −1,55 → −1,71 |

- TP rộng / không TP tốt hơn TP 1,1 ở **mọi** logic breakout; vẫn đúng khi bỏ quý 2023-Q4 (long RSI trung
  vị −0,12 → +0,12, top 10 0,18 → 0,61).
- **Trailing theo R không thắng timeout**; trailing 1R tệ nhất. Cái tạo lợi nhuận là **giữ lệnh đủ lâu** —
  timeout 288 nến hiện là exit thực tế, chưa được thử giá trị khác.
- Hồ sơ đổi thành "bắt sóng tăng": lãi lớn ở quý tăng, lỗ nặng hơn ở quý giảm; số quý dương vẫn 4–6/9.
- Meanrev / pullback / squeeze vẫn âm với mọi exit.

**Drawdown** (toàn dữ liệu, so theo cặp cùng entry; `results/drawdown_by_exit.log`):

| Logic | R/tháng TP 1,1 → không TP | Max DD theo giá thị trường (R) | DD so với TP 1,1 |
|---|---|---|---|
| Long RSI | 0,01 → 0,60 | 21,6 → 23,2 | 1,12× |
| RSI + regime | 0,15 → 0,71 | 13,7 → 15,8 | 1,14× |
| RSI + volume | 0,06 → 0,70 | 20,0 → 24,4 | 1,17× |

Drawdown tăng nhẹ (~12–17%), lợi nhuận tăng nhiều lần. Drawdown theo lệnh đã đóng đánh giá thấp DD của
"không TP" (lãi chưa chốt bị trả lại): chênh ~12–14% so với ~5–7% ở TP 1,1. DD tuyệt đối của phía long lớn
với mọi exit (14–24R / 3 năm ⇒ 14–24% nếu rủi ro 1%/lệnh).

## 6. Đối chứng vào lệnh ngẫu nhiên — entry có lợi thế thật

Cùng exit, cùng số lệnh, cùng cỡ stop theo độ rộng dải; chỉ khác thời điểm vào (200 bộ × 200 bản sao,
toàn dữ liệu; `results/random_entry/`):

| Cấu hình | Chiến thuật | Ngẫu nhiên | Lợi thế entry | % bộ lợi thế > 0 | % bộ vượt p95 ngẫu nhiên |
|---|---|---|---|---|---|
| Long RSI, không TP | +0,32 | −0,31 | **+0,61** | 96% | 23% |
| Long RSI, TP 1,1 | +0,10 | −0,41 | **+0,51** | 94% | 53% |
| RSI + regime, không TP | +0,71 | −0,32 | **+1,00** | 100% | 42% |
| RSI + volume, không TP | +0,64 | −0,36 | **+1,13** | 98% | 37% |
| Short RSI, TP 1,1 (đối chứng dương) | +0,35 | −0,06 | +0,52 | 91% | 33% |

- Lợi thế entry phía long ngang/lớn hơn short; dương ở 11/12 quý, **kể cả quý giảm** (chiến thuật lỗ ít hơn
  ngẫu nhiên khi thị trường giảm, lãi nhiều hơn khi tăng). Yếu đi ở 2025.
- **Lãi long = lợi thế entry + xu hướng thị trường dưới cùng exit.** Nhiều quý giảm kéo lệnh long ngẫu nhiên
  xuống −2…−3 R/tháng — entry tốt không bù nổi.
- TP 1,1 giữ được lợi thế (+0,51) nhưng mức nền ngẫu nhiên tệ hơn (−0,41) ⇒ chiến thuật chỉ còn +0,10.

## 7. Long/short bù trừ

Theo quý, trung vị toàn grid short RSI và long RSI + regime tương quan **−0,7**. Ghép 50/50 (mỗi bên 0,5R):
trung bình 0,33 (short riêng 0,52), độ dao động 0,39 (1,05), quý tệ nhất −0,44 (−1,28), 7/9 quý dương ⇒
lãi trên rủi ro 0,85 so với 0,50. **Không còn đúng với top 10** (tương quan −0,07; ghép làm mọi chỉ số tệ
hơn short riêng). Chưa mô phỏng danh mục theo ngày.

## 8. Tài liệu tham khảo (tóm tắt)

- Cổ phiếu: **leverage effect / volatility feedback** — biến động tăng sau cú giảm ("lên cầu thang, xuống
  thang máy"); "crisis alpha" của trend-following đến từ phía bán trong khủng hoảng.
- Crypto: **inverse leverage effect** (biến động phản ứng mạnh hơn với tin tốt, như tài sản đầu cơ) nhưng
  **không ổn định theo thời kỳ**; skewness/tâm lý vé số; **thanh lý dây chuyền** làm cú giảm ở khung phút cực
  nhanh (10/10/2025: ~19 tỷ USD trong vài giờ, 70% trong 40 phút) rồi phục hồi trong ngày.
- Funding perpetual được cho là thường dương (long trả short) — nguồn yếu, cần tự kiểm; backtest hiện tính
  phí giữ lệnh đối xứng ⇒ phía long có thể lạc quan.
- Hàng hoá: inverse leverage phổ biến ở giá giao ngay (vàng, khí), dầu/đồng giống cổ phiếu.
- Momentum crypto: long-only tốt hơn long-short; **đồng thua hay bật lại gây lỗ cho phía short** ⇒ short
  thắng trên AVAX 2023–2025 có thể đặc thù giai đoạn (AVAX giảm từ giữa 2024).

## 9. Exit bằng indicator (long breakout)

Engine có thêm `exit_rule` / `exit_param` / `max_holding_bars` theo từng tổ hợp. Exit được đánh giá ở close
của nến j và khớp ở close đó, sau các kiểm tra timeout → stop ban đầu → TP → hết dữ liệu. Stop ban đầu
luôn giữ, không TP. Đã kiểm tra: GPU khớp replay CPU (0 lệnh lệch), grid cũ tái tạo đúng.

| Rule | Thoát long khi | Tham số thử |
|---|---|---|
| RSI | `RSI(rsi_window) < level` | 40, 45, 50, 55 |
| Donchian | `close < đáy thấp nhất N nến trước` | 20, 50, 100, 200 |
| Chandelier | `close < đỉnh cao nhất từ lúc vào − k × ATR(50)` | 2, 3, 4, 5 |
| %B | `(close − lower)/(upper − lower) < b` (dải của entry) | 0,3 … 0,7 |

Walk-forward 9m/3m, giữ lệnh tối đa 288 và 1152 nến, cho long RSI / RSI+regime / RSI+volume
(`results/wf_ind_exit_*`, bảng gọn `results/ind_exit_summary.txt`). Tiêu chí chốt trước so với baseline
"không TP, 288": (a) trung vị toàn grid, (b) top 25%, (c) số fold dương của top 25% — mỗi cái ≥ baseline ở
≥ 2/3 logic; (d) drawdown mark-to-market ≤ 1,2 × TP 1,1.

**Trung bình 3 logic (R/tháng ở test, 9 quý):**

| Exit | Giữ tối đa | Trung vị grid | Top 25% | Top 10 | Quý tệ nhất top 10 | MTM DD so TP 1,1 | Đạt (a–d)? |
|---|---|---|---|---|---|---|---|
| TP 1,1 | 288 | 0,08 | 0,03 | 0,15 | −1,66 | 1,00× | — |
| Không TP (baseline) | 288 | 0,39 | 0,81 | 0,72 | −2,49 | 1,12–1,17× | — |
| **Donchian 50** | **1152** | **0,44** | **0,91** | **1,12** | **−0,91** | **0,96–1,02×** | **✓** |
| Donchian 50 | 288 | 0,44 | 0,80 | 0,82 | −1,13 | chưa đo | ✗ (b): top 25% hơn baseline 1/3 logic |
| Donchian 200 | 288 | 0,45 | 0,89 | 1,02 | −1,65 | 1,10× | ✓ |
| Donchian 200 | 1152 | 0,59 | 1,06 | 1,58 | −4,09 | 1,41–1,51× | ✗ (d) |
| RSI 50 | 1152 | 0,17 | 0,53 | 0,64 | −0,93 | — | ✗ (a, b) |
| Donchian (N chọn trên train) | 288 / 1152 | 0,29 / 0,35 | 0,58 / 0,64 | 0,78 / 0,53 | | | ✗ |
| Chandelier, %B (mọi tham số) | | ≤ 0,06 | ≤ 0,38 | ≤ 0,50 | | | ✗ |

Kết luận:
1. **Donchian 50, giữ tối đa 1152 nến** là exit được chọn: đạt cả 4 tiêu chí, trùng giá trị cố định theo lập
   luận đã chốt trước (N = 50), drawdown thấp nhất trong các exit "để lệnh chạy" (đóng lệnh: 0,83–0,86 × TP 1,1;
   giá thị trường: 0,96–1,02×), quý tệ nhất nhẹ hơn baseline rất nhiều, top 25% dương 6/9 quý ở cả 3 logic.
   Bỏ quý 2023-Q4 vẫn ≥ baseline (long RSI: +0,16 so với +0,12).
2. **Donchian 200** cho trung bình cao hơn nhưng phần lớn nhờ một quý (2023-Q4: +5…+11 R/tháng); bỏ quý đó
   thì âm. Với giữ 1152 nến, drawdown tăng 1,4–1,5× và lệnh vào ngẫu nhiên cũng hoà vốn (+0,02 R/tháng) ⇒ exit
   này chủ yếu ăn theo xu hướng thị trường, bằng chứng về timing yếu (chỉ 10–24% bộ vượt p95 ngẫu nhiên).
3. **Chọn tham số exit trên train tệ hơn cố định** (Donchian N chọn theo fold: 0,29–0,35 so với 0,44) — cùng
   bài học như mục 2.
4. **Chandelier và %B thua**: thoát quá sớm (giữ trung vị 4–11 nến), giống TP 1,1. RSI 50 ổn định nhất theo số
   quý dương (top 10 dương 6–8/9) nhưng lãi thấp.
5. Giữ lâu hơn **chỉ có lợi khi có exit indicator**: "không TP" với 1152 nến tệ hơn 288 (quý tệ nhất −10,2).
   Với Donchian 50, timeout 288 cắt đúng những lệnh đang theo xu hướng dài (lãi nhiều nhất) — nới lên 1152
   nâng top 25% từ 0,80 lên 0,91 (rõ nhất ở regime: 0,79 → 1,06); đa số lệnh vẫn thoát sớm nhờ Donchian.
6. **Đối chứng ngẫu nhiên** (`results/random_entry_exits_*`): Donchian 50 / 1152 giữ nguyên lợi thế entry
   (+0,54 / +0,93 / +1,10 R/tháng cho long RSI / regime / volume; 99,5–100% bộ dương; 31–50% vượt p95), mức
   nền ngẫu nhiên vẫn âm (−0,31…−0,48) ⇒ lãi đến từ timing chứ không phải xu hướng thị trường.

## 9b. Bảng so sánh các nghiên cứu (walk-forward 9m/3m, trung bình 3 logic long breakout)

| Phương án | Trung vị grid | Top 25% | Top 10 | Ghi chú |
|---|---|---|---|---|
| Long, TP 1,1 (gốc) | 0,08 | 0,03 | 0,15 | entry có lợi thế nhưng exit cắt mất |
| Long, TP 5 + trailing 3R | ~0,32 | ~0,45 | ~0,76 | trailing theo R không hơn timeout |
| Long, không TP, 288 | 0,39 | 0,81 | 0,72 | quý tệ nhất −2,5 |
| **Long, Donchian 50, 1152** | **0,44** | **0,91** | **1,12** | quý tệ nhất −0,9, DD ≈ TP 1,1 |
| Short RSI, TP 1,1 (tham chiếu) | 0,52 | 0,68 | 0,74 | top 10 dương 9/9 quý |

Lưu ý chung: danh sách exit và ngưỡng chốt trước khi chạy; nhưng phạm vi grid entry và việc chọn Martin có
chọn lọc từ trước (mục 2.5). Long vẫn phụ thuộc pha thị trường (top 10 dương 5–7/9 quý).

## 9c. Nhiều lệnh chồng nhau (`multi_position/`, `results/wf_multi_position/`)

Bản gốc giữ một lệnh và bỏ mọi tín hiệu lúc đang có lệnh. `multi_position/` (engine
`multi_position_engine.py`, long + short Bollinger + RSI, exit TP 1,1 / 288 nến như gốc, phí 32) cho **mỗi tín
hiệu một lệnh riêng**, đóng độc lập; `max_open` giới hạn số lệnh mở (64 = không giới hạn thực tế; **`max_open = 1`
trùng từng bit với script gốc**, đã kiểm tra trên 3.300 bộ mỗi phía). Thêm cột `mtm_max_dd` (drawdown khi định
giá cả lệnh đang mở), `max_concurrent`, `avg_concurrent`, `skipped_entries`. R tính **cho mỗi lệnh** ⇒ R/tháng
của bản nhiều lệnh bị thổi phồng theo số lệnh; so công bằng phải dùng thước đo không phụ thuộc quy mô
(Martin, R/tháng ÷ max DD MTM).

In-sample (grid notebook; long 2023-01 → 2024-07, short 2023-07 → 2025-06): số lệnh ×2,6 (long) / ×3,5 (short);
lệnh mở cùng lúc tối đa trung vị 8 / 10 (cao nhất 27 / 33), trung bình chỉ ~1 lệnh/nến ⇒ lệnh **dồn cụm** vào
cùng một nhịp giá. Max DD MTM trung vị 18 → 39 R (long), 15 → 38 R (short).

Walk-forward 9m/3m (cùng fold, cùng grid, `--set max_open=1,64`):

| | Long 1 lệnh | Long nhiều lệnh | Short 1 lệnh | Short nhiều lệnh |
|---|---|---|---|---|
| Trung vị grid, R/tháng | 0,08 | 0,34 | 0,52 | 1,92 |
| Trung vị grid, Martin | 0,19 | 0,29 | 0,47 | 0,46 |
| Trung vị grid, R/tháng ÷ DD MTM | 0,06 | 0,08 | 0,16 | 0,17 |
| Top 10, R/tháng (quý dương) | 0,20 (6/9) | 0,64 (6/9) | **0,74 (9/9)** | 0,62 (5/9) |
| Top 10, Martin | 0,65 | 0,72 | **1,69** | 0,44 |
| Top 10, R/tháng ÷ DD MTM | 0,18 | 0,21 | **0,32** | 0,11 |
| Top 10: lệnh mở tối đa ở test | 1 | 3,5 | 1 | 3,5 |

Kết luận:
1. **Cho chồng lệnh không tạo thêm lợi thế, chỉ tăng đòn bẩy.** R/tháng tăng 3–4 lần nhưng drawdown tăng theo;
   tính trên một đơn vị rủi ro (Martin, R/tháng ÷ DD MTM) cả họ tham số gần như **không đổi** (short 0,47 →
   0,46; long nhỉnh nhẹ 0,19 → 0,29). Tín hiệu bị bỏ ở bản gốc có chất lượng tương đương tín hiệu được vào,
   nhưng **tương quan cao** với lệnh đang mở (cùng nhịp giá) ⇒ lãi và lỗ cộng dồn cùng chiều.
2. **Chọn bộ tham số trên train kém đi ở short.** Top 10 short: Martin 1,69 → 0,44, quý dương 9/9 → 5/9. Xếp
   hạng theo Martin của equity đã chốt ưu tiên các bộ có cụm lệnh tình cờ thắng ở train; cụm đó không lặp lại.
   Long không đổi đáng kể.
3. Nếu muốn dùng nhiều lệnh thực tế: phải chia rủi ro (ví dụ R/N theo số lệnh mở tối đa, hoặc một trần rủi ro
   chung như P4′) — khi đó lợi nhuận quy về gần bản 1 lệnh (R/tháng ÷ lệnh mở tối đa: short 0,47 → 0,20
   in-sample). Bản 1 lệnh của short vẫn là phương án tốt nhất.
4. Xếp hạng bằng `performance/max_dd*r2**5` thay Martin (`results/wf_multi_position_pdr2/`): trung vị grid không
   đổi (xếp hạng không ảnh hưởng cả họ). Top 10 R/tháng (quý dương): short 1 lệnh **0,98 (9/9)**, best-1 0,80
   (8/9) — nhỉnh hơn Martin về R nhưng Martin của top 10 thấp hơn (1,43 vs 1,69); short nhiều lệnh 0,79 (6/9),
   Martin 0,36, phụ thuộc một quý (2025-Q1 +5,67); long 1 lệnh 0,23 (6/9), long nhiều lệnh 0,27 (5/9; xếp
   theo Martin là 0,64). Kết luận 1–2 giữ nguyên với cả hai công thức. Lưu ý: công thức thử sau khi đã thấy kết
   quả Martin ⇒ có chọn lọc.

## 9d. Short RSI trên 17 coin (`results/wf_multi_asset_short_rsi/`, `walk_forward_multi_asset.py`)

Cùng quy trình đã chốt (script short gốc 1 lệnh, grid notebook 102.900 bộ, phí 32, 9m train / 3m test) trên
`reference/data/`: 13 coin 2023-01 → 2026-01 (nến 15 phút dù tên file ghi `5min`), SUI, TON (ngắn hơn), BNB và
MATIC (5 phút, tới 2025-06 / 2024-09). Quý test theo quý dương lịch (coin 3 năm: 2024-Q1 → 2025-Q4, 8 quý). Lịch
cũ trên CSV AVAX cũ tái tạo đúng `wf_9m3m_martin` (0,524 / top 10 0,74). Tiêu chí chốt trước: trung vị grid > 0 ở
≥ 2/3 coin **và** top 25% trung bình các coin > 0 ở ≥ 2/3 quý.

Kết quả (xếp hạng Martin; `performance/max_dd*r2**5` cho kết luận giống hệt):
1. **Không đạt tiêu chí**: trung vị grid dương ở 10/17 coin (59%), top 25% liên coin dương ở 6/11 quý (55%;
   4/8 nếu chỉ tính các quý có ≥ 15 coin). Thua rõ: BTC −0,70, BNB −0,60, LTC −0,55 R/tháng (trung vị grid).
2. **Lãi chủ yếu là beta thị trường**: tương quan với lợi nhuận giá quý test −0,54 (trung vị grid), −0,45 (top 10).
   Quý giá giảm > 15%: trung vị grid +0,78, top 25% +0,93; quý đi ngang: +0,04 / +0,12; quý tăng > 15%: −0,84 /
   −0,92 R/tháng. Hồi quy theo lợi nhuận giá: tại thị trường 0% chỉ còn +0,08 (grid) / +0,15 (top 25%) R/tháng.
   Mọi coin cùng lãi ở 2025-Q1 (16/16) và cùng lỗ ở 2024-Q1, 2024-Q4, 2025-Q3 ⇒ đa dạng hoá coin không giúp.
3. **AVAX là ngoại lệ**: top 10 dương 8/8 quý (0,74), kể cả 2 quý dữ liệu hoàn toàn mới 2025-Q3 (+0,60, dù AVAX
   +67%) và 2025-Q4 (+0,89). Nhưng grid/công thức đã chỉnh trên AVAX, và coin khác không lặp lại ⇒ có thể là đặc
   thù AVAX hoặc do chọn lọc; không nên coi là logic chung cho crypto.
4. **Top 10 / top 100 chọn trên train không hơn bốc ngẫu nhiên** (`--top-n 10 100`, 130 fold, Martin): trung vị
   test top 10 / top 100 trung bình +0,02 / +0,02 R/tháng (cả grid −0,00); fold có trung vị dương 49% / 52%;
   tỉ lệ bộ có lãi ở test 50% / 51% (cả grid 48%). Theo pha giá: giảm > 15% ⇒ top 100 có lãi 69% bộ; đi ngang
   53%; tăng > 15% 31% — y như cả grid (70% / 51% / 25%). Riêng AVAX: top 10 có lãi 85% bộ, top 100 77%, trung vị
   dương 8/8 quý. Công thức `performance/max_dd*r2**5` cho kết quả tương tự.
5. **Bộ lọc xu hướng không cứu được** (`short_bollinger+RSI+regime/`, `results/wf_multi_asset_short_rsi_regime/`):
   chỉ short khi `close < SMA(trend_window)`, SMA tính trên toàn lịch sử (không khởi động lại mỗi cửa sổ);
   `trend_window` chốt trước 0 (tắt) / 672 / 1344 / 2880 / 5760 / 8640 nến = tắt / 1 tuần / 2 tuần / 30 / 60 / 90
   ngày; 617.400 bộ; `trend_window = 0` tái tạo đúng lần chạy không lọc. Tiêu chí chốt trước (so với tắt): (a) top
   25% ≥ ở ≥ 2/3 coin, (b) giảm tương quan với thị trường, (c) đạt chuẩn chuyển giao. **Không mức nào đạt**:
   - (a) Martin: 7–9/17 coin; riêng 1 tuần đạt 14/17 khi xếp bằng `performance/max_dd*r2**5` nhưng chỉ 9/17 với Martin.
   - (b) tương quan top 25% với thị trường giảm nhẹ: −0,57 → −0,37…−0,46.
   - (c) mọi mức FAIL (trung vị dương 9–11/17 coin, top 25% dương 5–6/11 quý).
   - Bộ lọc cắt lỗ ở quý tăng (top 25%: −0,92 → −0,54…−0,64) nhưng cắt lãi tương đương ở quý giảm (+0,93 →
     +0,60…+0,75); quý đi ngang tệ đi với SMA dài (60/90 ngày: −0,10 / −0,23). Tốt nhất là 1 tuần: top 25%
     +0,10 so với +0,04 (tắt), quý ngang +0,21 — chênh nhỏ, không bền giữa các coin.
   - Cho walk-forward tự chọn `trend_window` trên train ("all") không hơn tắt.
   ⇒ Vị trí giá so với SMA không báo trước được 3 ngày tới đủ để tách lệnh short tốt/xấu; lợi thế "biết chiều
   quý" ở ý 2 là nhìn trước tương lai, không lấy được bằng bộ lọc trễ. (Lọc thô theo lợi nhuận 30 ngày trước
   đầu quý trông tốt hơn — top 25% +0,32 vs −0,30 — nhưng đó là phân tích sau khi xem, 130 fold, không giữ được
   khi đưa vào từng lệnh.)
6. **Train 18 tháng / test 6 tháng** (`results/wf_multi_asset_short_rsi_18m6m/`, 47 fold: coin 3 năm có 3 cửa sổ
   2024-10 → 2025-03, 2025-04 → 2025-09, 2025-10 → 2026-01-14): vẫn **FAIL** (trung vị dương 9/17 coin, top 25%
   dương 3/7 cửa sổ). Trung bình test: cả grid −0,08, top 25% +0,03, top 10 +0,01 R/tháng; best-1 train +1,15 → test
   −0,07. Việc chọn nhỉnh hơn 9m/3m một chút (Spearman 0,11 vs 0,06; top 10 / top 100 có lãi 56% / 55% bộ so với cả
   grid 47%, ở 9m/3m chênh chỉ 1–3 điểm) nhưng 47 fold là ít. Tương quan với lợi nhuận giá của cửa sổ yếu hơn
   (−0,11) vì 6 tháng chứa cả nhịp lên lẫn xuống. AVAX vẫn tốt nhất (top 10 0,73, 100% bộ top 10 có lãi).
7. **AVAX, train 6 tháng / test 2 tháng** (`results/wf_avax_short_rsi_6m2m/`, 14 fold, test 2023-10 → 2026-01-14,
   Martin): việc chọn **mất tác dụng** — top 10 có lãi 52% bộ (cả grid 53%), so với 85% ở 9m/3m và 100% ở 18m/6m;
   best-1 train +3,31 → test +0,45; Spearman 0,03. Trung bình test: cả grid +0,13, top 25% +0,23, top 10 +0,19
   (dương 7/14). Bỏ 2 fold đầu (đợt tăng +132% cuối 2023, ngoài khoảng test của 9m/3m): grid +0,28, top 25% +0,43,
   top 10 +0,16 — vẫn kém 9m/3m cùng giai đoạn (0,51 / 0,66 / 0,74). Train 6 tháng quá ít lệnh cho 102.900 bộ ⇒ xếp
   hạng chủ yếu khớp nhiễu.
8. **18m/6m với `rsi_window` [35, 40, 50, 60, 70, 80, 90, 100, 120]** (sửa ô Cấu hình notebook short gốc; 132.300
   bộ; `results/wf_multi_asset_short_rsi_18m6m_rsiw/`): không khá hơn grid cũ — trung vị dương 7/17 coin (cũ 9/17),
   top 25% dương 3/7 cửa sổ, FAIL. Trung bình test (Martin): cả grid −0,03, top 25% +0,04, top 10 −0,08, top 10 có lãi
   54% bộ (cả grid 48%). Top 10 hay chọn `rsi_window` 120 (biên mới của grid) nhưng không đem lại lợi thế ngoài mẫu.
   **Tỉ lệ chạm `MAX_HOLDING_BARS` (288) của top 10** (`results/timeout_share.py`, replay float64 khớp 100% số lệnh
   GPU): test 16,4% số lệnh (train 19,0%); lúc bị cắt trung bình gross −0,05 R, 46% đang lãi, net −0,08 R/lệnh — các
   lệnh timeout là lệnh "đi ngang", không phải lệnh đang chạy đúng hướng. Quy tắc chốt trước (≥ 20% **và** đang lãi
   trung bình) không đạt ⇒ không tăng thời gian giữ. Cao ở coin biến động thấp/kém: TRX 45%, ETH 43%, BTC 30%.
9. **Xếp hạng bằng `performance/number_of_trade*r2**5`** (R trung bình mỗi lệnh × r2⁵; cùng grid và lịch 18m/6m;
   `results/wf_multi_asset_short_rsi_18m6m_rsiw_avgr/`): vẫn FAIL (7/17 coin, 3/7 cửa sổ). Top 10 +0,08 R/tháng
   (dương 28/47 fold, có lãi 58% bộ) so với Martin −0,08 (25/47, 54%) — chênh ghép cặp +0,16 ±0,075 (t 2,1), nhưng
   top 10 tự nó chỉ +0,08 ±0,07 (không khác 0), top 25% như nhau (+0,035 vs +0,040), và đây là công thức thứ ba thử
   sau khi đã xem kết quả ⇒ coi là nhiễu chọn lọc. Công thức này chọn bộ ít lệnh hơn (top 10: 18 lệnh/6 tháng test so
   với 28). AVAX top 10 +0,66 (97% bộ có lãi), TON +0,67.
10. **Ngưỡng ≥ 80 lệnh trên train** (thay 25; cùng grid 18m/6m, 3 công thức; `results/wf_multi_asset_short_rsi_18m6m_rsiw_min80/`):
    còn ~83.600/112.900 bộ hợp lệ mỗi fold; top 10 có trung vị 101–136 lệnh train (trước 41–64). **Không giúp, hơi tệ
    hơn**: top 25% −0,02 (trước +0,04; chênh −0,06 ±0,035 ở cả 3 công thức), top 10 −0,04…−0,08, top 10 có lãi
    49–51% bộ (trước 54–58%), best-1 train cao hơn (+1,3…+1,6) nhưng test ≈ 0. AVAX top 10 giảm còn +0,36 (63% có
    lãi). ⇒ Ít lệnh không phải nguyên nhân bộ được chọn không bền; bộ vào lệnh dày (tham số lỏng) còn yếu hơn.
11. **Khung 1h, 18m/6m, ≥ 40 lệnh train** (cùng grid notebook, phí 8 nến = 8 giờ, giữ tối đa 288 nến = 12 ngày; 3 công
    thức; `results/wf_multi_asset_short_rsi_1h_18m6m_min40/`): vẫn FAIL (trung vị dương 8/17 coin). "Top 25% dương
    6/7 cửa sổ" (Martin) bị thổi phồng bởi các cửa sổ chỉ 1–2 coin; ba cửa sổ có 13–14 coin: +0,11 (7/13 coin dương),
    −0,44 (0/13), +0,50 (11/14). Trung bình test: cả grid +0,01, top 25% +0,11 (15m: +0,04), top 10 ≈ 0, best-1 ≈ 0
    (train +1,0…+1,1); top 10 có lãi 46–49% bộ (cả grid 44%). Tương quan top 25% với thị trường lại mạnh (−0,47).
    AVAX ở 1h không còn nổi bật (top 10 −0,27, 1/3 cửa sổ); MATIC +1,11, TON +0,69 (1–2 cửa sổ). Lưu ý: so với 15m
    ngưỡng 25 lệnh, không cùng ngưỡng 40.
12. Hướng còn lại: đối chứng vào lệnh ngẫu nhiên phía short trên nhiều coin để tách timing khỏi beta.

## 9e. Mean reversion: Bollinger dài hạn + RSI ngắn hạn, vào lệnh ngược chiều (`long|short_bollinger+RSI+fade/`)

Đổi hướng theo yêu cầu: Bollinger là chỉ báo dài hạn (`boll_window` 48–672 nến = 12 giờ → 7 ngày), RSI ngắn hạn
(`rsi_window` 2–14); giá ra khỏi dải + RSI cực ⇒ vào **ngược chiều**. `entry_mode` 1 = nến đóng cửa vượt ra ngoài
dải, 2 = nến quay lại trong dải (RSI xét ở cùng nến). Risk = `sl × (upper − lower)`; chốt lời hai kiểu: R cố định
(`tp_sl_ratio` 0,5–2) hoặc dải giữa tại nến vào (`tp_sl_ratio = −1`). Grid 88.200 bộ mỗi phía; engine
`multi_position_engine.py` (cột `entry_mode`, TP dải giữa; `entry_mode = 0` vẫn trùng từng bit bản breakout gốc; GPU
khớp replay float64, 1.810 lệnh kiểm tra tay đúng luật). Walk-forward 15min 18m/6m, 17 coin, ≥ 40 lệnh train, 3 công
thức (`results/wf_multi_asset_{long,short}_rsi_fade_18m6m/`).

1. **Thua ở mọi biến thể, mọi coin.** Trung vị cả grid dương ở **0/17 coin** cho cả long và short, mọi tổ hợp chế độ
   vào × kiểu TP; top 25% dương 0–1/7 cửa sổ. Martin, R/tháng ở test:

   | | Long chế độ 1 | Long chế độ 2 | Short chế độ 1 | Short chế độ 2 |
   |---|---|---|---|---|
   | Cả grid (trung vị) | −0,41 | 0,00 (đa số bộ 0 lệnh) | −0,34 | 0,00 |
   | Top 25% | −0,55 | −0,38 | −0,51 | −0,28 |
   | Top 10 / best-1 | −0,35 / −0,28 | −0,34 / −0,21 | −0,34 / −0,52 | −0,33 / −0,40 |
   | Top 10 có lãi (cả grid) | 32% (24%) | 35% (20%) | 30% (28%) | 35% (20%) |

   Hai công thức score kia và mọi mức `tp_sl_ratio` (kể cả dải giữa) cho cùng kết quả (top 25% −0,42…−0,59).
2. Chế độ 2 gần như không vào lệnh (trung vị 1–4 lệnh / 18 tháng; chỉ ~3.200/44.100 bộ đủ 40 lệnh train): nến bật
   quay lại trong dải đẩy RSI ngắn hạn lên ngay, hiếm khi còn < ngưỡng ở chính nến đó.
3. Beta: long fade lãi theo thị trường tăng (tương quan top 25% với giá +0,43), short fade ngược lại (−0,66) — không
   có lợi thế riêng ngoài chiều thị trường.
4. In-sample AVAX 2024-07 → 2026-01 đã báo trước: chế độ 1 trung vị −0,6 R/tháng, chỉ 7% (long) / 2,5% (short) bộ
   có lãi. Đi ngược nhịp phá dải ở 15min là đánh ngược động lượng ngắn hạn mà breakout khai thác.
5. Timeout (288 nến) của top 10 ở test: long 20,5% số lệnh, lúc bị cắt trung bình +0,21 R gross, 63% đang lãi —
   **đạt** quy tắc "≥ 20% và đang lãi" của ý 9d.8; short 9,2% (không đạt). Tổng lệnh timeout của long vẫn dương
   (+263 R) trong khi toàn bộ −904 R ⇒ giữ lâu hơn khó đảo được kết quả, nhưng là phép thử tiếp theo hợp lệ.

## 9f. Thuận xu hướng: Bollinger dài hạn + RSI ngắn hạn (`long|short_bollinger+RSI+trend/`)

Bản đối xứng của 9e: giá đóng cửa phá ra khỏi dải Bollinger **dài** (48–672 nến) và RSI **ngắn** (2–14) xác nhận
(long: RSI > 65–95; short: RSI < 5–35) ⇒ vào **thuận** chiều phá dải. Luật breakout của engine (`entry_mode = 0`),
risk = `sl × (entry − dải đối diện)` ≈ `sl × độ rộng dải` (sl 0,25–1,0); TP 0,5/0,75/1/1,5/2/3 R hoặc **không TP**
(`tp_sl_ratio = 0`); 51.450 bộ mỗi phía; GPU khớp replay float64. Walk-forward 15min 18m/6m, 17 coin, ≥ 40 lệnh
train, 3 công thức (`results/wf_multi_asset_{long,short}_rsi_trend_18m6m/`). In-sample AVAX 2024-07 → 2026-01: long
không TP trung vị +0,73 R/tháng, 91% bộ có lãi.

1. **Short: lần đầu tiên ĐẠT chuẩn chuyển giao** (cả 3 công thức): trung vị cả grid dương **12/17 coin**, top 25%
   dương **5/7 cửa sổ**; riêng TP 1,5 / 2 / 3 R cũng đạt (TP 3: 13/17 coin). Martin, test: cả grid +0,03, top 25%
   +0,21 (dương 31/47 fold), top 10 +0,10, best-1 +0,23; top 10 / top 100 có lãi 58% / 57% bộ vs cả grid 47% (việc
   chọn có ích, Spearman 0,11). TP càng xa càng tốt: top 25% +0,03 (TP 0,5) → +0,37 (TP 3). Tốt nhất: TON +1,40, MATIC
   +0,94, AVAX +0,82 (top 25%); kém: LTC −0,62, TRX −0,35, SOL −0,19.
2. **Nhưng vẫn nghiêng theo thị trường**: tương quan top 25% với giá −0,28 (nhẹ hơn breakout cũ ở 9m/3m −0,57); cửa
   sổ giá giảm > 15% +0,55, tăng > 15% −0,15. Ba cửa sổ đủ 13–14 coin: +0,35 (12/13 coin dương), −0,28 (5/13), +0,49
   (9/14).
3. **Long: không đạt** (cả grid dương 7/17 coin). Riêng **không TP** suýt đạt (11/17 coin — cần 12; 6/7 cửa sổ): top 25%
   +0,57, top 10 +0,56, có lãi 60% bộ vs cả grid 52% — nhưng tương quan với thị trường **+0,77** (cửa sổ tăng +0,87,
   giảm −0,47) ⇒ chủ yếu là beta, như các phát hiện long trước. Có TP thì gần 0 hoặc âm (TP 0,5: −0,08).
4. **Thận trọng**: đây là khoảng biến thể thứ 10 thử trên cùng 17 coin × 47 fold; ngưỡng 2/3 đạt sát nút (12/17, 5/7) ⇒
   có thể là may mắn do thử nhiều. Cần kiểm tra độc lập trước khi tin: lịch 9m/3m (130 fold), khung 1h, đối chứng vào
   lệnh ngẫu nhiên phía short, và dữ liệu mới sau 2026-01-14 (chốt tham số/quy trình trước khi xem).

## 9g. Short trend trên dữ liệu Binance perp của lab (`data_binance_perp/`, `results/wf_binance_perp_short_rsi_trend_*`)

**Dữ liệu.** `build_binance_perp_csv.py` dựng nến 5min từ nến 1 phút giá giao dịch trong `data/perp-minutes/` (Binance
USDⓈ-M perp; BTC, ETH, BNB, SOL, XRP), lấy từ **2020-09-15 → 2025-10-01 23:59**.
- Mốc cuối là ngày cuối in-sample của lab. `perp-minutes` chạy tới 2026-10, tức là sang giai đoạn holdout, nên các part sau mốc bị bỏ qua mà không mở.
- Không thiếu phút nào.
- Nến 1h gộp lại khớp tuyệt đối `data/perp/*_1h.parquet` (file của lab gán nhãn theo giờ đóng nến). Chỉ lệch ở 2 nến lúc 2024-10-28 21–22h.

**Grid.** Lấy từ ô Cấu hình notebook do người dùng sửa, **khác grid đạt tiêu chí ở §9f**:
- `boll_window` 40–400, `rsi_window` 5–30, `rsi_threshold` 5–40, `sl_multiplier` 0,5–1,5.
- **TP 1,1 cố định**, tổng 19.008 bộ.
- Xếp hạng theo Martin, tối thiểu 40 lệnh, giữ tối đa 288 nến.

Tiêu chí chuyển giao chốt trước: ≥ 4/5 coin và ≥ 2/3 cửa sổ.

| | A: 15min, train 3 năm / test 1 năm | B: 5min, 18m/6m (giữ 288 nến = 1 ngày, phí mỗi 96 nến) |
|---|---|---|
| Fold | 2 cửa sổ test (2023-10, 2024-10) × 5 coin | 7 cửa sổ (2022-04 → 2025-04) × 5 coin |
| Coin có trung vị cả grid > 0 | 1/5 | 0/5 |
| Cửa sổ có top 25% > 0 | 0/2 | 2/7 |
| Tiêu chí | **FAIL** | **FAIL** |
| Cả grid / top 25% / top 10 (R/tháng) | −0,09 / −0,19 / −0,08 | −0,40 / −0,47 / −0,20 |
| % bộ có lãi: cả grid → top 10 | 27,5% → 49% | 25% → 36% |
| Top 10: train → test (trung vị R/tháng) | +0,56 → ≈ 0 | +0,95 → −0,23 |
| Tương quan top 25% với giá | (cả 10 cửa sổ đều tăng) | −0,46 (giảm +0,35, ngang −0,43, tăng −1,13) |

1. **Bản A không kiểm tra được ý tưởng.** Cả hai năm test đều là thị trường tăng mạnh với cả 5 coin (+19% → +613%), nên một chiến thuật short chỉ có thể lỗ nhẹ. Chọn tham số giúp tỉ lệ có lãi tăng từ 27% lên 49%, nhưng vẫn quanh 0.
2. **Bản B lỗ rõ ở 5min.** Chỉ có lãi khi giá giảm, tức là beta short:
   - Hai cửa sổ chưa từng thấy (2022) cho +0,28 (giá −53%) và −0,16 (giá +14%).
   - Cửa sổ 2023-Q4 (giá +268%) cho −1,56.
3. **Kết luận.** Không xác nhận được §9f. Tuy vậy, đây không phải phép thử sạch của §9f, vì:
   - grid khác (TP 1,1 cố định, trong khi §9f thấy TP xa tốt hơn; RSI dài hơn);
   - chỉ có 5 coin lớn, trong đó 3 coin đã yếu ngay ở §9f: top 25% của SOL −0,19, BNB −0,11, BTC −0,09. Chỉ ETH (+0,45) và XRP (+0,46) dương.

   Muốn kiểm tra đúng §9f thì cần chạy lại grid 51.450 bộ của §9f trên `data_binance_perp`.
4. **1h, 3y/1y** (`results/wf_binance_perp_short_rsi_trend_1h_3y1y/`; giữ tối đa 288 nến = 12 ngày, phí mỗi 8 nến). Kết quả: **FAIL**, 1/5 coin và 0/2 cửa sổ.

   | Chỉ số | Giá trị |
   |---|---|
   | Cả grid / top 25% / top 10 / best-1 (R/tháng) | −0,01 / −0,07 / **+0,05** / +0,21 |
   | % bộ có lãi: cả grid → top 10 | 32% → **63%** |
   | Top 10: train → test (trung vị R/tháng) | +0,50 → +0,07 |

   Đây là bản tốt nhất trong ba bản (§9g) về **giá trị của việc chọn tham số**: top 10 dương ở cả hai năm test (+0,01, +0,10) và ở 4/5 coin, dù cả hai năm đều là thị trường tăng. Nhưng mức lãi rất nhỏ, và chỉ có 10 điểm (2 năm × 5 coin), nên chưa đủ làm bằng chứng.

## 9h. Nhóm coin tốt / xấu cho short trend (grid notebook, `results/group_coins.py`)

**Thiết lập.** Grid notebook (19.008 bộ, TP 1.1, Martin, tối thiểu 50 lệnh), 15min, 17 coin trong `reference/data`.
- Hai lịch walk-forward: 18m/6m (`wf_multi_asset_short_rsi_trend_nbgrid_18m6m`, 3 cửa sổ mỗi coin) và 9m/3m (`…_9m3m`, 8 quý mỗi coin).
- Toàn bộ chạy: FAIL ở cả hai lịch. 18m/6m đạt 8/17 coin và 4/7 cửa sổ; 9m/3m đạt 11/17 coin và 6/11 cửa sổ.
- Cách chia nhóm:
  - **Tốt**: top 10 > 0, top 25% > 0, và top 10 dương ở ≥ ½ số cửa sổ.
  - **Xấu**: top 10 < 0 và top 25% < 0.

| Nhóm (9m/3m) | Coin | Top 10 / top 25% (R/tháng) | Top 10 có lãi |
|---|---|---|---|
| Tốt | DOGE, MATIC, XRP, XLM, OP, ADA, AVAX, SUI, 1000SHIB | +0,47 / +0,59 | 63% |
| Trung tính | SOL, TRX, TON | +0,09 / 0,00 | 53% |
| **Xấu** | **BTC, ETH, BNB, LTC, LINK** | −0,52 / −0,50 | 37% |

18m/6m cho gần như cùng kết quả: nhóm xấu là LTC, ETH, LINK, SOL, BTC; BNB nằm ở trung tính.

**Độ bền.** Chia nhóm theo các quý trước 2025-Q1, rồi xem kết quả từ 2025-Q1 → 2025-Q4:
- **Nhóm xấu bền**: cả 5 coin vẫn âm ở nửa sau (top 10 −0,47, top 25% −0,50), dù giá trong nửa sau đi ngang (−1%).
- **Nhóm tốt không bền**: nửa sau chỉ đạt top 10 +0,14. Nhóm "trung tính" ở nửa đầu (DOGE, XLM, 1000SHIB, SOL) lại làm tốt hơn (+0,91).
- Tương quan hạng giữa hai nửa: top 25% +0,72, nhưng top 10 chỉ +0,21.

Không phải beta: giữa các coin, top 10 gần như không tương quan với lợi nhuận giá (+0,08), và giá của nhóm xấu và nhóm tốt đi tương đương nhau.

**Kết luận.**
- Thứ dùng được là **danh sách loại trừ**: BTC, ETH, BNB, LTC, LINK, tức các coin vốn hoá lớn, lâu đời. Đây là giả thuyết, chưa kiểm chứng: thanh khoản sâu khiến phá dải dễ quay lại.
- Danh sách này khớp với §9f (grid khác): ở đó LTC, BNB, LINK, BTC cũng âm. Nó cũng khớp §9g (BTC, BNB âm trên dữ liệu Binance 2022–2025).
- **Không nên chọn top coin "tốt nhất"**: thứ hạng trong nhóm tốt đổi liên tục.
- Muốn dùng danh sách loại trừ thì phải chốt ngay bây giờ và kiểm tra trên dữ liệu sau 2026-01-14. Bản thân việc chọn coin là thêm một bước tối ưu trên cùng dữ liệu.

## 9i. Vì sao nhóm coin xấu (BTC, ETH, BNB, LTC, LINK) thất bại (`results/coin_traits.py`)

Dữ liệu: 17 coin, 15min, 2023-01 → 2026-01. Kết quả so sánh với walk-forward 9m/3m (grid notebook).

1. **Fade thất bại ở mọi coin, không riêng nhóm xấu.**
   - Long fade (top 10): nhóm xấu −0,30, nhóm tốt −0,47, trung tính −0,30. Short fade: −0,30, −0,39, −0,38.
   - Sau khi phá dải, giá không đảo chiều đủ ở coin nào: sau khi trừ xu hướng chung, mức trôi chỉ khoảng ±0,03 lần độ rộng dải. Con số này nhỏ hơn phí vào lệnh (0,02–0,05 R).
   - Nhóm xấu chỉ thực sự "xấu" ở bản thuận xu hướng.
2. **Biến động thấp là yếu tố tương quan mạnh nhất** với kết quả short trend. Spearman giữa 17 coin: 0,56–0,73. Với n = 17, ngưỡng có ý nghĩa thống kê là khoảng 0,48.

   | | Nhóm xấu | Nhóm tốt |
   |---|---|---|
   | Biến động ngày | 3,4% | 4,8% |
   | Độ rộng dải khi phá (Bollinger 100) | 3,1% | 4,3% |
   | Phí vào lệnh, sl 1 (R) | 0,032 | 0,022 |

   - Phí tính theo R cao hơn chỉ giải thích khoảng 0,15 R/tháng (khoảng 10 lệnh mỗi tháng × 0,015 R). Trong khi đó hai nhóm chênh nhau khoảng 1 R/tháng.
   - Phần còn lại: coin biến động thấp ít có những cú giảm dài sau khi phá dải.
3. **Xu hướng tăng đều so với biến động** (BTC, BNB: Sharpe mua-giữ trong giai đoạn test khoảng 0,7) làm cú phá dải dưới bị mua lên.
   - Chỉ giải thích được một phần: Spearman −0,27 đến −0,32, chưa có ý nghĩa thống kê.
   - XRP là ngoại lệ: Sharpe 0,67 nhưng vẫn tốt.
4. **Giá hồi lên sau khi phá dải dưới, ngoài xu hướng chung** (LINK, LTC, BNB): +0,03 đến +0,09 lần độ rộng dải sau 12 giờ đến 1 ngày. Ngược lại, BTC và ETH lại hơi tiếp diễn xuống.
   - Mức trôi sau 48 nến tương quan −0,58 với kết quả.
5. **Không thấy khác biệt về "độ ngẫu nhiên".**
   - Variance ratio ở 1h, 4h, 1 ngày như nhau giữa các nhóm (khoảng 0,9–0,96, tức hơi hồi về ở mọi coin).
   - Tỉ lệ phá dải giả (quay lại trong dải sau 4 nến) đều khoảng 69%.
   - Khối lượng giao dịch tương quan yếu (−0,30 / −0,09): LTC, LINK, BNB có khối lượng ngang AVAX, ADA, MATIC.
   - Tài liệu nói coin thanh khoản cao khó dự đoán hơn, nhưng dữ liệu ở đây chỉ ủng hộ giả thuyết đó qua biến động, không qua khối lượng.

**Hệ quả.** Thay danh sách tên coin bằng một luật có thể kiểm tra: chỉ giao dịch khi **biến động trượt** (tính trước mỗi cửa sổ test) đủ cao. Luật này kiểm tra được bằng walk-forward mà không nhìn trước. Cần kiểm tra trước khi tin, vì 17 coin trong một chu kỳ thị trường duy nhất là mẫu nhỏ.

## 9j. 47 coin, dữ liệu 5m thật 2021–2026, 5min và 15min 9m/3m (`data_5m/`, `results/wf_5m_*`)

**Dữ liệu.** `download_binance_um_klines.py` tải từ data.binance.vision (miễn phí): 17 coin cũ + **30 coin mới**.
- 30 coin mới chọn **không nhìn trước**: perp USDT niêm yết trước 2023, xếp theo khối lượng 2022, có cả coin đã hủy niêm yết (LUNA, EOS). Danh sách ở `data_5m/universe.csv`.
- Dữ liệu từ 2021-01 tới 2026-10-19, kèm funding. Khớp `reference/data` (chỉ 10–14 / 105 nghìn nến 15min lệch).
- **Lưu ý:** dữ liệu 2025-10 → 2026-10 của BTC, ETH, BNB, SOL, XRP là giai đoạn holdout của lab và nay đã được dùng (người dùng chọn). Holdout của lab cho 5 coin này không còn sạch với họ chiến thuật Bollinger.

**Kết quả walk-forward 9m/3m** (grid notebook, 20 quý test, 2021-Q4 → 2026-Q3):

| | 5min (giữ 288 nến = 1 ngày) | 15min |
|---|---|---|
| Coin có trung vị grid > 0 | 6/47 | 33/47 |
| Quý có top 25% > 0 | 9/20 | 12/20 (cần 14) |
| Tiêu chí chuyển giao | **FAIL** | **FAIL** (sát nút; bỏ LUNA vẫn 12/20) |
| Cả grid / top 25% / top 10 (R/tháng) | −0,19 / −0,41 / −0,28 | +0,04 / +0,09 / +0,09 |
| Tương quan top 25% với giá cửa sổ | −0,61 | −0,62 |
| Top 25% theo thị trường: giảm / ngang / tăng | +0,67 / −0,61 / −1,69 | +1,05 / −0,08 / −1,06 |

1. **5min tệ hơn hẳn 15min ở mọi nhóm coin.** Chỉ có lãi khi giá giảm.
2. **15min trên dữ liệu dài hơn: lãi chủ yếu đến từ 2021–22**, giai đoạn thị trường giảm (2022-Q2: top 25% +3,13).

   | Top 10 ở 15min | 2021–22 | 2023–26 |
   |---|---|---|
   | 30 coin mới | +0,96 | −0,09 |
   | 12 coin cũ còn lại | +0,56 | +0,17 |
   | 5 coin "xấu" | +0,30 | −0,63 |

   Vậy nhóm "xấu" không xấu ở 2021–22; nó chỉ xấu trong giai đoạn đã dùng để đặt tên nhóm (2023–26).
3. **Luật chọn coin theo biến động (chốt trước) FAIL trên 30 coin mới**:
   - Ở 15min: nhóm được chọn thắng 10/20 quý, top 10 +0,105, *kém hơn* nhóm không chọn (+0,160).
   - Ở 5min: thắng 11/20 quý, −0,25 so với −0,23.
   - Trên 17 coin cũ thì luật "đạt" (14/20 quý, +0,23 so với −0,06), đúng như kỳ vọng với dữ liệu đã dùng để tìm ra luật. Đó là thiên lệch do chọn.
   - Thử thêm (khám phá, không chốt trước): chọn coin có giá giảm trong 9 tháng train cũng không giúp (coin mới: +0,10 so với +0,17).
4. **Điểm chung của coin tốt**, khám phá trên 17 coin, 2023–26, `coin_traits.py --extra`:
   - Biến động ngày 0,65 / 0,62; biến động riêng (idio vol) 0,64 / 0,47; độ lệch 4h 0,52 / 0,38; beta với BTC 0,37 / 0,45.
   - Funding, tương quan với BTC và vốn hoá không có tác dụng. Vốn hoá CoinGecko 2025-11: −0,28; bỏ BTC, ETH thì −0,06; XRP vốn hoá lớn vẫn tốt, LTC và LINK vốn hoá vừa vẫn xấu.
   - Nhưng tính chất mạnh nhất (biến động) không giữ được trên coin mới. Giữa các coin mới, kết quả tương quan **−0,52 với lợi nhuận giá**, tức coin "tốt" chủ yếu là coin giảm giá trong giai đoạn test. Đây là beta, và không đoán trước được từ dữ liệu train (tương quan giữa lợi nhuận train và giá test chỉ −0,14).
5. **Coin mới có top 10 ở 15min cao:**
   - SAND +0,77, GMT +0,73, GALA +0,62, FIL +0,54, DOT +0,45. Phần lớn là token game/metaverse giảm giá dài, với lãi tập trung ở 2021–22.
   - Riêng 2023–26: GMT +0,73, SAND +0,58, GALA +0,58.

6. **30 coin mới, 15min, 18m/6m** (`results/wf_5m_data_newcoins_short_rsi_trend_15min_18m6m`). Notebook lúc này xếp hạng theo `performance/number_of_trade*r2**5`; Martin cho kết quả gần như giống hệt.
   - LUNA không đủ dữ liệu cho 18 tháng train, nên còn 29 coin. Các cửa sổ lệch quý theo ngày niêm yết của từng coin.
   - Kết quả: **FAIL**. Chỉ 15/29 coin có trung vị grid dương (cần 20). Cửa sổ có top 25% dương là 12/17, tức đạt riêng vế này.
   - Cả grid / top 25% / top 10: −0,00 / −0,02 / +0,05.
   - Tương quan top 25% với giá cửa sổ: −0,50. Theo thị trường: giảm +0,37, ngang −0,16, tăng −0,55.
   - Dấu của top 25% ở từng cửa sổ gần như trùng với chiều giá: các cửa sổ giá tăng (2023-Q3, 2023-Q4, 2026-Q2, 2026-Q3) đều từ −0,6 đến −0,7.
   - Coin có top 10 cao: GALA +0,34, GMT +0,50, ATOM +0,33, NEAR +0,29, MANA +0,30, ALGO +0,28. Ở 9m/3m thì danh sách này khác (SAND, FIL đứng đầu), tức thứ hạng coin không bền.

7. **Lọc coin bằng trung vị grid ở train** (`vol_rule_eval.py --rule train_median`, các thư mục `wf_5m_data_15min_{9m3m,18m6m}_tm`; harness nay lưu thêm `train_median` và `train_share_profit`).
   - Luật: chỉ giao dịch coin có trung vị cả grid ở train > 0. Với 9m/3m có 366/833 cặp (coin, quý) được chọn.
   - **Thứ hạng giữ được nhẹ.** Trong mỗi cửa sổ, tương quan Spearman giữa trung vị train và trung vị test là +0,17 (dương ở 17/20 quý, 9m/3m). Với 18m/6m: coin mới +0,07, coin cũ +0,48.
   - **Nhưng bộ lọc không làm kết quả tốt hơn.** Ở test, coin được chọn có trung vị grid +0,03 so với +0,05 của coin không chọn, và tỉ lệ trung vị dương như nhau (33%).
   - Top 10 của coin được chọn thấp hơn coin không chọn ở mọi cấu hình khi tính trung bình (ví dụ 9m/3m, coin mới: +0,06 so với +0,18; 18m/6m: −0,05 so với +0,17). Bỏ năm 2022 thì vẫn vậy.
   - Số quý thắng nhìn qua là đạt (12–14/20), nhưng nhóm không chọn thắng lớn ở các quý sập mạnh.
   - Kết luận (9m/3m, 18m/6m): trung vị grid của train **không dùng được làm bộ lọc coin**.
8. **15min, train 3 năm / test 1 năm, 47 coin** (`results/wf_5m_data_15min_3y1y_tm`).
   - Các cửa sổ chính là 2024, 2025, 2026 (tới 19/10), mỗi cửa sổ 31 coin; coin niêm yết muộn có cửa sổ lệch quý. LUNA và TON không đủ 3 năm dữ liệu.
   - Kết quả: **FAIL**. 23/45 coin có trung vị grid dương (cần 30). Cửa sổ có top 25% dương là 8/11, đạt riêng vế này.
   - Cả grid / top 25% / top 10: +0,02 / +0,09 / +0,00 (Martin: top 10 +0,08). Tương quan top 25% với giá −0,40, yếu hơn các lịch ngắn.

   | Cửa sổ | Giá trung bình | Top 25% | Top 10 | Coin có trung vị grid > 0 |
   |---|---|---|---|---|
   | 2024 | +51% | +0,09 | +0,05 | 11/31 |
   | 2025 | −43% | +0,27 | +0,07 | 17/31 |
   | 2026 | −7% | −0,12 | −0,14 | 3/31 |

   - **Lọc theo trung vị grid của 3 năm train ở lịch này lại cho thấy tác dụng**:
     - Coin mới: được chọn có top 25% +0,17, trung vị grid +0,055; bị loại chỉ +0,01 và +0,007.
     - Coin cũ: +0,30 so với −0,08.
     - Nhưng chỉ có khoảng 3 cửa sổ × 25 coin, và kết quả trái ngược với 9m/3m và 18m/6m, nên chưa đáng tin. Có thể train dài đo được tính chất của coin, còn train ngắn chỉ đo chiều thị trường.

**Kết luận.**
- Không tìm được đặc điểm nào **biết trước** để chọn coin tốt. Nhóm tốt hay xấu chủ yếu phản ánh coin đó tăng hay giảm giá trong giai đoạn test.
- Short trend ở 15min có lãi khi thị trường giảm và lỗ khi thị trường tăng, trên mọi tập coin. Ở 5min thì lỗ.
- Bước tiếp theo đáng thử là **kết hợp với một bộ lọc chế độ thị trường (regime) ở cấp danh mục**, không phải chọn coin. Bộ lọc xu hướng theo từng lệnh ở §9d đã thất bại, nên cần thiết kế khác.

## 9k. Danh mục theo xu hướng khung ngày (`portfolio_trend/daily_trend.py`, `results/portfolio_trend_daily/`)

**Thiết kế (chốt trước, không tối ưu tham số).** 47 coin perp trong `data_5m`, giai đoạn 2021-06 → 2026-10.
- **Danh sách coin:** top 30 theo khối lượng 30 ngày, tính lại mỗi tháng.
- **Tín hiệu:** trung bình dấu của lợi nhuận 20, 60 và 120 ngày.
- **Tỷ trọng:** `0,40 / biến động EWMA / N`, tổng `|w|` tối đa 2.
- **Đổi vị thế:** mỗi thứ Hai, khớp lúc 01:00 UTC.
- **Chi phí:** phí 0,06% mỗi chiều; funding theo dữ liệu thực tế.

**Kiểm tra code:**
- Không nhìn trước dữ liệu: sai lệch bằng 0.
- Cân đối sổ sách: lệch dưới 3e-16.
- Dữ liệu tổng hợp: coin có xu hướng thì lãi; coin đi ngang ngẫu nhiên lãi ≈ 0.
- LUNA được đóng vị thế ở giá cuối; dấu funding đúng.

**Kết quả: FAIL cả 4 tiêu chí.**

| | Sharpe ròng | Alpha so với long tất cả (t NW) | Beta | Ghi chú |
|---|---|---|---|---|
| **Bản chính (long + short)** | **−0,12** | −1,4%/năm (−0,18) | −0,14 | Ở phân vị 58 của 1.000 lần dấu ngẫu nhiên (phân vị 95 = 0,52) |
| Chỉ long | +0,26 | +1,5% (0,42) | 0,27 | Tương quan 0,69 với thị trường, tức là beta |
| Chỉ short | −0,33 | | −0,41 | |
| Chỉ L = 20 / 60 / 120 | −0,15 / −0,10 / −0,09 | | | Cả ba đều âm |
| Phí ×2 | −0,15 | | | |
| Đổi vị thế hằng ngày | +0,35 | +7,7% (0,95) | −0,15 | Cao nhất trong 8 bản nhưng vẫn dưới ngưỡng, và là 1 trong 8 lần thử |
| Đối chứng: long tất cả coin | +0,20 | | 1 | |
| Đối chứng: mua-giữ BTC | +0,54 | | | |

- **Theo năm**, Sharpe của bản chính: 2021 −0,08, 2022 +0,42, 2023 +0,08, 2024 −0,12, 2025 −0,37, 2026 −0,74.
  - Giai đoạn 2021–22: +0,29. Giai đoạn 2023–26: −0,27.
  - Năm 2022 có lãi nhờ chân short (+0,23). Năm 2023–24 chân long lãi nhưng chân short lỗ tương đương.
- **Phí và funding nhỏ:** khoảng 0,5–0,7%/năm phí và 0,2–2,6%/năm funding. Vòng quay khoảng 9–12 lần/năm.
  Thất bại nằm ở **lãi gộp trước phí**, không phải do chi phí.
- **Theo coin:** coin mới đóng góp −0,10, coin cũ −0,02.
  - Coin lãi nhất: BNB, SOL, LUNA, ETH. Coin lỗ nhất: XLM, BCH, ETC, APT, EOS.

**Cách hiểu.**
- Trên rổ coin perp 2021–2026, xu hướng 20–120 ngày **không có lợi thế** tốt hơn đặt dấu ngẫu nhiên.
- Giai đoạn 2023–26 nhiều đợt tăng giảm đảo chiều, nên chân short liên tục bị "quét".
- Bằng chứng trong nghiên cứu (Liu & Tsyvinski) chủ yếu dùng lợi nhuận 1–4 tuần, và dữ liệu là trước 2020.
  Bản "đổi vị thế hằng ngày" tốt hơn gợi ý rằng phản ứng nhanh hơn có ích. Đây là quan sát rút ra sau khi đã xem kết quả, phải chốt thành giả thuyết riêng và kiểm tra trên dữ liệu khác.

## 9l. Grid và martingale DCA (`grid_martingale/`, `results/grid_martingale/`)

**Thiết lập.** 47 coin, nến 5m, 2021-02 → 2026-10.
- Mỗi coin là một phần vốn riêng, không đòn bẩy (tối đa 1× tính theo giá vào lệnh).
- Phí: maker 0,02%; taker 0,05% + trượt giá 0,01%; funding theo dữ liệu thực tế.
- Khớp lệnh: giá phải vượt qua mức đặt mới tính là khớp. Đường đi trong nến bắt đầu từ giá đóng nến trước, nên bước nhảy giữa hai nến cũng được tính.
- Kernel numba, kiểm tra bằng:
  - lãi grid tổng hợp khớp tính tay;
  - giá vốn và chốt lời DCA khớp tính tay;
  - dấu funding đúng;
  - đi ngẫu nhiên không phí cho lãi ≈ 0;
  - numba ≡ Python thuần.
- Đối chứng: 20 lần xáo trộn nến trong từng tuần.

**Các chiến thuật:**
- **A1:** grid trung tính, vùng ±2σ của một tháng, 20 bậc; ra khỏi vùng thì đóng hết và dựng lại.
- **A2:** grid giữ vị thế khi giá ra khỏi vùng.
- **B1:** DCA kiểu 3Commas (8 SO, 2%, step 1,2, volume 1,5, chốt lời 2%), không cắt lỗ, hai chiều.
- **B2:** như B1 nhưng cắt lỗ ở SO cuối ±10%.

| | Sharpe | Tổng lãi | Max DD danh mục | Năm dương | Beta (tương quan) | Coin DD > 50% | Coin cháy | Xáo trộn: Sharpe trung vị |
|---|---|---|---|---|---|---|---|---|
| A1 grid, đặt lại vùng | **−0,74** | −72% | −75% | 1/6 | −0,01 | 96% | 12 | −0,09 |
| A2 grid, giữ vị thế | +0,17 | −27% | −67% | 4/6 | 0,13 (0,18) | 98% | 33 | +0,12 |
| B1 DCA long | +0,52 | +110% | −68% | 4/6 | **0,71 (0,91)** | 89% | 12 | **+1,42** |
| B1 DCA short | −0,17 | −99% | −100% | 3/6 | −0,66 | 100% | 46 | +0,03 |
| B2 DCA long, có cắt lỗ | +0,23 | +16% | −37% | 3/6 | 0,21 (0,74) | 68% | 0 | — |
| B2 DCA short, có cắt lỗ | −0,38 | −41% | −46% | 2/6 | −0,17 | 92% | 0 | — |
| Đối chứng: long tất cả coin | +0,54 | | | | 1 | | | |

**Kết quả: FAIL cả 4 chiến thuật lõi.** Riêng B1 long đạt tiêu chí 1 (Sharpe/năm), nhưng trượt drawdown, đối chứng xáo trộn và alpha (+2,4%/năm, t = 0,21).

1. **DCA long chỉ là beta:** tương quan 0,91 với thị trường, Sharpe bằng đúng việc long tất cả coin (0,52 so với 0,54). Có lãi năm 2021, 2023, 2024; lỗ 65% năm 2022.
2. **Cấu trúc giá thật bất lợi cho grid và martingale:** trên chuỗi đã xáo trộn, các chiến thuật này làm *tốt hơn* trên giá thật.
   - DCA long: 1,42 khi xáo trộn so với 0,52 thật. A1: −0,09 so với −0,74.
   - Tức là trong một tuần, giá thật đi thành xu hướng và biến động dồn cục (thang lệnh bị dùng hết, vùng bị phá), chứ không hồi về như grid cần.
   - Variance ratio < 1 ở mục §9i không bù được điều đó ở quy mô bậc vài %.
3. **Đuôi phân phối giết chiến thuật:** 89–100% số coin có drawdown > 50%.
   - Bản không cắt lỗ làm cháy 12–46 phần vốn: LUNA, LUNA2, GALA, SAND, MANA với long; hầu hết coin với short.
   - Có cắt lỗ thì không cháy, nhưng hết lợi nhuận.
4. **Lỗ hổng thiết kế grid:** với coin biến động rất cao (DOGE đầu 2021, σ khoảng 20%/ngày), vùng ±2σ/tháng thành ×0,1 đến ×9. Phía short tích vị thế khi giá tăng hơn 10 lần, và vốn cháy trước khi giá chạm mép để đóng.
5. **Thiên lệch khớp lệnh không phải nguyên nhân:** độ nhạy "chạm là khớp", "đảo đường đi" và "một phía mỗi nến" thay đổi rất ít (A1 từ −0,55 đến −0,73; DCA long từ 0,52 đến 0,56).

## 9m. Tái hiện Waka Waka EA: fade BB/RSI + grid martingale giới hạn (`waka/`, `results/waka/`)

**Dữ liệu forex.**
- Nguồn chính: FXCM, nến 1m bid/ask miễn phí, gộp thành 5m, 2018-01 → 2026-10-16 (AUDCAD, AUDNZD, NZDCAD, EURUSD).
  - FXCM thiếu khoảng 110 ngày giao dịch mỗi cặp, nặng nhất là 1/5–9/8/2026.
  - Vá bằng histdata.com: giá bid; giờ EST lệch +5h, đã đối chiếu với FXCM (lệch 0,1–0,7 pip); ask = bid + spread trung vị FXCM theo giờ. Khoảng 5% số nến.
  - Dukascopy chặn sau vài chục request.
- EURUSD của FXCM khớp `EURUSD5.csv`: 95% nến lệch < 1 pip, trung vị 0,1 pip.
- Spread trung vị 0,7–0,8 pip với cặp chéo, 0,2 pip với EURUSD.
- Swap: FRED không tải được, nên dùng −1%/năm cho cả hai chiều.

**Mô hình (chốt trước).**
- Tín hiệu: nến M15 đóng ngoài BB(20, 2) và RSI(14) ≥ 70 / ≤ 30 → vào ngược chiều.
- Grid: 35 pips, tính từ lệnh đầu. Lot nhân 1 / 2 / 1,6, tối đa 9 lệnh (1, 1, 2, 4, 8, 12,8, … tổng 134,5 L).
- Chốt lời: weighted TP = lệnh đầu × 10 pips.
- Cắt lỗ: SL 1000 pips; max floating DD tắt.
- Vốn và tài khoản:
  - Deposit load 0,5% ở 1:100, tức notional lệnh đầu = 0,5 × balance.
  - Mỗi cặp tối đa 1 rổ buy + 1 rổ sell.
  - Ba cặp chéo chung một tài khoản; stop-out khi equity < 50% margin.
  - Không mở rổ mới 20:30–23:00 UTC.
- EURUSD và crypto dùng Smart Distance thay thế: khoảng cách = 35 pips × σ30 ngày ÷ σ của AUDCAD 2018–20, sizing có rủi ro tương đương.
- Kiểm tra engine:
  - ví dụ lot/hòa vốn trong tài liệu ra đúng 6689,5 L-pips;
  - TP theo tiền, bid/ask và gap đều khớp tính tay;
  - đi ngẫu nhiên không phí cho lãi ≈ 0, với 95% số lần có lãi và lần tệ nhất −27%;
  - numba ≡ Python thuần; tín hiệu không nhìn trước.

**Bản chính.** Đối chứng gồm vào lệnh ngẫu nhiên (cùng số tín hiệu mỗi tháng) và xáo trộn nến trong tuần, mỗi loại 20 lần.

| | Sharpe | Tổng | DD equity | DD balance | Win rổ | Ngẫu nhiên p95 | Xáo trộn p95 |
|---|---|---|---|---|---|---|---|
| Cặp chéo 2018–26 | +0,30 | +12% | −90% | −90% | 98% | +0,38 | +1,09 |
| EURUSD 2018–26 | −0,12 | −64% | −77% | −76% | 97% | +0,23 | +0,83 |
| Crypto 47 coin 2021–26 | −0,43 | −61% | −63% | | | −0,11 | +2,17 |
| `EURUSD5.csv` 16 tháng (minh họa) | +1,37 | +13% | −8% | −3% | 97% | | |

**FAIL cả 4 tiêu chí ở cả 3 thị trường.**

1. **Đúng hình dạng track record live, nhưng không đúng thời điểm.**
   - Cặp chéo: 87% số tháng có lãi, trung vị +2,6%/tháng. Từ 2020 đến 2026 năm nào cũng lãi: +40%, +20%, +53%, +44%, +32%, +35%, +21%. Trông giống hệt marketing "88/89 tháng có lãi".
   - Nhưng 2018 −52% và 2019 −71%: hai lần stop-out (10/2018, 8/2019). Mỗi lần, rổ buy AUDCAD và rổ buy NZDCAD cùng đầy 8–9 lệnh, vì CAD là đồng tiền chung.
   - Các đợt live (11/2022, 6/2023, 3–6/2024) trong backtest lại là tháng lãi. Bản tái hiện là "kiểu Waka", không phải Waka (thiếu bộ lọc tin tức, ML, crash filter và tham số thật).
   - Kết quả phụ thuộc rất mạnh vào ngày bắt đầu.
2. **Tín hiệu không có giá trị.**
   - Chỉ một lệnh, không grid: Sharpe −0,47 (chéo), −0,29 (EURUSD), −0,81 (crypto).
   - Vào lệnh ngẫu nhiên có cùng grid: trung vị khoảng 0 ở chéo, so với 0,30 của tín hiệu thật.
   - Giá xáo trộn trong tuần: Sharpe trung vị khoảng +0,5 ở chéo và +1,5 ở crypto, *cao hơn* giá thật. Giống §9l: giá thật đi thành trend và biến động dồn cục, chống lại martingale.
3. **Biến thể (10 cái, xem sau khi chạy nên chỉ để tham khảo).**
   - Trên chéo:
     - max floating DD 30%: Sharpe 0,94, +567%, DD −46%;
     - deposit load low 0,25%: 0,69, DD −53%;
     - RSI 75/25: 0,69;
     - high 1,5%: −95%;
     - không martingale (1-1-1): 0,49, tốt hơn bản chính.
   - Trên EURUSD, khoảng cách cố định 35 pips cho 0,62 còn Smart Distance cho −0,12. Trên chéo thì ngược lại: Smart Distance −0,27.
   - Crypto: mọi biến thể đều âm.
4. **Tối ưu walk-forward** (`optimize_waka.py`). Grid search vét cạn: 15.552 bộ cho forex, 576 bộ cho crypto. Trong train chọn theo lợi nhuận năm ÷ DD equity. Kết quả: **FAIL cả 6 tổ hợp thị trường × lịch.**

| | Top-10 OOS ghép | DD | Sharpe | Best-1 OOS | Bản chính cùng cửa sổ |
|---|---|---|---|---|---|
| Chéo 3y/1y (2021–26) | +71% | −48% | 0,44 | −73% | +473%, DD −29%, Sharpe 1,20 |
| Chéo 18m/6m (2019H2–26) | +15% | −59% | 0,26 | −93% | +101%, DD −79% |
| EURUSD 3y/1y | −20% | −55% | −0,06 | −59% | −47% |
| EURUSD 18m/6m | −67% | −79% | −0,39 | −73% | −69% |
| Crypto 3y/1y | −17% | −23% | −0,62 | −5% | −51% |
| Crypto 18m/6m | −10% | −21% | −0,40 | −3% | −60% |

   - Spearman hạng train↔test trong khoảng −0,33 đến +0,42, quanh 0. Chọn theo train còn kém hơn trung vị toàn grid.
   - Best-1 luôn là bộ rủi ro nhất và cháy trong test.
   - Dấu vân tay của martingale:
     - Trong từng cửa sổ, đa số bộ có lãi (EURUSD: trung vị grid dương 15/15 nửa năm).
     - Nhưng một bộ điển hình thắng 67–80% số nửa năm vẫn dính một nửa năm lỗ nặng: trung vị nửa năm tệ nhất −28% đến −32%.
     - Ghép cả kỳ chỉ 53% (chéo), 40% (EURUSD), 8% (crypto) số bộ có lãi.
5. **Crypto vào lệnh thuận xu hướng** ("Reverse Strategy" của Waka, `--reverse`, `results/waka_reverse/`).
   - Thiết lập: buy khi close > BB trên và RSI ≥ 70, sell khi ngược lại. Grid/martingale vẫn nhồi lệnh khi giá đi ngược.
   - Bản chính: Sharpe −0,17, tổng −33%, DD −50%, win rổ 99%. Bản fade là −0,43.
   - Đối chứng: trung vị vào lệnh ngẫu nhiên −0,03 (p95 0,23); trung vị giá xáo trộn +1,19.
   - **FAIL cả 4 tiêu chí.**
   - Biến thể: chỉ một lệnh −0,90; không martingale −1,16; tốt nhất là RSI 75/25 với +0,03.
   - Tối ưu walk-forward FAIL: top-10 OOS −5% (3y/1y) và −12% (18m/6m); chỉ 6% số bộ có lãi cả kỳ.
   - Kết luận: đảo chiều tín hiệu không cứu được, vì tín hiệu BB/RSI M15 không có giá trị theo chiều nào. Thứ quyết định kết quả là cấu trúc martingale.
6. **Ghi chú engine §9l:** grid engine cũ khớp lệnh bị gap ở giá đặt lệnh, không phải giá gap. Với crypto 24/7 thì gap nhỏ nên ảnh hưởng không đáng kể. Engine Waka khớp ở giá gap.

## 9n. Tìm điều kiện vào lệnh cho grid/martingale Waka (`waka/regime_study.py`, `results/waka/regime/`)

**Thiết kế (chốt trong docstring trước khi xem kết quả).**
- **Rổ mẫu:** mỗi giờ mở một rổ mỗi chiều với grid/martingale mặc định, khoảng cách theo biến động (≈ 1σ ngày), không SL, đóng ở giá thị trường sau 30 ngày.
  - Crypto: 4,1 triệu rổ.
  - 3 cặp chéo: 313 nghìn rổ.
  - Holdout: 5 cặp chéo chưa từng dùng (EURGBP, EURCHF, AUDCHF, NZDCHF, CADCHF), 521 nghìn rổ.
- **Nhãn:**
  - lãi mỗi rổ, tính theo bậc grid;
  - rổ có phải nhồi đủ thang không;
  - first passage: P(đi thuận 1 bậc trước khi ngược 8 bậc), so với mức đường đi ngẫu nhiên theo log của từng rổ. Mức này ≈ 8/9 với forex và ≈ 0,92 với crypto.
- **12 đặc trưng ex-ante:** vol 4h/24h, xu hướng vol, efficiency ratio 24h/7d, momentum 24h và khoảng cách MA 7 ngày (có dấu theo chiều rổ), biên độ 7 ngày, cú nhảy lớn nhất, variance ratio 60 ngày; riêng crypto thêm funding và vol của BTC.
- **Chọn (chỉ trên train):** |Spearman theo decile| ≥ 0,7 và chênh lệch tercile trên/dưới có |t theo tuần| ≥ 3.
- **Chia train/test:** crypto 2021–23 / 2024–26; forex 2018–22 / 2023–26.
- Kernel `basket_outcomes` ≡ `waka_kernel` cho từng rổ (sai số 1e-13). First passage trên các đường ngẫu nhiên độc lập ra 0,8875 ± 0,014, so với lý thuyết 0,893.

**Kết quả quy trình đã đăng ký: không đặc trưng nào đạt ngưỡng ở cả hai thị trường, bộ lọc rỗng, không có gì để test.**
- Mạnh nhất: forex `dist_ma_7d`/`mom_24h` |t| ≈ 1,6; crypto `rv_24h` t = 1,4.
- Lãi mỗi rổ trên train: crypto −0,02 bậc (t −0,4); forex +0,09 (t 1,2).

**Giả thuyết khai báo thêm, kiểm một lần trên test và holdout.**
- **H1:** cặp chéo có P(+1 trước −8) cao hơn mức đường đi ngẫu nhiên, t ≥ 2.
- **H2:** lãi mỗi rổ > 0, t ≥ 2.

| | Mức vượt first passage | t (tuần) | H1 | Lãi/rổ (bậc) | t | H2 |
|---|---|---|---|---|---|---|
| 3 cặp chéo train 2018–22 | +0,020 | 3,9 | (gốc) | +0,09 | 1,2 | |
| 3 cặp chéo test 2023–26 | +0,046 | 9,2 | **ĐẠT** | +0,03 | 0,2 | TRƯỢT |
| Holdout 5 cặp, 2018–26 | +0,036 (cả 5 dương) | 10,4 | **ĐẠT** | +0,05 | 0,6 | TRƯỢT |
| Crypto train | −0,004 | −0,5 | | −0,02 | −0,4 | |

1. **RÚT LẠI: "forex chéo hồi về" là ảo giác do cách đếm (phát hiện ở §9o).**
   - **Cách đếm sai:** rổ chưa chạm +1 hay −8 sau 30 ngày bị loại khỏi phép đo. Số này là 6–7% với forex, 9% với crypto.
   - **Vì sao làm lệch:** mốc 8/9 tính trên mọi rổ, còn giá trị đo được chỉ tính các rổ kết thúc trong 30 ngày. Ngay cả với đường đi ngẫu nhiên, nhóm rổ kết thúc trong 30 ngày đã có xác suất +1 trước cao hơn 8/9, vì chạm +1 nhanh còn chạm −8 lâu.
   - **Đo lại đúng cách:** so với giá xáo trộn theo tuần, cùng điểm vào lệnh và cùng giới hạn 30 ngày, rổ chưa kết thúc tính là "không +1 trước".
     - 3 cặp chéo: 0,866 so với 0,870, chênh −0,004 (t −1,3).
     - Holdout 5 cặp: 0,860 so với 0,872, chênh −0,012 (t −4,2).
   - **Kết luận:** các cặp chéo **không** hồi về hơn giá xáo trộn, holdout còn kém hơn. Bỏ râu nến hay EURUSD cũng dính cùng lỗi đếm.
   - Kiểm tra trên đường đi ngẫu nhiên lúc trước không bắt được lỗi này vì ở đó mọi rổ đều kết thúc trước khi hết hạn.
2. **Martingale (mô tả vẫn đúng):**
   - 99,94% số rổ chốt TP, mỗi rổ khoảng +0,22 bậc.
   - 0,04–0,06% số rổ không đóng được sau 30 ngày, lỗ trung bình −300 đến −480 bậc.
   - Khoảng 1/1000 số rổ ăn mất khoảng 90% lãi; không đặc trưng ex-ante nào dự báo được những rổ đó.

## 9o. Lệnh khối lượng cố định theo first passage (`waka/first_passage.py`, `results/waka/first_passage/`)

**Thiết kế (chốt trong docstring trước khi chạy).**
- Mỗi cặp luôn giữ 1 lệnh long + 1 lệnh short; lệnh đóng thì mở lại ở đầu giờ kế tiếp.
- Bản chính S1: TP 1 bậc, SL 8 bậc, rủi ro 1% vốn mỗi lệnh khi chạm SL.
- Biến thể: TP/SL 1/4, 2/8, và grid 9 lệnh khối lượng đều (TP 1 bậc, SL bậc 9).
- Dev trên 8 cặp đã dùng; 7 cặp mới để dành kiểm tra một lần.

**Kết quả dev: TRƯỢT hết.**

| | S1 1/8 | 1/4 | 2/8 | Grid khối lượng đều |
|---|---|---|---|---|
| 3 cặp chéo: Sharpe | −2,27 | −3,88 | −2,22 | −0,61 |
| Holdout 5 cặp: Sharpe | −1,98 | −3,33 | −1,86 | +0,22 |

- Win rate của S1 là 88,4%, đúng mức đường đi ngẫu nhiên. Giá xáo trộn cho Sharpe trung vị −2,87, p95 −2,03, nên giá thật không hơn giá xáo trộn.
- Chi phí đáng kể: swap −1%/năm (giả định, cả hai chiều) cộng spread ăn khoảng 2%/năm. Lệnh thua thường giữ hàng tuần.
- Kernel đúng: S1 khớp nhãn first passage trên cùng điểm vào lệnh (176/176 lệnh dính SL đều có nhãn 0; chỉ 7 lệnh lệch do spread). Chính phép đối chiếu này làm lộ lỗi đếm ở §9n.
- **Không chạy 7 cặp mới**, vì tiền đề đã bị bác bỏ. Các cặp đó còn sạch:
  - GBPAUD, GBPNZD, GBPCAD, GBPCHF chưa tải;
  - EURAUD, EURNZD đã tải nhưng chưa phân tích;
  - EURCAD không có trên FXCM.

## 9p. Các chiến thuật Bollinger + RSI trên forex (`data_fx_wf/`, `results/wf_fx_*`)

**Thiết lập.**
- 9 cặp: AUDCAD, AUDNZD, NZDCAD, EURUSD, EURGBP, EURCHF, AUDCHF, NZDCHF, CADCHF. EURAUD và EURNZD để dành.
- Giá mid FXCM nến 5m, chạy ở khung 15min, 2018–2026.
- Grid lấy từ notebook, xếp hạng theo `r_martin_monthly`.
- Walk-forward 18m/6m và 3y/1y; `--min-trades 50`.
- Phí forex: `--entry-fee 0.0002` (≈ 2 pip mỗi vòng, gồm spread và commission), `--holding-fee 0.00001` mỗi 8h (≈ swap 1%/năm).
  - Trước đây phí crypto được viết cứng trong kernel. Nay `ENTRY_FEE_RATE`/`HOLDING_FEE_RATE` là hằng số module của `multi_position_engine.py` và `short_bollinger+RSI/train_full_grid_gpu.py`, mặc định giữ nguyên giá trị crypto.
- Chuyển dữ liệu: `waka/export_fx_for_wf.py`.

| R/tháng ở test | Trung vị toàn grid | Top 25% | Top-10 | Cặp có trung vị > 0 | Cửa sổ có top 25% > 0 |
|---|---|---|---|---|---|
| Short trend 18m/6m | −0,11 | −0,45 | −0,35 | 0/9 | 1/15 |
| Short trend 3y/1y | −0,11 | −0,29 | −0,19 | 0/9 | 0/6 |
| Short RSI 18m/6m | −0,39 | −0,58 | −0,36 | 0/9 | 1/15 |
| Short RSI 3y/1y | −0,40 | −0,43 | −0,28 | 0/9 | 0/6 |
| Short fade 18m/6m | −0,37 | −0,27 | −0,19 | 1/9 | 5/15 |
| Short fade 3y/1y | −0,31 | −0,17 | −0,12 | 1/9 | 0/6 |
| Long fade 18m/6m | −0,02 | −0,29 | −0,24 | 0/9 | 2/15 |
| Long fade 3y/1y | −0,02 | −0,18 | −0,17 | 0/9 | 0/6 |

**FAIL cả 8.**
- Grid trend có nhiều bộ ngưỡng RSI 5–15 hầu như không có lệnh trên forex, nên trung vị toàn grid gần 0.
- Trend không có lợi thế ngay cả khi bỏ phí: 3y/1y top 25% −0,10.

**Chẩn đoán phí (lịch 3y/1y, xem sau khi chạy, không phải lần thử chiến thuật).**

| Short fade | Phí 0 | 0,5 pip | 1 pip | 1,5 pip | 2 pip |
|---|---|---|---|---|---|
| Top 25% | +0,42 | +0,10 | −0,04 | −0,11 | −0,17 |
| Trung vị toàn grid | +0,27 | +0,06 | −0,08 | −0,21 | −0,31 |
| Cặp có trung vị > 0 | 8/9 (ĐẠT) | 5/9 | 3/9 | 2/9 | 1/9 |

- Long fade: phí 0 thì ĐẠT (top-10 +0,51, top 25% +0,32, 8/9 cặp); phí 1 pip thì −0,09.
- **Fade trên forex 15min có lợi thế thô thật**: giá hồi lại sau khi phá dải, và chọn theo train có tác dụng (top-10 > trung vị khi không phí).
- Nhưng điểm hòa vốn chỉ khoảng **0,7 pip mỗi vòng**, thấp hơn chi phí thực tế:
  - spread FXCM trung vị 0,2–1,5 pip, cộng commission ECN khoảng 0,7 pip;
  - lệnh fade vào đúng lúc giá chạy mạnh, khi spread rộng.
- Lý do: rủi ro mỗi lệnh (theo độ rộng dải 15min) chỉ 0,2–0,5% giá, nên mỗi pip phí tốn nhiều R.
- Hướng còn mở, chưa thử:
  - khung lớn hơn (1h/4h), để rủi ro mỗi lệnh lớn hơn so với phí;
  - vào lệnh bằng limit ở dải (maker, ăn spread), nhưng phải mô hình được việc lệnh limit chỉ khớp khi giá đang chạy ngược mình.
- Nếu thử, dùng EURAUD/EURNZD hoặc cặp GBP chưa tải làm bộ xác nhận.

**Siết ngưỡng phí tối đa mỗi lệnh (`MAX_FEE_R`; trước đây luật gốc cố định ở 20% R).**
- Lựa chọn được chốt trước trong `results/fx_fade_confirmation_preregistration.md`.
- **Dev (9 cặp, 3y/1y, phí 2 pip), top-10 R/tháng theo ngưỡng 3% / 5% / 10% / 20%:**
  - Short fade: +0,04 / **+0,11** / −0,06 / −0,12. Riêng ngưỡng 3% đạt transfer bar.
  - Long fade: **+0,03** / +0,00 / −0,08 / −0,17.
  - Theo luật "top-10 cao nhất": short chọn 5%, long chọn 3%.
- **Xác nhận một lần** trên 5 cặp chưa dùng: EURAUD, EURNZD, GBPNZD, GBPCAD, GBPCHF. GBPAUD không có trên FXCM và không thay bằng cặp khác.

| Xác nhận | Top-10 | Top-10 dương | Top 25% | Transfer bar | Kết luận |
|---|---|---|---|---|---|
| Short fade 5%, 3y/1y | −0,00 | 3/6 năm | −0,14 | FAIL (0/5 cặp) | FAIL |
| Short fade 5%, 18m/6m | +0,06 | 9/15 | −0,13 | FAIL | FAIL |
| Long fade 3%, 3y/1y | +0,05 (t ≈ 2,0) | 5/6 | +0,07 | FAIL (trung vị grid = 0) | FAIL |
| Long fade 3%, 18m/6m | +0,09 (t ≈ 2,0–2,3) | 11/15 | +0,09 | FAIL (trung vị grid = 0) | FAIL |

- Theo tiêu chí đã chốt, **FAIL cả 4**.
- **Short fade không lặp lại được trên cặp mới.**
- **Long fade đạt phần top-10** (dương, ≥ 2/3 số cửa sổ, t ≈ 2), nhưng trượt transfer bar.
  - Lý do trượt: với ngưỡng 3%, phần lớn bộ trong grid gần như không có lệnh, nên trung vị toàn grid = 0. Tiêu chí "trung vị > 0" vì thế suy biến; đây không phải lỗ.
  - Trên dev, chính bộ long fade này chỉ +0,03 (t ≈ 1).
  - Lợi thế rất mỏng: khoảng 14–16 lệnh mỗi năm mỗi bộ, +0,05–0,09 R/tháng, tức khoảng +0,05 R mỗi lệnh.
  - Kết luận: một ứng viên yếu, chưa đủ để coi là chiến thuật.

**Khung 1h** (chốt trước trong `results/fx_1h_preregistration.md`).
- Thiết lập: cùng 9 cặp dev, phí 2 pip, `MAX_FEE_R` 0,2, `--fee-bars 8`, grid notebook giữ nguyên số nến.
- Tiêu chí: top-10 > 0, top-10 dương ≥ 2/3 số cửa sổ, top 25% > 0.
- Đạt ở dev thì mới xác nhận trên 5 cặp JPY chưa dùng (EURJPY, GBPJPY, AUDJPY, CADJPY, NZDJPY; đã tải về).

| Dev 1h, R/tháng | Top-10 3y/1y · 18m/6m | Cửa sổ top-10 > 0 | Top 25% | Trung vị toàn grid |
|---|---|---|---|---|
| Short trend | −0,10 · −0,28 | 2/6 · 3/15 | −0,10 · −0,25 | −0,01 · −0,00 |
| Short RSI | −0,18 · −0,33 | 1/6 · 1/15 | −0,16 · −0,28 | −0,07 · −0,06 |
| Short fade | +0,02 · −0,20 | 3/6 · 5/15 | +0,02 · −0,08 | **+0,04 · +0,02** |
| Long fade | −0,16 · −0,22 | 1/6 · 1/15 | −0,14 · −0,27 | −0,01 · −0,00 |

- Không chiến thuật nào đạt, nên không chạy xác nhận; các cặp JPY vẫn sạch.
- Ở khung 1h, phí chiếm ít R hơn, nên trung vị toàn grid của short fade dương nhẹ ngay cả khi có phí 2 pip (15min: −0,31).
- Nhưng chọn theo train không thêm được gì: top-10 chỉ +0,02 và 3/6 năm dương.

## 9q. Các chiến thuật Bollinger + RSI trên vàng XAUUSD (`results/wf_gold_*`)

**Thiết lập (chốt trước trong `results/gold_preregistration.md`).**
- **Dữ liệu:** histdata.com, nến 1 phút giá bid, 2018-01 → 2026-10 (`waka/download_histdata.py`). Vàng tăng khoảng 1.300 → 4.300.
- **Phí giả định:** 0,015% giá mỗi vòng. Phí giữ lệnh: short ≈ 1%/năm, long ≈ 5,5%/năm (swap long vàng cao).
- **Chạy:** 4 chiến thuật × 5min/15min/1h × 3y/1y và 18m/6m = 24 lần, grid lấy từ notebook.
- **Tiêu chí:** top-10 > 0, top-10 dương ≥ 2/3 số cửa sổ, top 25% > 0. Đạt thì xác nhận trên bạc XAGUSD (đã tải).

**FAIL cả 24, nên không chạy bạc.** Top-10 R/tháng (3y/1y · 18m/6m):

| | 5min | 15min | 1h |
|---|---|---|---|
| Short trend | −0,02 · −0,27 | −0,17 · −0,38 | −0,11 · −0,35 |
| Short RSI | −0,47 · −0,61 | −0,28 · −0,31 | −0,11 · −0,29 |
| Short fade | −1,18 · −1,41 | −0,56 · −1,05 | −0,25 · −0,19 |
| Long fade | +0,13 (3/6 năm, top 25% −0,17) · −0,16 | +0,01 · −0,08 | −0,17 · −0,12 |

- Các bản short ngược gió với một thị trường tăng gấp ba: tương quan top-10 với giá vàng từ −0,6 đến −0,9.
- Short fade tệ nhất (trung vị toàn grid −0,7 đến −2,0 R/tháng): bán khi giá vượt dải trên, trong lúc vàng đang chạy lên.
- Long fade thuận chiều xu hướng nhưng vẫn không đạt: dương chỉ 3/6 hoặc 7/15 cửa sổ, top 25% âm. Phần lãi ít ỏi đi theo thị trường (tương quan +0,4 đến +0,8).

## 9r. Grid/martingale giữ lệnh ngắn (≤ 5 ngày) trên forex chéo (`waka/short_horizon_study.py`, `waka/anchor_grid.py`)

**Bước 1: đo hồi về ở quy mô vài ngày.**
- Phép đo: P(đi thuận 1 bậc trước khi ngược k bậc), trong vòng 5 ngày và không qua cuối tuần.
- Phạm vi: 14 cặp chéo, mỗi giờ một lần vào lệnh, cả hai chiều.
- Đối chứng: xáo trộn nến cùng khung giờ trong ngày giữa các ngày trong tháng. Cách này giữ nhịp biến động trong ngày và xu hướng của tháng; trên đường đi ngẫu nhiên nó cho kết quả giống giá thật.
- Kết quả (mức chênh giá thật − đối chứng, k = 4):
  - **Vào lệnh bất kỳ lúc nào:** 0,25σ +0,003, 0,5σ −0,018, 1σ −0,040. Đúng ở cả 14/14 cặp và mọi phiên; không có phiên nào "yên" hơn hẳn. Biến động dồn cục làm giá thật bất lợi cho grid.
  - **Vào theo chiều kéo về trung bình 5 ngày** (giá lệch ≥ 1σ): +0,021 (t 11), +0,017 (t 6), +0,004. Vào theo chiều xa trung bình thì −0,012 đến −0,073.
- Đây là lợi thế hồi về duy nhất tìm được ở quy mô vài ngày.

**Bước 2–3: grid neo trung bình** (chốt trước trong docstring).
- **Mở rổ:** ở đầu giờ, khi giá lệch ≥ 1σ khỏi trung bình 5 ngày, theo chiều kéo về.
- **Lưới:** bậc 0,5σ; lot 1 / 1,5 / 2 / 2,5 / 3; lệnh nhồi phải cách lệnh trước ≥ 4 giờ.
- **Thoát:** mỗi lệnh TP riêng 1 bậc (LIFO); đóng hết khi giá chạm trung bình; stop ở bậc 5 (lỗ 1% phần vốn); quá 5 ngày hoặc tới cuối tuần thì đóng.
- **Chi phí:** spread FXCM, commission, swap −1%/năm.
- Kernel `anchor_grid_kernel` khớp tính tay ở các phép kiểm tra LIFO, stop, dừng theo thời gian và thoát ở trung bình.

| 14 cặp, 2018–26 | Sharpe | Lãi/năm | DD | Năm dương | Cặp dương |
|---|---|---|---|---|---|
| Bản chính | −1,36 | −2,1% | −18% | 0/9 | 2/14 |
| Lot đều | −1,26 | −2,2% | −19% | 0/9 | 2/14 |
| Không giãn nhồi | −1,26 | −2,4% | −20% | 0/9 | 2/14 |
| Bậc 0,25σ | −3,53 | −5,9% | −42% | 0/9 | 0/14 |
| *Chẩn đoán: không chi phí* | *+1,32* | *+1,9%* | *−2,4%* | *8/9* | *13/14* |
| *Chẩn đoán: chỉ spread* | *−0,49* | *−0,7%* | *−9,5%* | *2/9* | *5/14* |

- **FAIL**, nên không chạy xác nhận JPY. Bản chính thắng cả hai đối chứng (vào lệnh ngẫu nhiên −11,4; giá xáo trộn −2,6), nhưng vẫn âm sau phí.
- Lợi thế thô có thật: không phí thì Sharpe 1,3, dương 13/14 cặp. Riêng spread đã đủ xóa nó, commission và swap đẩy hẳn xuống âm.
- Martingale và việc giãn thời gian nhồi gần như không đổi kết quả. Quyết định nằm ở tỉ lệ lợi thế/chi phí, không nằm ở cách nhồi lệnh.
- Bậc lưới nhỏ hơn thì tệ hơn, vì phí chiếm nhiều phần của mỗi bậc hơn.

**Chi phí cần để có lãi** (chẩn đoán trên dev, `results/waka/anchor_grid/cost_sweep_dev.csv`).
- Spread trung vị FXCM đã ở mức ECN thô: EURUSD 0,17 pip, EURGBP 0,41, các cặp chéo 0,5–1,0. Vì vậy bản "đủ chi phí" ở trên (spread FXCM + commission 3,5 USD/lot/chiều) chính là mô hình tài khoản ECN.
- **Grid neo trung bình, Sharpe theo tổng chi phí mỗi vòng (pip, cặp trung vị), swap −1%/năm:**
  - 0 → 1,11; 0,3 → 0,78; 0,5 → 0,21; 0,7 → −0,24 đến +0,35 (tùy spread hay commission); 0,9 → −0,21 đến −0,64; 1,5 (ECN thực tế) → −1,36.
  - Hòa vốn khoảng 0,5–0,6 pip. Muốn Sharpe ≥ 0,5 phải ≤ khoảng 0,3–0,4 pip. Ngay cả khi đó, lãi chỉ khoảng 1%/năm ở mức rủi ro 1% mỗi rổ.
- **Short fade 15min (3y/1y, 9 cặp), theo phí mỗi vòng:**

| Phí mỗi vòng | 0 | 0,25 | 0,5 | 0,75 | 1,0 | 1,5 | 2,0 pip |
|---|---|---|---|---|---|---|---|
| Top-10 | +0,58 | +0,18 | +0,08 | +0,00 | −0,10 | −0,10 | −0,12 |
| Trung vị toàn grid | +0,27 | +0,13 | +0,06 | −0,02 | −0,08 | −0,21 | −0,31 |

  - Hòa vốn khoảng 0,7 pip.
- **Chi phí ECN điển hình:** spread thô + commission 6–7 USD/lot mỗi vòng ≈ EURUSD 0,8–0,9 pip, cặp chéo 1,2–1,7 pip. Cả hai đều cao hơn điểm hòa vốn.
- Chỉ những tài khoản có commission rất thấp (≲ 1,5 USD/lot/chiều, thường dành cho khối lượng lớn) trên EURUSD mới chạm khoảng 0,5 pip, sát điểm hòa vốn và vẫn mỏng.

## 9s. Giữ lệnh vài tuần: grid neo giá trị dài hạn và momentum khung ngày (`waka/long_horizon.py`, `results/waka/long_horizon/`)

**Thiết lập (chốt trước trong `results/fx_long_horizon_preregistration.md`).**
- Dữ liệu: nến 1h dựng từ dữ liệu 1 phút của histdata (`data_fx_long/`).
- Người dùng dừng việc tải sau 7/19 cặp, nên chỉ chạy trên 7 cặp: EURUSD, EURGBP, EURCHF (từ 2005); AUDCAD, AUDNZD (2007); NZDCAD, AUDCHF (2008). Đây là sai lệch so với file đăng ký (14 cặp).
- Sau đó `data_fx_long/` đã bị xóa theo yêu cầu. Muốn chạy lại thì tải lại bằng `waka/download_histdata.py --start-year 2005 --timeframe 1h --out-dir reference/data_fx_long --no-cache`.
- Chi phí: spread trung vị FXCM + commission 0,7 pip, swap −1%/năm.
- Đối chứng: xáo trộn nguyên từng tuần trong mỗi năm, 20 lần.
- **Grid:**
  - neo trung bình 120 ngày; mở rổ khi giá lệch ≥ 1σ tháng, theo chiều kéo về;
  - bậc 0,5σ tháng; lot 1–3; lệnh nhồi cách nhau ≥ 2 ngày;
  - mỗi lệnh TP riêng 1 bậc; đóng hết ở neo; stop ở bậc 5 (1% vốn); giữ tối đa 4 tuần.
- **Momentum:** dấu lợi nhuận 63 ngày, chia vốn để mỗi cặp có biến động 10%/năm, rebalance hàng tuần.

| 7 cặp, 2005–2026 | Sharpe | Lãi/năm | DD | Năm dương | Cặp dương |
|---|---|---|---|---|---|
| Grid bản chính | +0,03 | +0,01% | −2,2% | 13/22 | 4/7 |
| Grid lot đều | +0,00 | 0,00% | −2,7% | 13/22 | 3/7 |
| Grid vào ở 1,5σ | +0,18 | +0,05% | −1,4% | 15/22 | 5/7 |
| Grid neo 250 ngày | −0,05 | −0,02% | −2,8% | 13/22 | 4/7 |
| Grid lot Waka 1-1-2-4-8 | +0,03 | +0,01% | −1,6% | 14/22 | 4/7 |
| Momentum 63 ngày (chính) | −0,31 | −3,1% | −50% | 6/22 | 0/7 |
| Momentum 21 / 126 / 252 ngày | −0,26 / −0,11 / −0,23 | | −40 đến −47% | 6–8/22 | 0–3/7 |

- **FAIL cả hai.**
- **Grid:**
  - Sharpe bằng mức đối chứng (trung vị 0,07, p95 0,19).
  - Chỉ khoảng 11 rổ mỗi cặp mỗi năm, win 81%. 30% số rổ đóng vì hết 4 tuần, chỉ 0,5% chạm neo.
  - Lãi gần như bằng 0. Martingale mạnh hay lot đều không đổi gì.
- **Momentum:**
  - Âm ở cả 7/7 cặp trong 20 năm (EURGBP −84%, AUDCAD −68%).
  - Đối chứng cũng âm (trung vị −0,51, p95 −0,26), vì swap và phí tính trên đòn bẩy cần để đạt biến động 10% với những cặp ít biến động.
  - Momentum thật hơn đối chứng một chút nhưng không có ý nghĩa thống kê. Chỉ có lãi ở năm có xu hướng mạnh (2008, 2015).
- **Kết luận:** ở quy mô vài tuần, các cặp chéo này gần như ngẫu nhiên, không nghiêng hẳn về hồi về hay về xu hướng. "Đi ngang" chỉ là tính chất ở quy mô nhiều năm.

## 10. Câu hỏi mở và bước tiếp theo

1. Đưa exit Donchian 50 / giữ 1152 vào ô Cấu hình các notebook long breakout (chưa làm — người dùng quyết định).
2. Mô phỏng danh mục ghép short RSI (TP 1,1) + long breakout (Donchian 50 / 1152) theo ngày, một trần rủi ro chung.
3. Thử exit Donchian cho phía short (đối xứng: close > đỉnh cao nhất N nến) — cần mở rộng engine sang short.
4. Funding thực tế (dữ liệu Binance miễn phí) thay phí giữ lệnh đối xứng — quan trọng hơn khi giữ lệnh tới 12 ngày.
5. Thử trên tài sản/giai đoạn khác (BTC, ETH, SOL; 2020–2021) để biết bất đối xứng long/short có phải đặc thù AVAX.
6. Dữ liệu mới sau 2025-06-20 làm kiểm tra sạch một lần cho quy trình đã chốt (không chỉnh sau khi xem).

## 11. Tái tạo

```bash
# walk-forward (9m/3m) — ví dụ long breakout với exit "không TP"
uv run python reference/walk_forward_15min.py --folders long_bollinger+RSI+regime long_bollinger+RSI+volume \
    --fee-bars 32 --set tp_sl_ratio=0,1.1 --split-by tp_sl_ratio --out <dir>
# exit indicator (mục 9) rồi gom bảng + chấm tiêu chí
uv run python reference/walk_forward_15min.py --folders long_bollinger+RSI+regime --fee-bars 32 \
    --exits tp:1.1 none rsi:50 donchian:50 donchian:200 --set max_holding_bars=288,1152 \
    --split-by exit_rule,tp_sl_ratio,max_holding_bars --split-by exit_rule,exit_param,tp_sl_ratio,max_holding_bars \
    --out <dir>/wf_ind_exit_long_bollinger+RSI+regime
uv run python reference/results/summarize_indicator_exits.py <dir>
# đối chứng ngẫu nhiên với exit indicator
uv run python reference/random_entry_benchmark.py --logics "long RSI" --exits donchian:50 --hold 1152 --out <dir>
# đối chứng vào lệnh ngẫu nhiên
uv run python reference/random_entry_benchmark.py --out <dir>
# nhiều lệnh chồng nhau: 1 lệnh (gốc) vs không giới hạn, cùng fold (mục 9c)
uv run python reference/walk_forward_15min.py --folders multi_position/long_bollinger+RSI \
    multi_position/short_bollinger+RSI --fee-bars 32 --set max_open=1,64 --split-by max_open --out <dir>
# short RSI trên mọi coin trong reference/data (mục 9d)
uv run python reference/walk_forward_multi_asset.py --data-dir reference/data --folder short_bollinger+RSI \
    --fee-bars 32 --score-formulas r_martin_monthly "performance/max_dd*r2**5" --out <dir>
# short RSI + bộ lọc xu hướng trên mọi coin, mỗi trend_window một dòng (mục 9d ý 5)
uv run python reference/walk_forward_multi_asset.py --data-dir reference/data --folder "short_bollinger+RSI+regime" \
    --fee-bars 32 --score-formulas r_martin_monthly "performance/max_dd*r2**5" --split-by trend_window --out <dir>
# dữ liệu Binance perp của lab (cắt ở 2025-10-01) rồi short trend 15min 3y/1y và 5min 18m/6m (mục 9g)
uv run python reference/build_binance_perp_csv.py --out reference/data_binance_perp
uv run python reference/walk_forward_multi_asset.py --data-dir reference/data_binance_perp \
    --folder "short_bollinger+RSI+trend" --timeframe 15min --fee-bars 32 --train-months 36 --test-months 12 \
    --min-trades 40 --save-top 10 --out <dir>
uv run python reference/walk_forward_multi_asset.py --data-dir reference/data_binance_perp \
    --folder "short_bollinger+RSI+trend" --timeframe 5min --fee-bars 96 --train-months 18 --test-months 6 \
    --max-holding-bars 288 --min-trades 40 --save-top 10 --out <dir>
# bản 1h: --timeframe 1h --fee-bars 8 --train-months 36 --test-months 12
# 47 coin 5m thật (mục 9j): tải, walk-forward 5min/15min 9m/3m, luật biến động, đặc điểm coin
uv run python reference/download_binance_um_klines.py --out reference/data_5m
uv run python reference/walk_forward_multi_asset.py --data-dir reference/data_5m --folder "short_bollinger+RSI+trend" \
    --timeframe 5min --fee-bars 96 --train-months 9 --test-months 3 --max-holding-bars 288 --min-trades 50 \
    --save-top 10 --out reference/results/wf_5m_short_rsi_trend_9m3m   # 15min: --timeframe 15min --fee-bars 32
uv run python reference/results/vol_rule_eval.py reference/results/wf_5m_short_rsi_trend_9m3m --data-dir reference/data_5m
uv run python reference/results/coin_traits.py --data-dir reference/data_5m --extra --start 2023-01-15 --end 2026-01-14 \
    --result reference/results/wf_multi_asset_short_rsi_trend_nbgrid_9m3m
# danh mục xu hướng khung ngày (mục 9k); notebook: reference/portfolio_trend/daily_trend.ipynb
uv run python reference/portfolio_trend/daily_trend.py --refresh --random-runs 1000
# grid và martingale DCA (mục 9l); notebook: reference/grid_martingale/grid_martingale.ipynb
uv run python reference/grid_martingale/run_grid_martingale.py --shuffles 20
# Waka Waka (mục 9m): dữ liệu forex FXCM + vá histdata, chạy bản chính/đối chứng, tối ưu walk-forward;
# notebook: reference/waka/waka.ipynb
uv run python reference/waka/download_fx.py
uv run python reference/waka/fill_gaps_histdata.py
uv run python reference/waka/run_waka.py --nulls 20
uv run python reference/waka/optimize_waka.py --markets crosses eurusd crypto --workers 8
# bản thuận xu hướng (results/waka_reverse/): thêm --reverse --markets crypto vào hai lệnh trên
# điều kiện vào lệnh (mục 9n): holdout = 5 cặp chéo mới
uv run python reference/waka/download_fx.py --pairs EURGBP EURCHF AUDCHF NZDCHF CADCHF
uv run python reference/waka/fill_gaps_histdata.py --pairs EURGBP EURCHF AUDCHF NZDCHF CADCHF
uv run python reference/waka/regime_study.py build --markets fx crypto holdout
uv run python reference/waka/regime_study.py select --markets fx crypto
# vàng (mục 9q): dữ liệu histdata, rồi --symbols xauusd --timeframe 5min|15min|1h --fee-bars 96|32|8
#   --entry-fee 0.00015 --holding-fee 0.00001 (long fade 0.00005)
uv run python reference/waka/download_histdata.py --pairs XAUUSD XAGUSD --start-year 2018
# Bollinger + RSI trên forex (mục 9p); phí forex qua --entry-fee/--holding-fee
uv run python reference/waka/export_fx_for_wf.py --pairs AUDCAD AUDNZD NZDCAD EURUSD EURGBP EURCHF AUDCHF NZDCHF CADCHF
uv run python reference/walk_forward_multi_asset.py --data-dir reference/data_fx_wf --folder "short_bollinger+RSI+fade" \
    --timeframe 15min --fee-bars 32 --train-months 36 --test-months 12 --min-trades 50 --save-top 10 \
    --entry-fee 0.0002 --holding-fee 0.00001 --out reference/results/wf_fx_short_fade_36m12m
# một script grid + kiểm tra CPU
uv run python "reference/long_bollinger+RSI+regime/train_full_grid_gpu.py" --verify 16
```
Các script phân tích phụ (ensemble, drawdown, gom bảng exit) nằm trong `results/`.
