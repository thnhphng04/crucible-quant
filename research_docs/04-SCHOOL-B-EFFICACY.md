# School B — Evidence Report on Efficacy

> Rule-based technical analysis across every breadth scale: from 1 instrument to baskets of 60+ markets (the CTA/managed futures industry).
> Research date: 2026-09-20 · Related: [[00-RESEARCH-SYNTHESIS]] · [[03-FEASIBILITY-FOR-INDIVIDUALS]]

---

## 0. Tracing the "Sharpe 0.3-0.8" figure — and correcting it

The figure I used in [[03-FEASIBILITY-FOR-INDIVIDUALS]] came from two sources:

1. File `92` in the folder — but there it describes **trend following on a multi-asset futures basket at the portfolio level**, meaning it already has breadth
2. 2026 quant community consensus: *"a genuine, tradeable, diversified quant strategy with a long-run Sharpe between 0.4 and 0.8 is a good outcome"* — the key word is **diversified**

⚠️ **I assigned it to "single-instrument rule-based TA" — that was the wrong context.** The correct figure for a single instrument is lower, and we now have exact numbers from the original paper (section 2.1).

---

## 1. What School B Includes

It is not just "a few indicators on one pair." It is **the entire family of explicit-rule, deterministic strategies that run on price series**:

| Strategy family | Examples | Typical scale |
|---|---|---|
| **Trend following / TS momentum** | MA crossover, Donchian breakout, 12-1 momentum | 1 → 60+ markets |
| **Breakout / channel** | Turtle System (20/55-day Donchian) | Futures basket |
| **Mean reversion** | RSI oversold, Bollinger reversion | 1 → basket |
| **Pairs trading / stat arb** | Distance method, cointegration | Equity basket |
| **Oscillator / filter rules** | MACD, stochastic, trading-range break | 1 → basket |

> 📌 **An important point missed in previous reports:** the **CTA / managed futures** industry, worth hundreds of billions of dollars, **is School B at institutional scale**. It is not a separate school. That means School B has the longest evidence base and the most real-money verification of any school.

---

## 2. Evidence by Strategy Family

### 2.1. 🟢 Trend Following / Time-Series Momentum — The Strongest Evidence

**Hurst, Ooi & Pedersen — "A Century of Evidence on Trend-Following Investing"** (AQR, JPM 2017)

- **67 markets, 4 asset classes** (29 commodities, 11 equity indices, 15 bonds, 12 currencies)
- **1/1880 → 12/2016** — 136 years
- Positions scaled to a **10% annual volatility target**
- Average returns were **positive in EVERY market**

**And this is the most important figure in this entire report:**

| Configuration | Sharpe |
|---|---|
| **Average individual market** | **~0.4** |
| Long-short equity factor | 0.95 |
| **Diversified across all 3 asset groups** | **1.60** |

> 🔑 **Same trading rule. Sharpe 0.4 → 1.60 only because of breadth.** This is quantitative evidence for the formula `IR ≈ IC × √breadth` in [[03-FEASIBILITY-FOR-INDIVIDUALS]], and it says plainly: **if you run a TA rule on 1 symbol, the reasonable expectation is Sharpe ~0.4, not 0.8.**

**The ceiling from adding markets — Graham Capital Management:**

> *"As markets are added to a portfolio, the Sharpe ratio increases until approximately 50 markets have been added, at which point there is a performance ceiling."*

The reason it stops at ~50: correlations between markets. *"Even though the number of markets included in a portfolio has more than doubled, the correlation structures between the portfolios are virtually identical."*

**AQR "Trends Everywhere"** (Babu, Levine, Ooi, Pedersen, Stamelos, JOIM 2020) — out-of-sample verification on **82 securities that had never been tested before** (EM equity index futures, fixed income swaps, EM currencies, exotic commodities, CDS indices, volatility futures) + 16 long-short equity factors. Conclusion: TS momentum works across asset classes and across multiple trend horizons.

### 2.2. 🔴 But This Very Foundation Has Been Seriously Challenged

**Huang, Li, Wang & Zhou — "Time Series Momentum: Is It There?"** (Journal of Financial Economics, 2020)

This is the **most important paper that none of the reports in the folder mentioned**. It directly attacks Moskowitz-Ooi-Pedersen (2012):

- **Asset-by-asset** regressions show **very little evidence of TSM, both in-sample and out-of-sample**
- The t-statistic in the **pooled regression** looks large but is **not reliable** — it is below the critical value of both parametric and non-parametric bootstrap tests
- Most importantly: the TSM strategy **is genuinely profitable, but its performance is almost identical** to a strategy based only on the **historical sample mean** — meaning it requires **no forecasting ability at all**

