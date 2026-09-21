# Trường phái B — Báo cáo bằng chứng về độ hiệu quả

> Rule-based technical analysis ở mọi quy mô breadth: từ 1 instrument tới rổ 60+ market (ngành CTA/managed futures).
> Ngày nghiên cứu: 2026-09-20 · Liên quan: [[00-TONG-HOP-NGHIEN-CUU]] · [[03-KHA-THI-CHO-CA-NHAN]]

---

## 0. Truy nguồn con số "Sharpe 0.3–0.8" — và đính chính

Con số này mình dùng ở [[03-KHA-THI-CHO-CA-NHAN]] đến từ hai nguồn:

1. File `92` trong thư mục — nhưng ở đó nó mô tả **trend following trên rổ futures đa tài sản ở cấp danh mục**, tức đã có breadth
2. Đồng thuận cộng đồng quant 2026: *"a genuine, tradeable, diversified quant strategy with a long-run Sharpe between 0.4 and 0.8 is a good outcome"* — chữ khóa là **diversified**

⚠️ **Mình đã gán nó cho "rule-based TA đơn instrument" — đó là dùng sai ngữ cảnh.** Con số đúng cho single-instrument thấp hơn, và giờ đã có số liệu chính xác từ paper gốc (mục 2.1).

---

## 1. Trường phái B gồm những gì

Không chỉ là "vài indicator trên 1 cặp". Nó là **toàn bộ họ chiến lược có luật tường minh, deterministic, chạy trên chuỗi giá**:

| Họ chiến lược | Ví dụ | Quy mô tiêu biểu |
|---|---|---|
| **Trend following / TS momentum** | MA crossover, Donchian breakout, 12-1 momentum | 1 → 60+ market |
| **Breakout / channel** | Turtle System (20/55-day Donchian) | Rổ futures |
| **Mean reversion** | RSI oversold, Bollinger reversion | 1 → rổ |
| **Pairs trading / stat arb** | Distance method, cointegration | Rổ cổ phiếu |
| **Oscillator / filter rules** | MACD, stochastic, trading-range break | 1 → rổ |

> 📌 **Điểm quan trọng bị bỏ sót trong các báo cáo trước:** ngành **CTA / managed futures** trị giá hàng trăm tỷ USD **chính là trường phái B ở quy mô tổ chức**. Nó không phải trường phái riêng. Điều đó có nghĩa: trường phái B có bằng chứng dài nhất và được kiểm chứng bằng tiền thật nhiều nhất trong mọi trường phái.

---

## 2. Bằng chứng theo từng họ

### 2.1. 🟢 Trend following / Time-series momentum — bằng chứng mạnh nhất

**Hurst, Ooi & Pedersen — "A Century of Evidence on Trend-Following Investing"** (AQR, JPM 2017)

- **67 market, 4 asset class** (29 commodity, 11 equity index, 15 bond, 12 currency)
- **1/1880 → 12/2016** — 136 năm
- Vị thế scale về **mục tiêu vol 10%/năm**
- Lợi nhuận trung bình **dương ở MỌI market**

**Và đây là con số quan trọng nhất của toàn bộ báo cáo này:**

| Cấu hình | Sharpe |
|---|---|
| **Trung bình mỗi market đơn lẻ** | **~0.4** |
| Long-short equity factor | 0.95 |
| **Đa dạng hóa qua cả 3 nhóm tài sản** | **1.60** |

> 🔑 **Cùng một luật giao dịch. Sharpe 0.4 → 1.60 chỉ nhờ breadth.** Đây là bằng chứng định lượng cho công thức `IR ≈ IC × √breadth` ở [[03-KHA-THI-CHO-CA-NHAN]], và nó nói thẳng: **nếu bạn chạy rule TA trên 1 symbol, kỳ vọng hợp lý là Sharpe ~0.4, không phải 0.8.**

**Trần của việc thêm market — Graham Capital Management:**

> *"As markets are added to a portfolio, the Sharpe ratio increases until approximately 50 markets have been added, at which point there is a performance ceiling."*

