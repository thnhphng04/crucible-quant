> ### ⚠️ ĐÍNH CHÍNH (20/9/2026) — nội dung gốc giữ nguyên bên dưới
>
> **Ngưỡng DSR ghi sai.** Mục Recommendations Giai đoạn 2 viết *"DSR > 0 sau khi deflate"*. **DSR là một XÁC SUẤT (0–1)**, là PSR với benchmark điều chỉnh theo số trial — nên "DSR > 0" luôn đúng một cách vô nghĩa. Ngưỡng đúng: **DSR > 0.95**. Xem [07-VALIDATION-LAYER.md](07-VALIDATION-LAYER.md) mục 0.
>
> ✅ **Báo cáo này ĐÚNG ở điểm quan trọng:** cảnh báo rằng `tarsyang/quantevolve` là **"repo cùng tên nhưng khác nhóm tác giả"** với paper QuantEvolve. Đã kiểm tra trực tiếp và xác nhận. Báo cáo `93` nhầm lẫn hai thứ này.
>
> **Ghi chú:** QuantEvolve là **workshop paper non-archival** (AI4F @ ICAIF 2025), không phải paper peer-review đầy đủ.

---

# Báo cáo: Kiến trúc AI hỗ trợ phát triển chiến lược trading — Repo, papers, và stack khuyến nghị

## TL;DR
- **Chưa có project mã nguồn mở nào match hoàn chỉnh cả 5 thành phần** của kiến trúc mục tiêu (agent-loop + backtest engine + optimizer + validation layer chống overfitting + strategy deterministic không gọi LLM khi chạy). Các match gần nhất là **QuantEvolve/MadEvolve/AlgoEvolve** (phía học thuật, evolutionary LLM) và **tarsyang/quantevolve** (repo, nhưng thiếu hẳn validation layer).
- Con đường thực tế nhất là **tự lắp ghép (assemble)** từ các building block tốt: agent-loop bằng **LangGraph + OpenEvolve**, engine bằng **vectorbt** (Python) hoặc **MT5 Strategy Tester qua MCP** (nếu cần EA/MQL5), optimizer bằng **Optuna**, và validation layer bằng **eslazarev/purged-cross-validation** hoặc **skfolio** (PBO/CSCV/DSR + walk-forward).
- Điểm yếu lớn nhất của toàn bộ hệ sinh thái: **validation layer chống overfitting hầu như chỉ được mô tả trong paper, rất ít khi được implement trong code**. Đây là lý do các paper audit như "What survives honest evaluation?" (arXiv 2608.27734) và AgentAlphaAudit ra đời — và cũng là chỗ bạn phải tự xây/bổ sung.

## Key Findings

1. **Các end-to-end match gần nhất đều là evolutionary LLM search** (kiểu AlphaEvolve/OpenEvolve áp dụng cho trading): QuantEvolve (arXiv 2510.18569), MadEvolve (arXiv 2605.23007), AlgoEvolve (arXiv 2606.26173). Tất cả sinh strategy dưới dạng code Python deterministic, backtest, đọc metrics, rồi refine — đúng agent-loop mục tiêu. Nhưng phần lớn **tự report kết quả (self-reported)** và validation chống overfitting còn mỏng.

2. **Repo có sẵn duy nhất khớp nhất về mặt code là tarsyang/quantevolve** (fork của OpenEvolve). Nó có agent-loop (Gemini), backtester custom trên dữ liệu Binance, và strategy là code Python deterministic. **Nhưng nó KHÔNG có validation layer** — chỉ tính PnL/Sharpe/MaxDD in-sample. Repo nhỏ (50 sao, Apache-2.0, ~13 commit), ít hoạt động.

3. **Building block cho validation layer là chỗ có tài nguyên tốt nhất và cũng quan trọng nhất**: `eslazarev/purged-cross-validation` (PBO + DSR + CPCV + walk-forward, MIT, mới và active) là lựa chọn tốt nhất hiện nay; `skfolio` (2.412 sao, BSD-3, có CombinatorialPurgedCV) và `esvhd/pypbo` (140 sao, AGPL-3.0, có PBO/CSCV/DSR/PSR nhưng cũ) là các lựa chọn thay thế.

