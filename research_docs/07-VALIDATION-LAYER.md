# Validation Layer — Detailed Explanation

> The layer that determines whether this project has any real value. The whole industry is missing it — QuantEvolve, RD-Agent(Q), and AlphaAgent all **do not have it**.
> Date: 2026-09-20 · Related: [[00-RESEARCH-SYNTHESIS]] · [[05-SMOOTH-FLOW]]

---

## ⚠️ 0. Important Correction on the DSR Threshold

Earlier documents in this folder stated **"DSR > 0"** (inherited from the original report). **Wrong.**

**DSR is a PROBABILITY, with values from 0 to 1.** It is the Probabilistic Sharpe Ratio with a benchmark adjusted by the number of trials.

| DSR value | Meaning |
| --- | --- |
| **> 0.95** | Strong evidence that the strategy has real alpha |
| 0.50 – 0.95 | Gray area, not enough to conclude |
| **< 0.50** | **No evidence beyond luck**, given the intensity of the search |

→ The correct threshold is **DSR > 0.95**. "DSR > 0" is always trivially true because a probability is never negative.

---

## 1. The Problem: Backtests Lie in THREE Different Ways

This is the most important and most commonly misunderstood point. People often group everything under "overfitting," but there are actually **three independent types of error**, and **each one needs its own tool**.

| # | Error type | Nature | Example |
| --- | --- | --- | --- |
| **1** | **Leakage** | Using data that is **not allowed to be used** at that point in time | Using an unclosed candle; repainting indicator; overlapping labels |
| **2** | **Selection bias** | Trying **too many times** and choosing the luckiest result | Hyperopt 200 epochs, then choosing the best epoch |
| **3** | **Memorization** | The LLM has **already seen** the test period during pretraining | Testing 2017–2020 with a model cutoff in 2024 |

> 🔴 **The fatal point:** DSR and PBO **only solve type 2**. They are **completely blind** to type 1 and type 3.
>
> Evidence (arXiv 2608.27734): a deliberately leaking **"leaky oracle"** uses future data, achieves Sharpe 34.7, and **still gets DSR = 1.00**.

So the validation layer needs **three layers**, not one.

---

## 2. Three-Layer Architecture

```
┌─ LAYER 1: STRUCTURAL GUARDRAIL ────── against LEAKAGE ─────┐
│  • Indicator whitelist (no repainting)                     │
│  • Ban future-index access (.shift(-n))                    │
│  • Purging + embargo when splitting folds                  │
│  • PIT data layer, fail-loud when data is missing          │
│  ⚡ Runs BEFORE backtest — cheap, rejects early             │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌─ LAYER 2: STATISTICAL CORRECTION ─ against SELECTION BIAS ─┐
│  • CPCV → Sharpe distribution (not one point estimate)     │
│  • PBO via CSCV → probability the selection rule overfits  │
│  • DSR → deflate by the TOTAL number of trials             │
│  • MinBTL → is the backtest long enough?                   │
│  ⚡ Runs AFTER backtest, BEFORE looking at holdout          │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌─ LAYER 3: TEMPORAL ISOLATION ─── against MEMORIZATION ─────┐
│  • Frozen holdout, run EXACTLY ONCE                        │
│  • Test window after knowledge cutoff (if using an LLM)    │
│  • Measure IS→OOS decay (>50% → suspect memorization)      │
│  • Dry-run / forward test                                  │
│  ⚡ Last step, no going back                                │
└────────────────────────────────────────────────────────────┘
```

The three layers **do not replace one another**. Remove any layer and one type of error gets through.

---

## 3. LAYER 1 — Structural Guardrail

### 3.1. Purging — Why Ordinary Cross-Validation Often Breaks

This is the least-known but most important concept.

**Situation:** you predict the next 5 sessions' return at time `t`. The label for sample `t` is calculated using data up to `t+5`.

```
Train set                    Test set
├────────────────────────┤ ├──────────────────┤
                    ↑ t
                    └──── label for t uses data up to t+5
                         └─── OVERLAPS the test set → LEAK
```

