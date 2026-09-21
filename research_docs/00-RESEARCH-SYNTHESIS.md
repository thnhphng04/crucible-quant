# Synthesis of 4 research reports — AI system for developing trading strategies

> This document synthesizes and cross-checks 4 research reports in the directory. Goal: extract points of convergence, points of contradiction, and the technical decisions that need to be finalized.
> Synthesis date: 2026-09-20

---

## 0. What the four reports cover

| File               | Topic                                            | Core question it answers                                                                       |
| ------------------ | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| [90](../research_docs_vi/90-NGUON-KIEN-TRUC-VA-REPO.md) | **Architecture & repo**                         | Is there any project with a ready-made 5-component architecture? If not, what should it be assembled from?           |
| [91](../research_docs_vi/91-NGUON-AI-AGENT-BACKTESTING.md) | **AI agents for backtesting (9/2025–9/2026)** | Do LLM trading agents actually generate alpha? What does the evidence say?                          |
| [92](../research_docs_vi/92-NGUON-THI-TRUONG-CHIEN-LUOC.md) | **Markets & strategies**              | Crypto / Forex / Stocks (+ Vietnam) — where should one start, and which strategies have evidence? |
| [93](../research_docs_vi/93-NGUON-AI-SINH-STRATEGY-MT5.md) | **AI building rule-based strategies (MT5)**    | Using an LLM to write EA/strategy code and self-backtest — how far can this go?                          |

The first two files take a **system/agent** perspective, file 3 takes a **market/alpha** perspective, and file 4 takes a **tooling/implementation** perspective. They complement rather than duplicate one another.

---

## 1. Six conclusions all 4 reports agree on

### 1.1. Overfitting is enemy number one — not finding a strategy

This is the throughline. None of the reports treats "finding a strategy" as the hard part. The summary from the MQL5 community (file 4): *"strategy is the hard part, code is the easy part"* — an LLM faithfully writes any logic, **including bad logic**.

### 1.2. The validation layer is the most missing part of the entire ecosystem

File 1 says it directly: the anti-overfitting layer **mostly exists only in papers and is very rarely implemented in code**. `tarsyang/quantevolve` does not have it. RD-Agent(Q) does not have DSR/PBO. This is both an industry gap and a **competitive advantage** if built in-house.

### 1.3. The LLM must not touch runtime

The pattern *"LLM never touches the trade"* (file 2, from ai-hedge-fund) and the requirement *"the final strategy must be deterministic and must NOT call an LLM at runtime"* (file 4). The LLM belongs only in the idea-generation and code-generation layers.

There is a concrete quantitative reason: BacktestBench shows that **Metrics Calculation is the LLM's "disaster zone"** — Qwen3 falls from 34.71% (235B version) to nearly 0% (4B version); Sharpe/Volatility have much lower pass rates than Win Rate/MaxDD. **KPI calculation must be handled by deterministic code.**

### 1.4. Current evidence is weak and mostly self-reported

- QuantEvolve "Sharpe > 1.5", MadEvolve "+0.62 to +1.83 OOS", RD-Agent(Q) "~2× ARR" — all **self-reported**, not independently audited.
- Alpha Arena Season 1 (real money): **4/6 models lost money**, GPT-5 lost 62.66%.
- StockBench: most LLMs outperform buy-and-hold **by very thin margins**, and only win in upturns while losing in downturns.
- FINSABER: FinMem/FinAgent **do not generate statistically significant CAPM alpha** when survivorship bias is controlled.

### 1.5. The more you search, the higher the probability of finding a fake "winner"

File 4: *"each added trial increases the probability of selecting a lucky strategy"*. File 2 puts it more sharply: *"agent productivity becomes a bias amplifier"* — the agent silently runs dozens of backtests before you have time to see a single number.

→ Mandatory consequence: **log EVERY trial** into a search ledger, and deflate by the **actual** number of trials, not the number of trials you report.

### 1.6. Backtests are for REJECTION, not tuning

López de Prado's principle (file 3): *"Backtesting is not a research tool. Feature importance is."*

---

