# Đánh giá khả thi: hai trường phái cho nhà đầu tư cá nhân

> So sánh **Trường phái A — Cross-sectional factor + ML** (RD-Agent(Q), AlphaAgent — xem [[08-LLM-QUANT-RESEARCHER]]) với **Trường phái B — Rule-based TA đơn/ít instrument** (mục tiêu ban đầu của dự án).
> Ngày đánh giá: 2026-09-20 · Có research web · Liên quan: [[00-TONG-HOP-NGHIEN-CUU]]

---

## TL;DR — Kết luận ngược với trực giác

1. **Trường phái A khả thi hơn nhiều so với 5 năm trước** — hai rào cản lớn nhất đã sụp đổ trong 2025–2026: dữ liệu PIT survivorship-free giờ chỉ **$29–49/tháng** (trước là $50k+ license), và **PDT rule $25.000 đã bị xóa bỏ từ 4/6/2026**. Báo cáo `92` trong thư mục đang **lỗi thời** ở điểm này.

2. **Nhưng rào cản còn lại của A là VỐN, không phải kiến thức hay công cụ.** Cross-sectional cần 50–100 vị thế đồng thời. Dưới ~$50k thì mỗi vị thế quá nhỏ để vượt lot size và chi phí cố định. Đây là rào cản cứng, không lách được bằng kỹ thuật.

3. **Cấu hình khả thi nhất mà cả 4 báo cáo gốc đều bỏ sót: cross-sectional trên CRYPTO PERP.** Nó cho bạn breadth của trường phái A mà **không cần** giấy phép short, không cần dữ liệu PIT fundamentals, không cần $50k, không vướng PDT. Đây là "cửa sau" duy nhất vào trường phái A cho vốn nhỏ.

4. ⚠️ **Nhưng bằng chứng lại không ủng hộ crypto cross-sectional:** nghiên cứu 2020–10/2025 cho thấy nó **thua** time-series momentum về risk-adjusted return, với **max drawdown 55%**, lý do là **các coin tương quan quá cao** → breadth hiệu dụng thấp hơn nhiều so với số coin danh nghĩa.

5. **Trường phái B có trần thấp nhưng sàn cao.** Sharpe thực tế 0.3–0.8, nhưng chạy được từ $1.000, không phụ thuộc hạ tầng, và là con đường duy nhất nếu bạn cần EA MQL5.

---

## 1. Vì sao cross-sectional cho Sharpe cao hơn — và vì sao cá nhân khó hưởng

**Fundamental Law of Active Management:**

```
IR ≈ IC × √breadth
```

Đây là toàn bộ câu chuyện. RD-Agent(Q) đạt IR 1.7382 với IC chỉ 0.0532 — edge cực mỏng nhân với **300 mã × 250 phiên/năm**.

| | Breadth danh nghĩa | Hệ quả |
|---|---|---|
| RD-Agent(Q), CSI 300 | 300 mã × 250 phiên | IR 1.74 với IC 0.05 |
| AlphaAgent, S&P 500 | 500 mã × 250 phiên | IR 1.05 với IC 0.0056 |
| **Rule-based TA, 1 symbol** | **1 × số lần vào lệnh** | **Trần Sharpe 0.3–0.8** |

> 🔑 **Đây là lý do cơ học khiến hai trường phái có trần hiệu năng khác nhau.** Không phải vì factor "thông minh hơn" rule TA, mà vì nó đặt nhiều cược hơn.

**Cái bẫy: breadth *hiệu dụng* luôn nhỏ hơn breadth danh nghĩa.** Công thức chỉ đúng khi các cược độc lập. Thực tế cổ phiếu cùng chịu market factor và sector factor; crypto còn tệ hơn — gần như mọi altcoin đi theo BTC. Nghiên cứu crypto cross-sectional chỉ rõ lợi nhuận thấp *"partly due to high correlations among cryptocurrencies"*.

---

## 2. Trường phái A — rào cản thực tế cho cá nhân

### ✅ 2.1. Hai rào cản đã sụp đổ (2025–2026)

**a) Dữ liệu PIT survivorship-free giờ rẻ**

