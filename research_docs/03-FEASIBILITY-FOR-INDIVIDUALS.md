# Feasibility Assessment: Two Schools for Individual Investors

> Comparing **School A — Cross-sectional factor + ML** (RD-Agent(Q), AlphaAgent — see [08-LLM-QUANT-RESEARCHER](08-LLM-QUANT-RESEARCHER.md)) with **School B — Rule-based TA on one/few instruments** (the project's original target).
> Assessment date: 2026-09-20 · Web research included · Related: [00-RESEARCH-SYNTHESIS](00-RESEARCH-SYNTHESIS.md)

---

## TL;DR — A Counterintuitive Conclusion

1. **School A is far more feasible than it was 5 years ago** — the two biggest barriers collapsed in 2025–2026: PIT survivorship-free data now costs only **$29–49/month** (previously a $50k+ license), and the **$25,000 PDT rule was eliminated from 2026-06-04**. Report `92` in this folder is **outdated** on this point.

2. **But A's remaining barrier is CAPITAL, not knowledge or tools.** Cross-sectional strategies need 50–100 simultaneous positions. Below ~$50k, each position is too small to clear lot-size constraints and fixed costs. This is a hard barrier; engineering cannot work around it.

3. **The most feasible configuration, missed by all 4 original reports: cross-sectional on CRYPTO PERPS.** It gives you School A's breadth while requiring **no** short-selling permission, no PIT fundamentals data, no $50k, and no PDT exposure. This is the only "back door" into School A for small capital.

4. ⚠️ **But the evidence does not support crypto cross-sectional:** research from 2020–10/2025 shows it **underperforms** time-series momentum on risk-adjusted return, with a **55% max drawdown**, because **coins are too highly correlated** → effective breadth is far lower than the nominal coin count.

5. **School B has a lower ceiling but a higher floor.** Realistic Sharpe is 0.3–0.8, but it can run from $1,000, does not depend on infrastructure, and is the only path if you need an MQL5 EA.

---

## 1. Why Cross-sectional Produces Higher Sharpe — and Why Individuals Struggle to Capture It

**Fundamental Law of Active Management:**

```
IR ≈ IC × √breadth
```

This is the whole story. RD-Agent(Q) reaches IR 1.7382 with IC of only 0.0532 — a very thin edge multiplied by **300 symbols × 250 sessions/year**.

| | Nominal breadth | Consequence |
|---|---|---|
| RD-Agent(Q), CSI 300 | 300 symbols × 250 sessions | IR 1.74 with IC 0.05 |
| AlphaAgent, S&P 500 | 500 symbols × 250 sessions | IR 1.05 with IC 0.0056 |
| **Rule-based TA, 1 symbol** | **1 × number of entries** | **Sharpe ceiling 0.3–0.8** |

> 🔑 **This is the mechanical reason the two schools have different performance ceilings.** It is not because factors are "smarter" than TA rules, but because they place more bets.

**The trap: *effective* breadth is always smaller than nominal breadth.** The formula only holds when bets are independent. In reality, equities share market and sector factors; crypto is worse — almost every altcoin follows BTC. Crypto cross-sectional research explicitly says lower profitability is *"partly due to high correlations among cryptocurrencies"*.

---

## 2. School A — Practical Barriers for Individuals

### ✅ 2.1. Two Barriers Have Collapsed (2025–2026)

**a) PIT survivorship-free data is now cheap**

| Provider | Price | Notes |
|---|---|---|
| Tradevo Data | **$29/month** | PIT US fundamentals, JSON endpoint has `as_of` |
| Valuein | from **$49/month** | PIT survivorship-free SEC fundamentals |
| Sharadar | retail tier | 16,000+ companies including delisted symbols, fundamentals from 12/1997, ~150 indicators |
| Norgate Data | retail tier | Survivorship-bias-free, EOD |
| *WRDS/Compustat* | *$50k+* | *Academic standard — no longer mandatory* |

→ The "expensive data" barrier described in report `92` is **no longer true**.

**b) The PDT rule has been eliminated**

From **2026-06-04**, under the FINRA Rule 4210 change, the $25,000 minimum capital requirement and the formal "Pattern Day Trader" designation **no longer exist**. They have been replaced by capital requirements proportional to actual intraday exposure; the general minimum remains **$2,000** under current margin rules.

→ Report `92` says *"PDT rule (US: requires $25,000...)"* — **this needs updating**.

### 🔴 2.2. The Remaining Barrier: CAPITAL and Position Structure