The training sample at `t` has already **"seen"** part of the test set. Standard sklearn K-Fold **knows nothing about this**.

**Purging** = deleting training samples whose labels overlap the test period.

### 3.2. Embargo — Why Purging Is Still Not Enough

After purging, training samples **immediately after** the test set can still be contaminated because of **serial correlation** — today's price is correlated with yesterday's price.

**Embargo** = deleting an additional buffer region after the test set.

```
├─── train ───┤ ✂purge✂ ├─ TEST ─┤ ✂embargo✂ ├─── train ───┤
```

### 3.3. Other Things in This Layer

| Guardrail | Method |
| --- | --- |
| Indicator whitelist | Only allow the agent to use indicators verified as non-repainting |
| Ban future-looking access | Reject code containing `.shift(-n)`, `iloc[i+1:]`, `.rolling(...).shift(-)` |
| Unclosed candles | Only allow reading closed bars — `df.iloc[-2]`, not `[-1]` |
| Fail-loud | Missing data → **throw an exception**, do not silently `fillna` |

---

## 4. LAYER 2 — Statistical Correction

### 4.1. CPCV — From ONE OOS Path to MANY Paths

Traditional walk-forward gives you **one** out-of-sample path. Its Sharpe is **one number** — you do not know whether it was lucky or unlucky.

**Combinatorial Purged CV** splits data into N blocks, takes K blocks as the test set, and runs all combinations:

```
N=6 blocks, K=2 test blocks → C(6,2) = 15 folds → 5 independent backtest paths
```

The result is a **Sharpe distribution**, not a point estimate. You can see median Sharpe, dispersion, and the percentage of negative paths.

> 💡 This is also the raw material for calculating PBO.

### 4.2. PBO via CSCV — Mechanism

**Probability of Backtest Overfitting** answers the question: *"What is the probability that the best in-sample configuration falls below the out-of-sample median?"*

How it runs:

```
1. Build matrix M: T rows (time) × N columns (strategy configurations)
2. Split T into S equal groups
3. For EVERY combination that selects S/2 groups as IS (the rest are OOS):
   a. Find the best configuration in IS  →  n*
   b. Compute the relative rank of n* in OOS  →  ω ∈ (0,1)
   c. λ = log( ω / (1-ω) )          ← logit
4. PBO = percentage of combinations with λ < 0
        = percentage of times the "IS champion falls below OOS median"
```

**Reading PBO correctly** *(correction 21 Sep 2026 — the previous version said "PBO approaches 1 as N increases, even with no edge". **Wrong.**)*

- With **no edge**, and OOS ranks independent of IS ranks, the IS winner lands at a uniformly random OOS position ⇒ **PBO ≈ 0.5**, however large N is. A large N does **not** by itself push PBO toward 1.
- PBO **> 0.5** happens when IS and OOS ranks are **negatively correlated** — e.g. the better a configuration fits IS noise, the worse it does OOS (parameters latching onto reverting patterns, structural overfitting).
- PBO **< 0.5** happens when a stable signal component makes good-IS configurations tend to be good OOS.
- What grows with N is the **expected IS Sharpe of the winner** under the null — that is **DSR**'s job (§4.3), not PBO's.

**Consequence:** PBO measures whether *a selection rule over a given configuration set* is OOS-stable. It is **not** a property of a single return series — you must define which configurations make up the matrix `M` and what the winner-selection rule is. In [Architecture_Design.md](Architecture_Design.md) §3.2: `M` = a pre-registered parameter grid around the candidate + the refinement variants actually tried; rule = highest IS Sharpe.

| PBO | Conclusion |
| --- | --- |
| < 0.2 | Good |
| 0.2 – 0.5 | Acceptable, with caution |
| **≥ 0.5** | **The strategy is noise. Reject — DO NOT tune** |

> ⚠️ "Do not tune" means: do not adjust parameters and try again. Every retry **increases DSR's N** (raising the Sharpe bar), and re-selecting after seeing PBO is a new round of selection the old PBO never measured. Reject it outright.

### 4.3. DSR — Mechanism