4. **MT5 fit vào kiến trúc qua các MCP server**: `PHUICMT/mcp-mt5` (17 sao, MIT, active) và `Roan2802/MT5-MCP` (MIT, fork sửa lỗi terminal.ini) cho phép LLM compile EA → chạy Strategy Tester → đọc report → lặp. Đây là cầu nối tốt nhất cho nhánh MQL5/EA. Bản gốc `Qoyyuum/mcp-metatrader5-server` (7 sao) thiên về market data/live trading, không mạnh về Strategy Tester automation.

5. **Microsoft RD-Agent(Q)** là hệ thống trưởng thành nhất về mặt kỹ thuật (14.6k sao, MIT, rất active) nhưng nó tối ưu **factor + model (ML)** trên Qlib, không phải rule-based TA strategy deterministic; và cũng **không có module DSR/PBO** — chỉ dùng train/val/test split của Qlib.

6. **Loại trừ đúng như yêu cầu**: TradingAgents, AI-Trader, các hệ LLM ra quyết định mua/bán lúc runtime không thuộc phạm vi. AlphaAgent/QuantAgent/Alpha-GPT là **alpha/factor mining** (LLM sinh factor, không phải strategy TA hoàn chỉnh với SL/TP), chỉ nên tái dùng ý tưởng regularization chống alpha decay.

## Details

### A. Các hệ end-to-end (agent-loop sinh strategy → backtest → refine)

**QuantEvolve** (arXiv 2510.18569, Yun, Lee, Jeon, 2025). Multi-agent evolutionary framework theo template AlphaEvolve: dùng MAP-Elites + island model + LLM mutation theo hypothesis để tiến hóa strategy dưới dạng code Python. Engine backtest là **Zipline**, metrics qua QuantStats (Sharpe, Sortino, IR, MaxDD). Có phân tách agent thành research/coding/evaluation. Báo cáo Sharpe > 1.5 trên test period. **Đánh giá**: match rất tốt về agent-loop + evolutionary search + deterministic output. Nhưng validation chống overfitting chỉ ở mức OOS test + composite objective, **không implement DSR/PBO**. Kết quả self-reported. Chưa rõ code chính thức có công khai đầy đủ không.

**tarsyang/quantevolve** (GitHub, fork của OpenEvolve). Đây là repo cùng tên nhưng **khác nhóm tác giả** với paper trên — cần phân biệt. Agent-loop dùng Google Gemini sinh/sửa code strategy Python; backtester custom trên OHLCV Binance; log đầy đủ quá trình tiến hóa. **50 sao, Apache-2.0, chỉ ~13 commit, hoạt động thấp**. **Không có validation layer** (README chỉ nêu tính PnL/Sharpe/MaxDD in-sample; chính README thừa nhận "current implementation relies on historical backtesting"). Dùng làm tham khảo kiến trúc, không nên dùng production.

**MadEvolve** (arXiv 2605.23007, Kvasiuk et al., 2026). Áp dụng framework MadEvolve (gốc dùng cho cosmology, cảm hứng AlphaEvolve) vào trading Bitcoin. Tiến hóa feature set cho signal, tối ưu các thành phần strategy, và đồng tiến hóa feature pipeline + execution. **Điểm mạnh nổi bật**: có so sánh với Claude Code và **đánh giá xác suất p-hacking** trên simulation — tức là có ý thức về overfitting. Giới hạn số parameter tunable (15–20) để kiểm soát complexity. Framework công khai tại madevolve.org (phần code công khai chủ yếu cho ứng dụng cosmology). **Đánh giá**: match tốt, có tư duy anti-overfitting, nhưng code phần trading chưa chắc công khai đầy đủ.

