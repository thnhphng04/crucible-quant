# Tổng hợp 4 báo cáo nghiên cứu — Hệ thống AI phát triển chiến lược trading

> Tài liệu này tổng hợp và đối chiếu 4 báo cáo research trong thư mục. Mục tiêu: rút ra điểm hội tụ, điểm mâu thuẫn, và quyết định kỹ thuật cần chốt.
> Ngày tổng hợp: 2026-09-20

---

## 0. Bốn báo cáo nói về cái gì

| File               | Chủ đề                                            | Câu hỏi cốt lõi nó trả lời                                                                       |
| ------------------ | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| [90](90-NGUON-KIEN-TRUC-VA-REPO.md) | **Kiến trúc & repo**                         | Có project nào làm sẵn kiến trúc 5 thành phần chưa? Nếu không thì lắp từ đâu?           |
| [91](91-NGUON-AI-AGENT-BACKTESTING.md) | **AI Agents cho backtesting (9/2025–9/2026)** | Các LLM agent trading có thật sự tạo alpha không? Bằng chứng nói gì?                          |
| [92](92-NGUON-THI-TRUONG-CHIEN-LUOC.md) | **Thị trường & chiến lược**              | Crypto / Forex / Stocks (+ Việt Nam) — nên bắt đầu ở đâu, chiến lược nào có bằng chứng? |
| [93](93-NGUON-AI-SINH-STRATEGY-MT5.md) | **AI xây dựng strategy rule-based (MT5)**    | Dùng LLM viết EA/code strategy rồi tự backtest — làm được tới đâu?                          |

Hai file đầu là góc nhìn **hệ thống/agent**, file 3 là góc nhìn **thị trường/alpha**, file 4 là góc nhìn **công cụ/triển khai**. Chúng bổ sung nhau chứ không trùng lặp.

---

## 1. Sáu kết luận cả 4 báo cáo đều đồng thuận

### 1.1. Overfitting là kẻ thù số một — không phải việc tìm ra chiến lược

Đây là sợi chỉ xuyên suốt. Không có báo cáo nào coi "tìm strategy" là phần khó. Câu tổng kết từ cộng đồng MQL5 (file 4): *"strategy là phần khó, code là phần dễ"* — LLM viết trung thực mọi logic, **kể cả logic tồi**.

### 1.2. Validation layer là chỗ thiếu nhất của toàn hệ sinh thái

File 1 nói thẳng: lớp chống overfitting **hầu như chỉ tồn tại trong paper, rất ít khi được implement trong code**. `tarsyang/quantevolve` không có. RD-Agent(Q) không có DSR/PBO. Đây vừa là lỗ hổng của ngành, vừa là **lợi thế cạnh tranh** nếu tự xây.

### 1.3. LLM không được chạm vào runtime

Pattern *"LLM never touches the trade"* (file 2, từ ai-hedge-fund) và yêu cầu *"strategy cuối cùng phải deterministic, KHÔNG gọi LLM lúc runtime"* (file 4). LLM chỉ ở tầng sinh ý tưởng và sinh code.

Có lý do định lượng cụ thể: BacktestBench cho thấy **Metrics Calculation là "vùng thảm họa"** của LLM — Qwen3 rớt từ 34.71% (bản 235B) xuống gần 0% (bản 4B); Sharpe/Volatility có pass rate thấp hơn hẳn Win Rate/MaxDD. **Tính KPI phải để code deterministic làm.**

### 1.4. Bằng chứng hiện tại còn yếu và chủ yếu tự báo cáo

- QuantEvolve "Sharpe > 1.5", MadEvolve "+0.62 đến +1.83 OOS", RD-Agent(Q) "~2× ARR" — tất cả **self-reported**, chưa qua audit độc lập.
- Alpha Arena Season 1 (tiền thật): **4/6 model lỗ**, GPT-5 mất 62.66%.
- StockBench: đa số LLM vượt buy-and-hold **ở biên rất mỏng**, và chỉ thắng trong upturn, thua trong downturn.
- FINSABER: FinMem/FinAgent **không tạo alpha CAPM có ý nghĩa thống kê** khi kiểm soát survivorship bias.

