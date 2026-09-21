> ### ⚠️ ĐÍNH CHÍNH (20/9/2026) — nội dung gốc giữ nguyên bên dưới
>
> **1. PDT rule đã bị XÓA BỎ.** Báo cáo viết *"PDT rule (Mỹ: cần $25.000 để day-trade > 3 lần/5 ngày)"*. Từ **4/6/2026**, theo thay đổi FINRA Rule 4210, yêu cầu vốn tối thiểu $25.000 và chính danh hiệu "Pattern Day Trader" **không còn tồn tại** — thay bằng yêu cầu vốn tỷ lệ với exposure thực tế trong phiên; mức tối thiểu chung còn **$2.000**.
>
> **2. Dữ liệu PIT survivorship-free KHÔNG còn đắt.** Báo cáo viết *"Dữ liệu chất lượng (survivorship-bias-free + point-in-time fundamentals — CRSP, Compustat) đắt"*. Hiện có tier retail: **Tradevo $29/tháng**, **Valuein từ $49/tháng**, cùng Sharadar và Norgate. WRDS/Compustat ($50k+) không còn bắt buộc.
>
> **3. Bổ sung về crypto cross-sectional.** Bằng chứng 2020–10/2025: cross-sectional momentum **thua** time-series momentum về risk-adjusted return (TS đạt 31,96%/năm), với **max drawdown 55%** — do các coin tương quan quá cao. Xem [03-KHA-THI-CHO-CA-NHAN.md](03-KHA-THI-CHO-CA-NHAN.md).
>
> **4. Về con số "Sharpe thực tế sau phí 0.3–0.8".** Con số này đúng ở **cấp danh mục đa tài sản**. Ở cấp **từng market đơn lẻ**, Hurst-Ooi-Pedersen (67 market, 1880–2016) cho **Sharpe trung bình ~0.4**, và **1.60** khi đa dạng hóa qua 3 nhóm tài sản. Xem [04-TRUONG-PHAI-B-HIEU-QUA.md](04-TRUONG-PHAI-B-HIEU-QUA.md).

---

# Nghiên cứu định lượng và xây dựng chiến lược hệ thống: CRYPTO, FOREX, STOCKS (bao gồm thị trường Việt Nam)

## TL;DR
- Với một AI Engineer làm việc solo/nhóm nhỏ xây dựng hệ thống rule-based (chủ yếu technical analysis), backtest và tối ưu hóa, **crypto là thị trường khởi đầu tốt nhất** (dữ liệu 24/7 miễn phí/rẻ qua ccxt, không rào cản vốn pháp lý, bằng chứng học thuật rõ nhất cho time-series momentum và cash-and-carry/funding-rate carry), tiếp theo là **futures cho trend following**; **cổ phiếu Việt Nam (HOSE/VN30 futures)** là lựa chọn "sân nhà" hấp dẫn vì có bằng chứng momentum và vnstock/API sẵn, nhưng bị giới hạn bởi price limit ±7%, T+2, chưa có short-selling và thanh khoản tập trung.
- Bằng chứng học thuật mạnh nhất và bền vững nhất là **trend following / time-series momentum trên rổ futures đa tài sản** (Moskowitz-Ooi-Pedersen 2012; Hurst-Ooi-Pedersen 2017), với Sharpe thực tế sau phí chỉ khoảng **0.3–0.8** ở cấp danh mục — thấp hơn nhiều so với marketing. Pure TA rules có bằng chứng hỗn hợp: mạnh trong FX/futures giai đoạn trước ~1990, suy giảm sau đó do alpha decay (McLean-Pontiff: giảm ~50% sau công bố) và data-snooping.
- Cạm bẫy lớn nhất không phải tìm ra chiến lược mà là **overfitting**: dùng Deflated Sharpe Ratio (Bailey & López de Prado 2014), Probability of Backtest Overfitting/CSCV (Bailey-Borwein-López de Prado-Zhu 2016) và ngưỡng t-stat > 3.0 (Harvey-Liu-Zhu 2016) là bắt buộc. Thống kê retail rất khắc nghiệt: 74–89% tài khoản CFD/forex retail thua lỗ (ESMA); chỉ ~7% người mua thử thách prop firm từng nhận được payout.

## Key Findings