**AlgoEvolve** (arXiv 2606.26173). LLM-driven evolutionary framework sinh strategy Python, đánh giá qua "rigorous testing protocol", có **meta-evolution** (outer loop tiến hóa chính prompt sinh code). Thể hiện logic thích ứng theo regime. Match tốt về concept.

**Microsoft RD-Agent / RD-Agent(Q)** (github.com/microsoft/RD-Agent, arXiv 2505.15155, NeurIPS 2025). **14.6k sao (fork 1.9k, 37 contributors, theo star-history.com và header repo tháng 9/2026)**, MIT, rất active (v0.8.0, 11/2025). Multi-agent tự động hóa toàn bộ pipeline quant: sinh hypothesis → Co-STEER sinh code factor/model → backtest trên **Qlib** → feedback → lặp, có multi-armed bandit scheduler. Theo README: "at a cost under $10, RD-Agent(Q) achieves approximately 2× higher ARR than benchmark factor libraries while using over 70% fewer factors"; paper MSR (Li et al.) báo cáo chế độ joint đạt IC 0.0532 và annualized return 14.21% trên CSI 300 (test 2017–2020). **Đánh giá**: trưởng thành nhất, nhưng output là **factor + ML model**, không phải rule-based TA strategy với entry/exit/SL/TP deterministic; và **không có DSR/PBO** trong code. Tái dùng tốt: kiến trúc agent-loop, Co-STEER code-gen, tích hợp Qlib.

### B. Building block cho từng thành phần

**1. Agent-loop / evolutionary LLM search**
- **OpenEvolve** (algorithmicsuperintelligence/openevolve, trước là codelion/openevolve; **~6.3k sao, fork 1k**, Apache-2.0 theo tài liệu công khai, active — release mới nhất v0.2.27 ngày 18/3, tác giả codelion / Asankhaya Sharma): implement AlphaEvolve mở nguồn — LLM ensemble + island-based evolution + MAP-Elites quality-diversity archive. Bạn chỉ cần cung cấp initial program + evaluator. **Đây là nền tảng agent-loop tốt nhất** để gắn evaluator = backtest+validation của trading.
- **ShinkaEvolve, CodeEvolve** (arXiv 2510.14150, inter-co/science-codeevolve): các framework evolutionary coding agent khác, có thể thay thế OpenEvolve.
- **LangGraph/AutoGen/CrewAI**: cho custom loop có state; LangGraph phù hợp nhất với yêu cầu (bạn đã quen).

**2. Backtest engine (deterministic)**
- **vectorbt** (polakowo/vectorbt, Apache-2.0 + Commons Clause): nhanh (Numba), có sẵn walk-forward optimization, robustness testing, tích hợp QuantStats. Tốt nhất cho pure-Python.
- **marketcalls/vectorbt-backtesting-skills** (~184 sao, active 2026): bộ "agent skills" biến prompt ngôn ngữ tự nhiên → backtest vectorbt chuẩn, có template chống lookahead bias, mô hình phí thực tế, **walk-forward + Monte Carlo robustness** (nhưng không DSR/PBO). Rất hữu ích làm lớp skill cho coding agent.
- **backtrader, backtesting.py, Zipline, NautilusTrader, LEAN, Freqtrade**: các lựa chọn khác. Freqtrade có hyperopt built-in (Optuna, trước là scikit-optimize) + hỗ trợ walk-forward.
- **MT5 Strategy Tester**: dùng khi bắt buộc EA/MQL5 hoặc cần tick data thật của broker.

**3. Optimizer**
- **Optuna**: TPE + GP, chuẩn de-facto cho Bayesian optimization. Tích hợp tốt với mọi engine Python.
- **Freqtrade Hyperopt**: dùng Optuna (NSGAIII sampler), có nhiều loss function (Sharpe, Sortino, MultiMetric...).
- **Genetic programming**: DEAP, PyGAD; hoặc dùng engine thương mại (StrategyQuant X, Build Alpha).

