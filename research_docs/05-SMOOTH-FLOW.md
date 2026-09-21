# Smooth Flow: Free Data -> Backtest -> Live

> Seam-free pipeline, $0 data, one strategy file running end to end.
> Date: 2026-09-20 · Related: [00-RESEARCH-SYNTHESIS](00-RESEARCH-SYNTHESIS.md) · [04-SCHOOL-B-EFFICACY](04-SCHOOL-B-EFFICACY.md)

---

## 0. The Problem to Solve: "Seams"

The typical beginner setup:

```
pandas/Jupyter          vectorbt            ccxt script
(data tinkering)  ->   (backtest)    ->   (live bot)
     ↑                    ↑                   ↑
  rewrite              rewrite              rewrite
```

**Every arrow is a logic rewrite, and that is where bugs creep in.** When a strategy backtests at Sharpe 1.5 and then loses money live, the reason is often not overfitting but **live code that differs from backtest code**.

**Smooth flow = delete all those arrows.** One strategy file runs through every stage.

---

## 1. Platform Choice: Two Candidates That Can Remove the Seam

|                                     | **Freqtrade**           | **NautilusTrader**                              |
| ----------------------------------- | ----------------------------- | ----------------------------------------------------- |
| Same strategy file backtest->live  | ✅                            | ✅                                                    |
| Free data                      | ✅ ccxt                       | ✅ ccxt/adapter                                       |
| Built-in data download              | ✅`download-data`           | 🟡 write it yourself                                          |
| Built-in backtest                   | ✅                            | ✅                                                    |
| **Built-in optimizer**        | ✅**hyperopt (Optuna)** | ❌ assemble yourself                                           |
| Dry-run (paper)                     | ✅                            | ✅                                                    |
| Live                                | ✅ 14+ exchanges                   | ✅ Binance, Bybit, IB, Databento, Betfair, Polymarket |
| Monitoring UI                       | ✅ web UI                     | 🟡                                                    |
| **Multi-asset beyond crypto** | ❌**crypto only**       | ✅**futures, equities via IB/Databento**        |
| Real microstructure                | 🟡                            | ✅ order book, queue position, partial fill           |
| Learning curve                      | 🟢 low                      | 🔴 high                                                |

**Each side's technical commitment:**

- **Freqtrade:** *"The backtesting engine feeds historical OHLCV data into the same `populate_*` hooks; the live freqtrade trade process pulls real-time candles via ccxt and triggers the same hooks."*
- **NautilusTrader:** *"The DataEngine guarantees 100% identical data handling in both backtesting and live trading"* — deploy *"with no code changes"*.

### 🎯 Recommendation: Start With Freqtrade

Three reasons, with the third being the most important:

1. **Batteries included** — download-data, backtest, hyperopt, dry-run, live, UI: one CLI, nothing extra to assemble
2. **Hyperopt already uses Optuna** — the optimizer recommended in [00-RESEARCH-SYNTHESIS](00-RESEARCH-SYNTHESIS.md), with no additional installation
3. 🔑 **Its rigid structure is an ADVANTAGE for code-generating agents.** A Freqtrade strategy always has exactly three hooks: `populate_indicators` / `populate_entry_trend` / `populate_exit_trend`. This is a **fixed template for an LLM to fill in** instead of letting the LLM freely invent the file structure, which is an endless source of bugs.

**When to move to NautilusTrader:** when you need to go beyond crypto to get real breadth (multi-asset futures — see [04-SCHOOL-B-EFFICACY](04-SCHOOL-B-EFFICACY.md) §5), or when you need order-book simulation.

---

## 2. Data: Why $0 Is Enough

```bash
freqtrade download-data \
  --exchange binance \
  --pairs BTC/USDT ETH/USDT SOL/USDT ... \
  --timeframes 1h 4h 1d \
  --timerange 20200101-
```

- **No API key required** — Binance historical klines are public endpoints
- Saved as local files, reusable indefinitely for backtests and hyperopt
- The same `ccxt` will pull real-time candles live -> **no data-source mismatch**

> ⚠️ **Free data does not mean sufficient data.** Backtesting funding-rate carry needs four streams (OHLCV + 8h funding + open interest + liquidation), while Freqtrade only gives you OHLCV. If your strategy touches funding, you must pull the extra data yourself with ccxt.

---

## 3. Full Flow — 8 Steps, One Strategy File

