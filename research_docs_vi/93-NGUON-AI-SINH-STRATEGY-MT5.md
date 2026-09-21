> ### ⚠️ ĐÍNH CHÍNH (20/9/2026) — nội dung gốc giữ nguyên bên dưới
>
> **Mục 1 (Details), đoạn về QuantEvolve — SAI:** câu *"Code mở nguồn (fork của OpenEvolve)"* là nhầm lẫn. Đã kiểm tra trực tiếp: [`tarsyang/quantevolve`](https://github.com/tarsyang/quantevolve) **không phải code của paper arXiv 2510.18569** — nó dùng Google Gemini (paper dùng Qwen3), không có MAP-Elites/Zipline/QuantStats, và không trích dẫn paper. **Không tìm thấy code chính thức của paper ở bất kỳ đâu.** Xem [00-TONG-HOP-NGHIEN-CUU.md](00-TONG-HOP-NGHIEN-CUU.md) mục 4.
>
> **Ghi chú thêm:** QuantEvolve là **workshop paper non-archival** (AI4F @ ICAIF 2025), không phải paper peer-review đầy đủ — mức bằng chứng thấp hơn RD-Agent(Q) (NeurIPS 2025) và AlphaAgent (KDD 2025).
>
> ✅ Ngưỡng *"DSR > 0.95 confidence"* ở mục Recommendations của báo cáo này là **đúng** (DSR là xác suất 0–1). Hai báo cáo khác trong thư mục ghi "DSR > 0" là sai.

---

# AI dùng để XÂY DỰNG, backtest và tối ưu chiến lược giao dịch rule-based (kiểu MT5 Strategy Tester): Toàn cảnh 9/2025 – 9/2026

## TL;DR
- **AI (LLM và coding agent) hiện đã trở thành công cụ chủ đạo để VIẾT code chiến lược** (MQL5 EA, Pine Script, Python) từ mô tả tiếng tự nhiên, rồi tự chạy backtest, đọc report và sửa lại — cột mốc lớn nhất là MetaTrader 5 tích hợp AI Assistant + MCP native (build 6060, 23/7/2026). Nhưng AI giỏi phần "viết code" hơn hẳn phần "phán đoán chiến lược có edge thật hay không".
- **Bằng chứng định lượng cho thấy cách làm này CHƯA tạo ra chiến lược robust một cách đáng tin cậy**: model dẫn đầu trên benchmark BacktestBench (Gemini 3 Pro) chỉ đạt 67.41% overall accuracy, và tác vụ Metrics Calculation là "vùng thảm họa" (ví dụ Qwen3 rớt từ 34.71% ở bản 235B xuống gần 0% ở bản 4B); các paper evolutionary (QuantEvolve, MadEvolve) đều tự thừa nhận nguy cơ data-snooping/overfitting là điểm yếu lộ liễu nhất.
- **Kiến trúc thực tế nên dùng**: agent-loop (sinh code → backtest → đọc report → tinh chỉnh) + engine deterministic (MT5 hoặc vectorbt/backtrader) + optimizer (Optuna Bayesian + walk-forward) + lớp kiểm định chống overfitting BẮT BUỘC (Deflated Sharpe, PBO/CSCV, White's Reality Check, Monte Carlo, out-of-sample, log mọi trial). Chiến lược cuối cùng phải deterministic, không cần LLM lúc runtime.

## Key Findings

1. **MetaTrader 5 đã có AI native.** Từ build 6060 (23/7/2026), MT5 hỗ trợ Model Context Protocol (MCP) và AI agent. AI Assistant trong MetaEditor có thể viết chương trình MQL5 từ mô tả text, tìm lỗi, sửa code, compile và kiểm tra kết quả; có thể kết nối OpenAI Codex, Claude Code, hoặc API key riêng (OpenAI, Anthropic, Gemini, DeepSeek, Ollama). Build 6090 (30/7/2026) thêm khả năng thao tác indicator trên chart; build 6140 (21/8/2026) thêm nhiều nhà cung cấp LLM (Alibaba, Cerebras, z.ai...) và cho phép AI dừng Strategy Tester. Theo MetaQuotes (báo cáo 11/8/2026): "Just three weeks after the official launch of MCP support and the built-in AI Assistant, MetaTrader 5 users have already processed more than 1 trillion tokens through the free MQL5 Lite model" — cho thấy mức độ quan tâm rất lớn.

2. **Hệ sinh thái so sánh đều đã gắn LLM:** QuantConnect có Mia V2 (agent viết code, chạy backtest, debug, deploy qua MCP cloud-only); TradingView có hàng loạt bộ sinh Pine Script bằng AI (PineGen AI, TradeSage, Pine Script Wizard, OctoBot); StrategyQuant X / Build Alpha / EA Studio dùng genetic programming và nay nối được LLM để cấu hình.

3. **Chất lượng code MQL5 do LLM sinh ra còn nhiều lỗi đặc thù:** trộn cú pháp MQL4/MQL5, dùng chữ ký hàm indicator đã deprecated (vd iRSI), lỗi retcode 10016 (SL/TP nằm trong SYMBOL_TRADE_STOPS_LEVEL), bỏ qua SYMBOL_VOLUME_MIN/STEP, hard-code symbol không xử lý suffix. Nguyên nhân gốc: dữ liệu huấn luyện MQL thiếu và lẫn lộn.

4. **Tối ưu vượt genetic algorithm mặc định của MT5:** Optuna (Bayesian/TPE), walk-forward optimization, multi-objective (profit factor, drawdown, Sharpe, recovery factor), kết hợp kiểm định robustness (Monte Carlo, độ nhạy tham số, out-of-sample, multi-symbol/multi-timeframe).

5. **Cạm bẫy lớn nhất là overfitting do search tự động số lượng lớn** — mỗi trial thêm vào làm tăng xác suất chọn phải chiến lược "may mắn". Công cụ kiểm soát: Deflated Sharpe Ratio, Probability of Backtest Overfitting (PBO) qua CSCV, White's Reality Check, và cấu hình backtest realistic (real ticks, spread, slippage, commission).

## Details

### 1. Sinh chiến lược bằng AI: từ ý tưởng đến code

**Vòng lặp agentic (agent viết → backtest → đọc report → sửa)** giờ là mô hình chuẩn. LangGraph cho phép tạo chu trình generate → check (chạy code) → reflect (đọc lỗi) → regenerate. Trong tài chính, cách này được hiện thực hóa qua:
- **QuantEvolve** (arXiv 2510.18569, 2025 — Yun, Lee & Jeon): framework multi-agent evolutionary (MAP-Elites + island model + mutation LLM theo giả thuyết), sinh chiến lược Python hoàn chỉnh, backtest bằng Zipline. Paper nêu: "The system shows monotonic improvement across generations, achieving Sharpe ratios above 1.5 on a held-out test period"; mỗi chu trình tiến hóa cần 5–10 lần suy luận LLM. Đáng chú ý, nhóm tác giả tự thừa nhận rủi ro "snooping bias" và "have not formally validated" chất lượng giả thuyết. Code mở nguồn (fork của OpenEvolve).
- **MadEvolve** (arXiv 2605.23007, 2026): lấy cảm hứng AlphaEvolve, tối ưu hệ thống giao dịch (testbed Bitcoin); một run tốt đánh giá 743 candidate qua 334 thế hệ trong ~16 giờ. Paper báo cáo: "Out-of-sample Sharpe improves in all four runs, by +0.62 for Run 1, +1.29 for Run 2, +1.29 for Run 3, and +1.83 for Run 5" (đây là mức cải thiện delta, không phải Sharpe tuyệt đối). Cần lưu ý báo cáo thiếu confidence interval/kiểm định thống kê, và run có OOS Sharpe cao nhất lại có khoảng chênh validation→test lớn nhất.
- **BacktestBench** (arXiv 2605.17937, KDD 2026 — Wang et al., Beijing Normal University): benchmark lớn đầu tiên cho tự động backtest — 6,549,254 bản ghi thị trường sau làm sạch, phủ 5,401 công ty niêm yết trên sàn Thượng Hải/Thâm Quyến/Bắc Kinh (2/1/2020–30/9/2025, 1,395 trading days), tạo thành 18,246 cặp QA trên 4 tác vụ (tính metric, chọn ticker, chọn chiến lược, xác nhận tham số). Đánh giá 23 LLM. Kết quả: "a clear dominance of closed-source models, with Gemini 3 Pro achieving the highest overall accuracy of 67.41%"; model mã nguồn mở tốt nhất GLM 4.7 đạt 56.83%. Baseline multi-agent AutoBacktest gồm Summarizer + Retriever + Coder.

**Công cụ thực tế:** MetaEditor AI Assistant (chính chủ MetaQuotes); Mia của QuantConnect; các bộ sinh Pine Script; VectorBT Backtesting Skills (marketcalls) — bộ "skill" cho 40+ coding agent (Claude Code, Cursor, Codex...) để setup, backtest, optimize, walk-forward bằng hội thoại.

### 2. Hệ sinh thái MT5 cụ thể

**AI chính chủ:** MetaEditor AI Assistant ban đầu là autocomplete dựa trên model OpenAI (GPT-3.5/4 Turbo, 4o), miễn phí. Từ build 6060 nâng cấp thành agent đầy đủ, đồng bộ cài đặt giữa terminal và MetaEditor. Quyền thực thi lệnh giao dịch có thể cấm hoàn toàn / cho phép / yêu cầu xác nhận thủ công. Lưu ý MetaQuotes nhấn mạnh: AI Assistant không thay compile, code review hay Strategy Tester — nó chỉ rút ngắn đường từ ý tưởng đến prototype kiểm chứng được.

**MCP server và cầu nối Python:** Có nhiều MCP server mở nguồn nối LLM với MT5: `ariadng/metatrader-mcp-server` (32 trading tool, kèm Claude skill), `Qoyyuum/mcp-metatrader5-server` (cài cho Claude Desktop/Code, Cursor, Gemini CLI qua FastMCP). Cảnh báo bảo mật: MCP không có xác thực sẵn — cần firewall/reverse proxy/SSH tunnel. Gói `MetaTrader5` cho Python cho phép lấy OHLCV, đặt lệnh; nhưng Python chạy ngoài MT5 nên KHÔNG chạy được như EA compiled trong Strategy Tester.

**Tự động hóa Strategy Tester:** Chạy backtest/optimization qua command line với file `.ini` (`terminal64.exe /config:"path\config.ini"`), tham số [Tester] gồm TestExpertParameters, TestSymbol, TestPeriod, TestModel (0=Every tick, 1=Control points, 2=Open prices). Người dùng thường viết batch/Python script sinh `.ini` hàng loạt cho nhiều symbol/timeframe rồi parse report. Dự án `StrategyTester5` (MegaJoctan) cho phép backtest kiểu MT5 trong Python.

**Chất lượng code:** Một bài test thực tế trên MQL5.com ("Can ChatGPT Actually Write an MT5 EA? I Tested It", 25/5/2026) cho thấy ChatGPT sinh ~150 dòng MQL5 hợp lệ về cấu trúc nhưng compile lần đầu có 3 lỗi + 2 warning (biến chưa khai báo, sai kiểu tham số iRSI deprecated, thiếu dấu chấm phẩy), cần nhiều vòng debug (~35 phút, mỗi lần sửa lại phát sinh bug mới), và bản EA hoàn chỉnh vẫn thua lỗ (-4.2% backtest, -9.4% live 6 tuần). Kết luận nhất quán từ cộng đồng: "strategy là phần khó, code là phần dễ" — LLM viết trung thực mọi logic kể cả logic tồi.

### 3. Hệ sinh thái so sánh

- **TradingView / Pine Script:** PineGen AI (validate theo compiler TradingView, preview trên chart thật, tóm tắt backtest), TradeSage (extension Chrome, có Strategy Optimizer, ~$9.99/tháng), Pine Script Wizard, OctoBot AI. Điểm mạnh: vòng lặp mô tả → sinh v6 → compile → backtest → refine.
- **QuantConnect / LEAN:** Mia V2 — agent có quyền đọc/ghi file project, tự viết code, compile, tự debug đến khi hết lỗi, chạy backtest, deploy live; là "wrapper" trên các model LLM hàng đầu, dùng MCP cloud-only để tuân thủ license dữ liệu. QuantConnect docs tự công bố: "Our benchmarks show Mia is able to generate working QuantConnect code in 75% of test cases vs OpenAI o3 25%" (self-reported, không công bố phương pháp). Có hệ thống nhiều assistant chuyên biệt (ideation, validation, coding, paper trading) + Conductor điều phối.
- **Python stack:** vectorbt (vector hóa, chạy hàng ngàn cấu hình bằng Numba/Rust); backtesting.py, Backtrader, NautilusTrader; Freqtrade + FreqAI (ML) + Hyperopt (dùng Optuna, sampler NSGAIII).
- **Genetic programming builder:** StrategyQuant X (build 144, 20/5/2026) — sinh chiến lược tự động, Monte Carlo, walk-forward, system parameter permutation, export code MT4/5, NinjaTrader, TradeStation; Build Alpha — 5.000+ tín hiệu, White's Reality Check, "Vs. Random test", nay nối ChatGPT/Claude/Gemini/Grok để tự cấu hình và lặp; EA Studio (browser-based, đơn giản hơn).

### 4. Tối ưu và kiểm định robustness

- **Bayesian optimization (Optuna):** khám phá không gian tham số thông minh hơn grid/genetic, ít trial hơn nhờ TPE/GP/CMA; giảm selection bias. Freqtrade Hyperopt đã tích hợp Optuna.
- **Walk-forward optimization (WFO):** tối ưu trên in-sample, kiểm tra out-of-sample rolling; phát hiện tham số mong manh trước khi vào tiền thật. Không chứng minh strategy sẽ lãi tương lai, chỉ loại bớt cái dễ vỡ.
- **Multi-objective + robustness:** profit factor, drawdown, Sharpe, recovery factor; Monte Carlo (xáo trộn thứ tự trade / bootstrap), độ nhạy tham số, test đa symbol/timeframe.
- **Kiểm soát overfitting (bắt buộc khi search lớn):**
  - **Deflated Sharpe Ratio (DSR)** — Bailey & López de Prado (2014): hiệu chỉnh Sharpe cho số lần thử (selection bias) và non-normality (skew/kurtosis).
  - **Probability of Backtest Overfitting (PBO)** qua **CSCV** — Bailey, Borwein, López de Prado, Zhu: PBO tiến tới 1 khi số cấu hình N tăng, kể cả khi không có edge thật *(⚠️ đính chính 21/9/2026: sai — không có edge thì PBO ≈ 0.5; xem [07](07-VALIDATION-LAYER.md) §4.2)*. Có package R (`pbo`) và Python.
  - **White's Reality Check** (2000) và Hansen SPA (2005): kiểm định data-snooping.
  - Log MỌI trial; giữ holdout thật sự chưa đụng đến.

### 5. Cạm bẫy đặc thù của chiến lược do AI sinh/tối ưu

- **Overfitting & data-snooping:** đây là rủi ro lớn nhất. Càng nhiều trial tự động (agent + optimizer sinh hàng ngàn biến thể) càng dễ tìm ra "winner" giả. QuantEvolve và MadEvolve đều tự thừa nhận điều này.
- **Look-ahead bias & repainting** trong code sinh ra: dùng dữ liệu tương lai (vd nến chưa đóng), indicator repaint. Cần review deterministic và test forward. BacktestBench nhấn mạnh logic thời gian (temporal) là thách thức đặc trưng của tự động backtest.
- **Backtest thiếu thực tế:** MT5 có 4 chế độ mô hình hóa: "Open prices only", "1 minute OHLC", "Every tick", "Every tick based on real ticks". Real ticks sát thực nhất nhưng dữ liệu lớn, có thể thiếu → fallback tick tổng hợp; xem % modeling quality (dưới ~90% là đáng lo với chiến lược phụ thuộc SL/TP intrabar). Commission/swap lấy từ symbol specification (không phải từ code EA) — nếu để 0 sẽ báo cáo sai. Spread cố định là "hư cấu" với tài khoản ECN spread thả nổi. Slippage của tester chỉ là delay mili-giây ngẫu nhiên, không mô phỏng gap thật.
- **LLM hallucination trên tính toán tài chính:** LLM thường tính sai KPI phức tạp. Theo BacktestBench, Metrics Calculation là "vùng thảm họa" — ví dụ Qwen3 rớt từ 34.71% (bản 235B) xuống gần 0% (bản 4B); các KPI thống kê bậc cao như Sharpe Ratio/Volatility có pass rate thấp hơn hẳn Win Rate/Max Drawdown (ngưỡng đúng: sai số < 10⁻³ so với ground truth). Đây là lý do phải để phần tính KPI cho code deterministic, không để LLM tự tính.

### 6. Kiến trúc thực tế cho kỹ sư

Kiến trúc đề xuất (mọi thành phần đều deterministic trừ lớp sinh code):
1. **Idea/prompt layer** → LLM đề xuất rule TA (indicator, entry/exit, SL/TP, sizing).
2. **Code generation** → sinh MQL5 EA (cho MT5) hoặc Python (vectorbt/backtrader) hoặc Pine Script.
3. **Compile/validate** → compile trong MetaEditor (F7) hoặc chạy thử; feed lỗi ngược lại cho agent để patch (không rewrite toàn bộ).
4. **Backtest engine** → MT5 Strategy Tester (real ticks, spread/commission/slippage đúng) hoặc vectorbt (nhanh, song song).
5. **Optimizer** → Optuna + walk-forward, multi-objective.
6. **Evaluator/report analysis** → parse report, tính DSR/PBO, Monte Carlo, so với random baseline; agent đọc và quyết định lặp tiếp hay dừng.
7. **Trial logging + holdout** → lưu mọi trial để tính effective number of trials; giữ out-of-sample thật.

**Repo mở nguồn để bắt đầu:** `tarsyang/quantevolve`; `marketcalls/vectorbt-backtesting-skills`; `polakowo/vectorbt`; `MegaJoctan/StrategyTester5`; `ariadng/metatrader-mcp-server` và `Qoyyuum/mcp-metatrader5-server`; `mrbcuda/pbo` (R) cho PBO; `alex-muci/Strategy-Generator` (đã tích hợp PBO/CSCV, Reality Check, DSR).

**Bằng chứng về độ robust:** Chưa có bằng chứng mạnh cho thấy cách làm này tự động sinh ra chiến lược robust. Benchmark cho thấy LLM tốt nhất chỉ đạt 67.41% accuracy tổng thể trên các tác vụ backtest và gần như thất bại ở tác vụ tính KPI thống kê, còn các framework tiến hóa đều cảnh báo data-snooping. Giá trị thực tế nằm ở tăng tốc độ khám phá và viết code, KHÔNG phải thay thế kiểm định thống kê nghiêm ngặt.

## Recommendations

**Giai đoạn 1 — Prototype nhanh (tuần 1–2):**
- Dùng MetaEditor AI Assistant hoặc ChatGPT/Claude để sinh EA MQL5 đơn giản từ ý tưởng. Luôn compile trong MetaEditor, copy lỗi chính xác đưa lại cho AI để patch.
- Bắt buộc thêm vào mọi bản draft: chuẩn hóa lot (SYMBOL_VOLUME_MIN/STEP), kiểm tra SYMBOL_TRADE_STOPS_LEVEL, xử lý suffix symbol, lưu state khi restart.
- *Ngưỡng chuyển giai đoạn:* EA compile sạch (0 lỗi/warning ở mức 4) và chạy được Strategy Tester với "Every tick based on real ticks".

**Giai đoạn 2 — Backtest nghiêm túc (tuần 3–4):**
- Cấu hình realistic: real ticks, commission từ symbol spec, spread thả nổi, slippage/delay. Kiểm tra modeling quality ≥ 90%.
- Tự động hóa qua `.ini` command line + parse report. Test đa symbol và đa timeframe.
- *Ngưỡng:* nếu edge biến mất khi siết chi phí hoặc đổi model → loại bỏ, quay lại giai đoạn 1.

**Giai đoạn 3 — Tối ưu + chống overfitting (tuần 5–8):**
- Dùng Optuna + walk-forward thay vì chỉ genetic của MT5. Multi-objective (profit factor, drawdown, Sharpe, recovery).
- Bắt buộc tính DSR và PBO (CSCV) trên TOÀN BỘ trial đã chạy; chạy White's Reality Check / Vs. Random. Monte Carlo cho drawdown.
- *Ngưỡng dừng/chấp nhận:* PBO < 0.5 (lý tưởng < 0.2), DSR > 0.95 confidence, degradation IS→OOS nhỏ, sống sót qua nhiều cell walk-forward. Nếu PBO ~0.5+ hoặc Reality Check p cao → chiến lược là noise, bỏ.

**Giai đoạn 4 — Forward test & deploy (tuần 9+):**
- Demo/paper trading tối thiểu 4–6 tuần trên đúng broker với spread/commission thật.
- Chiến lược cuối cùng phải deterministic, KHÔNG gọi LLM lúc runtime.

**Nguyên tắc xuyên suốt:** dùng AI như lớp tăng năng suất, không phải lớp quyết định edge. Đếm và log mọi trial. Kỷ luật thống kê là edge đáng tin cậy nhất.

## Caveats
- **Nhiều nguồn là marketing / self-reported:** benchmark "Mia sinh code chạy được 75% vs OpenAI o3 25%" của QuantConnect không công bố phương pháp; các sản phẩm PineGen/TradeSage/StrategyQuant mô tả năng lực theo hướng quảng cáo. Con số Sharpe của QuantEvolve/MadEvolve là tự báo cáo trên setup mô phỏng (một phần là Bitcoin), thiếu kiểm định thống kê — cần thận trọng.
- **Bài test code MQL5 là trải nghiệm cá nhân (blog), không phải nghiên cứu có kiểm soát** — mang tính minh họa.
- **Cảnh báo bảo mật MCP:** MCP mặc định không xác thực; nhiều MCP server thực thi lệnh thật ngay lập tức — luôn test trên demo trước.
- **Phạm vi:** Báo cáo này CHỦ ĐÍCH loại trừ AI ra quyết định giao dịch/dự báo thị trường (TradingAgents, LLM sentiment trading, Alpha Arena). Trọng tâm là AI như công cụ phát triển chiến lược deterministic.
- **Chú ý ngày tháng:** một số build MT5 và bản cập nhật (6060, 6090, 6140) và các paper 2026 rất mới; tính năng có thể thay đổi. Windows 7 không được hỗ trợ cho AI Assistant từ build 6090.
- **Đa số nguồn khẳng định:** không có cách nào (kể cả AI) đảm bảo backtest tốt sẽ thành live tốt; overfitting vẫn là kẻ thù số một.