**Problem:** if you try 1,000 completely useless strategies, the best one will still have a fairly high positive Sharpe — **purely by luck**.

**DSR idea:**

```
1. Compute the expected MAXIMUM Sharpe of N trials WITH NO SKILL
   → depends on N and Sharpe dispersion across trials
   → this is the "deflated benchmark"

2. DSR = P( true Sharpe > that benchmark )
   → adjusted for skew, kurtosis, and sample length
```

DSR is essentially **PSR with the benchmark raised according to the number of trials**.

**Three parameters determine the result:**

| Parameter | Effect |
| --- | --- |
| **N (number of independent trials)** | Larger → higher benchmark → lower DSR. The paper distinguishes *actual* from *independent* trials — highly correlated trials do not count in full |
| **V[SR] (variance of Sharpe across trials)** | Larger → higher benchmark. You must store the Sharpe **of every trial**, not just the winner's |
| **Negative skew** | Heavily penalized ("picking up pennies in front of a steamroller" strategies) |
| **Sample length** | Shorter → less trustworthy |

> 🔑 **N is the parameter you can most easily lie to yourself about.** See section 6.

### 4.4. MinBTL — A Cheap but Effective Gate

**Minimum Backtest Length:** given the number of trials N already run, how many years must the backtest cover at minimum for an observed Sharpe to be trustworthy?

Use it as an **early gate**: if your backtest is shorter than the MinBTL corresponding to the number of trials already run → **no need to calculate anything else, reject immediately**. Much cheaper than running CPCV.

### 4.5. MinTRL

**Minimum Track Record Length:** how many observations are needed to state that the observed Sharpe exceeds a benchmark at a given confidence level. Use it to decide **how long a dry-run is enough**.

---

## 5. LAYER 3 — Temporal Isolation

| Tool | Protects against | Rule |
| --- | --- | --- |
| **Frozen holdout** | Accumulated selection bias | Run **exactly once**. Once you look at it, it is burned — forever |
| **Post-cutoff test** | LLM memorization | If using an LLM to generate strategies, prioritize a test window after the knowledge cutoff |
| **IS→OOS decay** | Memorization + overfit | Decay > 50% → suspicious (Profit Mirage benchmark) |
| **Dry-run** | Everything that remains | Minimum 4–6 weeks, with real spreads/fees |

> ⚠️ **A holdout can only be used ONCE.** If you run the holdout, see bad results, modify the strategy, and run it again — that holdout is **dead**; it has become in-sample. This is a discipline failure, not a technical failure, and no tool can save it.

---

## 6. Trial Ledger — The Easiest Place to Deceive Yourself

DSR depends directly on **N**. Understating N → artificially beautiful DSR.

### WRONG Count

```
"I ran hyperopt for 200 epochs"  →  N = 200
```

### CORRECT Count

```
N = total of EVERY time a configuration is evaluated, accumulated across the whole project:

  50 strategies generated by the agent
× 200 hyperopt epochs each
+ 30 times you manually edited and reran a backtest
+ 12 times you tried a different timeframe
+ 8 times you changed the universe
─────────────────────────────
= 10,050 trials
```

**And N never resets.** It accumulates over the entire project lifecycle.

> 🆕 **But not every log line is a trial** *(added 21 Sep 2026)*. A trial = a configuration **whose performance was measured on data**. An LLM call, a file that fails to compile, a reviewer reading a report — record them for audit, but they are **not** tests that can create selection bias. Mixing them into `N` makes DSR wrong. Conversely, highly correlated trials (same idea, neighbouring parameters) are not independent trials either — use `N_eff` from clustering and always report `N_raw` alongside. The two-record design: [Architecture_Design.md](Architecture_Design.md) §4.1.

> 💡 This is exactly what arXiv 2608.27734 calls a **search ledger**, and it is why agent loops are more dangerous than manual work: *"the agent's productivity becomes a bias amplifier"*. The agent can run thousands of hidden trials before you ever see one number.

**Minimum schema:**