**4. Validation layer chống overfitting (thành phần quan trọng nhất và thiếu nhất)**
- **eslazarev/purged-cross-validation** (PyPI `purgedcv`, MIT, mới & active, đang nộp JOSS/pyOpenSci tháng 9/2026): **lựa chọn tốt nhất**. Implement đầy đủ: purging, embargo, WalkForwardSplit, PurgedKFold, **CombinatorialPurgedCV (CPCV)** với tái dựng backtest path, cùng **PSR, Deflated Sharpe Ratio, Minimum Track Record Length, Minimum Backtest Length, và Probability of Backtest Overfitting (PBO)**. Tương thích sklearn splitter, 285 test. Đủ cả DSR + PBO + CPCV trong một thư viện.
- **skfolio** (**2.412 sao, 252 fork, BSD-3-Clause, cập nhật 16/9/2026**): có `CombinatorialPurgedCV` + `WalkForward` trong `skfolio.model_selection`, tính phân phối Sharpe/CVaR trên nhiều OOS path. Có PR thêm `deflated_sharpe_ratio` + `expected_max_sharpe_ratio`. Trưởng thành, tài liệu tốt, dùng được cho portfolio.
- **esvhd/pypbo** (140 sao, AGPL-3.0, cũ/ít bảo trì): PBO/CSCV + DSR + PSR + MinTRL + MinBTL theo đúng paper Bailey & López de Prado. AGPL cần lưu ý về license.
- **mrbcuda/pbo** (R): implement PBO/CSCV gốc bằng R.
- **mlfinlab** (Hudson & Thames): CombinatorialPurgedKFold + deflated/haircut Sharpe, nhưng đã chuyển sang thương mại một phần.
- **Monte Carlo robustness + random-entry baseline**: `yossiferoz/llm-strategy-gen` và `marketcalls/vectorbt-backtesting-skills` có implement trade-shuffle Monte Carlo; vectorbt có sẵn công cụ robustness.
- **Lý thuyết nền**: Bailey, Borwein, López de Prado, Zhu — "The Probability of Backtest Overfitting"; Bailey & López de Prado — "The Deflated Sharpe Ratio"; White's Reality Check / Hansen SPA.

**5. LLM → code strategy + MT5 automation**
- **MT5 MCP servers**: `PHUICMT/mcp-mt5` (17 sao, MIT, active, v0.5.0): pipeline đầy đủ compile→deploy→run Strategy Tester→parse report, có sample `tester.ini`, 32 tools. `Roan2802/MT5-MCP` (MIT, fork sửa terminal.ini date-range + auto HTML/CSV report). `chymian/metatrader-mcp` (điều khiển MT5 cho EA optimization). Đây là các cầu nối tốt nhất cho nhánh MQL5.
- **LLM → vectorbt**: dùng `marketcalls/vectorbt-backtesting-skills`.
- **LLM → backtrader loop với validation**: `yossiferoz/llm-strategy-gen` (prototype giáo dục nhưng rất đúng tinh thần): NL goal → Claude viết strategy Backtrader → backtest trên dữ liệu SPY đóng băng → feedback tối đa 2 vòng → chạy suite validation (OOS test + Monte Carlo trade-shuffle + random-entry baseline 100 strategy), có tường in-sample/out-of-sample cứng. Đây là **mẫu tham khảo tốt nhất về cách nhúng validation vào loop**.
- **alex-muci/Strategy-Generator**: sinh strategy uncorrelated, bar loop Numba, có walk-forward (`walkforward.window_backtest`), warm-up buffer đúng cách, meta-labelling theo AFML. Code chú trọng tránh các bug leakage (đã fix nhiều bug về OOS buffer, fill giá sai). Đáng tham khảo về kỹ thuật walk-forward sạch.