| Nhà cung cấp | Giá | Ghi chú |
|---|---|---|
| Tradevo Data | **$29/tháng** | PIT US fundamentals, endpoint JSON có `as_of` |
| Valuein | từ **$49/tháng** | PIT survivorship-free SEC fundamentals |
| Sharadar | retail tier | 16.000+ công ty gồm mã đã delist, fundamentals từ 12/1997, ~150 chỉ số |
| Norgate Data | retail tier | Survivorship-bias-free, EOD |
| *WRDS/Compustat* | *$50k+* | *Chuẩn học thuật — không còn bắt buộc* |

→ Rào cản "dữ liệu đắt" mà báo cáo `92` nêu **đã không còn đúng**.

**b) PDT rule đã bị xóa bỏ**

Từ **4/6/2026**, theo thay đổi FINRA Rule 4210: yêu cầu vốn tối thiểu $25.000 và chính danh hiệu "Pattern Day Trader" **không còn tồn tại**. Thay vào đó là yêu cầu vốn tỷ lệ với exposure thực tế trong phiên; mức tối thiểu chung còn **$2.000** theo quy định margin hiện hành.

→ Báo cáo `92` viết *"PDT rule (Mỹ: cần $25.000...)"* — **cần cập nhật**.

### 🔴 2.2. Rào cản còn lại: VỐN và cấu trúc vị thế

Đây mới là rào cản thật, và nó **không lách được**.

Cross-sectional cần giữ 50–100 vị thế đồng thời. Với $10.000 chia 100 vị thế = **$100/vị thế**:

- Không vượt nổi lot size tối thiểu ở nhiều thị trường (A-shares TQ: lot 100 cổ phiếu — một mã ¥50 cần ¥5.000 ≈ $700/lot → **100 vị thế cần ~$70.000**)
- Chi phí cố định tối thiểu mỗi lệnh nuốt hết lợi nhuận
- Odd lot bị spread xấu hơn

**Ngưỡng vốn thực tế ước tính:**

| Vốn | Trường phái A khả thi? |
|---|---|
| < $10k | ❌ Không (trừ crypto perp) |
| $10k–50k | 🟡 Chỉ với universe nhỏ (20–30 mã), breadth giảm → IR giảm |
| > $50k | ✅ Đủ để giữ 50–100 vị thế có ý nghĩa |

### 🔴 2.3. Chi phí giao dịch với turnover cao

Cả RD-Agent(Q) lẫn AlphaAgent đều **rebalance hàng ngày**. Nghiên cứu về chi phí factor investing:

- Chi phí giao dịch trung vị **tổ chức** là **6,24 bps mỗi lần rebalance** trên NYSE — cá nhân cao hơn
- Khi chi phí giao dịch tỷ lệ chạm **0,5%**, tối ưu là **giữ toàn bộ vốn trong tài khoản ngân hàng thay vì giao dịch**
- Khi đưa chi phí vào tối ưu hóa, nhà đầu tư chỉ nên rebalance **~15% số phiên** — tức turnover penalty tự nhiên đẩy về gần buy-and-hold
- Chi phí ngầm (market impact) *"may substantially erode a strategy's expected excess returns"*

**Hệ quả trực tiếp:** nếu bạn tái hiện RD-Agent(Q) với chi phí retail thật, **hãy giảm tần suất rebalance xuống tuần hoặc 2 tuần**, chấp nhận IR thấp hơn. Rebalance hàng ngày là xa xỉ của tổ chức.

### 🔴 2.4. Short side

RD-Agent(Q) dùng **long-short**. Với cá nhân:

- **Mỹ:** short được, nhưng borrow fee dao động từ **0,25%/năm** (large-cap dễ mượn) tới **100%+/năm** (hard-to-borrow). Chính những mã mà model xếp hạng bét thường là mã đắt để short
- **Trung Quốc A-shares:** hạn chế nặng
- **Việt Nam:** ❌ **chưa có short-selling** (lộ trình 2026–2028)

→ Nếu chỉ chạy long-only, bạn mất một nửa alpha của mô hình long-short.

---

## 3. Cửa sau: cross-sectional trên CRYPTO PERP

Đây là cấu hình duy nhất cho phép cá nhân vốn nhỏ tiếp cận trường phái A, và **không báo cáo nào trong thư mục đề cập**.