This is the real barrier, and it **cannot be bypassed**.

Cross-sectional strategies need to hold 50–100 positions simultaneously. With $10,000 split across 100 positions = **$100/position**:

- It cannot clear the minimum lot size in many markets (China A-shares: 100-share lots — one ¥50 stock requires ¥5,000 ≈ $700/lot → **100 positions require ~$70,000**)
- Minimum fixed costs per order consume the profit
- Odd lots face worse spreads

**Estimated practical capital thresholds:**

| Capital | Is School A feasible? |
|---|---|
| < $10k | ❌ No (except crypto perps) |
| $10k–50k | 🟡 Only with a small universe (20–30 symbols), lower breadth → lower IR |
| > $50k | ✅ Enough to hold 50–100 meaningful positions |

### 🔴 2.3. Transaction Costs with High Turnover

Both RD-Agent(Q) and AlphaAgent **rebalance daily**. Research on factor-investing costs:

- Median **institutional** transaction cost is **6.24 bps per rebalance** on NYSE — higher for individuals
- When proportional transaction costs reach **0.5%**, the optimal choice is to **keep all capital in a bank account instead of trading**
- Once costs are included in optimization, investors should rebalance only **~15% of sessions** — the turnover penalty naturally pushes the strategy toward buy-and-hold
- Hidden costs (market impact) *"may substantially erode a strategy's expected excess returns"*

**Direct implication:** if you replicate RD-Agent(Q) with real retail costs, **reduce rebalance frequency to weekly or biweekly**, accepting a lower IR. Daily rebalancing is an institutional luxury.

### 🔴 2.4. Short Side

RD-Agent(Q) uses **long-short**. For individuals:

- **US:** shorting is available, but borrow fees range from **0.25%/year** (easy-to-borrow large caps) to **100%+/year** (hard-to-borrow). The very stocks the model ranks worst are often expensive to short
- **China A-shares:** heavily restricted
- **Vietnam:** ❌ **no short-selling yet** (roadmap 2026–2028)

→ If you run long-only, you lose half of the long-short model's alpha.

---

## 3. The Back Door: Cross-sectional on CRYPTO PERPS

This is the only configuration that lets small-capital individuals access School A, and **none of the reports in the folder mention it**.

| A's barrier | How crypto perps solve it |
|---|---|
| Need $50k+ | ✅ Perps allow small positions with leverage |
| Cannot short | ✅ **Shorting is native** — no borrowing, no borrow fee |
| PIT data is expensive | ✅ Only OHLCV is needed, **free via ccxt** |
| PDT / trading hours | ✅ 24/7, no rule constraint |
| Small universe | ✅ 100–200 liquid perp pairs |

**But the costs are harsh:**

- Taker fee ~**4 bps/order** (standard assumption in perp research)
- Funding rate is charged **every 8 hours**, compounding if held for long periods
- ⚠️ **Thin altcoin liquidity: 1–3% spreads** — *"instantly dwarfs a 0.1% trading fee"*
- Small altcoins need **2–5%** slippage tolerance, versus 0.5–1% for large pairs
- Retail roundtrip cost can reach **6.45%** on some platforms after spread and slippage

### ⚠️ And the Evidence Does Not Support It

Crypto momentum research (2020-01-01 – 2025-10-31):

- Cross-sectional momentum *"delivered only modest long-term profitability"*
- **Time-series momentum achieved 31.96%/year and outperformed cross-sectional on a risk-adjusted basis**
- Cross-sectional had **55.0% max drawdown** and lower profits, *"partly due to high correlations among cryptocurrencies"*
- Crypto carry: Sharpe 6.45 over the full 2020–2025 sample but **fell to 4.06 in 2024 and turned negative in 2025** — classic model decay
- ⚠️ These studies *"does not incorporate transaction costs, slippage, funding rates, or liquidity constraints"* — meaning the real numbers are worse

**Conclusion on this back door:** feasible from an *infrastructure* perspective, but the evidence says **time-series momentum remains better than cross-sectional in crypto**. If you plan to trade crypto, cross-sectional is not a strong enough reason to abandon time-series.

---

## 4. School B — Rule-based TA

### ✅ Pros

| | |
|---|---|
| Minimum capital | **$1,000** or lower |
| Universe | 1 symbol is enough |
| Data | Free (ccxt, broker feed) |
| Runtime | A few indicator calculations — no model inference needed |
| Export MQL5 EA | ✅ Yes |
| Readability/debugging | ✅ Entire logic is transparent |
| Explicit SL/TP risk | ✅ Clear risk control |