Lý do dừng ở ~50: tương quan giữa các market. *"Even though the number of markets included in a portfolio has more than doubled, the correlation structures between the portfolios are virtually identical."*

**AQR "Trends Everywhere"** (Babu, Levine, Ooi, Pedersen, Stamelos, JOIM 2020) — kiểm chứng OOS trên **82 chứng khoán chưa từng được test** (EM equity index futures, fixed income swaps, EM currencies, exotic commodities, CDS indices, volatility futures) + 16 long-short equity factor. Kết luận: TS momentum hoạt động xuyên suốt các asset class và nhiều trend horizon.

### 2.2. 🔴 Nhưng chính nền tảng này đã bị thách thức nghiêm trọng

**Huang, Li, Wang & Zhou — "Time Series Momentum: Is It There?"** (Journal of Financial Economics, 2020)

Đây là paper **quan trọng nhất mà không báo cáo nào trong thư mục nhắc tới**. Nó tấn công trực diện Moskowitz-Ooi-Pedersen (2012):

- Hồi quy **từng tài sản một** cho thấy **rất ít bằng chứng về TSM, cả in-sample lẫn out-of-sample**
- t-statistic trong **pooled regression** trông lớn nhưng **không đáng tin** — nó nhỏ hơn giá trị tới hạn của cả bootstrap tham số lẫn phi tham số
- Quan trọng nhất: chiến lược TSM **có lãi thật, nhưng hiệu năng gần như y hệt** một chiến lược chỉ dựa trên **historical sample mean** — tức **không cần khả năng dự báo nào cả**

> ⚠️ Nghĩa là: phần lớn lợi nhuận "trend following" có thể chỉ là **long-bias có scale theo volatility**, không phải do bắt được xu hướng.

**Zakamulin** — nghiên cứu MA trading rules trên **155 năm dữ liệu**: chứng minh hiệu năng *"too good to be true"* của chiến lược moving average được báo cáo trong nhiều nghiên cứu trước **là do mô phỏng có look-ahead bias**. Khi mô phỏng đúng cách, hiệu năng thật thấp hơn hẳn.

### 2.3. 🟡 TA rules cổ điển — hỗn hợp và suy giảm theo thời gian

**Park & Irwin** (Journal of Economic Surveys, 2007) — khảo sát 95 nghiên cứu hiện đại:

| Kết quả | Số nghiên cứu |
|---|---|
| Dương | **56** |
| Âm | 20 |
| Hỗn hợp | 19 |

Nhưng kèm hai cảnh báo:

- TA sinh lời *"at least until the early 1990s"* — ở thị trường cổ phiếu Mỹ là **tới cuối thập niên 1980**
- *"Most empirical studies are subject to various problems in their testing procedures, e.g. data snooping, ex post selection of trading rules or search technologies, and difficulties in estimation of risk and transaction costs"*

**Sullivan, Timmermann & White** (Journal of Finance, 1999) — bài kinh điển về data-snooping:

- Mở rộng từ 26 rule của Brock-Lakonishok-LeBaron lên một universe lớn, áp dụng **White's Reality Check bootstrap** trên **100 năm dữ liệu DJIA**
- **In-sample:** rule tốt nhất vẫn vượt trội **kể cả sau khi hiệu chỉnh data-snooping** ✅
- **Out-of-sample:** rule tốt nhất **KHÔNG còn vượt trội trong 10 năm kế tiếp** ❌
- **S&P 500 futures:** **không có bằng chứng** rule tốt nhất vượt trội sau khi tính data-snooping ❌

> Đây là mẫu hình lặp lại xuyên suốt: **rule sống được in-sample kể cả sau hiệu chỉnh thống kê, rồi chết ở OOS.**

### 2.4. 🔴 Pairs trading / stat arb — ví dụ sách giáo khoa về alpha decay

**Gatev, Goetzmann & Rouwenhorst (1962–2002):** pairs chọn theo minimum distance cho **~11% excess return/năm, Sharpe ~1.5, beta gần 0**. Con số rất đẹp.