### 1. Xếp hạng bằng chứng hiệu quả (từ mạnh đến yếu, sau chi phí)
1. **Time-series momentum / trend following trên futures đa tài sản** — bằng chứng peer-reviewed mạnh nhất. Hurst-Ooi-Pedersen (Journal of Portfolio Management 2017) cho thấy time-series momentum có return dương mỗi thập kỷ kể từ 1880 và hoạt động tốt trong 8/10 giai đoạn khủng hoảng lớn nhất thế kỷ ("crisis alpha").
2. **Cross-sectional momentum (Jegadeesh-Titman 1993)** — mạnh nhưng có "momentum crashes" (Daniel-Moskowitz) và alpha decay sau công bố.
3. **Crypto time-series momentum + carry (funding rate/basis)** — bằng chứng học thuật mới nổi tốt (Liu-Tsyvinski 2021, Liu-Tsyvinski-Wu 2022), nhưng basis/funding đã bị nén mạnh 2024–2026.
4. **FX carry** — Sharpe ~0.4–0.9 nhưng skew âm mạnh ("lên cầu thang, xuống thang máy" — crash risk).
5. **Pure TA rules (MA crossover, breakout)** — hỗn hợp; edge tồn tại trong crypto và FX ở một số giai đoạn nhưng dễ bị data-snooping.
6. **Statistical arbitrage / pairs trading, market making** — hiệu quả cao cho tổ chức nhưng đòi hỏi hạ tầng; khó cho cá nhân.

### 2. Xếp hạng độ phổ biến theo loại người tham gia
- **Retail forex**: MT4/MT5 + Expert Advisors (EA), chủ yếu trend/breakout/scalping. Cộng đồng khổng lồ nhưng 74–89% thua lỗ.
- **Retail crypto**: Freqtrade, Hummingbot, ccxt, 3Commas — grid, DCA, momentum, arbitrage.
- **Retail stocks (US/global)**: QuantConnect (LEAN), Alpaca, Interactive Brokers, Backtrader/vectorbt/Zipline.
- **Retail stocks Việt Nam**: vnstock (Python), API môi giới (SSI, TCBS...), cộng đồng nhỏ nhưng đang lớn nhanh; retail chiếm phần lớn giá trị giao dịch.
- **Prop firms**: chủ yếu forex/futures/CFD, mô hình bán thử thách.
- **Tổ chức/hedge funds**: CTA trend following (Man AHL, Winton, Aspect, AQR, Transtrend...), stat arb, market making (XTX, Jane Street, Citadel Securities); crypto quant funds.

### 3. Thị phần giao dịch thuật toán (algo)
Theo tổng hợp dữ liệu ngành (SelectUSA/các báo cáo tổng hợp), hệ thống tự động hóa thực hiện **khoảng 70% giao dịch cổ phiếu Mỹ, 60–75% khối lượng cổ phiếu toàn cầu, khoảng 58% giao dịch forex, và hơn một nửa giao dịch futures**; riêng các công ty HFT chiếm chỉ ~2% số công ty nhưng ~73% khối lượng giao dịch cổ phiếu. Crypto có tỷ lệ bot cao nhưng khó đo lường tin cậy. (Lưu ý: đây là ước tính ngành, không phải số liệu kiểm toán.)

---

## Details

### CRYPTO

**Chiến lược chính và cơ chế:**
- *Time-series momentum*: Liu & Tsyvinski (Review of Financial Studies 2021) chứng minh momentum time-series mạnh ở tần suất ngày và tuần cho BTC/ETH/XRP — ví dụ, tăng 1 độ lệch chuẩn của return BTC hôm nay dự báo tăng 0.33% return ngày kế tiếp.
- *Cross-sectional factors*: Liu, Tsyvinski & Wu (Journal of Finance 2022) xác lập mô hình 3 nhân tố crypto (market, size, momentum) giải thích phần lớn cross-section; nhân tố này vẫn còn hiệu lực trong mẫu post-2020.
- *Cash-and-carry / funding-rate carry*: long spot + short perp để thu funding, delta-neutral. Với funding 0.005–0.02%/kỳ trong thị trường ổn định cho ~8–18%/năm sau phí; lên 55–110%/năm khi bull (nhưng ngắn hạn, thường tự điều chỉnh trong 1–4 tuần).
- *Cross-exchange arbitrage*: Makarov & Schoar (Journal of Financial Economics 2020) ghi nhận cơ hội arbitrage lớn, tái diễn giữa các sàn, đặc biệt xuyên biên giới (capital controls); thành phần order flow chung giải thích 80% return BTC.
- *Market making, statistical arbitrage, mean reversion*.