```sql
CREATE TABLE trials (
    id            INTEGER PRIMARY KEY,
    ts            TIMESTAMP,
    strategy_hash TEXT,      -- hash of the code, detects duplicates
    params        JSON,
    universe      TEXT,
    timeframe     TEXT,
    timerange     TEXT,
    sharpe_is     REAL,
    returns_path  TEXT,      -- path to returns file for later DSR calculation
    verdict       TEXT       -- PASS / REJECT_PBO / REJECT_DSR / REJECT_GUARDRAIL
);
```

**Iron rule:** every backtest must go through the ledger. **No bypass.** If it is possible to run a backtest without writing to the ledger, sooner or later you will do it.

---

## 7. Implementation with `purgedcv`

The API below is taken from the package's official documentation, 🆕 checked against the `purgedcv` 0.1.6 source.

> ✅ **Verified against the source code** *(21 Sep 2026, P1-01)*. Pinned `purgedcv>=0.1.6,<0.2` (MIT). The signatures below match the actual code. What we use and what we write ourselves: [ADR-0007](../implement_docs/adr/0007-purgedcv-wrap-vs-implement.md) — DSR/PSR implemented ourselves (from moments), PBO and CPCV wrapped.

```python
from purgedcv import (
    WalkForwardSplit,
    PurgedKFold,
    PurgedGroupKFold,
    CombinatorialPurgedCV,
    probabilistic_sharpe_ratio,
    deflated_sharpe_ratio,
    deflated_sharpe_ratio_full,      # returns DSRDiagnostics
    min_track_record_length,
    minimum_backtest_length,
    probability_of_backtest_overfitting,   # returns PBOResult
)
```

### 7.1. Purged K-Fold with Embargo

```python
cv = PurgedKFold(
    n_splits=4,
    prediction_times=pred,      # time when the signal is generated
    evaluation_times=evalu,     # time when the label is finalized
    purge_horizon="2D",
    embargo="2D",               # or embargo_observations=10 / embargo_fraction=0.01
)
```

### 7.2. Walk-Forward

```python
cv = WalkForwardSplit(
    n_splits=3,
    test_size=4,
    window="expanding",         # or "sliding" (then train_size is needed)
    prediction_times=pred,
    evaluation_times=evalu,
    purge_horizon="2D",
)
for train_idx, test_idx in cv.split(X):
    ...
```

### 7.3. CPCV — Generating Multiple Backtest Paths

```python
cv = CombinatorialPurgedCV(
    n_splits=6,                 # N blocks
    n_test_groups=2,            # K test blocks
    prediction_times=pred,
    evaluation_times=evalu,
)
# C(6,2) = 15 folds → 5 backtest paths
splits = list(cv.split(X))      # + reconstruct_paths(...); backtest_paths needs an sklearn estimator — not used
```

### 7.4. PSR and MinTRL

```python
psr = probabilistic_sharpe_ratio(strategy_returns, benchmark_skill=0.0)

n_min = min_track_record_length(
    observed_sharpe=0.7,
    target_sharpe=0.5,
    alpha=0.05,
    skew=0.0,
    kurtosis=3.0,
)
```

🆕 **Verified signatures (0.1.6):** `deflated_sharpe_ratio(returns, n_trials, var_sharpe, *, bars_per_year=None)` — takes `N` and `V[SR]` **separately**, but only a returns series (no moments); `probability_of_backtest_overfitting(returns, n_splits=16, *, metric=sharpe, …)` — matrix `(n_configs, n_obs)`, selection rule = IS argmax Sharpe, returns `PBOResult`.

---

## 8. Where to Put the Gates — Order Matters

Put **cheap gates first, expensive gates later**. Rejecting early saves compute *and* reduces N.