**Do & Faff (OOS 2003–2009):** lợi nhuận **suy giảm mạnh**, và tiếp tục xấu đi.

**Phân rã nguyên nhân suy giảm — chi tiết đáng học:**

| Nguyên nhân | Tỷ trọng |
|---|---|
| **Arbitrage risk xấu đi** | tới **70%** |
| Thị trường hiệu quả hơn | ~30% |

> 💡 Bài học: alpha không chết chủ yếu vì "thị trường thông minh lên", mà vì **rủi ro thực thi tăng lên** — pairs phân kỳ lâu hơn, khó giữ vị thế hơn. Đây là loại suy giảm mà backtest **không bao giờ nhìn thấy**.

### 2.5. 🟡 Crypto — mảng duy nhất còn tranh cãi thật sự

**Hudson & Urquhart** — test **~15.000 technical trading rule** trên 2 thị trường Bitcoin + 3 crypto khác, có kiểm soát multiple hypothesis bằng **cả FWER lẫn FDR**:

- In-sample: **predictability và profitability có ý nghĩa ở mọi lớp rule** ✅
- ⚠️ **Out-of-sample: KHÔNG có predictability cho Bitcoin**, nhưng **vẫn còn ở các crypto khác** ❌/✅

**Deprez & Frömmel** — test simple TA rules trên Bitcoin với hiệu chỉnh False Discovery Rate và hành vi nhà đầu tư thực tế → tìm thấy rule đơn giản vượt buy-and-hold OOS.

**Trend following crypto** (Rozario, Holt, West & Ng, arXiv 2009.12155, dữ liệu 2011–2019):

- **Sharpe 0.5–1.5**
- Walk-forward annualised return **255%** (giai đoạn đầu của Bitcoin — không lặp lại được)
- EMA/DEMA thường vượt SMA sau tối ưu

> 📌 **Đọc đúng:** con số 255% đến từ giai đoạn Bitcoin còn sơ khai (2011–2019), khi BTC tăng từ vài USD lên hàng nghìn USD. **Không suy ra được cho 2026.** Sharpe 0.5–1.5 là con số đáng tin hơn.

**Kết luận về crypto:** đây là thị trường **duy nhất** mà bằng chứng TA chưa ngã ngũ — Deprez-Frömmel tìm thấy edge OOS, Hudson-Urquhart không tìm thấy cho BTC. Và **cả hai đều đồng ý BTC là đồng khó nhất** (thanh khoản cao nhất → hiệu quả nhất).

---

## 3. Thực tế tiền thật: ngành CTA 2022–2026

Đây là trường phái B chạy bằng tiền thật, có kiểm toán, công bố hàng tháng.

| Năm | SG Trend Index | Ghi chú |
|---|---|---|
| **2022** | **+27.3%** | Năm tốt nhất lịch sử, **Sharpe > 1** |
| 2023 | +1.18% (YTD tháng 9) | Gần như đi ngang |
| 2024 | **+2.4%** | *"close to flat despite the good start"* |
| 2025 | **−9.3%** (YTD tháng 4) | Riêng tháng 4/2025: −4.9% |
| 2026 | Hồi phục | ETF managed futures: KMLM +7%, DBMF +8%, CTA +8% (YTD tới 8/4/2026) |

**Con số đau nhất** (Morningstar): **3 năm tính tới 31/8/2025, quỹ systematic trend điển hình LỖ trung bình −2.3%/năm.** Riêng nửa đầu 2025 lỗ **−5.8%**.

**Vì sao thất bại 2023–2025:** *"the drag is real during calm bull markets when trends are choppy and reversals are frequent."* Trend following cần xu hướng lớn kéo dài — thị trường bull êm đềm với nhiều đảo chiều nhỏ là môi trường tệ nhất.

**Vì sao hồi phục 2026:** *"Equity selloffs tend to be accompanied by large, sustained moves in rates, currencies, and commodities."*