**Hiệu quả:** Theo Crypto Fund Research (đánh giá 2025), Sharpe trung bình các crypto hedge fund ~1.6 (return trung bình ~36%, volatility ~46%; nhóm quant dẫn đầu ~48%) — **dữ liệu ngành, không kiểm toán độc lập**. Funding rate/basis carry cho lợi suất ổn định nhưng basis đã bị nén mạnh khi thị trường futures/ETF trưởng thành 2024–2026 (model decay điển hình — nhiều quỹ dựa vào basis trade thấy return giảm khi spread thu hẹp). Bằng chứng TA: các nghiên cứu peer-reviewed (Detzel-Liu-Strauss-Zhou-Zhu, Financial Management 2021; Gerritsen et al., Finance Research Letters 2020) cho thấy tỷ số price/moving-average dự báo return BTC in- và out-of-sample, và trading-range breakout vượt buy-and-hold về Sharpe (bootstrap). Nghiên cứu OOS gần đây nhất (Deprez & Frömmel 2024, test 75,360 rules với hiệu chỉnh False Discovery Rate) tìm thấy rule đơn giản có thể vượt buy-and-hold OOS về risk-return; nhưng Hudson-Urquhart cho kết quả trái chiều, và **BTC có thể là đồng ít sinh lời nhất khi test OOS** vì thanh khoản/hiệu quả hóa cao. Đây là điểm căng thẳng thực sự trong tài liệu, không phải kết quả đã ngã ngũ.

**Microstructure:** 24/7, không giờ nghỉ; thanh khoản phân mảnh giữa sàn; funding rate thường mỗi 8h (một số sàn 4h/1h); đòn bẩy cao (perp); rủi ro đối tác/sàn (bài học FTX); dữ liệu phân mảnh, chất lượng khác nhau giữa sàn (funding rate lịch sử thường thiếu/không nhất quán).

**Tooling:** ccxt/ccxt.pro (đa sàn), Freqtrade, Hummingbot (market making), vectorbt (nghiên cứu tốc độ cao — quét hàng nghìn tham số trong mili-giây), NautilusTrader (event-driven, Rust-backed, đa tài sản, live parity). Dữ liệu: CoinAPI, Kaiko, dữ liệu sàn miễn phí. Backtest funding arb cần 4 luồng dữ liệu: OHLCV, funding rate 8h, open interest, và liquidation data.

**Rào cản cá nhân:** Thấp nhất trong 3 thị trường — không yêu cầu vốn tối thiểu pháp lý, API mở, dữ liệu rẻ. Failure mode: overfitting trên dữ liệu ngắn, bỏ qua funding/slippage/phí (Sharpe tính trên funding rate thô mà không trừ leverage decay/phí/slippage/basis risk là "fantasy"), rủi ro sàn.

### FOREX

**Chiến lược chính:** Trend following/breakout (phổ biến retail qua MT4/MT5 EA), carry trade (long lãi suất cao, short lãi suất thấp), mean reversion, news/event-driven, scalping/HFT (tổ chức).

**Hiệu quả:**
- *Carry*: Sharpe ~0.4–0.9 cho G10 (Jurek: Sharpe 0.40–0.90 giai đoạn 1990–2012; Quantpedia/nghiên cứu hedge: ~0.71 chưa hedge, tăng lên ~1.29 nếu hedge unpriced risk), nhưng skew âm mạnh (Brunnermeier-Nagel-Pedersen: carry crash risk, ~1/3 excess return là bù đắp crash risk). Nghiên cứu out-of-sample gần đây (Journal of International Money and Finance 2024) cảnh báo tính sinh lời bất ổn, tập trung ở giai đoạn 1998–2005 và phụ thuộc may mắn.
- *TA rules*: Park & Irwin (Journal of Economic Surveys 2007) — trong 95 nghiên cứu modern, 56 dương, 20 âm, 19 hỗn hợp; TA sinh lời trong FX/futures ít nhất đến đầu 1990s, sau đó suy giảm; nhiều nghiên cứu có vấn đề data-snooping, ex-post rule selection, ước lượng chi phí/rủi ro.
- *Trend following (CTA)*: SG Trend Index đạt năm tốt nhất lịch sử 2022 (+27.3%; SG CTA Index +20.1%), rồi SG CTA Index gần như đi ngang 2024 (+2.4%) và tiếp tục khó khăn đầu 2025 (SG Trend -4.54% YTD tính đến tháng 3/2025). (Con số SG Trend lỗ ~3.3% năm 2023 được một số nguồn tin tức trích dẫn nhưng không xác nhận được chính xác — một số nguồn cho thấy 2023 hơi dương.)