## 2. The most important point: DSR and PBO are NOT enough

**This is the most valuable finding from cross-checking the 4 files**, and it revises the default recommendation of file 3 and file 4 themselves.

File 1 and file 2 both cite arXiv 2608.27734 (Gençay, 8/2026):

> A deliberate **"leaky oracle"** that leaks future data, achieving Sharpe 34.7 (design) / 51.5 (eval), **still survives Deflated Sharpe Ratio with DSR = 1.00**.

Meaning: statistical correction catches **selection bias** (selecting too many times), but **does not catch leakage** (using data that should not be allowed). These are two different classes of error.

**Practical conclusion — 3 layers are needed, not 1:**

| Layer                             | What it catches                                      | Tools                                                                                                                                       |
| -------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Structural guardrail**   | Look-ahead leakage                                    | Tool registry excludes future-looking functions; PIT data layer filters by *filing date* (not report period); fail-loud when data is missing |
| **Statistical correction** | Selection bias / winner's curse                       | DSR, PBO/CSCV, White's Reality Check, deflation by search ledger                                                                                |
| **Temporal isolation**     | Parametric leakage (LLM "remembers" prices in its weights) | Test window **after knowledge cutoff**; measure IS→OOS decay; forward/paper trading                                                             |

Layer 3 is a **completely new** problem that appears only with LLMs — see section 3.

---

## 3. Parametric look-ahead bias — an LLM-specific risk

File 2 spends substantial space on this problem, and it deserves its own section.

**Nature of the issue:** An LLM with a 2025 cutoff has "seen" 2010–2024 price movements during pretraining. The bias lives **inside the model weights**, not in the code pipeline → **code audits cannot detect it**.

**Evidence** (Lopez-Lira, Tang & Zhu, arXiv 2504.14765): GPT-4o remembers S&P 500 values accurately with ~0.01% error for dates before the cutoff, then its error "explodes" for dates after the cutoff.

**Quantifying the damage** (Profit Mirage, arXiv 2510.07920) — comparing 2021 (in-sample) vs 2024 (out-of-sample) under equivalent market conditions:

| System    | Sharpe decay | Total Return decay |
| ------------- | ------------ | ------------------ |
| QuantAgent    | 51.48%       | —                 |
| TradingAgents | 55.68%       | 50.18%             |
| FinCON        | 62.23%       | —                 |
| FinMem        | —           | 71.85%             |

→ **Diagnostic threshold: if Sharpe/return decay is > 50% when moving to post-cutoff data → suspect memorization, not alpha.**

**Mitigations, in pragmatic order:**

1. Choose a test window entirely after the cutoff — simplest, at the cost of a shorter window
2. Entity anonymization (mask company names)
3. Temporal RAG (strict timestamping)
4. FinCAD (arXiv 2605.24564) — subtract the "memory prior" logits from the context logits, without retraining
5. PIT models (DatedGPT) — still immature, only a 1.3B model

**Note for this project:** if the chosen direction is "LLM generates deterministic strategy code" (file 1 + file 4) rather than "LLM makes trading decisions" (file 2), parametric leakage risk is **much lower** — because the LLM only writes rules, while those rules run on data you control. But it is **not zero**: the LLM can still propose a rule it "knows" worked in a specific period.

---

## 4. Five-component architecture and convergent stack

All 4 files point to the same architecture. The merged version:

```
[1] Idea layer       → LLM proposes TA rules (indicator, entry/exit, SL/TP, sizing)
         ↓
[2] Code generation  → Python (vectorbt/backtrader) or MQL5 EA
         ↓
[3] Compile/validate → feed errors back to the agent for PATCHing (not full rewrite)
         ↓
[4] Backtest engine  → deterministic, realistic cost
         ↓
[5] Optimizer        → Optuna + walk-forward, multi-objective
         ↓
[6] Evaluator        → DSR/PBO/Monte Carlo/random baseline → decide whether to iterate or stop
         ↓
[7] Trial ledger     → log EVERY trial + untouched frozen holdout
```

**Recommended stack** (file 1, cross-checked with file 4):