> 🔑 **Bài học quan trọng nhất từ dữ liệu này:** trường phái B **phụ thuộc regime nặng nề**. Sharpe 0.4–1.6 là con số *dài hạn*. Trong thực tế bạn sẽ gặp chuỗi **3 năm liên tiếp âm** — và đó là các quỹ chuyên nghiệp với 60+ market, phí thấp, hạ tầng tốt.

---

## 4. Kết quả của cá nhân — dữ liệu tàn khốc

Khoảng cách giữa "bằng chứng học thuật" và "kết quả cá nhân" là rất lớn.

| Nghiên cứu | Phạm vi | Kết quả |
|---|---|---|
| **Barber & Odean (Taiwan)** | Toàn sàn, 1992–2006, 15 năm | **~1%** day trader có lãi đáng tin cậy qua các năm. Lỗ trung bình **23.9 bps/ngày** sau phí. Hiệu năng tổng thể **âm ở 14/15 năm** |
| **Brazil (index futures)** | Người giao dịch >300 phiên | **97% lỗ**. Chỉ **1.1%** kiếm hơn lương tối thiểu |
| **Barber & Odean (Mỹ, 2000)** | Quintile giao dịch nhiều nhất | Thua thị trường **~6.5 điểm %/năm** |
| **ESMA (CFD/forex EU)** | Disclosure quy định | **74–89%** tài khoản retail lỗ |
| **FPFX (prop firm)** | 300k tài khoản | **14%** pass challenge, ~45% trong đó nhận payout → **~7%** tổng |

Các con số này **hội tụ đáng kinh ngạc** qua nhiều thập kỷ và nhiều quốc gia.

### ⚠️ Nhưng đọc cho đúng

Các nghiên cứu này đo **day trader nói chung** — chủ yếu discretionary, đòn bẩy cao, tần suất cao. **Chúng không đo systematic rule-based trader ở tần suất thấp.**

Điều đó **không** có nghĩa bạn miễn nhiễm. Nó có nghĩa: nguyên nhân thất bại được ghi nhận là **tần suất cao + chi phí + đòn bẩy + kỷ luật**, và một hệ thống rule-based tần suất thấp tránh được phần lớn — nhưng không tránh được **overfitting**, là cái bẫy riêng của systematic.

---

## 5. Bảng Sharpe kỳ vọng theo cấu hình

Tổng hợp từ toàn bộ bằng chứng trên. **Đây là bảng dùng để đặt kỳ vọng.**

| Cấu hình | Sharpe kỳ vọng | Nguồn / lý do |
|---|---|---|
| 1 instrument, rule TA | **~0.4** | Hurst-Ooi-Pedersen: trung bình mỗi market |
| 5–10 instrument ít tương quan | **0.6–0.9** | Nội suy theo đường cong breadth |
| 20–30 instrument đa asset class | **1.0–1.3** | Tiệm cận |
| **50+ market, đa asset class** | **~1.6** | Hurst-Ooi-Pedersen diversified; **trần** theo Graham Capital |
| Crypto trend following | **0.5–1.5** | Rozario et al. 2011–2019 |
| Pairs trading (giai đoạn vàng 1962–2002) | ~1.5 | Gatev et al. — **đã chết sau 2002** |
| **Thực tế ngành CTA 2022–2025** | **âm 3 năm liền** | Morningstar: −2.3%/năm |

**Ba ngưỡng hoài nghi:**

| Backtest cho ra | Phản ứng |
|---|---|
| Sharpe > 1.0 trên **1 instrument** | Nghi ngờ — gấp 2.5 lần trung bình lịch sử |
| Sharpe > 2.0 bất kỳ đâu | **Gần như chắc chắn là in-sample** |
| Sharpe > 3.0 | **Nghi lỗi phương pháp** (look-ahead, survivorship, multiple testing) |

---

## 6. Sáu kết luận

**1. Trường phái B có bằng chứng dài nhất trong mọi trường phái — 136 năm, 67 market.** Không trường phái nào khác có điều này.