### 1.5. Search càng nhiều, xác suất tìm thấy "winner" giả càng cao

File 4: *"mỗi trial thêm vào làm tăng xác suất chọn phải chiến lược may mắn"*. File 2 diễn đạt sắc hơn: *"productivity của agent trở thành bias amplifier"* — agent tự chạy hàng chục backtest ngầm trước khi bạn kịp thấy một con số.

→ Hệ quả bắt buộc: **log MỌI trial** vào một search ledger, và deflate theo số trial **thực tế** chứ không phải số trial bạn báo cáo.

### 1.6. Backtest dùng để BÁC BỎ, không dùng để tuning

Nguyên tắc López de Prado (file 3): *"Backtesting is not a research tool. Feature importance is."*

---

## 2. Điểm quan trọng nhất: DSR và PBO KHÔNG đủ

**Đây là phát hiện có giá trị nhất khi đối chiếu chéo 4 file**, và nó điều chỉnh lại khuyến nghị mặc định của chính file 3 và file 4.

File 1 và file 2 cùng trích arXiv 2608.27734 (Gençay, 8/2026):

> Một **"leaky oracle"** cố tình rò rỉ dữ liệu tương lai, đạt Sharpe 34.7 (design) / 51.5 (eval), **vẫn sống sót qua Deflated Sharpe Ratio với DSR = 1.00**.

Nghĩa là: hiệu chỉnh thống kê bắt được **selection bias** (chọn quá nhiều lần), nhưng **không bắt được leakage** (dùng dữ liệu không được phép dùng). Đây là hai loại lỗi khác nhau.

**Kết luận thực hành — cần 3 lớp, không phải 1:**

| Lớp                             | Bắt được gì                                      | Công cụ                                                                                                                                       |
| -------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Structural guardrail**   | Look-ahead leakage                                    | Tool registry loại bỏ hàm nhìn tương lai; PIT data layer lọc theo*filing date* (không phải report period); fail-loud khi thiếu data |
| **Statistical correction** | Selection bias / winner's curse                       | DSR, PBO/CSCV, White's Reality Check, deflate theo search ledger                                                                                |
| **Temporal isolation**     | Parametric leakage (LLM "nhớ" giá trong trọng số) | Test window**sau knowledge cutoff**; đo IS→OOS decay; forward/paper trading                                                             |

Lớp 3 là vấn đề **hoàn toàn mới**, chỉ xuất hiện với LLM — xem mục 3.

---

## 3. Parametric look-ahead bias — rủi ro đặc thù LLM

File 2 dành nhiều dung lượng cho vấn đề này và nó đáng được tách riêng.

**Bản chất:** LLM có cutoff 2025 đã "thấy" biến động giá 2010–2024 trong lúc pretraining. Bias nằm **trong trọng số mô hình**, không nằm trong pipeline code → **audit code không phát hiện được**.

**Bằng chứng** (Lopez-Lira, Tang & Zhu, arXiv 2504.14765): GPT-4o nhớ chính xác giá trị S&P 500 với sai số ~0.01% cho ngày trước cutoff, rồi sai số "explode" cho ngày sau cutoff.

**Định lượng mức thiệt hại** (Profit Mirage, arXiv 2510.07920) — so 2021 (in-sample) vs 2024 (out-of-sample) trong điều kiện thị trường tương đương:

| Hệ thống    | Sharpe decay | Total Return decay |
| ------------- | ------------ | ------------------ |
| QuantAgent    | 51.48%       | —                 |
| TradingAgents | 55.68%       | 50.18%             |
| FinCON        | 62.23%       | —                 |
| FinMem        | —           | 71.85%             |

→ **Ngưỡng chẩn đoán: nếu Sharpe/return decay > 50% khi chuyển sang post-cutoff → nghi memorization, không phải alpha.**

**Cách xử lý, theo thứ tự thực dụng:**