| Component         | Primary choice                                                        | Alternatives                                    | Notes                                                |
| -------------------- | ------------------------------------------------------------------------ | -------------------------------------------- | ------------------------------------------------------- |
| Agent-loop           | **LangGraph** (custom) → **OpenEvolve** (phase 2)     | ShinkaEvolve, CodeEvolve                     | OpenEvolve ~6.3k stars, Apache-2.0, implements AlphaEvolve |
| Engine               | **vectorbt**                                                       | backtrader, NautilusTrader, MT5 Tester       | vectorbt has Commons Clause — check license        |
| Optimizer            | **Optuna** (TPE/GP)                                                | Freqtrade Hyperopt (NSGAIII)                 | Better than MT5's default genetic optimizer                  |
| **Validation** | **`eslazarev/purged-cross-validation`** (PyPI `purgedcv`, MIT) | skfolio (BSD-3), pypbo (AGPL — use caution) | Includes PBO + DSR + CPCV + WalkForward in one lib     |
| MT5 bridge           | `PHUICMT/mcp-mt5` (MIT)                                                | `Roan2802/MT5-MCP`                         | Only when EA/MQL5 is mandatory                             |

**Conclusion on whether something ready-made exists:** no project fully matches all 5 components. The closest in code is `tarsyang/quantevolve`, but it **completely lacks the validation layer** (small repo, ~13 commits, only calculates in-sample PnL/Sharpe/MaxDD).