**2. Nhưng nền tảng của nó đang bị lung lay.** Huang-Li-Wang-Zhou (JFE 2020) cho thấy TSM ở cấp từng tài sản gần như không tồn tại, và chiến lược TSM cho kết quả tương đương một chiến lược **không cần dự báo gì cả**. Đây là thách thức nghiêm trọng mà giới hành nghề ít nhắc tới.

**3. Breadth là biến số quan trọng nhất, không phải chất lượng rule.** Cùng một rule: 0.4 → 1.60. Đừng tốn thời gian tinh chỉnh rule khi bạn chỉ chạy 1 symbol — hãy thêm instrument.

**4. Trần breadth là ~50 market.** Thêm nữa không cải thiện vì tương quan. Đây là mục tiêu rõ ràng và **khả thi cho cá nhân** — 50 crypto perp hoặc 20–30 instrument đa asset class.

**5. Alpha decay là quy luật, không phải ngoại lệ.** TA cổ phiếu chết sau thập niên 1980. Pairs trading chết sau 2002 (**70% do arbitrage risk, không phải do hiệu quả thị trường**). Crypto TA đang trong quá trình đó — BTC đã mất edge OOS trước, các altcoin còn.

**6. Regime risk là thứ giết bạn trước khi overfitting kịp.** Quỹ chuyên nghiệp với 60+ market vừa trải qua **3 năm âm liên tiếp**. Bạn phải sống sót qua giai đoạn đó — về mặt vốn và về mặt tâm lý.

---

## 7. Hàm ý cho dự án

| Quyết định | Căn cứ |
|---|---|
| ❌ **Đừng tối ưu rule trên 1 symbol** | Trần Sharpe ~0.4 dù rule hoàn hảo |
| ✅ **Mục tiêu đầu tiên: 15–30 instrument ít tương quan** | Đường cong breadth dốc nhất ở đoạn này |
| ✅ **Đa asset class quan trọng hơn đa instrument cùng loại** | 30 altcoin ≈ 1 bet (tương quan cao với BTC) |
| ✅ **Ưu tiên tần suất thấp** | Nghiên cứu retail: thất bại tập trung ở tần suất cao |
| ⚠️ **Chuẩn bị tâm lý cho 3 năm âm** | Dữ liệu CTA 2023–2025 |
| ⚠️ **Đặt Sharpe mục tiêu ở 0.6–1.0, không phải 2.0** | Mọi bằng chứng đều hội tụ về dải này |
| ✅ **Ghi nhận arbitrage risk, không chỉ hiệu quả thị trường** | Do & Faff: 70% suy giảm đến từ đây |

---

## 8. Caveats

- **Sharpe 1.60 của Hurst-Ooi-Pedersen là mô phỏng ở vol target 10%, không phải kết quả quỹ thật.** Quỹ thật (SG Trend) có phí, slippage, capacity constraint → thấp hơn.
- **AQR là bên bán sản phẩm trend following.** Các paper Hurst-Ooi-Pedersen và "Trends Everywhere" đều của AQR. Chất lượng học thuật cao nhưng có xung đột lợi ích — và Huang-Li-Wang-Zhou (bên độc lập) phản bác.
- **Park & Irwin dừng ở 2007.** 19 năm dữ liệu sau đó chưa được tổng hợp lại ở quy mô tương đương.
- **Dữ liệu retail là về day trader nói chung**, không riêng systematic — không suy trực tiếp sang bạn được.
- **Con số SG Trend 2025 là YTD tháng 4**, chưa phải cả năm. Số 2026 là YTD tháng 4/2026.
- **Số 255% của crypto trend following** đến từ 2011–2019, giai đoạn không lặp lại.

---

## 9. Nguồn