1. Chọn test window hoàn toàn sau cutoff — đơn giản nhất, đánh đổi bằng cửa sổ ngắn
2. Entity anonymization (che tên công ty)
3. Temporal RAG (timestamp nghiêm ngặt)
4. FinCAD (arXiv 2605.24564) — trừ logit của "memory prior" khỏi logit context, không cần retrain
5. PIT models (DatedGPT) — còn non, mô hình chỉ 1.3B

**Lưu ý cho dự án này:** nếu đi hướng "LLM sinh code strategy deterministic" (file 1 + file 4) thay vì "LLM ra quyết định trade" (file 2), rủi ro parametric leakage **thấp hơn nhiều** — vì LLM chỉ viết rule, còn rule đó chạy trên data bạn kiểm soát. Nhưng **không bằng 0**: LLM vẫn có thể đề xuất rule mà nó "biết" là đã hoạt động trong một giai đoạn cụ thể.

---

## 4. Kiến trúc 5 thành phần và stack hội tụ

Cả 4 file đều dẫn về cùng một kiến trúc. Bản gộp:

```
[1] Idea layer       → LLM đề xuất rule TA (indicator, entry/exit, SL/TP, sizing)
         ↓
[2] Code generation  → Python (vectorbt/backtrader) hoặc MQL5 EA
         ↓
[3] Compile/validate → feed lỗi ngược cho agent để PATCH (không rewrite toàn bộ)
         ↓
[4] Backtest engine  → deterministic, realistic cost
         ↓
[5] Optimizer        → Optuna + walk-forward, multi-objective
         ↓
[6] Evaluator        → DSR/PBO/Monte Carlo/random baseline → quyết định lặp hay dừng
         ↓
[7] Trial ledger     → log MỌI trial + holdout đóng băng chưa từng đụng
```

**Stack khuyến nghị** (file 1, đối chiếu file 4):

| Thành phần         | Lựa chọn chính                                                        | Thay thế                                    | Ghi chú                                                |
| -------------------- | ------------------------------------------------------------------------ | -------------------------------------------- | ------------------------------------------------------- |
| Agent-loop           | **LangGraph** (custom) → **OpenEvolve** (giai đoạn 2)     | ShinkaEvolve, CodeEvolve                     | OpenEvolve ~6.3k sao, Apache-2.0, implement AlphaEvolve |
| Engine               | **vectorbt**                                                       | backtrader, NautilusTrader, MT5 Tester       | vectorbt có Commons Clause — kiểm tra license        |
| Optimizer            | **Optuna** (TPE/GP)                                                | Freqtrade Hyperopt (NSGAIII)                 | Tốt hơn genetic mặc định của MT5                  |
| **Validation** | **`eslazarev/purged-cross-validation`** (PyPI `purgedcv`, MIT) | skfolio (BSD-3), pypbo (AGPL — cẩn trọng) | Đủ cả PBO + DSR + CPCV + WalkForward trong 1 lib     |
| MT5 bridge           | `PHUICMT/mcp-mt5` (MIT)                                                | `Roan2802/MT5-MCP`                         | Chỉ khi bắt buộc EA/MQL5                             |

**Kết luận về "có sẵn hay không":** không có project nào match hoàn chỉnh cả 5 thành phần. Gần nhất về code là `tarsyang/quantevolve` nhưng **thiếu hẳn validation layer** (repo nhỏ, ~13 commit, chỉ tính PnL/Sharpe/MaxDD in-sample).