**Retail outcomes:** ESMA (2018): "phân tích của các NCA về giao dịch CFD trên các khu vực pháp lý EU cho thấy **74–89% tài khoản retail thường thua lỗ**, với lỗ trung bình mỗi khách hàng €1,600–€29,000." CFTC Mỹ: ~75–80% tài khoản thua lỗ. Prop firm (dữ liệu FPFX Tech, 300,000+ tài khoản/100,000 trader/10 firm, Finance Magnates 9/2024): **14% pass thử thách, trong đó ~45% nhận payout → chỉ ~7% tổng số trader nhận payout**, payout trung bình 4% quy mô tài khoản; FTMO công bố pass rate ~10% cho thử thách 2 bước.

**Microstructure:** 24/5; thanh khoản cực cao (major pairs); spread thấp nhưng dealing desk/requotes ở broker kém; đòn bẩy cao (ESMA giới hạn 30:1 major cho retail, 2:1 crypto CFD); rủi ro broker B-book (xung đột lợi ích với khách).

**Tooling:** MT5 Strategy Tester (**tốt nhất cho FX/CFD retail** — tick data, tối ưu hóa tham số, EA marketplace), MT4 (legacy). Python: backtrader, vectorbt qua dữ liệu broker/Dukascopy.

**Rào cản:** Vốn thấp, nhưng đòn bẩy + broker B-book + spread biến động khiến backtest kém thực tế. Failure mode: bỏ qua spread/swap/slippage, overfit trên tick data kém chất lượng, requotes.

### STOCKS (US/global)

**Chiến lược:** Factor investing (value, momentum, quality, low-vol), cross-sectional momentum (Jegadeesh-Titman), mean reversion, stat arb/pairs, event-driven, intraday/HFT.

**Hiệu quả & khủng hoảng nhân bản (replication crisis):**
- Hou-Xue-Zhang (Review of Financial Studies 2020) tái chạy 452 anomalies, khoảng một nửa không sống sót dưới phương pháp nhất quán; 102/106 (96%) nhóm "trading frictions" thất bại trong single tests.
- McLean-Pontiff (Journal of Finance 2016): return anomaly giảm ~26% trong mẫu, và ~58% so với giai đoạn out-of-sample-trước-công-bố (thường trích dẫn ~50% giảm sau công bố) — "anomaly decay".
- Harvey-Liu-Zhu (Review of Financial Studies 2016): rà soát 316 nhân tố; cần **t-stat > 3.0** (không phải 2.0) cho nhân tố mới; "hầu hết phát hiện nghiên cứu tài chính có thể là sai."

**Microstructure:** Giờ giao dịch sàn (không 24h); thanh khoản cao; phí thấp/zero-commission (US); short-selling khả dụng; **PDT rule** (Mỹ: cần $25,000 để day-trade > 3 lần/5 ngày với tài khoản margin); đòn bẩy Reg-T ~2:1.

**Tooling:** QuantConnect (LEAN), Alpaca, Interactive Brokers, Zipline+pyfolio (factor research), vectorbt. Dữ liệu: cần survivorship-bias-free + point-in-time fundamentals (CRSP, Compustat — đắt).

**Rào cản:** Dữ liệu chất lượng (survivorship bias, PIT fundamentals) đắt; PDT rule; cạnh tranh khốc liệt từ tổ chức/HFT.

### STOCKS — VIỆT NAM (HOSE/HNX/UPCoM, VN30 futures)