```
[Agent generates strategy]
        ↓
  ① GUARDRAIL         ← cheapest, no backtest needed
     • AST similarity vs zoo
     • Scan for .shift(-n), future-index access
     • Is the indicator in the whitelist?
        ↓ pass
  ② MinBTL            ← only needs N and backtest length
     • Is the backtest long enough for the current number of trials?
        ↓ pass
  ③ BACKTEST IS       ← compute cost begins
     • Does the edge survive stricter fees?
        ↓ pass
  ④ CPCV + PBO        ← most expensive
     • PBO < 0.5?
        ↓ pass
  ⑤ DSR               ← deflate by TOTAL N from the ledger
     • DSR > 0.95?
        ↓ pass
  ⑥ FROZEN HOLDOUT    ← ONE TIME ONLY
     • Sharpe OOS ≥ 50% Sharpe IS?
        ↓ pass
  ⑦ DRY-RUN 4–6 WEEKS
        ↓ pass
  ⑧ LIVE, smallest size
```

**Every rejection is written to the ledger** — rejected trials are still trials and still count toward N.

---

## 9. Leaky Oracle Test — A Test for the Harness Itself

This is the best idea in arXiv 2608.27734, and it is the **first gate you should build**.

**How to do it:** write a deliberately cheating strategy that uses future prices:

```python
# leaky_oracle.py — NEVER deploy
def populate_entry_trend(df):
    # Blatant cheating: looking at tomorrow's price
    df["enter_long"] = df["close"].shift(-1) > df["close"]
    return df
```

Run it through the whole pipeline.

| Result | Conclusion |
| --- | --- |
| Pipeline **rejects at gate ①** | ✅ Guardrail works |
| Pipeline lets it through, DSR = 1.00, PBO = 0 | 🔴 **Your harness is broken** — exactly as the paper warns |

> This is why I said in [[05-SMOOTH-FLOW]]: **the week-1 gate is not "is the strategy profitable?", but "can the harness reject a leaky oracle?"**.

You should have **a suite of oracles** with different levels of sophistication:

1. Blatant `.shift(-1)`
2. Using the `close` of the currently forming candle (unclosed)
3. Using a repainting indicator
4. Using real information that was published late (simulating a PIT error)

Level 4 is the hardest and also closest to real-world failures.

---

## 10. Summary Threshold Table

| Gate | Threshold | If violated |
| --- | --- | --- |
| Guardrail | 0 violations | Reject, write ledger |
| **MinBTL** | Backtest ≥ MinBTL(N) | Reject or extend data |
| **PBO** | < 0.5 (ideally < 0.2) | **Reject, DO NOT tune** |
| **DSR** | **> 0.95** | Reject |
| IS→OOS Sharpe | OOS ≥ 50% IS | Suspect overfit/decay |
| Sharpe decay post-cutoff | < 50% | Suspect memorization |
| Dry-run | ≥ MinTRL observations | Not enough data to conclude |

---

## 11. The Three Most Common Mistakes

**1. Calculating DSR/PBO at the end as a report.** Wrong — it must be built into the **fitness function of the loop**. AlphaAgent and RD-Agent(Q) both fail to do this, which is why their results are not trustworthy (see [[08-LLM-QUANT-RESEARCHER]] section 5).

**2. Resetting N every run.** N accumulates over the entire project lifecycle. Resetting it = deceiving yourself.

**3. Believing DSR/PBO are enough.** They only protect against selection bias. Leakage and memorization need other layers.

---

## 12. Sources

- [Bailey &amp; López de Prado — The Deflated Sharpe Ratio (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) · [PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- [Deflated Sharpe ratio — Wikipedia](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)
- [Quantdare — Deflated Sharpe Ratio: how to avoid being fooled by randomness](https://quantdare.com/deflated-sharpe-ratio-how-to-avoid-been-fooled-by-randomness/)
- [purgedcv on PyPI](https://pypi.org/project/purgedcv/)
- López de Prado — *Advances in Financial Machine Learning* (2018), chapters 7 (purging/embargo) and 11–12 (CPCV, PBO)
- arXiv 2608.27734 — "What survives honest evaluation?" (leaky oracle, search ledger)

---

## One-Sentence Summary

> Backtests lie in three different ways, and **DSR/PBO only cure one**. The cheapest layer (structural guardrail) catches the most dangerous error type. And the most important parameter in the whole system is **N — the number you can most easily lie to yourself about**.