**Trend following / TS momentum**
- [Hurst, Ooi & Pedersen — A Century of Evidence on Trend-Following Investing (AQR)](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing) · [PDF](https://static.twentyoverten.com/593e8a9e7299b471eaecf644/SkLoGL67M/A-Century-of-Evidence-on-Trend-Following-Investing.pdf)
- [AQR — Trends Everywhere (JOIM 2020)](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-Trends-Everywhere_JOIM.pdf)
- [🔴 Huang, Li, Wang & Zhou — Time Series Momentum: Is It There? (JFE 2020)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X19301953) · [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3165284) · [Tóm tắt — Alpha Architect](https://alphaarchitect.com/are-trend-following-and-time-series-momentum-research-results-robust/)
- [Graham Capital — Market Diversification (2017)](https://www.grahamcapital.com/wp-content/uploads/2023/08/Market-Diversification_Graham-Research_August-2017.pdf)

**TA cổ điển & data snooping**
- [Park & Irwin — What Do We Know About the Profitability of Technical Analysis? (JES 2007)](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-6419.2007.00519.x)
- [Sullivan, Timmermann & White — Data-Snooping, Technical Trading Rule Performance, and the Bootstrap (JF 1999)](https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00163) · [PDF](https://www.kevinsheppard.com/files/teaching/mfe/advanced-econometrics/Sullivan_Timmermann_White.pdf)
- [Zakamulin — A Comprehensive Look at the Empirical Performance of Moving Average Trading Strategies](https://www.ssrn.com/abstract=2677212) · [Market Timing with Moving Averages](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2585056)

**Pairs trading**
- [Gatev, Goetzmann & Rouwenhorst — Pairs Trading (PDF)](http://stat.wharton.upenn.edu/~steele/Courses/434/434Context/PairsTrading/PairsTradingGGR.pdf)
- [Do & Faff — The profitability of pairs trading strategies](https://pure.bond.edu.au/ws/portalfiles/portal/36339487/AM_The_profitability_of_pairs_trading_strategies.pdf)

**Crypto**
- [Hudson & Urquhart — Technical Trading and Cryptocurrencies (PDF)](https://centaur.reading.ac.uk/85715/8/Hudson-Urquhart2019_Article_TechnicalTradingAndCryptocurre.pdf)
- [Deprez & Frömmel — Are Simple Technical Trading Rules Profitable in Bitcoin Markets?](https://www.sciencedirect.com/science/article/abs/pii/S1059056024003010)
- [Rozario et al. — A Decade of Evidence of Trend Following in Cryptocurrencies (arXiv 2009.12155)](https://arxiv.org/abs/2009.12155)

**Ngành CTA — kết quả tiền thật**
- [SG Prime Services Indices](https://wholesale.banking.societegenerale.com/en/prime-services-indices/) · [SG Trend Index — BarclayHedge](https://portal.barclayhedge.com/cgi-bin/indices/displayHfIndex.cgi?indexCat=SG-Prime-Services-Indices&indexName=SG-Trend-Index)
- [Morningstar — Managed-Futures Funds Look to Rebound](https://www.morningstar.com/alternative-investments/managed-futures-funds-look-rebound-can-they-help-diversify-your-portfolio)
- [Man Group — Trend Following and Drawdowns: Is This Time Different?](https://www.man.com/insights/is-this-time-different)
- [AlphaSimplex — Market Cycles and Managed Futures Drawdowns (Kaminski & Wen, 2025)](https://www.alphasimplex.com/assets/files/2025.06---market-cycles-and-managed-futures---kaminski-and-wen.pdf)

**Kết quả cá nhân**
- [Barber & Odean — Do Individual Day Traders Make Money? Evidence from Taiwan (PDF)](https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trade%20040330.pdf)
- [Day Trading Statistics: What Academic Research Shows](https://curvedtrading.com/articles/en/trading/day-trading-statistics/)
- [Is Day Trading Profitable? What the Data and Studies Show](https://www.currentmarketvaluation.com/posts/the-data-on-day-trading.php)

---

## Một câu tóm tắt

> Trường phái B có 136 năm bằng chứng trên 67 market — nhưng con số thật là **Sharpe ~0.4 cho mỗi market đơn lẻ, và 1.60 chỉ khi đa dạng hóa**. Biến số quyết định là **breadth, không phải chất lượng rule**. Và ngay cả nền tảng đó cũng đang bị thách thức: Huang-Li-Wang-Zhou cho thấy chiến lược TSM cho kết quả tương đương một chiến lược **không cần dự báo gì cả**.