> ⚠️ Meaning: much of the "trend following" return may simply be a **volatility-scaled long bias**, not actual trend capture.

**Zakamulin** — a study of MA trading rules on **155 years of data**: proves that the *"too good to be true"* performance of moving-average strategies reported in many earlier studies **came from look-ahead bias in the simulation**. When simulated correctly, the real performance is much lower.

### 2.3. 🟡 Classic TA Rules — Mixed and Decaying Over Time

**Park & Irwin** (Journal of Economic Surveys, 2007) — a survey of 95 modern studies:

| Result | Number of studies |
|---|---|
| Positive | **56** |
| Negative | 20 |
| Mixed | 19 |

But with two warnings:

- TA was profitable *"at least until the early 1990s"* — in the US equity market, only **through the late 1980s**
- *"Most empirical studies are subject to various problems in their testing procedures, e.g. data snooping, ex post selection of trading rules or search technologies, and difficulties in estimation of risk and transaction costs"*

**Sullivan, Timmermann & White** (Journal of Finance, 1999) — the classic paper on data snooping:

- Expanded the 26 rules from Brock-Lakonishok-LeBaron into a large universe, applying **White's Reality Check bootstrap** on **100 years of DJIA data**
- **In-sample:** the best rule still outperformed **even after correcting for data snooping** ✅
- **Out-of-sample:** the best rule **NO LONGER outperformed in the next 10 years** ❌
- **S&P 500 futures:** **no evidence** that the best rule outperformed after accounting for data snooping ❌

> This is the recurring pattern throughout: **a rule survives in-sample even after statistical correction, then dies out-of-sample.**

### 2.4. 🔴 Pairs Trading / Stat Arb — A Textbook Example of Alpha Decay

**Gatev, Goetzmann & Rouwenhorst (1962-2002):** pairs selected by minimum distance produced **~11% excess return/year, Sharpe ~1.5, beta near 0**. A very attractive number.

**Do & Faff (OOS 2003-2009):** returns **deteriorated sharply**, and continued worsening.

**Cause decomposition of the deterioration — a detail worth studying:**

| Cause | Share |
|---|---|
| **Worsening arbitrage risk** | up to **70%** |
| More efficient market | ~30% |

> 💡 Lesson: alpha does not die mainly because "the market gets smarter," but because **implementation risk rises** — pairs diverge for longer and positions become harder to hold. This is the kind of decay that a backtest **never sees**.

### 2.5. 🟡 Crypto — The Only Area Still Truly Debated

**Hudson & Urquhart** — tested **~15,000 technical trading rules** on 2 Bitcoin markets + 3 other cryptocurrencies, controlling for multiple hypotheses with **both FWER and FDR**:

- In-sample: **predictability and profitability were significant across every rule class** ✅
- ⚠️ **Out-of-sample: NO predictability for Bitcoin**, but **still present in other cryptocurrencies** ❌/✅

**Deprez & Frömmel** — tested simple TA rules on Bitcoin with False Discovery Rate correction and realistic investor behavior → found simple rules that outperformed buy-and-hold out-of-sample.

**Crypto trend following** (Rozario, Holt, West & Ng, arXiv 2009.12155, 2011-2019 data):

- **Sharpe 0.5-1.5**
- Walk-forward annualised return **255%** (the early Bitcoin period — not repeatable)
- EMA/DEMA often outperformed SMA after optimization

> 📌 **Read this correctly:** the 255% figure comes from Bitcoin's early period (2011-2019), when BTC rose from a few dollars to thousands of dollars. **It cannot be extrapolated to 2026.** Sharpe 0.5-1.5 is the more credible figure.

**Conclusion on crypto:** this is the **only** market where the evidence on TA is not settled — Deprez-Frömmel found out-of-sample edge, while Hudson-Urquhart did not find it for BTC. And **both agree that BTC is the hardest coin** (highest liquidity → highest efficiency).

---

## 3. Real-Money Reality: The CTA Industry, 2022-2026

This is School B run with real money, audited and reported monthly.

| Year | SG Trend Index | Note |
|---|---|---|
| **2022** | **+27.3%** | Best year in history, **Sharpe > 1** |
| 2023 | +1.18% (YTD September) | Almost flat |
| 2024 | **+2.4%** | *"close to flat despite the good start"* |
| 2025 | **−9.3%** (YTD April) | April 2025 alone: −4.9% |
| 2026 | Recovery | Managed futures ETFs: KMLM +7%, DBMF +8%, CTA +8% (YTD to 2026-04-08) |