**Cấu trúc & vi cấu trúc:**
- **T+2 settlement** cho cổ phiếu (T+1 cho trái phiếu).
- **Price limit (biên độ ngày)**: HOSE ±7%, HNX ±10%, UPCoM ±15%; ngày đầu niêm yết/sau tạm ngừng biên độ rộng hơn (±20%/±30%/±40%). Khi chạm trần/sàn tạo tình trạng "lock" — lệnh chất đống không có đối ứng, **ảnh hưởng lớn đến backtest realism** (không có ở thị trường Mỹ).
- **Short-selling**: chưa được phép; lộ trình cho phép short-selling có kiểm soát, securities borrowing/lending (SBL), và T+0 dự kiến triển khai **2026–2028** (Quyết định 2014/2025 của Bộ Tài chính).
- **Foreign ownership limit (FOL)**: thường 30% (ngân hàng) đến 49%/50% tùy ngành; Nghị định 245/2025 (11/9/2025) bỏ quy định cho phép công ty tự đặt FOL thấp hơn mức luật, buộc công khai tỷ lệ trong 12 tháng.
- **Nâng hạng FTSE**: FTSE Russell công bố nâng Việt Nam lên **Secondary Emerging Market vào 7/10/2025, hiệu lực 21/9/2026** (đánh giá tạm thời 3/2026). World Bank dự báo dòng vốn ngắn hạn ~US$5 tỷ và tới US$25 tỷ vào 2030; HSBC dự báo US$3.4 tỷ (quỹ chủ động) đến US$10.4 tỷ (gồm passive); Việt Nam ~0.5% FTSE Emerging Market Index (27 cổ phiếu được thêm).
- **Thanh khoản**: HOSE ~US$700 triệu–1 tỷ/phiên, tập trung ở số ít large-cap. Hệ thống KRX (ra mắt 5/2025) tăng công suất, hỗ trợ order type mới, cập nhật FOL real-time, và nền tảng cho covered short-selling + CCP (dự kiến Q1/2027).
- **VN30 index futures** (HNX, niêm yết 2017): hệ số nhân 100,000 VND/điểm, biên độ ±7%, **cho phép giao dịch T+0** (đặc điểm hấp dẫn nhất cho systematic vì cho phép intraday và short qua vị thế futures); >75,000 tài khoản phái sinh; khối lượng trung bình gần đây ~225,000 hợp đồng/phiên. VN100 futures thêm 10/2025.

**Hiệu quả momentum/TA:**
- Vo & Truong (Journal of Behavioral and Experimental Finance 2018): momentum tồn tại — chiến lược formation 6 tháng, hold 9 tháng sinh lời đáng kể; bác bỏ hiệu quả thị trường (đặc trưng thị trường mới nổi). Nghiên cứu khác (Macrothink): sau trừ chi phí, chỉ vài chiến lược ngắn hạn (K=1,2; J=1) sinh lời dương đáng kể — return 1 tuần chứa nhiều thông tin hơn dài hạn.
- Nghiên cứu stat arb VN30 (Journal of Behavioral and Experimental Finance): VN30 và một số cổ phiếu thành phần cho cơ hội stat arb vừa phải sau phí; short-term momentum hoạt động cho cổ phiếu cụ thể (risk-return ratio 1.20–1.62 theo lookback); long-short **không** mạnh hơn long-only (vì hạn chế short).
- Một nghiên cứu 2025 (Business Perspectives) kết hợp entropy + overreaction đạt Sharpe backtest 3.96 (2023–2025) so với 0.64 của VNINDEX buy-and-hold — **con số này là in-sample/backtest, cần xem thận trọng** (chưa chắc trừ đủ chi phí thực tế; nghi ngờ overfit).

**Tooling & dữ liệu VN:** vnstock (Python, dữ liệu từ TCBS/SSI, free tier), vnstock_data (bản trả phí), API môi giới (SSI FastConnect, TCBS...), iTick/DataCore/Vietstock DataFeed. Cộng đồng quant/algo VN nhỏ nhưng lớn nhanh (Algotrade Lab, các dự án GitHub như Aether-Quant dùng multi-factor scoring momentum/volume).

**Rào cản đặc thù VN:** Thiếu short-selling → khó long-short/market-neutral; price limit gây "lock" méo mó backtest; T+2 với cổ phiếu (nhưng T+0 với VN30 futures); dữ liệu lịch sử ngắn (thị trường mới thực sự đại diện từ 2008–2009); thanh khoản mỏng ở midcap/smallcap; phí + thuế (thuế TNCN 0.1% trên giá trị bán).

---

## Ma trận so sánh