> ⚠️ **Correction (repo checked directly, 2026-09-20):** the original report `93` wrote *"QuantEvolve — Open-source code (fork of OpenEvolve)"*. **Incorrect.** [`tarsyang/quantevolve`](https://github.com/tarsyang/quantevolve) **is not the code for the QuantEvolve paper** (arXiv 2510.18569):
>
> | | Paper 2510.18569 | tarsyang/quantevolve |
> |---|---|---|
> | LLM | Qwen3 ensemble | Google Gemini |
> | Engine | Zipline + QuantStats | custom evaluator (Binance) |
> | MAP-Elites / feature map | Yes | **Not mentioned** |
> | Paper citation | — | **None** |
>
> **The official code for the paper could not be found anywhere**, including the "evolved strategies" dataset that the abstract promised to release. (alphaXiv also links incorrectly to this repo.) Report `90` was correct to warn that these are different things. The best reference pattern for *embedding validation into the loop* is `yossiferoz/llm-strategy-gen` (hard OOS wall + Monte Carlo trade-shuffle + 100-strategy random-entry baseline) — even though it is only an educational prototype.

---

## 5. Decision-threshold table (aggregated from all 4 files)

This section is immediately usable. Apply these as **hard gates**, not suggestions.

| Threshold                              | Value                                                                                    | If violated                                      | Source                 |
| ------------------------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------- | ---------------------- |
| **PBO** (CSCV)                  | < 0.5, ideally < 0.2                                                                     | Reject the strategy, **do not fine-tune**        | Bailey-Borwein-LdP-Zhu |
| **Deflated Sharpe**             | **> 0.95** (DSR is a 0–1 PROBABILITY, deflated by actual total trials) | Reject                                              | Bailey & LdP 2014      |
| **Factor t-stat**          | > 3.0 (not 2.0)                                                                     | Treat as noise                                     | Harvey-Liu-Zhu 2016    |
| **OOS Sharpe / IS Sharpe**      | ≥ 50%                                                                                       | Suspect overfit / alpha decay                         | McLean-Pontiff         |
| **Sharpe decay post-cutoff**    | < 50%                                                                                        | Suspect **memorization** (not alpha)    | Profit Mirage          |
| **Modeling quality (MT5)**      | ≥ 90%                                                                                       | Do not trust backtests that depend on intrabar SL/TP     | MT5 docs               |
| **Spearman(IS rank, OOS rank)** | High                                                                                          | Low → harness has not controlled leakage | file 2                 |
| **White's Reality Check**       | Low p-value                                                                                      | High p-value → strategy is noise                        | White 2000             |

**Additional rules:**

- Strategy depends on **one single trade** (e.g., basis carry) → assume short shelf life, prepare for model decay
- Edge disappears when **costs are tightened** or the **backtest model changes** → reject, return to the beginning
- Do not allocate capital based on backtests. Forward/paper test for at least 4–6 weeks (file 4), ideally several months covering at least **one regime reversal** (file 2)

---

## 6. Market selection (file 3)

**Evidence ranking (strong → weak, after costs):**

1. **Time-series momentum / trend following on multi-asset futures** — strongest peer-reviewed evidence (positive returns in every decade since 1880, "crisis alpha" in 8/10 major crises)
2. Cross-sectional momentum — strong but subject to "momentum crashes"
3. Crypto TS momentum + funding/basis carry — good emerging evidence, **but basis has compressed heavily in 2024–2026**
4. FX carry — Sharpe 0.4–0.9, **strong negative skew** (crash risk)
5. Pure TA rules — **mixed, unresolved** (Deprez-Frömmel 2024 finds OOS edge, Hudson-Urquhart does not)
6. Stat arb / market making — highly effective but requires institutional infrastructure

⚠️ **Realistic after-fee Sharpe at the portfolio level is only around 0.3–0.8** — much lower than marketing claims.

**Recommendation: start with crypto.** Reasons: free/cheap 24/7 data via ccxt, no legal capital barrier, the clearest academic evidence for TS momentum + funding carry, and the lowest individual-access barrier.

**On the Vietnamese market** (if targeting the "home field"):

- ✅ **VN30 index futures are the best choice** — **T+0**, allow short exposure through futures positions, ±7% price limit, volume of ~225k contracts/session
- ❌ **Avoid single stocks at the beginning**: price limits create "locks" (orders pile up with no counterparty → **severe backtest distortion**, a phenomenon that does not exist in the US market), T+2, no short-selling yet
- 📅 The regulatory framework is changing quickly: FTSE upgrade effective **2026-09-21**; short-selling/T+0 roadmap **2026–2028**; CCP expected **Q1/2027**
- Tooling: vnstock + broker APIs (SSI FastConnect, TCBS)
- ⚠️ The Sharpe 3.96 figure in one 2025 Vietnam study is an **in-sample backtest**, likely overfit

**Retail statistics to know before starting:** 74–89% of retail CFD/forex accounts lose money (ESMA); only **~7%** of prop-firm challenge buyers ever receive a payout (14% pass × 45% of those receive money).

---

## 7. Merged roadmap

**Phase 0 — Finalize the evaluation harness FIRST, agent SECOND** (most important, often skipped)

- Build a PIT data layer, fail-loud when data is missing
- Choose a test window after the knowledge cutoff
- Build a trial ledger (SQLite/Postgres) + frozen holdout
- ✅ *Gate:* the harness runs and can **reject** an intentionally inserted leaky oracle

**Phase 1 — Pure-Python framework** (2–4 weeks)

- LangGraph + vectorbt + Optuna + `purgedcv`. Package with FastAPI + Docker
- Borrow the loop spirit from `yossiferoz/llm-strategy-gen`
- Start with **TS momentum** on crypto (strongest evidence, simple mechanism, suitable for rule-based strategies)
- ✅ *Gate:* ≥50 automated iterations, every strategy passes walk-forward + DSR/PBO, every trial is fully logged

**Phase 2 — Evolutionary search** (2–3 weeks)

- Replace the custom loop with **OpenEvolve**; evaluator = vectorbt + fitness **deflated by trial count**
- 🔑 **Key point: put DSR/PBO inside the fitness function itself, not only in the final report**
- ✅ *Gate:* winner survives frozen OOS holdout + PBO < 0.5 + **DSR > 0.95** after deflation

**Phase 3 — MT5/EA branch** (optional, 2–4 weeks)

- Do this only if an MQL5 EA or real broker tick data is **mandatory**
- MT5 is Windows-only → run in a separate Windows Docker/VM, isolated from the main Python service
- MT5 has no built-in DSR/PBO → export returns back to Python to run them

**Phase 4 — Independent audit + forward test**

- Waterfall: reported → reproduced → PIT data → lag sensitivity → cost sensitivity → frozen OOS
- Demo/paper trading on the actual broker, with real spread/commission
- Final strategy must be **deterministic and must not call an LLM**

---

## 8. Pitfalls by layer

**Code layer (LLM-generated):**

- MQL5: mixing MQL4/MQL5 syntax, deprecated `iRSI` signature, retcode 10016 (SL/TP inside `SYMBOL_TRADE_STOPS_LEVEL`), ignoring `SYMBOL_VOLUME_MIN/STEP`, hard-coding symbols without handling suffixes
- Look-ahead & repainting: using an unclosed candle, repainting indicators
- 📌 Real test: ChatGPT generated ~150 lines of MQL5 → **3 errors + 2 warnings on the first compile**, ~35 minutes of debugging (each fix generated a new bug), and the finished EA still lost −4.2% in backtest / −9.4% live over 6 weeks

**Backtest layer:**

- MT5: choose "Every tick based on real ticks"; commission/swap come from the **symbol specification** (not from EA code) — leaving them at 0 will misreport; fixed spread is "fictional" for ECN accounts; tester slippage is only random delay and **does not simulate real gaps**
- Crypto: backtesting funding arb requires **4 data streams** (OHLCV + 8h funding + open interest + liquidation); Sharpe calculated on raw funding without subtracting leverage decay/fees/slippage/basis risk is "fantasy"
- Stocks: survivorship bias (must use historical constituent lists including delisted stocks), PIT fundamentals are expensive

**Operations layer:**

- ⚠️ **MCP has no built-in authentication** — requires firewall/reverse proxy/SSH tunnel; many MCP servers execute real orders immediately → **always test on demo first**
- Non-determinism: fix seeds, use low temperature, cache API responses by (ticker, date)
- Costs: limit decision frequency (daily rather than intraday) unless doing HFT

**Licenses to check before integration:**

- `pypbo`: AGPL-3.0 — strong obligations if used commercially
- `vectorbt`: Apache-2.0 + **Commons Clause** — cannot resell software for which it is a main component
- `mlfinlab`: partly commercialized

---

## 9. Questions the reports do not answer

> ℹ️ *Update 21 Sep 2026:* the status of these 4 questions is now tracked in the **decision register** in [Architecture_Design.md](Architecture_Design.md) §10 (D1–D4) — the single source. Questions 1, 2 and 3 are decided (Q3: no MQL5 EA, live through NautilusTrader); Q4 remains open.

1. **Which market is this project targeting?** File 3 recommends crypto, file 4 focuses on MT5/forex. The two branches have materially different stacks → this must be settled before writing the first line of code.
2. **What output is needed?** Rule-based TA strategy (→ vectorbt/OpenEvolve) or ML factor (→ RD-Agent + Qlib)? The two directions do not share the same architecture.
3. **Must it be an MQL5 EA?** If not → drop the MT5 branch entirely, save 2–4 weeks, and avoid the Windows-only constraint.
4. **What is the threshold for "good enough to allocate capital"?** The reports provide technical gates but not economic thresholds (minimum capital, tolerable drawdown, payback period).

---

## 10. Warnings about this document set itself

- Many figures are **self-reported** from papers and have not been independently audited
- Many 2026-generation arXiv IDs (26xx.xxxxx) are **very new preprints, some not yet peer-reviewed**
- GitHub star counts are from the survey time (9/2026) and will change
- Some report metadata is **self-admittedly unverified**: last commit dates for some repos, licenses for some projects, and whether `Hmz904/AgentAlphaAudit` is the official code for Gençay's paper
- The reports focus on research/open-source; **internal systems at large quantitative funds are almost never published**, so the picture of "commercial state of the art" may be skewed

---

## One-sentence summary

> The hard part is not getting AI to write a strategy — the hard part is **proving that strategy is not luck**. Use AI as a productivity layer, not as the layer that decides the edge. Statistical discipline is the most reliable edge.