> ⚠️ **Đính chính (đã kiểm tra trực tiếp repo, 20/9/2026):** báo cáo gốc `93` viết *"QuantEvolve — Code mở nguồn (fork của OpenEvolve)"*. **Sai.** [`tarsyang/quantevolve`](https://github.com/tarsyang/quantevolve) **không phải code của paper QuantEvolve** (arXiv 2510.18569):
>
> | | Paper 2510.18569 | tarsyang/quantevolve |
> |---|---|---|
> | LLM | Qwen3 ensemble | Google Gemini |
> | Engine | Zipline + QuantStats | evaluator custom (Binance) |
> | MAP-Elites / feature map | Có | **Không nhắc tới** |
> | Trích dẫn paper | — | **Không có** |
>
> **Không tìm thấy code chính thức của paper ở đâu cả**, kể cả dataset "evolved strategies" mà abstract hứa release. (alphaXiv cũng link nhầm sang repo này.) Báo cáo `90` đã cảnh báo đúng rằng hai thứ này khác nhau. Mẫu tham khảo tốt nhất về *cách nhúng validation vào loop* là `yossiferoz/llm-strategy-gen` (OOS wall cứng + Monte Carlo trade-shuffle + random-entry baseline 100 strategy) — dù chỉ là prototype giáo dục.

---

## 5. Bảng ngưỡng quyết định (gom từ cả 4 file)

Phần dùng được ngay. Áp dụng như **gate cứng**, không phải gợi ý.

| Ngưỡng                              | Giá trị                                                                                    | Nếu vi phạm                                      | Nguồn                 |
| ------------------------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------- | ---------------------- |
| **PBO** (CSCV)                  | < 0.5, lý tưởng < 0.2                                                                     | Loại strategy,**không tinh chỉnh**        | Bailey-Borwein-LdP-Zhu |
| **Deflated Sharpe**             | **> 0.95** (DSR là XÁC SUẤT 0–1, deflate theo tổng trial thực tế) | Loại                                              | Bailey & LdP 2014      |
| **t-stat của factor**          | > 3.0 (không phải 2.0)                                                                     | Coi như noise                                     | Harvey-Liu-Zhu 2016    |
| **OOS Sharpe / IS Sharpe**      | ≥ 50%                                                                                       | Nghi overfit / alpha decay                         | McLean-Pontiff         |
| **Sharpe decay post-cutoff**    | < 50%                                                                                        | Nghi**memorization** (không phải alpha)    | Profit Mirage          |
| **Modeling quality (MT5)**      | ≥ 90%                                                                                       | Không tin backtest phụ thuộc SL/TP intrabar     | MT5 docs               |
| **Spearman(IS rank, OOS rank)** | Cao                                                                                          | Thấp → harness chưa kiểm soát được leakage | file 2                 |
| **White's Reality Check**       | p thấp                                                                                      | p cao → strategy là noise                        | White 2000             |

**Quy tắc bổ sung:**

- Strategy phụ thuộc **một trade duy nhất** (vd basis carry) → giả định shelf life ngắn, chuẩn bị model decay
- Edge biến mất khi **siết chi phí** hoặc **đổi model backtest** → loại, quay lại đầu
- Không cấp vốn dựa trên backtest. Forward/paper test tối thiểu 4–6 tuần (file 4), lý tưởng là vài tháng phủ ít nhất **một regime reversal** (file 2)

---

## 6. Chọn thị trường (file 3)

**Xếp hạng bằng chứng (mạnh → yếu, sau chi phí):**

1. **Time-series momentum / trend following trên futures đa tài sản** — bằng chứng peer-reviewed mạnh nhất (return dương mỗi thập kỷ từ 1880, "crisis alpha" trong 8/10 khủng hoảng lớn)
2. Cross-sectional momentum — mạnh nhưng có "momentum crashes"
3. Crypto TS momentum + funding/basis carry — bằng chứng mới nổi tốt, **nhưng basis đã bị nén mạnh 2024–2026**
4. FX carry — Sharpe 0.4–0.9, **skew âm mạnh** (crash risk)
5. Pure TA rules — **hỗn hợp, chưa ngã ngũ** (Deprez-Frömmel 2024 tìm thấy edge OOS, Hudson-Urquhart thì không)
6. Stat arb / market making — hiệu quả cao nhưng cần hạ tầng tổ chức

⚠️ **Sharpe thực tế sau phí ở cấp danh mục chỉ khoảng 0.3–0.8** — thấp hơn nhiều so với marketing.

**Khuyến nghị: bắt đầu với crypto.** Lý do: dữ liệu 24/7 free/rẻ qua ccxt, không rào cản vốn pháp lý, bằng chứng học thuật rõ nhất cho TS momentum + funding carry, rào cản cá nhân thấp nhất.

**Về thị trường Việt Nam** (nếu muốn đánh "sân nhà"):

- ✅ **VN30 index futures là lựa chọn tốt nhất** — **T+0**, cho phép short qua vị thế futures, biên độ ±7%, khối lượng ~225k hợp đồng/phiên
- ❌ **Tránh cổ phiếu đơn lẻ lúc đầu**: price limit gây "lock" (lệnh chất đống không có đối ứng → **méo mó backtest nghiêm trọng**, hiện tượng không tồn tại ở thị trường Mỹ), T+2, chưa có short-selling
- 📅 Khung pháp lý đang đổi nhanh: FTSE upgrade hiệu lực **21/9/2026**; short-selling/T+0 lộ trình **2026–2028**; CCP dự kiến **Q1/2027**
- Tooling: vnstock + API môi giới (SSI FastConnect, TCBS)
- ⚠️ Con số Sharpe 3.96 trong một nghiên cứu VN 2025 là **backtest in-sample**, nghi overfit

**Thống kê retail cần biết trước khi bắt đầu:** 74–89% tài khoản CFD/forex retail thua lỗ (ESMA); chỉ **~7%** người mua thử thách prop firm từng nhận payout (14% pass × 45% trong số đó nhận được tiền).

---

## 7. Lộ trình gộp

**Giai đoạn 0 — Chốt evaluation harness TRƯỚC, agent SAU** (quan trọng nhất, hay bị bỏ qua)

- Dựng PIT data layer, fail-loud khi thiếu dữ liệu
- Chọn test window sau knowledge cutoff
- Dựng trial ledger (SQLite/Postgres) + holdout đóng băng
- ✅ *Gate:* harness chạy được và **từ chối** được một leaky oracle cố tình cài vào để thử

**Giai đoạn 1 — Khung pure-Python** (2–4 tuần)

- LangGraph + vectorbt + Optuna + `purgedcv`. Đóng gói FastAPI + Docker
- Tham khảo tinh thần loop từ `yossiferoz/llm-strategy-gen`
- Bắt đầu với **TS momentum** trên crypto (bằng chứng mạnh nhất, cơ chế đơn giản, hợp rule-based)
- ✅ *Gate:* ≥50 iteration tự động, mỗi strategy đều qua walk-forward + DSR/PBO, log đầy đủ mọi trial

**Giai đoạn 2 — Evolutionary search** (2–3 tuần)

- Thay custom loop bằng **OpenEvolve**; evaluator = vectorbt + fitness **đã deflate theo số trial**
- 🔑 **Điểm mấu chốt: đưa DSR/PBO vào chính hàm fitness, không chỉ report ở cuối**
- ✅ *Gate:* winner sống sót OOS holdout đóng băng + PBO < 0.5 + **DSR > 0.95** sau deflate

**Giai đoạn 3 — Nhánh MT5/EA** (tùy chọn, 2–4 tuần)

- Chỉ làm nếu **bắt buộc** cần EA MQL5 hoặc tick data thật của broker
- MT5 là Windows-only → chạy trong Docker Windows/VM riêng, tách khỏi service Python chính
- MT5 không có sẵn DSR/PBO → export returns về Python để chạy

**Giai đoạn 4 — Audit độc lập + forward test**

- Waterfall: reported → reproduced → PIT data → lag sensitivity → cost sensitivity → frozen OOS
- Demo/paper trading trên đúng broker, với spread/commission thật
- Strategy cuối phải **deterministic, không gọi LLM**

---

## 8. Cạm bẫy theo từng tầng

**Tầng code (LLM sinh):**

- MQL5: trộn cú pháp MQL4/MQL5, chữ ký `iRSI` đã deprecated, retcode 10016 (SL/TP nằm trong `SYMBOL_TRADE_STOPS_LEVEL`), bỏ qua `SYMBOL_VOLUME_MIN/STEP`, hard-code symbol không xử lý suffix
- Look-ahead & repainting: dùng nến chưa đóng, indicator repaint
- 📌 Test thực tế: ChatGPT sinh ~150 dòng MQL5 → **3 lỗi + 2 warning ngay lần compile đầu**, ~35 phút debug (mỗi lần sửa lại sinh bug mới), và EA hoàn chỉnh vẫn lỗ −4.2% backtest / −9.4% live 6 tuần

**Tầng backtest:**

- MT5: chọn "Every tick based on real ticks"; commission/swap lấy từ **symbol specification** (không phải từ code EA) — để 0 sẽ báo cáo sai; spread cố định là "hư cấu" với tài khoản ECN; slippage của tester chỉ là delay ngẫu nhiên, **không mô phỏng gap thật**
- Crypto: backtest funding arb cần **4 luồng dữ liệu** (OHLCV + funding 8h + open interest + liquidation); Sharpe tính trên funding thô mà không trừ leverage decay/phí/slippage/basis risk là "fantasy"
- Stocks: survivorship bias (phải dùng constituent list lịch sử gồm cả cổ phiếu delisted), PIT fundamentals đắt

**Tầng vận hành:**

- ⚠️ **MCP không có xác thực sẵn** — cần firewall/reverse proxy/SSH tunnel; nhiều MCP server thực thi lệnh thật ngay lập tức → **luôn test trên demo trước**
- Non-determinism: cố định seed, temperature thấp, cache API response theo (ticker, date)
- Chi phí: giới hạn tần suất quyết định (daily thay vì intraday) trừ khi làm HFT

**License cần kiểm tra trước khi tích hợp:**

- `pypbo`: AGPL-3.0 — ràng buộc mạnh nếu dùng thương mại
- `vectorbt`: Apache-2.0 + **Commons Clause** — không được bán lại phần mềm mà nó là thành phần chính
- `mlfinlab`: một phần đã thương mại hóa

---

## 9. Câu hỏi các báo cáo chưa trả lời

> ℹ️ *Cập nhật 21/9/2026:* trạng thái của 4 câu hỏi này giờ được theo dõi tại **sổ quyết định** trong [Architecture_Design.md](Architecture_Design.md) §10 (D1–D4) — nguồn duy nhất. Câu 1, 2, 3 đã chốt (câu 3: không cần EA MQL5, live qua NautilusTrader); câu 4 còn mở.

1. **Dự án này nhắm thị trường nào?** File 3 khuyến nghị crypto, file 4 tập trung MT5/forex. Hai nhánh có stack khác nhau đáng kể → cần chốt trước khi viết dòng code đầu tiên.
2. **Output cần là gì?** Rule-based TA strategy (→ vectorbt/OpenEvolve) hay ML factor (→ RD-Agent + Qlib)? Hai hướng không dùng chung kiến trúc.
3. **Có bắt buộc phải là EA MQL5 không?** Nếu không → bỏ hẳn nhánh MT5, tiết kiệm 2–4 tuần và tránh ràng buộc Windows-only.
4. **Ngưỡng "đủ tốt để cấp vốn" là bao nhiêu?** Các báo cáo đưa gate kỹ thuật nhưng không đưa ngưỡng kinh tế (vốn tối thiểu, drawdown chịu được, thời gian hoàn vốn).

---

## 10. Cảnh báo về chính bộ tài liệu này

- Nhiều con số là **self-reported** từ paper, chưa qua audit độc lập
- Nhiều arXiv ID thế hệ 2026 (26xx.xxxxx) là **preprint rất mới, một số chưa peer-review**
- Số sao GitHub là tại thời điểm khảo sát (9/2026), sẽ biến động
- Một số metadata các báo cáo **tự thừa nhận chưa xác minh được**: ngày commit cuối của vài repo, license của một số project, và việc `Hmz904/AgentAlphaAudit` có phải code chính thức của paper Gençay hay không
- Báo cáo tập trung mảng research/open-source; **hệ thống nội bộ của các quỹ định lượng lớn hầu như không công bố**, nên bức tranh "state of the art thương mại" có thể lệch

---

## Một câu tóm tắt

> Phần khó không phải là làm cho AI viết được strategy — phần khó là **chứng minh strategy đó không phải là may mắn**. Dùng AI như lớp tăng năng suất, không phải lớp quyết định edge. Kỷ luật thống kê là edge đáng tin cậy nhất.