**The most painful number** (Morningstar): **over the 3 years through 2025-08-31, the typical systematic trend fund LOST an average of −2.3%/year.** In the first half of 2025 alone, it lost **−5.8%**.

**Why 2023-2025 failed:** *"the drag is real during calm bull markets when trends are choppy and reversals are frequent."* Trend following needs large, sustained trends — a calm bull market with many small reversals is the worst environment.

**Why 2026 recovered:** *"Equity selloffs tend to be accompanied by large, sustained moves in rates, currencies, and commodities."*

> 🔑 **The most important lesson from this data:** School B is **heavily regime-dependent**. Sharpe 0.4-1.6 is a *long-run* figure. In practice, you will encounter a **3-year negative streak** — and that is for professional funds with 60+ markets, low fees, and good infrastructure.

---

## 4. Individual Results — Brutal Data

The gap between "academic evidence" and "individual outcomes" is very large.

| Study | Scope | Result |
|---|---|---|
| **Barber & Odean (Taiwan)** | Entire exchange, 1992-2006, 15 years | **~1%** of day traders were reliably profitable across years. Average loss **23.9 bps/day** after fees. Overall performance was **negative in 14/15 years** |
| **Brazil (index futures)** | Traders with >300 sessions | **97% lost money**. Only **1.1%** earned more than minimum wage |
| **Barber & Odean (US, 2000)** | Highest-turnover trading quintile | Underperformed the market by **~6.5 percentage points/year** |
| **ESMA (CFD/forex EU)** | Required disclosures | **74-89%** of retail accounts lost money |
| **FPFX (prop firm)** | 300k accounts | **14%** passed the challenge, ~45% of those received payout → **~7%** overall |

These numbers **converge remarkably** across decades and countries.

### ⚠️ But Read Them Correctly

These studies measure **day traders in general** — mostly discretionary, highly levered, high frequency. **They do not measure low-frequency systematic rule-based traders.**

That does **not** mean you are immune. It means the documented causes of failure are **high frequency + costs + leverage + discipline**, and a low-frequency rule-based system can avoid much of that — but it cannot avoid **overfitting**, which is the specific trap of systematic trading.

---

## 5. Expected Sharpe by Configuration

Synthesized from all the evidence above. **This is the table to use for setting expectations.**

| Configuration | Expected Sharpe | Source / rationale |
|---|---|---|
| 1 instrument, rule TA | **~0.4** | Hurst-Ooi-Pedersen: average per market |
| 5-10 low-correlation instruments | **0.6-0.9** | Interpolated from the breadth curve |
| 20-30 instruments across asset classes | **1.0-1.3** | Approaching the ceiling |
| **50+ markets, multi-asset-class** | **~1.6** | Hurst-Ooi-Pedersen diversified; **ceiling** per Graham Capital |
| Crypto trend following | **0.5-1.5** | Rozario et al. 2011-2019 |
| Pairs trading (golden period 1962-2002) | ~1.5 | Gatev et al. — **dead after 2002** |
| **Actual CTA industry 2022-2025** | **negative for 3 consecutive years** | Morningstar: −2.3%/year |

**Three skepticism thresholds:**

| Backtest outputs | Reaction |
|---|---|
| Sharpe > 1.0 on **1 instrument** | Be suspicious — 2.5x the historical average |
| Sharpe > 2.0 anywhere | **Almost certainly in-sample** |
| Sharpe > 3.0 | **Suspect methodological error** (look-ahead, survivorship, multiple testing) |

---

## 6. Six Conclusions

**1. School B has the longest evidence base of any school — 136 years, 67 markets.** No other school has this.

**2. But its foundation is being shaken.** Huang-Li-Wang-Zhou (JFE 2020) shows that TSM at the individual-asset level almost does not exist, and that a TSM strategy delivers results equivalent to a strategy that **requires no forecasting at all**. This is a serious challenge that practitioners rarely mention.

**3. Breadth is the most important variable, not rule quality.** Same rule: 0.4 → 1.60. Do not waste time fine-tuning the rule when you only run 1 symbol — add instruments.

**4. The breadth ceiling is ~50 markets.** Adding more does not improve results because of correlation. This is a clear target and **feasible for an individual** — 50 crypto perps or 20-30 instruments across asset classes.

**5. Alpha decay is the rule, not the exception.** Equity TA died after the 1980s. Pairs trading died after 2002 (**70% due to arbitrage risk, not market efficiency**). Crypto TA is going through the same process — BTC lost its OOS edge first, while altcoins still have some.

**6. Regime risk kills you before overfitting gets the chance.** Professional funds with 60+ markets just went through **3 consecutive negative years**. You have to survive that period — financially and psychologically.