| Rào cản của A | Crypto perp giải quyết thế nào |
|---|---|
| Cần $50k+ | ✅ Perp cho phép vị thế nhỏ, có đòn bẩy |
| Không short được | ✅ **Short là native** — không cần mượn, không borrow fee |
| Dữ liệu PIT đắt | ✅ Chỉ cần OHLCV, **free qua ccxt** |
| PDT / giờ giao dịch | ✅ 24/7, không quy định |
| Universe nhỏ | ✅ 100–200 cặp perp thanh khoản |

**Nhưng chi phí thì khắc nghiệt:**

- Taker fee ~**4 bps/lệnh** (mô hình chuẩn trong nghiên cứu perp)
- Funding rate tính **mỗi 8 giờ**, cộng dồn nếu giữ lâu
- ⚠️ **Altcoin thanh khoản mỏng: spread 1–3%** — *"instantly dwarfs a 0.1% trading fee"*
- Altcoin nhỏ cần slippage tolerance **2–5%**, so với 0,5–1% ở cặp lớn
- Roundtrip cost retail có thể lên tới **6,45%** trên một số nền tảng khi tính cả spread và slippage

### ⚠️ Và bằng chứng không ủng hộ

Nghiên cứu momentum crypto (1/2020 – 31/10/2025):

- Cross-sectional momentum *"delivered only modest long-term profitability"*
- **Time-series momentum đạt 31,96%/năm và vượt cross-sectional về risk-adjusted**
- Cross-sectional có **max drawdown 55,0%** và lợi nhuận thấp hơn, *"partly due to high correlations among cryptocurrencies"*
- Carry crypto: Sharpe 6,45 cho toàn mẫu 2020–2025 nhưng **rơi xuống 4,06 năm 2024 và âm năm 2025** — model decay điển hình
- ⚠️ Các nghiên cứu này *"does not incorporate transaction costs, slippage, funding rates, or liquidity constraints"* — tức con số thật còn tệ hơn

**Kết luận về cửa sau này:** khả thi về mặt *hạ tầng*, nhưng bằng chứng nói **time-series momentum vẫn tốt hơn cross-sectional trong crypto**. Nếu bạn định làm crypto, cross-sectional không phải lý do đủ mạnh để bỏ time-series.

---

## 4. Trường phái B — rule-based TA

### ✅ Ưu

| | |
|---|---|
| Vốn tối thiểu | **$1.000** hoặc thấp hơn |
| Universe | 1 symbol là đủ |
| Dữ liệu | Free (ccxt, broker feed) |
| Runtime | Vài phép tính indicator — không cần model inference |
| Export EA MQL5 | ✅ Được |
| Đọc hiểu/debug | ✅ Toàn bộ logic minh bạch |
| Rủi ro có SL/TP tường minh | ✅ Kiểm soát rủi ro rõ ràng |

### ❌ Nhược

**Trần hiệu năng thấp do breadth = 1.** Đồng thuận từ cộng đồng quant 2026:

> *"A genuine, tradeable, diversified quant strategy with a long-run Sharpe between 0.4 and 0.8 is a good outcome, and backtests promising 2.0 or better are almost always describing the in-sample period."*

> *"A realistic Sharpe above 3 in any strategy at 2026 should make you suspicious of methodology errors (look-ahead bias, survivorship, multiple-testing)."*

**Short-horizon đang chết dần:** *"Short-horizon trend models that once worked well have become harder to monetize as markets have grown faster and more efficient, while slower signals remain more resilient."*

**Nhưng signal chậm vẫn sống:** dữ liệu tháng 1979–2025, bộ lọc trend đơn giản (MA 10 tháng hoặc momentum 12–1) giữ được return ~10% CAGR, **giảm max drawdown từ −54% xuống ~−20%**, và nâng Sharpe **từ 0,72 lên 0,93**.

> 📌 Chú ý: con số 0,93 này là cho **equity index**, không phải single-instrument intraday. Và nó là bộ lọc trend cực kỳ đơn giản — không cần agent, không cần LLM.

### 📊 Điều kiện sống sót cho cá nhân

Đồng thuận về hệ thống retail thực sự tồn tại lâu dài:

> *"Quant systems that survive typically feature low frequency, not very sensitive to exact execution time, simple enough to run manually before/after work, and are boringly stable."*

---

## 5. Bảng đánh giá tổng hợp