```
┌─ [1] DOWNLOAD ────────────────────────────────────────────┐
│  freqtrade download-data --timerange 20200101-            │
│  → $0 data, reusable forever                              │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [2] AGENT GENERATES STRATEGY ────────────────────────────┐
│  LLM fills in the 3-hook template                         │
│  → user_data/strategies/GenStrat_007.py                   │
│  🔒 Gate: AST similarity vs strategy zoo (from AlphaAgent) │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [3] IN-SAMPLE BACKTEST ──────────────────────────────────┐
│  freqtrade backtesting --strategy GenStrat_007 \          │
│    --timerange 20200101-20240101                          │
│  🔒 Gate: does the edge survive stricter fees? No → reject │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [4] HYPEROPT (Optuna) ───────────────────────────────────┐
│  freqtrade hyperopt --strategy GenStrat_007 \             │
│    --epochs 200 --hyperopt-loss SharpeHyperOptLoss        │
│  📝 WRITE ALL 200 trials to the ledger                    │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [5] VALIDATION LAYER ⚠️ YOU MUST BUILD THIS YOURSELF ────┐
│  export returns → purgedcv                                │
│  → DSR (deflate by ACTUAL total trial count)              │
│  → PBO via CPCV                                           │
│  🔒 Gate: PBO < 0.5 AND DSR > 0.95 → only then continue   │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [6] FROZEN OOS HOLDOUT ──────────────────────────────────┐
│  freqtrade backtesting --timerange 20240101-20260101      │
│  🔒 Run EXACTLY ONCE. Once you look, it is burned.         │
│  🔒 Gate: OOS Sharpe ≥ 50% of IS Sharpe                   │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [7] DRY-RUN (paper) ─────────────────────────────────────┐
│  freqtrade trade --dry-run                                │
│  🔒 Gate: 4–6 weeks minimum, real spreads/fees            │
└──────────────────────────┬────────────────────────────────┘
                           ↓
┌─ [8] LIVE ────────────────────────────────────────────────┐
│  freqtrade trade                                          │
│  → start with the smallest feasible size                  │
└───────────────────────────────────────────────────────────┘
```

**Steps 2 -> 8: THE SAME `GenStrat_007.py` FILE.** Not a single line is rewritten.

---

## 4. ⚠️ Two Things Freqtrade Does NOT Handle for You

This is the most important part of this document. Freqtrade makes the flow smooth, **but it does not provide an anti-overfitting layer** — exactly as [00-RESEARCH-SYNTHESIS](00-RESEARCH-SYNTHESIS.md) §1.2 concludes about the whole field.

### 4.1. 🔴 Hyperopt Is an Overfit Generator

`--epochs 200` = **200 trials**. Choosing the best epoch from 200 attempts without deflation is precisely the trap described in synthesis §1.5.

And if an agent runs 50 strategies × 200 epochs = **10,000 actual trials**, not 200.

**Mandatory:** the ledger must count the **system-wide cumulative total of trials**, not trials per run.

```python
# Pseudo — step 5
returns = load_freqtrade_trades(backtest_result)
n_trials = ledger.total_trials()        # CUMULATIVE, do not reset

dsr = deflated_sharpe_ratio(returns, n_trials=n_trials)
pbo = probability_of_backtest_overfitting(is_matrix, oos_matrix)

if pbo >= 0.5 or dsr <= 0.95:    # DSR is a 0–1 probability, not a Sharpe value
    ledger.record(strategy, verdict="REJECT")
    return  # DO NOT tweak, DO NOT retry
```

### 4.2. 🔴 No Structural Guardrail Against Look-Ahead

Freqtrade has basic lookahead warnings, but an LLM can still generate code that sees the future (using an unclosed candle, repainting indicators).

And remember the lesson from arXiv 2608.27734: **DSR/PBO DO NOT catch leakage** — a leaky oracle with Sharpe 34.7 still passes with DSR = 1.00. You need a separate structural guardrail:

- Only allow the agent to use a whitelist of indicators verified not to repaint
- Reject code containing `.shift(-n)` or future index access
- Install a **fake leaky oracle** in the harness — if the pipeline does not reject it, the pipeline is broken

---

## 5. Proposed Directory Structure