### ❌ Cons

**Low performance ceiling because breadth = 1.** Consensus from the 2026 quant community:

> *"A genuine, tradeable, diversified quant strategy with a long-run Sharpe between 0.4 and 0.8 is a good outcome, and backtests promising 2.0 or better are almost always describing the in-sample period."*

> *"A realistic Sharpe above 3 in any strategy at 2026 should make you suspicious of methodology errors (look-ahead bias, survivorship, multiple-testing)."*

**Short-horizon is decaying:** *"Short-horizon trend models that once worked well have become harder to monetize as markets have grown faster and more efficient, while slower signals remain more resilient."*

**But slow signals still live:** monthly data from 1979–2025 shows that a simple trend filter (10-month MA or 12–1 momentum) preserves ~10% CAGR, **reduces max drawdown from −54% to ~−20%**, and raises Sharpe **from 0.72 to 0.93**.

> 📌 Note: the 0.93 figure is for an **equity index**, not single-instrument intraday. And it is an extremely simple trend filter — no agent, no LLM needed.

### 📊 Survival Conditions for Individuals

Consensus on retail systems that actually survive long term:

> *"Quant systems that survive typically feature low frequency, not very sensitive to exact execution time, simple enough to run manually before/after work, and are boringly stable."*

---

## 5. Summary Assessment Table

| Criterion | **A — Cross-sectional equity** | **A′ — Cross-sectional crypto** | **B — Rule-based TA** |
|---|---|---|---|
| **Minimum capital** | 🔴 ~$50k | 🟢 $2–5k | 🟢 $1k |
| **Data cost** | 🟢 $29–49/month | 🟢 Free (ccxt) | 🟢 Free |
| **Short side** | 🟡 Borrow 0.25–100%/year | 🟢 Native (perp) | 🟢 Native |
| **Transaction costs** | 🔴 High (daily turnover) | 🔴 Alt spreads 1–3% | 🟢 Low if low-freq |
| **Legal barriers** | 🟢 PDT removed (6/2026) | 🟢 None | 🟡 Broker B-book |
| **Theoretical Sharpe ceiling** | 🟢 1.0–1.7 | 🟡 Lower than TS momentum | 🔴 0.3–0.8 |
| **Academic evidence** | 🟢 Strong (but alpha decay) | 🟡 Weaker than TS momentum | 🟡 Mixed |
| **Infrastructure complexity** | 🔴 High | 🟡 Medium | 🟢 Low |
| **Export MQL5 EA** | ❌ | ❌ | ✅ |
| **Interpretability** | 🔴 Model is a black box | 🔴 Same as above | 🟢 Full |
| **Time to prototype** | 🔴 2–3 months | 🟡 1–2 months | 🟢 2–4 weeks |

---

## 6. Recommendations

### 🎯 With Capital < $10,000

**Choose B, but add some breadth.** Do not try to do cross-sectional equity — the capital barrier is hard.

But **do not stop at 1 symbol**: run the same TA rule across a **basket of 10–20 low-correlation instruments** (crypto majors + a few FX pairs + index futures). This is how to get breadth without cross-sectional ranking:

```
IR ≈ IC × √breadth
     breadth = 1  → Sharpe 0.3–0.8
     breadth = 15 low-correlation instruments → meaningful improvement
```

This is exactly how CTA trend following works, and it is the strategy with the **strongest peer-reviewed evidence** according to section 6 of [00-RESEARCH-SYNTHESIS](00-RESEARCH-SYNTHESIS.md).

### 🎯 With Capital $10k–50k

**Time-series momentum on a basket of the 15–30 most liquid crypto perp coins.**

- Captures part of the breadth benefit
- Native shorting, no borrow fee
- Free data
- Evidence favors TS over cross-sectional (31.96%/year versus inferior cross-sectional performance)
- Avoid long-tail altcoins: **1–3% spreads kill every edge**

### 🎯 With Capital > $50k

School A opens up at this point. But you should still:

- **Reduce rebalancing to weekly**, not daily like the paper (evidence: only ~15% of sessions should be rebalanced when real costs are included)
- Start long-only, then add shorts after validation (avoid borrow-fee erosion)
- Begin with known simple factors (Alpha158) as the baseline before letting agents generate new factors

### ⛔ Stop Thresholds — How to Know You Are Wrong