| Tiêu chí | Crypto | Forex | Stocks (US/global) | Stocks Việt Nam (HOSE/VN30F) |
|---|---|---|---|---|
| Chiến lược phổ biến | TS momentum, funding/basis carry, arbitrage, market making, grid | Trend/breakout, carry, scalping | Factor (value/mom/quality/low-vol), stat arb, mean reversion | Momentum, TA, stat arb VN30; futures cho intraday |
| Phổ biến retail | Cao (Freqtrade/ccxt/Hummingbot) | Rất cao (MT4/MT5 EA) | Cao (QC/Alpaca/IBKR) | Đang lớn (vnstock) |
| Phổ biến tổ chức | Đang lớn (~28% quant funds) | Cao (bank/HFT) | Rất cao (factor/HFT) | Thấp (chủ yếu quỹ ngoại) |
| Bằng chứng hiệu quả | Tốt (momentum, carry) | Trung bình (carry Sharpe 0.4–0.9) | Mạnh nhưng alpha decay | Trung bình (momentum tồn tại) |
| Sharpe thực tế sau phí | ~0.5–1.5 (quant fund tốt >2 cho market-neutral) | ~0.3–0.8 | ~0.3–0.7 (factor) | Chưa chuẩn hóa; nghiên cứu risk-return 1.2–1.6 gross |
| Chi phí giao dịch | Trung bình (phí + funding + slippage) | Thấp spread nhưng swap/slippage | Rất thấp (zero-commission US) | Trung bình + thuế 0.1% bán |
| Chất lượng dữ liệu | Phân mảnh, rẻ/free | Tick data kém từ broker | Đắt (survivorship-free, PIT) | Hạn chế, lịch sử ngắn |
| Đòn bẩy | Rất cao (perp) | Cao (30:1 retail) | Thấp (PDT, Reg-T 2:1) | Thấp cổ phiếu; futures có margin |
| Tooling tốt nhất | ccxt/Freqtrade/vectorbt/NautilusTrader | MT5 Strategy Tester | QuantConnect/Zipline/vectorbt | vnstock + API môi giới |
| Rào cản cá nhân | Thấp nhất | Thấp (nhưng broker risk) | Trung bình (PDT, data cost) | Trung bình (no short, price limit) |
| Phù hợp rule-based TA cá nhân | Cao | Cao | Trung bình | Trung bình-cao (qua futures) |

---

## Xếp hạng chiến lược theo thị trường

**CRYPTO** — (a) phổ biến: TS momentum > grid/DCA > funding carry > cross-exchange arb > market making; (b) hiệu quả (bằng chứng): TS momentum ≈ funding/basis carry (khi chưa nén) > cross-exchange arb (đang biến mất do arbitrage) > pure TA.

**FOREX** — (a) phổ biến: trend/breakout EA > scalping > carry; (b) hiệu quả: carry (Sharpe 0.4–0.9, có crash risk) > trend following (CTA, regime-dependent) > pure TA (suy giảm sau 1990).

**STOCKS US** — (a) phổ biến: factor investing/smart beta > momentum > mean reversion > stat arb; (b) hiệu quả: momentum + quality/profitability (sống sót replication) > value > các nhân tố lạ (đa số không nhân bản được, Hou-Xue-Zhang).

**STOCKS VN** — (a) phổ biến: TA/momentum discretionary > multi-factor scoring; (b) hiệu quả: short-term momentum (long-only) > medium-term momentum > stat arb VN30 (vừa phải).

---

## Recommendations

**Giai đoạn 1 (0–3 tháng) — Bắt đầu với crypto + Python:**
- Chọn crypto spot/perp trên 1–2 sàn lớn qua ccxt. Pipeline: ccxt (dữ liệu) → vectorbt (nghiên cứu nhanh, quét tham số) → NautilusTrader hoặc backtest event-driven (kiểm tra realism với slippage/funding/phí).
- Bắt đầu với **time-series momentum** (bằng chứng học thuật mạnh nhất, cơ chế đơn giản, phù hợp rule-based) và **funding-rate carry** delta-neutral.
- Xây dựng **anti-overfitting layer ngay từ đầu** — đây là lợi thế cạnh tranh của bạn với tư cách AI engineer:
  - **Walk-forward** analysis (out-of-sample rolling).
  - **Deflated Sharpe Ratio** (Bailey & López de Prado, Journal of Portfolio Management 2014) — hiệu chỉnh Sharpe cho số lần thử (selection bias / "winner's curse"), non-normality, độ dài mẫu.
  - **Probability of Backtest Overfitting** qua CSCV (Bailey-Borwein-López de Prado-Zhu, Journal of Computational Finance 2016) — đo xác suất chiến lược tối ưu in-sample lại xếp dưới median out-of-sample.
  - Áp dụng nguyên tắc López de Prado (*Advances in Financial Machine Learning*, 2018): "Backtesting is not a research tool. Feature importance is." Dùng backtest để **bác bỏ**, không để "tuning".