| Tiêu chí | **A — Cross-sectional equity** | **A′ — Cross-sectional crypto** | **B — Rule-based TA** |
|---|---|---|---|
| **Vốn tối thiểu** | 🔴 ~$50k | 🟢 $2–5k | 🟢 $1k |
| **Chi phí dữ liệu** | 🟢 $29–49/tháng | 🟢 Free (ccxt) | 🟢 Free |
| **Short side** | 🟡 Borrow 0,25–100%/năm | 🟢 Native (perp) | 🟢 Native |
| **Chi phí giao dịch** | 🔴 Cao (turnover ngày) | 🔴 Spread alt 1–3% | 🟢 Thấp nếu low-freq |
| **Rào cản pháp lý** | 🟢 PDT đã bỏ (6/2026) | 🟢 Không có | 🟡 Broker B-book |
| **Trần Sharpe lý thuyết** | 🟢 1,0–1,7 | 🟡 Thấp hơn TS momentum | 🔴 0,3–0,8 |
| **Bằng chứng học thuật** | 🟢 Mạnh (nhưng alpha decay) | 🟡 Yếu hơn TS momentum | 🟡 Hỗn hợp |
| **Độ phức tạp hạ tầng** | 🔴 Cao | 🟡 Trung bình | 🟢 Thấp |
| **Export EA MQL5** | ❌ | ❌ | ✅ |
| **Interpretability** | 🔴 Model là hộp đen | 🔴 Như trên | 🟢 Toàn bộ |
| **Thời gian tới prototype** | 🔴 2–3 tháng | 🟡 1–2 tháng | 🟢 2–4 tuần |

---

## 6. Khuyến nghị

### 🎯 Với vốn < $10.000

**Chọn B, thêm một phần breadth.** Đừng cố làm cross-sectional equity — rào cản vốn là cứng.

Nhưng **đừng dừng ở 1 symbol**: chạy cùng một rule TA trên **rổ 10–20 instrument ít tương quan** (crypto majors + một vài FX pair + index future). Đây là cách lấy breadth mà không cần cross-sectional ranking:

```
IR ≈ IC × √breadth
     breadth = 1  → Sharpe 0.3–0.8
     breadth = 15 instrument ít tương quan → cải thiện đáng kể
```

Đây chính là cách CTA trend following hoạt động, và là chiến lược có **bằng chứng peer-reviewed mạnh nhất** theo mục 6 [[00-TONG-HOP-NGHIEN-CUU]].

### 🎯 Với vốn $10k–50k

**Time-series momentum trên rổ crypto perp 15–30 coin thanh khoản nhất.**

- Lấy được một phần breadth
- Short native, không borrow fee
- Dữ liệu free
- Bằng chứng ủng hộ TS hơn cross-sectional (31,96%/năm vs cross-sectional thua kém)
- Tránh long tail altcoin: **spread 1–3% giết mọi edge**

### 🎯 Với vốn > $50k

Lúc này trường phái A mới mở ra. Nhưng vẫn nên:

- **Giảm rebalance xuống tuần**, không theo ngày như paper (bằng chứng: chỉ nên rebalance ~15% số phiên khi tính chi phí thật)
- Long-only trước, thêm short sau khi đã validate (tránh borrow fee ăn mòn)
- Bắt đầu bằng factor đơn giản đã biết (Alpha158) làm baseline trước khi cho agent sinh factor mới

### ⛔ Ngưỡng dừng — khi nào biết mình sai

| Tín hiệu | Hành động |
|---|---|
| Edge biến mất khi đưa spread altcoin 1–3% vào | Thu universe về top-20 thanh khoản |
| Backtest Sharpe > 2,0 trên single instrument | **Nghi overfit** — không phải ăn mừng |
| Backtest Sharpe > 3,0 bất kỳ đâu | **Nghi lỗi phương pháp** (look-ahead, survivorship, multiple testing) |
| Cross-sectional crypto thua TS momentum | Bỏ cross-sectional, quay về TS |
| Cần > 50 vị thế mà vốn < $50k | Rào cản cứng — đổi hướng, không cố |

---

## 7. Điều chỉnh cho các tài liệu cũ