| Signal | Action |
|---|---|
| Edge disappears after adding 1–3% altcoin spreads | Shrink universe to top-20 by liquidity |
| Backtest Sharpe > 2.0 on a single instrument | **Suspect overfit** — do not celebrate |
| Backtest Sharpe > 3.0 anywhere | **Suspect methodology error** (look-ahead, survivorship, multiple testing) |
| Cross-sectional crypto underperforms TS momentum | Drop cross-sectional, return to TS |
| Need > 50 positions but capital < $50k | Hard barrier — change direction, do not force it |

---

## 7. Corrections for Older Documents

| Document | Point to fix |
|---|---|
| `92` | ❌ *"PDT rule: requires $25,000"* → **eliminated from 2026-06-04**, with $2,000 remaining under the margin rule |
| `92` | ❌ *"Quality data (survivorship-free, PIT) is expensive"* → now **$29–49/month** |
| [00-RESEARCH-SYNTHESIS](00-RESEARCH-SYNTHESIS.md) | Section 6 should add: cross-sectional crypto **underperforms** time-series momentum based on 2020–2025 evidence |

---

## 8. Sources

**Transaction costs & turnover**
- [Transaction Costs of Factor-Investing Strategies — Financial Analysts Journal](https://www.tandfonline.com/doi/full/10.1080/0015198X.2019.1567190)
- [The market impact of rebalancing factor investing strategies — Alpha Architect](https://alphaarchitect.com/the-market-impact-of-rebalancing-factor-investing-strategies/)
- [Transaction-cost-aware Factors — Baldi-Lanfranchi (AFA)](https://afajof.org/management/viewp.php?n=135184)
- [Large-Scale Portfolio Allocation Under Transaction Costs](https://arxiv.org/pdf/1709.06296)

**Crypto cross-sectional & momentum**
- [Momentum Trading in Cryptocurrencies: Time-Series vs Cross-Sectional](https://www.journals.vu.lt/BATP/en/article/download/44540/42590/138419)
- [Systematic Trend-Following with Adaptive Portfolio Construction (arXiv 2602.11708)](https://arxiv.org/html/2602.11708v1)
- [Cross-sectional Momentum in Cryptocurrency Markets — Starkiller Capital](https://www.starkiller.capital/post/cross-sectional-momentum-in-cryptocurrency-markets)
- [Unravelling cross-sectional patterns in cryptocurrencies — China Accounting and Finance Review](https://www.emerald.com/cafr/article/27/4/493/1271913/Unravelling-cross-sectional-patterns-in)

**Crypto costs**
- [All Crypto Trading Fees Explained — BloFin](https://blofin.com/en/academy/education/crypto-trading-fees)
- [What Is Slippage in Crypto? 2025 Guide](https://blog.sei.io/s/what-is-slippage-crypto-guide/)

**Regulation & retail costs**
- [PDT Rule Change — E*TRADE](https://us.etrade.com/knowledge/library/margin/pattern-day-trading-rule-change)
- [FINRA eliminates $25,000 day-trading rule — Yahoo Finance](https://finance.yahoo.com/markets/options/articles/finra-just-killed-25-000-223500606.html)
- [Short Sale Cost — Interactive Brokers](https://www.interactivebrokers.com/en/pricing/short-sale-cost.php)
- [The Risks of Shorting: Borrow Fees — IBKR Campus](https://www.interactivebrokers.com/campus/traders-insight/securities/short-selling/the-risks-of-shorting-series-part-ii-borrow-fees/)

**Data**
- [Sharadar](https://sharadar.com/) · [Tradevo Data PIT $29/mo](https://tradevodata.com/alternatives/sharadar) · [Point-in-Time Fundamentals: what & how to choose](https://dev.to/tradevodata/point-in-time-fundamentals-data-what-it-is-why-it-matters-and-how-to-choose-a70)

**Realistic expectations**
- [Realistic Sharpe Ratios in 2026: HFT vs Retail Algos — Elite Trader](https://www.elitetrader.com/et/threads/realistic-sharpe-ratios-in-2026-hft-vs-retail-algos-deep-dive.388680/)
- [Quant Trading: What the Evidence Actually Shows](https://paperswithbacktest.com/course/quant-trading)
- [Realistic expectations in algo trading — QuantConnect](https://www.quantconnect.com/forum/discussion/5720/realistic-expectations-in-algo-trading/)

---

## One-sentence Summary

> School A has a higher ceiling because it places more bets, not because it is smarter. For an individual, the question is not "which is better" but **"how much breadth can I buy with my capital"**. Below $10k, the answer is to run one simple rule across 10–20 low-correlation instruments — not to rebuild RD-Agent(Q).