---

## 7. Implications for the Project

| Decision | Basis |
|---|---|
| ❌ **Do not optimize rules on 1 symbol** | Sharpe ceiling ~0.4 even with a perfect rule |
| ✅ **First target: 15-30 low-correlation instruments** | The breadth curve is steepest in this region |
| ✅ **Multi-asset-class matters more than multiple instruments of the same type** | 30 altcoins ≈ 1 bet (high correlation with BTC) |
| ✅ **Prioritize low frequency** | Retail studies: failure is concentrated in high frequency |
| ⚠️ **Prepare psychologically for 3 negative years** | CTA data 2023-2025 |
| ⚠️ **Set target Sharpe at 0.6-1.0, not 2.0** | All evidence converges on this range |
| ✅ **Account for arbitrage risk, not only market efficiency** | Do & Faff: 70% of deterioration came from this |

---

## 8. Caveats

- **Hurst-Ooi-Pedersen's Sharpe 1.60 is a simulation at a 10% volatility target, not real fund performance.** Real funds (SG Trend) have fees, slippage, and capacity constraints → lower performance.
- **AQR sells trend-following products.** The Hurst-Ooi-Pedersen and "Trends Everywhere" papers are both from AQR. The academic quality is high, but there is a conflict of interest — and Huang-Li-Wang-Zhou (independent) pushes back.
- **Park & Irwin stops in 2007.** The 19 years of data after that have not been synthesized again at comparable scale.
- **Retail data is about day traders in general**, not systematic traders specifically — you cannot directly extrapolate it to yourself.
- **The SG Trend 2025 figure is YTD April**, not the full year. The 2026 figure is YTD April 2026.
- **The 255% crypto trend-following figure** comes from 2011-2019, a period that will not repeat.

---

## 9. Sources

**Trend following / TS momentum**
- [Hurst, Ooi & Pedersen — A Century of Evidence on Trend-Following Investing (AQR)](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing) · [PDF](https://static.twentyoverten.com/593e8a9e7299b471eaecf644/SkLoGL67M/A-Century-of-Evidence-on-Trend-Following-Investing.pdf)
- [AQR — Trends Everywhere (JOIM 2020)](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-Trends-Everywhere_JOIM.pdf)
- [🔴 Huang, Li, Wang & Zhou — Time Series Momentum: Is It There? (JFE 2020)](https://www.sciencedirect.com/science/article/abs/pii/S0304405X19301953) · [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3165284) · [Summary — Alpha Architect](https://alphaarchitect.com/are-trend-following-and-time-series-momentum-research-results-robust/)
- [Graham Capital — Market Diversification (2017)](https://www.grahamcapital.com/wp-content/uploads/2023/08/Market-Diversification_Graham-Research_August-2017.pdf)

**Classic TA & data snooping**
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

**CTA industry — real-money results**
- [SG Prime Services Indices](https://wholesale.banking.societegenerale.com/en/prime-services-indices/) · [SG Trend Index — BarclayHedge](https://portal.barclayhedge.com/cgi-bin/indices/displayHfIndex.cgi?indexCat=SG-Prime-Services-Indices&indexName=SG-Trend-Index)
- [Morningstar — Managed-Futures Funds Look to Rebound](https://www.morningstar.com/alternative-investments/managed-futures-funds-look-rebound-can-they-help-diversify-your-portfolio)
- [Man Group — Trend Following and Drawdowns: Is This Time Different?](https://www.man.com/insights/is-this-time-different)
- [AlphaSimplex — Market Cycles and Managed Futures Drawdowns (Kaminski & Wen, 2025)](https://www.alphasimplex.com/assets/files/2025.06---market-cycles-and-managed-futures---kaminski-and-wen.pdf)

**Individual results**
- [Barber & Odean — Do Individual Day Traders Make Money? Evidence from Taiwan (PDF)](https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trade%20040330.pdf)
- [Day Trading Statistics: What Academic Research Shows](https://curvedtrading.com/articles/en/trading/day-trading-statistics/)
- [Is Day Trading Profitable? What the Data and Studies Show](https://www.currentmarketvaluation.com/posts/the-data-on-day-trading.php)

---

## One-Sentence Summary

> School B has 136 years of evidence across 67 markets — but the real figure is **Sharpe ~0.4 for each individual market, and 1.60 only with diversification**. The decisive variable is **breadth, not rule quality**. And even that foundation is being challenged: Huang-Li-Wang-Zhou shows that the TSM strategy produces results equivalent to a strategy that **requires no forecasting at all**.