| Tài liệu | Điểm cần sửa |
|---|---|
| `92` | ❌ *"PDT rule: cần $25.000"* → **đã bị xóa bỏ từ 4/6/2026**, còn $2.000 theo margin rule |
| `92` | ❌ *"Dữ liệu chất lượng (survivorship-free, PIT) đắt"* → giờ **$29–49/tháng** |
| [[00-TONG-HOP-NGHIEN-CUU]] | Mục 6 nên bổ sung: cross-sectional crypto **thua** time-series momentum theo bằng chứng 2020–2025 |

---

## 8. Nguồn

**Chi phí giao dịch & turnover**
- [Transaction Costs of Factor-Investing Strategies — Financial Analysts Journal](https://www.tandfonline.com/doi/full/10.1080/0015198X.2019.1567190)
- [The market impact of rebalancing factor investing strategies — Alpha Architect](https://alphaarchitect.com/the-market-impact-of-rebalancing-factor-investing-strategies/)
- [Transaction-cost-aware Factors — Baldi-Lanfranchi (AFA)](https://afajof.org/management/viewp.php?n=135184)
- [Large-Scale Portfolio Allocation Under Transaction Costs](https://arxiv.org/pdf/1709.06296)

**Crypto cross-sectional & momentum**
- [Momentum Trading in Cryptocurrencies: Time-Series vs Cross-Sectional](https://www.journals.vu.lt/BATP/en/article/download/44540/42590/138419)
- [Systematic Trend-Following with Adaptive Portfolio Construction (arXiv 2602.11708)](https://arxiv.org/html/2602.11708v1)
- [Cross-sectional Momentum in Cryptocurrency Markets — Starkiller Capital](https://www.starkiller.capital/post/cross-sectional-momentum-in-cryptocurrency-markets)
- [Unravelling cross-sectional patterns in cryptocurrencies — China Accounting and Finance Review](https://www.emerald.com/cafr/article/27/4/493/1271913/Unravelling-cross-sectional-patterns-in)

**Chi phí crypto**
- [All Crypto Trading Fees Explained — BloFin](https://blofin.com/en/academy/education/crypto-trading-fees)
- [What Is Slippage in Crypto? 2025 Guide](https://blog.sei.io/s/what-is-slippage-crypto-guide/)

**Quy định & chi phí retail**
- [PDT Rule Change — E*TRADE](https://us.etrade.com/knowledge/library/margin/pattern-day-trading-rule-change)
- [FINRA eliminates $25,000 day-trading rule — Yahoo Finance](https://finance.yahoo.com/markets/options/articles/finra-just-killed-25-000-223500606.html)
- [Short Sale Cost — Interactive Brokers](https://www.interactivebrokers.com/en/pricing/short-sale-cost.php)
- [The Risks of Shorting: Borrow Fees — IBKR Campus](https://www.interactivebrokers.com/campus/traders-insight/securities/short-selling/the-risks-of-shorting-series-part-ii-borrow-fees/)

**Dữ liệu**
- [Sharadar](https://sharadar.com/) · [Tradevo Data PIT $29/mo](https://tradevodata.com/alternatives/sharadar) · [Point-in-Time Fundamentals: what & how to choose](https://dev.to/tradevodata/point-in-time-fundamentals-data-what-it-is-why-it-matters-and-how-to-choose-a70)

**Kỳ vọng thực tế**
- [Realistic Sharpe Ratios in 2026: HFT vs Retail Algos — Elite Trader](https://www.elitetrader.com/et/threads/realistic-sharpe-ratios-in-2026-hft-vs-retail-algos-deep-dive.388680/)
- [Quant Trading: What the Evidence Actually Shows](https://paperswithbacktest.com/course/quant-trading)
- [Realistic expectations in algo trading — QuantConnect](https://www.quantconnect.com/forum/discussion/5720/realistic-expectations-in-algo-trading/)

---

## Một câu tóm tắt

> Trường phái A có trần cao hơn vì nó đặt nhiều cược hơn, không phải vì nó thông minh hơn. Với cá nhân, câu hỏi không phải "cái nào tốt hơn" mà là **"tôi mua được bao nhiêu breadth với số vốn của mình"**. Dưới $10k, câu trả lời là chạy một rule đơn giản trên 10–20 instrument ít tương quan — không phải xây lại RD-Agent(Q).