### C. Alpha/factor mining (chỉ tái dùng thành phần, KHÔNG phải match)
- **AlphaAgent** (arXiv 2502.16789, KDD'25): LLM mining factor chống alpha decay bằng 3 cơ chế regularization (originality qua AST similarity, hypothesis-factor alignment, complexity control). Backtest trên Qlib (CSI 500, S&P 500). Tái dùng: ý tưởng regularization + complexity control để chống overfitting.
- **QuantAgent** (arXiv 2402.03755): self-improving LLM two-layer loop mining trading signal. Tái dùng: cơ chế inner/outer loop + knowledge base.
- **Alpha-GPT** (arXiv 2308.00016, 2402.09746): human-AI interactive alpha mining.
- Các paper khác: FactorEngine, AlphaForge, XALPHA, AlphaMemo, Cognitive Alpha Mining (2511.18850) — đều là alpha mining.

### D. Benchmark & audit (đánh giá chất lượng bằng chứng)
- **"What survives honest evaluation?"** (arXiv 2608.27734, Eray Gençay, 8/2026): quan trọng về mặt phương pháp. Chỉ ra rằng nhiều paper LLM-trading sinh nhiều candidate, report cái tốt nhất, không sửa look-ahead bias lẫn cường độ search. Minh chứng một "leaky oracle" có Sharpe = 35 **vẫn qua được** DSR và PBO — tức chỉ correction thống kê là chưa đủ, cần guardrail cấu trúc (tool registry loại bỏ look-ahead) + ghi lại toàn bộ search ledger để deflate theo số trial. **Đây là bài bắt buộc đọc.**
- **AgentAlphaAudit** (Hmz904/AgentAlphaAudit, 0 sao, không có LICENSE, mới 3 commit): framework audit agent quant, target đầu tiên là RD-Agent(Q) 0.8.0. Implement DSR diagnostic + effective-trial count (participation ratio) + PIT data rules + holdout isolation + cost/lag sensitivity + frozen OOS + "Alpha Audit Waterfall". **Nhưng README nói rõ chưa có kết quả audit thật** — mới là scaffolding + synthetic demo. (Chưa xác nhận đây có phải code chính thức của paper Gençay hay không.)
- **BacktestBench / AutoBacktest** (arXiv 2605.17937): benchmark LLM tự động backtest, 18.246 QA pair từ >6 triệu record thị trường TQ. AutoBacktest = multi-agent (Summarizer + Retriever SQL + Coder Python). Dùng để đánh giá năng lực agent, không phải hệ sản xuất strategy.
- **Backtrader-Bench** (arXiv 2608.11232), **AlphaForgeBench** (arXiv 2602.18481), **SysTradeBench** (arXiv 2604.04812): các benchmark liên quan, hữu ích để chọn LLM/kiểm thử.

### E. Công cụ thương mại (tham chiếu)
- **StrategyQuant X**: no-code, genetic programming sinh hàng nghìn strategy, có đầy đủ **Monte Carlo (2 loại, 9+ simulation), walk-forward/matrix, parameter permutation, optimization profile, OOS**, export EA full source cho MT4/5, TradeStation, MultiCharts. Đây là hiện thân thương mại gần nhất với kiến trúc mục tiêu (trừ phần LLM). **Giá (theo review StatOasis 19/8/2026, đối chiếu strategyquant.com/pricing): bản Ultimate $2.900 one-time (lifetime, chưa VAT) hoặc 12 kỳ $290/tháng — "không phải subscription; ngừng trả sau 12 kỳ"; Starter $1.290, Professional $1.490. Lưu ý các robustness test nâng cao + Walk-Forward Optimizer chỉ có từ bản Professional trở lên, không có ở Starter.**
- **Build Alpha**: tương tự, genetic + robustness testing.
Cả hai đều **deterministic, rule-based, không gọi LLM runtime** — đúng tiêu chí output, chỉ thiếu LLM ở khâu sinh ý tưởng.

## Ma trận so sánh (5 thành phần)

Ghi chú: (1) Agent-loop, (2) Backtest engine, (3) Optimizer, (4) Validation chống overfitting, (5) Output deterministic không LLM runtime. Full = đầy đủ, Partial = một phần, None = không.

| Project | (1) Agent-loop | (2) Engine | (3) Optimizer | (4) Validation | (5) Deterministic output | Maturity |
|---|---|---|---|---|---|---|
| QuantEvolve (2510.18569) | Full (evolutionary multi-agent) | Full (Zipline) | Partial (evolutionary) | Partial (OOS, không DSR/PBO) | Full | Research |
| tarsyang/quantevolve | Full (Gemini) | Full (custom) | Partial | **None** | Full | Prototype yếu |
| MadEvolve (2605.23007) | Full | Full (custom BTC) | Partial | Partial (p-hacking eval) | Full | Research |
| AlgoEvolve (2606.26173) | Full (+ meta-evolution) | Full | Partial | Partial | Full | Research |
| RD-Agent(Q) | Full | Full (Qlib) | Full | Partial (train/val/test) | Partial (ML model) | Tool trưởng thành |
| yossiferoz/llm-strategy-gen | Full (Claude, 2 vòng) | Full (backtrader) | None | **Full (OOS+MC+baseline)** | Full | Prototype giáo dục |
| marketcalls/vectorbt-skills | Partial (skill) | Full (vectorbt) | Full | Partial (WF+MC, không DSR/PBO) | Full | Usable tool |
| OpenEvolve | Full | None (tự cấp) | Partial | None | Full | Usable tool |
| eslazarev/purged-cv | None | None | None | **Full (PBO+DSR+CPCV+WF)** | N/A | Usable lib |
| skfolio | None | Partial (portfolio) | Full | Full (CPCV, DSR) | N/A | Usable lib |
| esvhd/pypbo | None | None | None | Full (PBO/DSR/PSR) | N/A | Lib cũ |
| PHUICMT/mcp-mt5 | Partial (bridge) | Full (MT5 Tester) | Partial | None | Full (EA) | Usable tool |
| StrategyQuant X | None (no LLM) | Full | Full (genetic+WF) | **Full (MC+WF+OOS)** | Full (EA export) | Thương mại |

## Recommendations

**Giai đoạn 1 — Dựng khung pure-Python (2–4 tuần).** Dùng stack: **LangGraph** điều phối agent-loop; **vectorbt** làm engine; **Optuna** làm optimizer; **eslazarev/purged-cross-validation** làm validation layer bắt buộc. Đóng gói bằng **FastAPI + Docker** như bạn quen. Mẫu tham khảo tinh thần loop: `yossiferoz/llm-strategy-gen` (đã có OOS wall + Monte Carlo + random baseline). Strategy sinh ra là code Python thuần (indicator + entry/exit + SL/TP), **không gọi LLM khi backtest/run**.
- *Benchmark chuyển giai đoạn*: loop chạy được ≥50 iteration tự động, mỗi strategy đều qua walk-forward + tính DSR/PBO, và log đầy đủ mọi trial vào SQLite/Postgres.

**Giai đoạn 2 — Thêm evolutionary search (2–3 tuần).** Thay custom loop bằng **OpenEvolve**: initial program = template strategy, evaluator = hàm chạy vectorbt + trả về fitness đã **phạt theo số trial (deflated)** và loại strategy có PBO cao. Đây là điểm mấu chốt: đưa DSR/PBO vào chính hàm fitness, không chỉ report cuối. Áp dụng bài học từ arXiv 2608.27734: (a) giới hạn feature qua registry loại bỏ look-ahead; (b) ghi search ledger để deflate.
- *Benchmark*: strategy "winner" phải sống sót OOS holdout đóng băng + PBO < 0.5 + DSR > 0 sau khi deflate theo tổng số trial thực tế.

**Giai đoạn 3 — Nhánh MT5/EA (tùy chọn, 2–4 tuần).** Nếu cần EA MQL5 hoặc tick data broker thật: cắm **PHUICMT/mcp-mt5** hoặc **Roan2802/MT5-MCP** vào LangGraph để LLM compile EA → chạy Strategy Tester → parse report → refine. Lưu ý MT5 là Windows-only; chạy trong Docker Windows hoặc VM riêng, tách khỏi service Python chính. Dùng MT5 optimization built-in cho walk-forward, rồi export returns về Python để chạy DSR/PBO (MT5 không có sẵn các test này).
- *Khi nào chọn MT5 vs pure-Python*: chọn **pure-Python (vectorbt)** khi cần tốc độ, nhiều asset, và validation thống kê chặt. Chọn **MT5** khi sản phẩm cuối bắt buộc là EA cho forex/CFD, cần mô phỏng spread/slippage/tick thật của broker.

**Giai đoạn 4 — Audit độc lập trước khi tin.** Trước khi coi bất kỳ strategy nào là "thật", chạy qua checklist kiểu **AgentAlphaAudit / arXiv 2608.27734**: reported → reproduced → PIT data → lag sensitivity → cost sensitivity → frozen OOS. Nếu strategy không sống sót waterfall này thì loại.

**Ngưỡng thay đổi quyết định:**
- Nếu nhóm bạn cần sản phẩm nhanh, không cần LLM sinh ý tưởng: cân nhắc **StrategyQuant X** (đã có genetic + toàn bộ robustness + export EA) — rẻ hơn nhiều so với tự xây, và validation trưởng thành hơn.
- Nếu cần novelty/diversity cao: đi theo **QuantEvolve/MadEvolve** (quality-diversity, MAP-Elites) thay vì optimizer đơn thuần.
- Nếu output cần là ML factor thay vì rule TA: chuyển sang **RD-Agent(Q) + Qlib**.

## Caveats
- **Chất lượng bằng chứng**: hầu hết kết quả của các paper evolutionary LLM (QuantEvolve, MadEvolve, AlgoEvolve, RD-Agent(Q)) là **self-reported**, chưa có kiểm định độc lập. Sharpe > 1.5 hay "ARR gấp 2 lần" cần đọc kèm điều kiện (universe nhỏ, giai đoạn cụ thể, có/không transaction cost).
- **Validation "trên giấy" vs "trong code"**: nhiều project mô tả walk-forward/OOS nhưng **không implement DSR/PBO trong code**. Chỉ `eslazarev/purged-cross-validation`, `skfolio`, `esvhd/pypbo`, `mlfinlab` là có DSR/PBO thực thi. tarsyang/quantevolve và RD-Agent **không có** lớp này.
- **Cảnh báo phương pháp cốt lõi** (từ arXiv 2608.27734): DSR và PBO **không bắt được look-ahead leakage** — một strategy rò rỉ dữ liệu vẫn qua được test. Phải kết hợp guardrail cấu trúc (chống leakage) + deflate theo search intensity, không chỉ dựa vào test thống kê.
- **License**: pypbo là AGPL-3.0 (ràng buộc mạnh nếu dùng thương mại); vectorbt có Commons Clause (không được bán lại phần mềm chủ yếu là nó); mlfinlab một phần đã thương mại hóa. Kiểm tra license trước khi tích hợp.
- **Một số metadata chưa xác nhận được**: ngày commit cuối của quantevolve/pypbo/llm-strategy-gen; số sao chính xác của Roan2802/MT5-MCP, eslazarev/purged-cross-validation, yossiferoz/llm-strategy-gen; license của Qoyyuum/mcp-metatrader5-server, marketcalls/vectorbt-backtesting-skills, yossiferoz/llm-strategy-gen; và việc Hmz904/AgentAlphaAudit có phải code chính thức của paper Gençay hay không (thematic match mạnh nhưng chưa xác nhận tác giả).
- **Số sao GitHub biến động** theo thời gian; các con số trên là tại thời điểm khảo sát (9/2026).
- **MT5 build 6060 MCP / MetaEditor AI Assistant**: không xác minh được thông tin cụ thể trong phạm vi khảo sát này — cần kiểm tra trực tiếp release notes của MetaQuotes trước khi dựa vào.