```
TradingProject/
├── research_docs/                ← research documents
├── user_data/
│   ├── data/binance/              ← downloaded data ($0)
│   ├── strategies/
│   │   ├── _template.py           ← 3-hook template for the LLM to fill
│   │   ├── _zoo/                  ← strategy zoo for AST similarity
│   │   └── GenStrat_*.py          ← generated by the agent
│   └── hyperopt_results/
├── pipeline/
│   ├── agent_loop.py              ← LangGraph: generate → backtest → read → loop
│   ├── ledger.py                  ← SQLite: EVERY trial
│   ├── validation.py              ← purgedcv: DSR/PBO/CPCV
│   ├── guardrail.py               ← AST similarity + lookahead check
│   └── leaky_oracle.py            ← fake strategy for testing the harness
└── config.json                    ← Freqtrade config
```

---

## 6. Roadmap

**Week 1 — Build the harness FIRST, agent SECOND**

This is the order emphasized in [00-RESEARCH-SYNTHESIS](00-RESEARCH-SYNTHESIS.md) §7, and it is often done backwards.

```bash
pip install freqtrade purgedcv
freqtrade create-userdir --userdir user_data
freqtrade download-data --exchange binance \
  --pairs BTC/USDT ETH/USDT SOL/USDT BNB/USDT XRP/USDT \
  --timeframes 1h --timerange 20200101-
```

Hand-write **one simple strategy** (EMA crossover) -> run all 8 steps manually once.
✅ *Week 1 gate:* the harness **successfully rejects `leaky_oracle.py`** when you deliberately install it.

**Weeks 2–3 — Ledger + validation layer**
Build `ledger.py` + `validation.py`. Re-run the week 1 strategy through DSR/PBO.
✅ *Gate:* every backtest is written to the ledger, with no bypass path.

**Weeks 4–6 — Agent loop**
LangGraph: LLM fills template -> run Freqtrade CLI -> parse result -> loop.
✅ *Gate:* ≥50 automated iterations, each passing the full gate sequence.

**Week 7+ — Expand breadth**
Increase the whitelist to 15–30 **low-correlation pairs**.
⚠️ Remember: 30 altcoins ≈ 1 bet (all follow BTC). Mix in less-correlated pairs.

**After that — move beyond crypto**
To get real breadth (Sharpe 0.4 -> 1.6), you need multi-asset futures -> **migrate to NautilusTrader** + Norgate ($270/year).

---

## 7. Trade-Offs to Know Up Front

| Gain                       | Cost                                                         |
| ----------------------------- | ------------------------------------------------------------ |
| $0 data                  | OHLCV only — no funding/OI/liquidation                  |
| Smooth, seam-free           | Locked into the Freqtrade structure                              |
| Built-in hyperopt (Optuna)        | **Hyperopt becomes an overfit machine if you do not deflate** |
| Good template for LLM codegen | Harder to express overly complex logic                       |
| Live immediately on 14+ exchanges            | **Crypto only** -> low breadth ceiling                 |
| Fast path to end-to-end         | Rougher microstructure simulation than Nautilus                 |

**The biggest pain point:** crypto-only means your Sharpe ceiling is limited because every coin is correlated with BTC. [04-SCHOOL-B-EFFICACY](04-SCHOOL-B-EFFICACY.md) shows that breadth is the decisive variable — and crypto gives high *nominal* breadth but low *effective* breadth.

-> Treat Freqtrade as a **launchpad for building and debugging the agent loop at $0 cost**, not the destination.

---

## 8. Sources

- [Freqtrade — Backtesting](https://www.freqtrade.io/en/stable/backtesting/) · [Hyperopt](https://www.freqtrade.io/en/stable/hyperopt/) · [Strategy Customization](https://www.freqtrade.io/en/stable/strategy-customization/) · [Bot Basics](https://www.freqtrade.io/en/stable/bot-basics/) · [Utility Sub-commands](https://www.freqtrade.io/en/stable/utils/)
- [NautilusTrader — Live Trading](https://nautilustrader.io/docs/latest/concepts/live/) · [Adapters](https://nautilustrader.io/docs/latest/concepts/adapters/) · [Bybit integration](https://nautilustrader.io/docs/nightly/integrations/bybit/)
- [Freqtrade 2026 — Installation &amp; Usage Tutorial](https://addrom.com/freqtrade-2026-complete-installation-guide-and-practical-usage-tutorial-for-the-leading-open-source-cryptocurrency-trading-bot/)

---

## One-Sentence Summary

> Freqtrade gives you a seam-free flow on $0 data — but it **does not have an anti-overfitting layer**, and `--epochs 200` becomes an overfit generator if you do not count trials. The part Freqtrade handles is the easy part; the part you must build yourself (ledger + DSR/PBO + guardrail) is the decisive part.