**Giai đoạn 2 (3–6 tháng) — Mở rộng sang futures trend following hoặc VN30 futures:**
- Nếu muốn bằng chứng bền vững nhất: trend following trên rổ futures đa tài sản (cần broker futures + dữ liệu chất lượng).
- Nếu muốn "sân nhà": **VN30 index futures** (T+0, cho phép short qua futures, vnstock + API môi giới) — test momentum/breakout intraday. Tránh cổ phiếu đơn lẻ VN lúc đầu vì price limit "lock" và không short được.

**Giai đoạn 3 — Chỉ khi có edge đã validate:** cân nhắc forex qua MT5 (nếu muốn tận dụng EA marketplace) nhưng cực kỳ thận trọng với broker B-book và thống kê 74–89% thua lỗ.

**Ngưỡng quyết định (thay đổi khuyến nghị):**
- Nếu **Deflated Sharpe < 0.95** hoặc **PBO > 0.5** → loại chiến lược, không tinh chỉnh (tránh overfit).
- Nếu **t-stat của nhân tố < 3.0** → coi như noise (Harvey-Liu-Zhu).
- Nếu **Sharpe out-of-sample < 50% Sharpe in-sample** → nghi ngờ overfit/alpha decay (McLean-Pontiff benchmark).
- Nếu chiến lược phụ thuộc một trade duy nhất (vd basis carry) → giả định shelf life ngắn, chuẩn bị model decay.

**Cạm bẫy theo thị trường:**
- *Crypto*: rủi ro sàn (custody), funding flip, dữ liệu funding lịch sử thiếu, basis đã nén.
- *Forex*: broker B-book/requotes, spread biến động, swap; tick data kém.
- *Stocks US*: survivorship bias, PDT rule, chi phí dữ liệu PIT.
- *Stocks VN*: price limit "lock", thiếu short-selling, lịch sử ngắn, thanh khoản mỏng, thay đổi pháp lý nhanh.

## Caveats
- **Nguồn peer-reviewed vs marketing vs anecdotal**: Các con số Sharpe học thuật (Moskowitz-Ooi-Pedersen 2012, Hurst-Ooi-Pedersen 2017, Liu-Tsyvinski 2021, Liu-Tsyvinski-Wu 2022, Park-Irwin 2007, Jurek/Brunnermeier carry, Detzel et al. 2021, Deprez-Frömmel 2024) là **peer-reviewed/working paper**. Các con số hiệu suất quỹ (SG Trend Index, crypto quant fund Sharpe ~1.6, funding carry APR) là **dữ liệu ngành, không kiểm toán độc lập**, dễ có survivorship/selection bias. Thống kê pass rate prop firm (FPFX/Finance Magnates) là **directional từ một vendor**; loss rate retail (ESMA) là **disclosure quy định, đáng tin**. Con số VN30 futures Sharpe 3.96 là **backtest in-sample** của một nghiên cứu, cần xem như chưa trừ đầy đủ chi phí thực tế.
- **Alpha decay & regime dependence**: Mọi bằng chứng lịch sử đều chịu alpha decay sau công bố (McLean-Pontiff: ~50%) và phụ thuộc regime (trend following khó khăn 2023–2025; basis carry nén 2024–2026).
- **Pure TA edge**: Bằng chứng TA có edge là **hỗn hợp** và phụ thuộc mạnh vào giai đoạn, chi phí, và điều chỉnh data-snooping. Trong crypto, Deprez-Frömmel (2024) tìm thấy edge sống sót OOS còn Hudson-Urquhart thì không — chưa ngã ngũ. Đừng cho rằng một rule TA sẽ hoạt động chỉ vì nó backtest đẹp.
- **Việt Nam**: Khung pháp lý đang thay đổi nhanh (short-selling, T+0, CCP Q1/2027, FTSE upgrade hiệu lực 9/2026); các đặc điểm có thể thay đổi trong 2026–2028. Dữ liệu lịch sử ngắn làm giảm độ tin cậy của backtest dài hạn.
- **Một số con số cần kiểm chứng thêm**: SG Trend Index năm 2023 (nguồn tin trái chiều giữa lỗ ~3.3% và hơi dương); các con số thị phần algo là ước tính tổng hợp ngành, không phải đo lường chính thức.