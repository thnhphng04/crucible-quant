# Crucible Quant — Architecture Design

> *Crucible Quant — an AI quantitative research lab that evolves systematic trading strategies and puts every one through the fire before it trades.*
>
> Design for an AI system that generates and validates deterministic, multi-market trading strategies.
> Version: 0.5 (draft) · Date: 2026-09-21
> Research foundation: [[00-RESEARCH-SYNTHESIS]] · [[04-SCHOOL-B-EFFICACY]] · [[06-MULTI-MARKET-PLATFORM]] · [[07-VALIDATION-LAYER]] · [[08-LLM-QUANT-RESEARCHER]]
>
> **Changes 0.1 → 0.2** (source: the 22-system survey in [[08-LLM-QUANT-RESEARCHER]]): the QuantEvolve architecture was independently confirmed by MadEvolve (§3.1) · added mandatory deviations **#5 closed DSL** and **#6 fresh-context reviewer** (§3.1.6) · **DSR computed on the consolidated portfolio, not per cell** (§3.1.6 — the easiest thing to get wrong) · added **gate ⓪ spec-drift** (§3.1.7) · replaced the "use small models" recommendation with a **heterogeneous routing table + a warning against reasoning models** (§3.1.9) · ledger gained `model_used` / `gen_attempts` / `gen_failures` / `drift_delta` plus the `starved_cells` view (§4.1) · **P6 raised to OS level, evaluator runs in its own process** (§4.2) · meta-evolution and RFT removed from scope (§9) · warning against using paper numbers as benchmarks (§11)
>
> **Changes 0.2 → 0.3** (source: design review of 21 Sep 2026, 8 findings — all verified as correct): the holdout is locked per **research campaign + frozen portfolio**, not per strategy (§4.2) · the **audit log** (every LLM call, every compile failure) is separated from the **statistical trial record** (only configurations whose performance was measured); DSR uses `N_eff` + the cross-trial Sharpe variance (§4.1) · added **§3.2.1, a specification for building and selecting the portfolio** · **PBO correction** — it does not tend to 1 as N grows; the CSCV configuration set is now defined (§3.2) · **fixed a sizing bug that divided by volatility twice**; `Signal.risk_pct` → `Signal.strength` (§3.3, §3.4) · gate ⓪ gets a normalized Levenshtein distance, a fixed anchor trace, and runs **after** the static AST check (§3.1.7) · conclusions drawn from external benchmarks (reasoning models, MadEvolve) downgraded to **hypotheses requiring an internal benchmark** (§3.1, §3.1.9) · **§10 is now the single decision register** — it absorbs the 4 questions from [[00-RESEARCH-SYNTHESIS]] §9 and distinguishes *decided / provisional default / open*; the `purgedcv` API is flagged as unverified (§6)
>
> **Changes 0.3 → 0.4** (source: reading the MadEvolve repo, §3.1.10): **strategy template with a fixed region + an evolvable region**, enforced by hash (§3.3.1) · **parameters declared as `TUNABLE`** — the source of the PBO grid and the ≤ 6-parameter rule (§3.3.1, §3.2) · **evaluator contract splitting public/private metrics** (§3.3.2) · **sandbox specification** for running generated code (§3.3.3) · the Coding Team uses **diff/block-rewrite patches, strict, retry with the error** (§3.1.2) · **asynchronous pipeline**, migration/curation counted in candidates (§3.1.8) · **fixed feature-map bin bounds** (§3.1.3) · island integration test (§3.1.5, phase 2) · parameter-optimizer evaluations **are trials** (§4.1)
>
> **Changes 0.4 → 0.5** (source: full text of the MadEvolve paper, arXiv 2605.23007): **multi-engine architecture** — 4-agent QuantEvolve + a MadEvolve-style simple loop + random search running in parallel, isolated/collaborative modes (§3.1.11) · **parameter optimizer switched off inside the evolution loop**, calibration only once before the freeze (§3.3.1, §3.2.1) · **module-wise evolution**: entry / exit+stop / regime regions (§3.3.1) · **minimum trade count + holding time** (gate ③) · **indicator correlation constraint** (§3.3.1) · **IS→OOS degradation curve** on CPCV (§3.2) · **pessimistic fill model + cost-sensitivity check** (§3.5, gate ⑥′) · **multiple seeds** (§3.1.8)

---

## 0. Assumptions — correct them if wrong

This document has to make decisions, so the following assumptions are locked in based on the full discussion. **If any of them is wrong, the architecture changes materially** — the last column says how.

| # | Assumption | Basis | If wrong |
|---|---|---|---|
| **A1** | Output is **deterministic rule-based TA** (entry/exit/SL/TP), not an ML factor | Throughout the discussion | If ML factor → switch to RD-Agent(Q) + Qlib, a completely different architecture |
| **A2** | Final scope: **crypto + forex + international equities + Vietnam** | Direct requirement | If crypto only → use Freqtrade, far simpler ([[05-SMOOTH-FLOW]]) |
| **A3** | **No MQL5 EA needed — live trading through NautilusTrader** | ✅ Confirmed by the user, 21 Sep 2026 | Condition: every target venue has a live Nautilus adapter (§3.5). If an MT5-only broker is ever required → write an **MT5 bridge adapter** for Nautilus; still no EA |
| **A4** | LLM lives only at the **code-generation** layer, never at runtime | Original project requirement | — |
| **A5** | Initial capital **< $10k**, scaling later | Inferred from personal context | If > $50k → open up cross-sectional equity as well |
| **A6** | Runs **locally**, no cloud dependency | The agent loop needs thousands of backtests | If cloud is acceptable → QuantConnect is simpler |
| **A7** | **All data must be FREE** — hard constraint | Direct requirement | If spending $270/yr (Norgate) is allowed → drop the custom roll module, saving 2–3 weeks in Phase 5 |

---

## 1. Design principles — non-negotiable

Six principles drawn from the research. Every technical decision defers to them.

| # | Principle | Why |
|---|---|---|
| **P1** | **Harness first, agent second** | The agent generates faster than you can check. No harness = producing garbage at high speed |
| **P2** | **Every backtest goes through the ledger, no bypass** | `N` determines DSR. If a bypass exists, you will use it ([[07-VALIDATION-LAYER]] §6) |
| **P3** | **Strategies speak in RELATIVE units (`strength` + `stop_distance`), not lots/shares/contracts** | The precondition for one codebase running across 4 markets. Sizing is the Risk layer's job (§3.4) |
| **P4** | **Vol targeting mandatory on every position** | Without it, 4 markets are still 1 bet and the breadth benefit is lost (Sharpe 0.4 → 1.6) |
| **P5** | **Backtest ≡ Live, same code** | Every rewrite is a new source of bugs |
| **P6** | **Holdout: write once, open once per research campaign — for one frozen portfolio** | Once you've looked, it's burned forever. Opening it per strategy = one more round of selection (§4.2) |

---

## 2. System overview

```
╔═══════════════════════════════════════════════════════════════╗
║  AGENT LAYER — QuantEvolve         (LLM — only here)          ║
║                                                               ║
║   Island 1 ─┐                      ┌── Evolutionary DB ──┐   ║
║   Island 2 ─┤  migrate top 10%     │  Feature map (MAP-  │   ║
║   Island 3 ─┤  every M gens        │  Elites, 16 bins×6D)│   ║
║      …    ─┘                       │  + Archive          │   ║
║                                    └──────────┬──────────┘   ║
║   each generation, each island:               │              ║
║   SampleParent(α=.5) + Cousins(2best/3div/2rnd)              ║
║        ↓                                      │              ║
║   ② Research Agent  → hypothesis (6 XML tags) │              ║
║        ↓                                      │              ║
║   ③ Coding Team     → code → backtest → refine│              ║
║        ↓            (⓪ drift check each pass) │              ║
║   ④ Evaluation Team → analysis + insight ─────┘              ║
║      (FRESH-CONTEXT reviewer, holds veto power)              ║
║                          ↓                                    ║
║                    Insight Repository (curated every 50 gens) ║
║   (① Data Agent runs once at init → N = C+1 islands)         ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓ strategy.py (deterministic)
╔═══════════════════════════════════════════════════════════════╗
║  VALIDATION LAYER       (cheap → expensive, all → ledger)     ║
║  ①a AST/DSL  ⓪ Spec-drift  ①b Oracle  ② MinBTL  ③ BT IS     ║
║  ④ CPCV+PBO  ⑤ DSR(portfolio, N_eff statistical trials)      ║
║  ⑥′ Data-source robustness ⑥ Holdout(once) ⑦ Dry-run ⑧ Live ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  STRATEGY RUNTIME          (venue-agnostic)                   ║
║  Strategy → Signal(strength, stop_distance)                   ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  RISK & SIZING LAYER       ★ more important than Adapters ★   ║
║  Vol targeting → Position sizing → FX conversion              ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  EXECUTION CORE            NautilusTrader (backtest ≡ live)   ║
╚════════════════════════════╤══════════════════════════════════╝
                             ↓
╔═══════════════════════════════════════════════════════════════╗
║  ADAPTERS  Binance✅ Bybit✅ IB✅ Databento✅ SSI🔴build it  ║
╚═══════════════════════════════════════════════════════════════╝

        ┌──────────────────────────────────────────┐
        │  LEDGER (SQLite→Postgres)                │
        │  Every trial. Never reset. No bypass.    │
        └──────────────────────────────────────────┘
```

---

## 3. Layer specifications

### 3.1. Agent Layer — following the QuantEvolve architecture (arXiv 2510.18569)

Replacing the earlier Generator/Critic/Scheduler design, the agent layer uses QuantEvolve's **quality-diversity evolutionary** architecture: **feature map (MAP-Elites) + island model + 4 agents**.

> 📖 Source: Yun, Lee & Jeon — *QuantEvolve: Automating Quantitative Strategy Discovery through Multi-Agent Evolutionary Framework*, AI Tech Lab, Qraft Technologies. Read directly from the original PDF. Critical assessment: [[00-RESEARCH-SYNTHESIS]].

> ℹ️ **Independent design convergence (updated 21 Sep 2026).** **MadEvolve** (arXiv 2605.23007, May 2026) rebuilds almost exactly the same thing — MAP-Elites grid + **5 islands** + **10% migration every 5 generations** + inspiration retrieval (i.e. "cousins") — in a completely different domain (Bitcoin order execution), **without citing QuantEvolve**. The 10% migration figure matches exactly.
>
> ⚠️ **Limits of this evidence (v0.3):** two independent groups choosing the same architecture shows it is **technically feasible and a natural choice** — it does **not** prove it is effective for our problem. Both papers are self-reported, unaudited, and have no control for multi-market rule-based TA. Treat it as a **working hypothesis**; the internal validation criterion lives in phase 2 of the roadmap (beat a random-search baseline at the same trial budget). Full landscape of the field's 5 architectures: [[08-LLM-QUANT-RESEARCHER]].
>
> 🔍 **Repo cross-check (21 Sep 2026):** the public code [`tianyi-stack/MadEvolve`](https://github.com/tianyi-stack/MadEvolve) **does not run the island model** (the parent is always taken from the global top-20; the island code exists but is never called) and contains no trading domain code. The convergence evidence is therefore **weaker** than described above — it exists only in the paper's text. What is worth learning (and what not to copy) from the repo: §3.1.10.

#### 3.1.1. Main loop (Algorithm 1)

```
Require: N islands, G generations, M migration interval,
         K insight curation interval, feature dims 𝒟, bin sizes ℬ,
         exploitation-exploration balance α ∈ [0,1]
Notation: h (hypothesis), c (code), m (backtest results), a (analysis)

 1: Initialize feature map ℱ with dims 𝒟, bins ℬ
 2: Initialize evolutionary database 𝒟ℬ (feature map + archive)
 3: Initialize N islands with seed strategies via DataAgent
 4: Initialize insight repository ℐ ← ∅
 5: for generation g = 0 … G-1 do
 6:   for each island Iᵢ do
 7:     s_p ← SampleParent(Iᵢ, 𝒟ℬ, α)          # Eq. 1
 8:     C   ← SampleCousins(s_p, Iᵢ, 𝒟ℬ)       # Eq. 2
 9:     h   ← ResearchAgent(s_p, C, ℐ)
10:     (c, m) ← CodingTeam(h, 𝒟ℬ, C)
11:     a   ← EvaluationTeam(h, c, m)
12:     s_new ← (h, c, m, a)
13:     f   ← ComputeFeatures(s_new)
14:     UpdateDatabase(s_new, f, 𝒟ℬ, Iᵢ)
15:     ℐ   ← ℐ ∪ {a}
16:   end for
17:   if g mod M = 0 and g > 0 then
18:     MigrateStrategies(...)      # top 10% of each island → neighbour island
19:   if g mod K = 0 and g > 0 then
20:     ManageInsights(ℐ)           # deduplicate, consolidate insights
21: end for
```

#### 3.1.2. The four agents

| Agent | When it runs | Job |
|---|---|---|
| **① Data Agent** | Init, **once** | Analyze the data universe (format, schema, columns, metadata) → produce a **Data Schema Prompt**. In parallel: identify `C` **feasible strategy categories** from the available data (momentum, arbitrage, breakout, seasonality, mean-reversion…). The Coding Team generates a seed strategy per category + 1 buy-and-hold benchmark → **N = C + 1 islands** |
| **② Research Agent** | Every generation | Generate a new hypothesis from: (1) the hypothesis/code/results/analysis of **parent + cousins**, (2) the Data Schema Prompt, (3) **accumulated insights** from previous generations |
| **③ Coding Team** | Every generation | Hypothesis → Python. 4 stages: Initial Implementation → Backtesting → **Iterative Refinement** → Performance Reporting. Writes only into the template's **evolvable region** (§3.3.1). Refines with **two patch modes**: SEARCH/REPLACE (default ~70%) and rewriting the whole evolvable region (~30%, increasing on stagnation); patches are applied **strictly** — one failed block fails the whole patch; failures → **multi-turn retry with the error message**, up to 3. Runs in the **sandbox** (§3.3.3) |
| **④ Evaluation Team** | Every generation | 5 functions: Hypothesis Analysis → Code Analysis → Backtest Analysis → Insight Extraction → **Insight Management** (every 50 gens: deduplicate, consolidate). 🆕 Runs with a **fresh context** — see note below |

**A hypothesis must have exactly 6 components** (the Research Agent emits XML-like tags):

```xml
<hypothesis>        A testable claim grounded in financial theory
<rationale>         Why this hypothesis — cite specific concepts/indicators
<objectives>        Quantitative targets, used as evaluation criteria
<expected_insights> What we learn whether the hypothesis holds or fails
<risks_limitations> Risks: data bias, overfitting, abnormal market conditions
<next_step_ideas>   Variants/extensions for the next generation
```

> 🔑 This is the mechanism against **post-hoc rationalization**: the hypothesis is written **before** the code, and the Evaluation Team grades whether the code actually implements the stated hypothesis.

> 🆕 **The Evaluation Team must have a fresh context** (following AgonAlpha — [[08-LLM-QUANT-RESEARCHER]] §5.3).
>
> It receives **exactly three things**: (1) the frozen hypothesis, (2) the final code, (3) the raw backtest results.
>
> It does **NOT** receive: the refinement history, the failed attempts, or any explanation from the Coding Team.
>
> **Why:** an agent that sees the search process gets persuaded by the story the Coding Team tells about itself — *"I tried 5 approaches, this one was best"* sounds like evidence, but it is exactly best-of-N. This is **temporal isolation applied to the agent itself**, not just to the data.
>
> Alongside this: the Evaluation Team holds an **absolute veto** — if any reported number fails to reproduce on re-run, the verdict is `REJECT_FABRICATION`, written to the ledger, with no appeal.

#### 3.1.3. Feature map — MAP-Elites

**6 dimensions** (Table 1 of the paper); each cell keeps the **best strategy** for that feature vector:

| Dimension | Description |
|---|---|
| Strategy Category | Momentum, mean-reversion, arbitrage… — **binary encoded** (momentum + mean-rev = `101`) |
| Trading Frequency | Trades per period |
| Maximum Drawdown | Largest peak-to-trough decline |
| Sharpe Ratio | Risk-adjusted return |
| Sortino Ratio | Penalizes downside only |
| Total Return | Cumulative profit |

> ⚠️ **Key ablation — use 16 bins, no fewer.** The 16-bin configuration keeps improving (SR 1.52, CR 256% at gen 150); **1-bin and 4-bin converge early then stagnate** (SR falls to 1.12 and 1.06). Fewer bins → genuinely different strategies are forced to compete for the same cell → diversity is destroyed.
>
> 🆕 **Fixed bin bounds (v0.4).** The bounds of every continuous dimension (Sharpe, MDD, frequency…) are declared in `evaluation.lock.yaml` **before the campaign starts**; out-of-range values fall into the first/last bin. **Do not** let bounds stretch with observed values as MadEvolve does (§3.1.10, X4): when bounds change, old elites sit in the wrong cell and nobody notices.
>
> ⚠️ **The Strategy Category dimension is mandatory.** Without it the dominant bin swallows **46.3%** of all strategies; with it the distribution is balanced (top bin 17.9%).

**Extensions for a multi-market project** (the paper explicitly states the feature map is extensible):

| Added dimension | Reason |
|---|---|
| **Asset class** | crypto / forex / equity index / futures — prevents every strategy piling into crypto |
| **Holding period** | intraday / swing / position |
| **Roll yield** *(futures)* | suggested by the paper for futures |

#### 3.1.4. Parent & cousin sampling

**Parent** — pick a random island, then one of two ways (Eq. 1):

```
P(s_p = s) = α / |M_I|     if s ∈ M_I   (best parent — from the feature map)
             (1-α) / |I|    if s ∈ I     (diverse parent — whole population)
```

High α → strong selection pressure; low α → more diversity. **The paper uses α = 0.5.**

**Cousins** — 3 types, to enrich the Research Agent's context:

| Type | Selection | Count |
|---|---|---|
| **Best Cousins** | High-performing strategies from the parent's island | **2** |
| **Diverse Cousins** | Near the parent in **feature space** (Eq. 2) | **3** |
| **Random Cousins** | Uniformly random from the parent's island | **2** |

Diverse cousins are produced by perturbing the parent's feature vector (Eq. 2):

```
f_c^d = ⌊𝒩(f_p^d, σ_d²)⌋        if dimension d is continuous     (σ_d = 1.0)
        BitFlip(f_p^d, k_bf)      if d is strategy category        (k_bf = n/4)
```

#### 3.1.5. Island model & migration

- `N = C + 1` islands, each seeded with **a strategy from a different category**
- They evolve **independently** at first → developing deep expertise per direction
- Every `M` generations: **migrate the top 10% of each island to a neighbour island**
- 🆕 **Prove the islands actually run** (MadEvolve lesson, §3.1.10 X3 — the island code exists but is never called): log each parent's island; an integration test asserts (a) the parent is drawn from the island being processed, (b) migrants are not duplicated on the destination island, (c) a migrant's children belong to the destination island
- Effect: the centre of gravity shifts from *depth within each category* to *breadth across the whole space*

#### 3.1.6. 🔴 Six mandatory deviations from the paper

QuantEvolve admits it itself: *"strategies generated by the framework may be susceptible to data snooping bias. Future work will integrate more sophisticated and rigorous validation methodologies."* That gap is exactly what this project exists to fill.

| # | What the paper does | What we must change it to | Why |
|---|---|---|---|
| **1** | `Score = SR + IR + MDD` (weights 1:1:1), **no deflation** | `Score = DSR(returns, N_eff_current) − λ₁·AST_sim − λ₂·n_params`, **reject if PBO ≥ 0.5** (PBO over the candidate's own parameter grid, §3.2). ⚠️ This is a **ranking score for the search**, not a gate — the real DSR gate is ⑤, on the portfolio | 150 generations × N islands = thousands of trials. Without deflation, Score only measures luck ([[07-VALIDATION-LAYER]]) |
| **2** | Look-ahead prevented **by prompt alone**: *"The strategy must avoid Lookahead Bias… This is the most important constraint"* | **Structural guardrail**: AST scan blocking `.shift(-n)`, indicator whitelist, leaky-oracle test suite | A prompt is not an enforcement mechanism. LLMs still generate code that reads the future |
| **3** | Does not count trials | **Two separate records:** (a) an **audit log** of *all* activity — LLM calls, compile failures, AST rejects; (b) **statistical trials** = every configuration whose **performance was measured** on data (including ones the feature map rejects after backtesting) | `N` determines DSR. A strategy that was backtested and then rejected is still a trial (P2). But a reviewer call or a file that doesn't compile is **not** a test on the data — counting them into `N` biases DSR the other way (§4.1) |
| **4** | Zipline: `initialize(context)` + `handle_data(context, data)` | **`Signal(strength, stop_distance)`** on NautilusTrader | P3 + P5 — venue-agnostic, backtest ≡ live |
| **5** | Free-form Python, the LLM decides what to use | **Constrained DSL** + a deterministic engine owning the evaluation rules. The agent **may not modify** data splits, gate thresholds, or metric definitions during a session | Narrowing the search space ⇒ **fewer trials spent** ⇒ a more forgiving DSR bar. And it is a *structural* guardrail, not a promise (Crypto Constrained Agents, [[08-LLM-QUANT-RESEARCHER]] §7.3) |
| **6** | The Evaluation Team sees the entire search history | **Fresh-context reviewer** + **veto power** | An agent that sees the history gets persuaded by the story the Coding Team tells. This is *temporal isolation applied to the agent itself* (AgonAlpha, [[08-LLM-QUANT-RESEARCHER]] §5.3) |

> 🔴 **The fatal detail about how DSR is computed — read carefully.**
>
> The feature map produces hundreds of cells, one strategy each. The natural temptation is to compute DSR **per cell** and keep the cells that clear the threshold. **That reintroduces selection bias through the back door** — you have just run best-of-N again, precisely what DSR exists to prevent.
>
> **The correct rule:** compute DSR on the **return series of the consolidated portfolio** — built by the **pre-registered rule** in §3.2.1, not picked by hand — with statistical inputs taken from the **statistical trial record** (§4.1), not the audit log:
>
> - `N_eff` = the number of **effectively independent** trials, estimated by clustering the return series of every performance-measured trial (Bailey & López de Prado explicitly distinguish the *actual* number of trials from the number of *independent* trials). `N_raw` is always reported alongside; if `N_eff` cannot be estimated yet, use `N_raw` (conservative).
> - `V[SR]` = the variance of Sharpe **across trials** — DSR needs it to compute the expected maximum Sharpe under the null.
> - **Add the number of portfolio variants tried** (`portfolio_variants`, §3.2.1). Changing the cell-selection rule or the weights and re-running is also selection.
>
> The deeper reason: the purpose of the feature map in this project is **not** to find one best strategy, but to **fill ~20 weakly-correlated cells to obtain breadth** (Sharpe 0.4 → 1.6, [[04-SCHOOL-B-EFFICACY]]). If the output is a portfolio, the object under test must be that portfolio. ⚠️ But merely switching the return series to the portfolio is **not enough** to show selection bias is controlled — it takes both a fixed portfolio-construction rule and a count of the portfolio variants tried.
>
> **Budget consequence:** Alpha-R1 (Sep 2026) — the only one of 22 surveyed systems that dares report DSR — only reaches **DSR 0.39–0.85 at 34–158 trials**, i.e. it **fails the 0.95 threshold**, despite training on 64×H800 for 120 hours. Our plan calls for **4,500–9,000 *LLM calls*** per full run (§3.1.8). The statistical trial count will be lower (one strategy consumes many calls; many failed generations never reach a backtest), and `N_eff` lower still because trials within a cell are strongly correlated — but it will still be on the order of **hundreds to thousands**, many times Alpha-R1's.
>
> ⇒ **Trial count is the scarcest budget in this project — scarcer than money or time.** Every mechanism that reduces the trials required (insight repository, restricted DSL, good seeds) is worth **double**: it saves cost *and* lowers the Sharpe bar you must clear.

**Kept from the paper** (these constraints are already good; port them straight into our template):

- *"When implementing trading logic, you can only use past and present data"*
- *"You have to order less than or equal to the available cash"*
- *"Properly handle exceptions and edge cases (e.g., insufficient data, contract rollover)"*
- Fees/slippage configured in `initialize`, never inside strategy logic
- Code runs in an **isolated subprocess**; errors are fed back for patching (not a full rewrite) — 🆕 isolated means the **sandbox** of §3.3.3, not merely a separate process

**Our additional constraints:**

- Only indicators on the **whitelist** (verified non-repainting)
- Only **closed** bars may be read
- Rules must be **scale-invariant** — returns/ATR/z-scores, **never absolute price levels** (so that rolling a continuous contract doesn't break the logic)
- **At most 6 free parameters** (AlphaAgent-style complexity control) — 🆕 counted through `TUNABLE` declarations (§3.3.1); an undeclared numeric constant in the evolvable region ⇒ AST reject
- 🆕 **Closed DSL:** strategies may only be composed from registered operators (`indicator/*`, `compare`, `cross`, `and/or/not`, `atr_mult`). Any call outside the DSL is AST-rejected **before execution** — not after a backtest
- 🆕 **Deterministic engine owns the evaluation rules:** data splits, gate thresholds and metric definitions live in a **read-only config file, hashed before each session**. The agent has no write path to it
- 🆕 **Append-only trace:** every experiment appends; nothing is ever edited or deleted

> ⚠️ **Risk not yet handled in the current design: the feature map will fill unevenly, biased toward the easy regions.**
>
> SysTradeBench measured a **Spearman ρ = −0.68 (p < 0.05)** correlation between strategy complexity and valid-code generation rate: Double MA Crossover **88.2%**, Index Enhancement **35.3%**.
>
> Our feature map has a `strategy category` dimension whose purpose is to push the system toward diversity — i.e. toward increasing complexity. But the valid-code rate **falls with complexity**. Consequence: cells corresponding to complex strategies will **stay empty far longer, not because they are worse but because the code doesn't run**. The feature map will *look* thoroughly explored when in fact only the easy region got filled.
>
> **Mitigation:** the ledger records `gen_attempts` and `gen_failures` per cell (§4.1). A cell with a code-generation failure rate **> 50%** is a signal to **allocate more refinement budget to that cell**, not to conclude that the region holds no good strategies.

#### 3.1.7. Preventing spec drift across refinement passes

The Coding Team has 4 stages, of which **Iterative Refinement** runs multiple passes. The risk: the agent quietly **edits the hypothesis** to match the code that happens to run, instead of fixing the code to match the hypothesis. That is post-hoc rationalization in its most subtle form — and the 6 XML tags **do not stop it**, because the agent is allowed to rewrite the tags.

A two-layer mechanism (idea from SysTradeBench; v0.3 adds the specification the paper leaves out):

```
Layer 1 — static checksum (does NOT execute code)
    SHA256 over the normalized core logic fields: entry rule, exit rule, parameter values
    Any change ⇒ must be declared in <change_log>, never silent

    ── ①a AST/DSL guardrail runs HERE, before layer 2 ──
    Code that has not passed the static check is NEVER executed, not even on micro-scenarios

Layer 2 — trace regression (runs in a sandbox, after ①a)
    Micro-scenario set: fixed, versioned in evaluation.lock.yaml (~20 synthetic bar series:
        uptrend/downtrend, sideways, gap, spike, missing bars)
    Trace: each bar → one token ∈ {L, S, F, X} (long / short / flat / exit), concatenated across scenarios
    Reference trace: a FIXED ANCHOR = the trace of the hypothesis's first implementation
        (not the previous pass — comparing to the previous pass allows drift in small steps,
         each < 0.05 but adding up to a completely different strategy)
    Δ = Levenshtein(new_trace, anchor_trace) / max(len(new_trace), len(anchor_trace))   ∈ [0, 1]

    Δ < 0.05         → technical bug fix, allowed
    0.05 ≤ Δ < 0.15  → flagged, needs human review
    Δ ≥ 0.15         → suspicious divergence → ABORT the pass, ledger verdict REJECT_DRIFT
```

> This is **gate ⓪** — it runs *inside* the agent loop on every refinement pass. The correct order: **⓪ layer 1 → ①a static AST/DSL → ⓪ layer 2 (sandbox) → ①b leaky-oracle test**. Version 0.2 put ⓪ ahead of all of ① — wrong, because layer 2 has to *execute* code.
>
> ⚠️ The 0.05/0.15 thresholds are **provisional defaults**. They only mean something once Δ is normalized as above; they must be calibrated in phase 2 against a hand-labeled set of edits (pure bug fix vs logic change) before being used to abort automatically.

#### 3.1.8. Operating parameters

| Parameter | Paper value | We use | Note |
|---|---|---|---|
| Bins per dimension | **16** | 16 | Ablation: 1/4 bins converge early |
| α (exploit/explore) | 0.5 | 0.5 | |
| σ_d (diverse cousin) | 1.0 | 1.0 | |
| k_bf (bit flip) | n/4 | n/4 | n = bit length of the category encoding |
| Cousins per parent | 2 best + 3 diverse + 2 random | as in paper | |
| Migration interval `M` | — | 10 gens | Paper doesn't state it; 10 is a reasonable starting point. 🆕 With the asynchronous pipeline, "1 gen" = **N islands' worth of evaluated candidates** — migration and curation (`K`) count candidates, not wall-clock time |
| Insight curation `K` | **50** | 50 | |
| Generations `G` | 150 (equity), 100 (futures) | ≥150 | SR 1.52 is only reached at gen 150 |
| LLM inferences/cycle | **5–10** | — | Used for cost estimation |
| 🆕 Parallel LLM producer threads | — | 1–2 (local GPU) | Asynchronous pipeline: LLM threads push candidates into a prefetch queue, backtest slots (CPU) run in parallel |
| 🆕 Parallel backtest slots | — | = cores − 1 | |
| 🆕 Seeds per engine configuration | — | ≥ 3 | MadEvolve paper §6.3: a slightly changed prompt dropped the OOS improvement from +627% to +44%. Report the **distribution** across seeds; every seed adds to `N` |

> ⚠️ **Cost:** 5–10 LLM inferences × 150 generations × N islands. With N=6 islands → **4,500–9,000 LLM calls** for one full run. The paper itself names this as its scalability limit.
>
> ⚠️ **These are LLM calls, not trials.** One strategy consumes many calls (research, code, refine, review); a call that does not lead to a performance-measured configuration goes only into the audit log. `N` for DSR comes from the statistical trial record (§4.1).

#### 3.1.9. Heterogeneous model routing

> 🔴 **Correction (21 Sep 2026).** An earlier version of this section recommended "use small models for most calls". **That recommendation was wrong on two levels** — see [[08-LLM-QUANT-RESEARCHER]] §6.
>
> **Wrong on level 1:** the paper's ensemble is **Qwen3-30B-A3B**, an MoE model activating only **3.3B parameters/token**. The paper was *already* running at 3B inference cost. There were no savings left to capture.
>
> **Wrong on level 2 (more serious):** CogAlpha's ablation shows **Llama3-8B produces factors with RankIC = −0.0074** — systematically wrong-signed, worse than doing nothing. And `gpt-oss-20B` is **worse than** Llama3-8B on IC. **Parameter count is not the right axis.**

**The counterintuitive finding to design around:**

| Model | IC | RankIC | Source |
|---|---|---|---|
| Llama3 8B | 0.0121 | **−0.0074** | CogAlpha |
| gpt-oss-20B | 0.0061 | 0.0075 | CogAlpha |
| **gpt-oss-120B** | **0.0300** | **0.0318** | CogAlpha |
| GPT-4.1 | 0.0118 | 0.0114 | CogAlpha |
| **o3** (reasoning) | **0.0019** | **−0.0050** | CogAlpha |

> 🔑 **The strongest reasoning model is the worst performer — on this benchmark.** One *plausible* explanation (consistent with QuantEvolver's argument about *"stable generation preferences"* causing search stagnation):
>
> The Research Agent's job is **not** to find the right answer. It is to **generate many diverse hypotheses, most of which will be wrong**, so that the selection mechanism can do its work. Reasoning models are optimized to *converge* — and here, convergence **is** mode collapse. The more "certain" the model, the emptier the feature map.
>
> This is also the deep reason MAP-Elites exists: it is a **mechanism that counteracts the LLM's natural tendency to converge**.
>
> ⚠️ **Limits of the evidence (v0.3):** CogAlpha is a **cross-sectional factor-generation benchmark on CSI300** (school A), one run per model. It does **not** establish mode collapse as the cause, and does **not** show the result transfers to multi-market rule-based TA. The recommendations below are therefore **initial defaults + hypotheses**, to be A/B-tested internally in phase 2 (measured on feature-map coverage, valid-code rate, and IS Sharpe distribution at the same trial budget).

**Routing table:**

| Agent | Model | Reason |
|---|---|---|
| **① Data Agent** | The largest context window available | Runs once, must read the entire schema |
| **② Research Agent** | A strong model, **non-reasoning by default**. `gpt-oss-120b` is a starting point with (external) evidence. Reasoning models are an **A/B arm**, not banned | Needs diversity, not convergence — a hypothesis from CogAlpha, pending internal validation |
| **③ Coding Team** | A coding-specialized model, **Mid tier** per SysTradeBench (GLM-4.6, DeepSeek-V3, Claude Sonnet class) | 90–95% of Top-tier quality at 40–50% of the cost |
| **④ Evaluation Team** | Mid tier + **constrained decoding** | Grades against a fixed schema; creativity is not needed |

> ⚠️ **Default: no non-fine-tuned dense model under 10B** (negative RankIC in CogAlpha). Same evidence limits as above — reopen if the internal benchmark says otherwise.
> ⚠️ **Do not do RFT/RL fine-tuning.** Alpha-R1 consumed 64×H800 for 120 hours — out of reach under A6 (local execution).
>
> Note a useful contradiction: **o3 is Top tier on SysTradeBench** (writing code that matches a spec) but **bottom of the table on CogAlpha** (inventing good factors). Not a contradiction at all — two different skills. That is precisely why routing must be heterogeneous rather than "pick the best model".

#### 3.1.10. 🆕 Lessons from the MadEvolve repo

> Source: read directly from [`tianyi-stack/MadEvolve`](https://github.com/tianyi-stack/MadEvolve) (commit `8b881d3`, 3 Mar 2026, ~12.6k lines of Python, MIT) on 21 Sep 2026. Read: `engine/orchestrator.py`, `repository/topology/*`, `repository/selection/*`, `synthesizer/composer.py`, `transformer/{blocks,patcher,parallel}.py`, `provider/strategy/allocation.py`, `executor/runners/native.py`, `engine/configuration.py`. Not read: `analyzer/*`, the SLURM runner, prompt templates.

**Worth learning — five mechanisms, ✅ brought into the spec in v0.4** (L1, L2 → §3.3.1 · L3 → §3.3.2 · L4 → §3.1.2 · L5 → §3.1.8):

| # | Mechanism in the repo | How it applies to us |
|---|---|---|
| **L1** | **`EVOLVE-BLOCK-START/END`**: only the region between the markers may be edited; the infrastructure outside stays fixed | The strategy template gets 2 regions: **outside the block** = the `Signal` contract, fees/slippage, data access (fixed); **inside the block** = signal logic. ⚠️ The repo only *asks* via the prompt — we **enforce** it: hash(prefix + suffix) must be unchanged after every patch, otherwise `AST_REJECT` |
| **L2** | **`# TUNABLE: name = v, bounds=(a, b)`** — declares free parameters with bounds | Exactly what §3.2 needs: **the PBO grid comes straight from the TUNABLE declarations**; `n_params` for the complexity penalty is counted here; the ≤ 6-parameter rule is enforced here. An undeclared numeric constant inside the block ⇒ reject |
| **L3** | **`public_metrics` vs `private_metrics`**: the evaluator returns two groups; only public ones reach the prompt, private ones are only stored | Everything beyond IS (CPCV paths, PBO, robustness results) is **private** — the agent never sees it. ⚠️ The evaluator's `text_feedback` channel also goes straight into the prompt ⇒ it must be generated **from public metrics only** |
| **L4** | **Two patch modes**: SEARCH/REPLACE (~70%) + rewrite the whole block (~30%), with more rewrites when stagnating; a failed patch → **multi-turn retry with the error message**, up to 3 | Use it for the Coding Team (Iterative Refinement). Diff mode naturally keeps drift Δ small (§3.1.7). ⚠️ The repo accepts **partially** applied patches (failed blocks are skipped) — we use `strict=True`: one failed block ⇒ the whole patch fails |
| **L5** | **Asynchronous pipeline**: LLM producer threads push candidates into a prefetch queue; evaluation slots run in parallel; a "generation" is a budget counter | Fits local execution: the LLM (GPU) and backtests (CPU) don't wait on each other. Migration and insight curation must then be defined by **number of evaluated candidates**, not by synchronous generations |

**Do not copy — seven weaknesses, each violating one of our principles** (✅ countermeasures now in the spec: X1, X2 → §4.1 · X3 → §3.1.5 + phase 2 · X4 → §3.1.3 · X5 → §3.3.3 · X6 → §3.1.9 · X7 → §3.2):

| # | Weakness in the code | Violates | What we do instead |
|---|---|---|---|
| **X1** | **Failed evaluations are not recorded** in the store — only printed to the console | P2 | Every failure goes into `generation_log` (§4.1) |
| **X2** | The inner-loop optimizer calls the evaluator dozens of times per candidate (`generation=-1`) **without recording them** — hidden trials | P2, DSR | Every optimizer evaluation is a row in `trials` — it *is* a statistical trial |
| **X3** | **The island model is dead code**: `get_selection_pool()` returns `[]`, the parent always comes from the global top-20. Migration also accumulates duplicate copies, and a migrant's children return to the source island | — | Requires an **integration test**: an empty island ⇒ no children can come from it; the per-island parent distribution is logged and checked |
| **X4** | Feature-map dimensions = code length (characters) + embedding distance + an aggregate fitness score; bin bounds **stretch with observed values**, so old elites sit in the wrong cell when bounds change | — | Keep QuantEvolve's 6 behavioural dimensions (§3.1.3) with **fixed bounds** in `evaluation.lock.yaml` |
| **X5** | Generated code runs via `subprocess` with **the full `os.environ`** ⇒ LLM code can read API keys; no network blocking, no file limits | A6, safety | Sandbox: empty env, no network, read-only data mount, timeout + memory limit |
| **X6** | Model bandit: `record_outcome()` never passes `improvement` ⇒ with the default UCB (`use_improvement=True`) the mean is always 0; reward = "beat the global best" — which rewards luck | §3.1.9 | A/B models with **fixed allocation**, measuring coverage + valid-code rate, not IS Sharpe |
| **X7** | `best.py` = max `combined_score` on the same evaluation data; no deflation, no data split in the repo | P6, DSR | As in §3.2 — no "best" leaves the gate pipeline |

> **Conclusion:** the repo is worth using as a **source of engineering patterns** (L1–L5 are all cheap and proven to run), **not** as a code base — its quality-diversity part (X3, X4) is looser than the QuantEvolve paper we follow, and its trial recording (X1, X2) runs against P2. How to use it: reimplement to our design, referring to `transformer/patcher.py`, `transformer/blocks.py` and `transformer/parallel.py` (MIT — copying with attribution is allowed).

#### 3.1.11. 🆕 Multiple strategy-generation engines

The system runs **several candidate-generation engines** in parallel, sharing everything downstream: template, DSL, sandbox, gates, ledger, the portfolio-construction rule (§3.2.1), holdout. Only *generation* differs.

```
                    ┌─► Engine A: 4-agent QuantEvolve ──┐
config/user.yaml ───┤                                    ├─► scheduler ─► sandbox ─► gates ⓪–④ ─► ledger
 (budget shares)    ├─► Engine B: simple loop ──────────┤  (enforces shares)                         │
                    └─► Engine C: random search ────────┘                                              ▼
                                                               portfolio built from candidates passing ④ from ALL engines
```

| Engine | How it generates | LLM calls / candidate | Role |
|---|---|---|---|
| **A — QuantEvolve** | §3.1.1–3.1.9: Research Agent (6-tag hypothesis) → Coding Team → Evaluation Team → insight repository | 5–10 | Main system: deep, hypothesis-driven, accumulates knowledge |
| **B — Simple loop** | MadEvolve-style: pick a parent (rank-based power law) + 2–3 inspirations → **one** LLM call returning a diff or a rewrite of the evolvable region → evaluate → archive. No hypothesis, no Evaluation Team, no insights | 1 (+ retries) | Cheap, produces many small variants; the control for the agent layers |
| **C — Random search** | No LLM: random combinations of DSL operators, random parameters within `TUNABLE` bounds | 0 | Control for "does the LLM beat chance at all" — kept for the whole project at a small share |

**Two modes:**

| Mode | When | Archive | Purpose |
|---|---|---|---|
| `isolated` | Phase 2 — the harness-test campaign | One archive per engine, **no exchange** | Clean comparison |
| `collaborative` | From phase 3 | Separate archives, **top 10% periodically transferred** between A ↔ B (like island migration). C receives no migrants | A contributes depth, B contributes variant volume |

**Budget is split by statistical trials**, not by LLM calls or GPU hours — otherwise engine B (5–10× cheaper) would consume all of `N`. The scheduler in the asynchronous pipeline (§3.1.8) enforces the shares declared in `user.yaml` (§10.1). Phase 2 uses **fixed shares**. After phase 2, allocation may adapt to **trial efficiency** (strategies passing ④ per 100 trials) — **never** to IS Sharpe.

**The phase-2 comparison** (`isolated` mode, same data, same gates, same trial budget, ≥ 3 seeds per engine). Compared on:

1. Trial efficiency: strategies passing ④ per 100 trials
2. Coverage: feature-map cells holding a strategy that passed ④
3. DSR of a portfolio built per §3.2.1 from each engine alone, at the same `N`
4. Cost: LLM calls and GPU hours per strategy passing ④
5. IS→OOS degradation on CPCV (§3.2)

**Do not** compare the IS Sharpe of the best strategy — that is best-of-N.

**Decision rule — locked before running:**

| Outcome | Action |
|---|---|
| A beats both B and C by more than the seed-to-seed spread | Keep A as the main engine; B runs alongside in `collaborative` mode |
| A **ties** with B | **B becomes the main engine** (5–10× cheaper). Re-add A's components (e.g. the 6-tag hypothesis) only if a dedicated ablation shows they pay off |
| Neither A nor B beats C | The problem lies in the DSL, the data, or the gates — **stop and find the cause** before building further |

> ⚠️ **Running in parallel does not give free trials.** Every engine evaluates on the same IS data ⇒ every trial adds to the same `N`. The benefit is diversity + a permanent control, not volume. Strategies produced in the harness-test campaign (phase 2) **may not enter the portfolio**.

### 3.2. Validation Layer

Full detail in [[07-VALIDATION-LAYER]]. Contract summary:

```python
@dataclass(frozen=True)
class GateResult:
    passed:  bool
    gate:    str            # "drift" | "guardrail" | "minbtl" | "pbo" | "dsr" | ...
    value:   float | None
    reason:  str

class Gate(Protocol):
    cost: int               # 1=cheap ... 5=expensive — used for ordering
    def check(self, candidate: StrategyCandidate) -> GateResult: ...
```

**Rule:** gates run in ascending `cost` order. **Every `GateResult` is written to the ledger**, including passes.

**Full ordering:**

| # | Gate | cost | Applies to | Note |
|---|---|---|---|---|
| **⓪** layer 1 | 🆕 Spec-drift: static checksum | 1 | Each strategy | Does not execute code (§3.1.7) |
| ①a | Static guardrail (AST + DSL + template hash) | 1 | Each strategy | **Must pass before any code is executed.** 🆕 Checks the fixed region is unchanged + `TUNABLE` declarations are valid (§3.3.1) |
| **⓪** layer 2 | 🆕 Spec-drift: trace regression | 1 | Each strategy | Sandbox, fixed micro-scenarios, normalized Δ (§3.1.7) |
| ①b | Dynamic guardrail (leaky-oracle test) | 2 | Each strategy | |
| ② | MinBTL | 1 | Each strategy | |
| ③ | Backtest IS | 3 | Each strategy | 🆕 From here on, the strategy counts as **one statistical trial** (§4.1). 🆕 v0.5: reject if **trade count < `min_trades`** or **average holding time < `min_holding`** (§10.1) — blocks inflating Sharpe by trading very rarely (MadEvolve §3.4); reject on **indicator-correlation violations** (§3.3.1) |
| ④ | CPCV + PBO | 4 | Each strategy (parameter grid) | Reject if PBO ≥ 0.5. Configuration set: see below |
| — | 🆕 Portfolio construction | 3 | Portfolio | Pre-registered rule, §3.2.1 |
| **⑤** | **DSR** | 4 | 🔴 **CONSOLIDATED PORTFOLIO** | `N_eff` + `V[SR]` from the statistical trial record, plus `portfolio_variants`. **Never per cell** — see §3.1.6 |
| ⑥′ | Data-source robustness + 🆕 cost sensitivity | 4 | Portfolio | 🆕 Re-run with **fees × 2** and **slippage × 2**: the portfolio must keep Sharpe > 0 and DSR must not drop past a campaign-locked threshold |
| ⑥ | Holdout | 5 | Frozen portfolio | Once **per research campaign**, via `evaluator_proc.py` (§4.2) |
| ⑦ | Dry-run | 5 | Portfolio | |
| ⑧ | Live | — | Portfolio | |

> Note the "applies to" column: gates ⓪–④ filter **individual candidates**, but from ⑤ onward the object under test is the **portfolio** — because this system's output is a basket of weakly-correlated strategies, not a single strategy. 🆕 **Execution order follows this table, not ascending `cost`** — the two disagree (② has cost 1 but runs after ①b); see [ADR-0002](../implement_docs/adr/0002-ledger-and-config-additions.md).

**🆕 What PBO actually tests (v0.3).** CSCV needs a **performance matrix** `T × M` over `M` compared configurations *and* a **winner-selection rule**. PBO measures the probability that this selection rule picks a configuration whose OOS rank falls below the median. It is not a property of a single return series. For gate ④ we define:

- **Configuration set** = a **pre-registered parameter grid** around the candidate, 🆕 built automatically from the `TUNABLE` declarations (§3.3.1): each free parameter (≤ 6) takes 3–5 values within ±30% (fixed step in `evaluation.lock.yaml`), plus the variants the refinement loop *actually tried* for this hypothesis (read from the ledger). `M` is capped (e.g. ≤ 200, sampled if exceeded).
- **Selection rule** = highest IS Sharpe — the same rule the evolution loop uses.
- **Meaning:** low PBO ⇒ the parameter-selection rule is OOS-stable in this region. High PBO ⇒ the chosen parameters won on noise. PBO does **not** replace DSR: it does not penalize the number of hypotheses tried across the project.
- At portfolio level, if several portfolio-construction rules are tried (§3.2.1), run an additional CSCV whose configuration set is those portfolio variants.

**🆕 IS→OOS degradation curve (v0.5, after MadEvolve Figure 11 — but without the holdout).** Every `K` candidates, take each engine's current IS record holder and record its median Sharpe across the **CPCV OOS paths** (a *private* metric, §3.3.2). Plot two lines: the IS record and that same strategy's CPCV-OOS. An OOS line that is flat or falling while IS rises = a p-hacking signal ⇒ **stop that engine early**. MadEvolve measures this curve on the *test set* for every champion — for us that would mean opening the holdout thousands of times, violating P6.

#### 3.2.1. 🆕 Building & selecting the portfolio

Version 0.2 required DSR on the portfolio but never said how the portfolio is built — leaving a large degree of freedom open for selection bias. The rule below is **pre-registered** in `evaluation.lock.yaml`, hashed together with the other thresholds:

```
Input: every candidate that passed ④ (PBO < 0.5) as of the freeze point

1. Cell representative  Each cell keeps exactly 1 candidate: the highest DSR-rank in the cell
2. Correlation filter   Sort representatives by DSR-rank, descending; accept in order,
                        drop any candidate with |ρ| > 0.5 (IS returns) against an accepted strategy
3. Size cap             At most K = 20 strategies (breadth target ~20 cells, [[04-SCHOOL-B-EFFICACY]])
4. Weights              Naive risk parity: every strategy gets the same vol budget (§3.4) —
                        do NOT optimize weights on IS Sharpe (extra degrees of freedom, overfitting)
5. Rebalancing          On a fixed schedule (default: monthly) + when a strategy enters/leaves
5b. Calibration (optional) One Optuna pass over each selected strategy's TUNABLE region,
                       fixed budget; EVERY evaluation recorded in trials (source = param_opt).
                       PBO and DSR are RE-computed on the new parameters afterwards
6. Freeze               Hash(set of strategy_hash + weights + rule + parameters) → portfolio_hash
```

- **Changing any step** (ρ threshold, K, representative choice, rebalancing schedule) and re-evaluating = **a new portfolio variant**, recorded in the `portfolio_variants` table (§4.1) and **added to `N`** at gate ⑤.
- The 0.5 / 20 / monthly parameters are **provisional defaults** — but they must be locked *before* the first portfolio evaluation, not tuned after seeing DSR.

### 3.3. Strategy Runtime — the core contract

This is the most important interface in the whole system. It is what makes P3 + P5 possible.

```python
@dataclass(frozen=True)
class Signal:
    direction:     Literal["long", "short", "flat"]
    strength:      float          # ∈ [0, 1] — degree of participation; NOT a quantity, NOT % of capital
    stop_distance: float          # price units (typically k × ATR) — used for exits + the risk cap
    take_profit:   float | None = None

class Strategy(Protocol):
    """Venue-agnostic. Knows NOTHING about lots, shares, contracts, or currency."""
    params: dict

    def indicators(self, bars: Bars) -> Features: ...
    def signal(self, features: Features) -> Signal: ...
```

> 🔴 **A strategy must never import anything from the adapter layer.** This is a hard architectural boundary — enforce it with a lint rule, not just convention.

#### 3.3.1. 🆕 Strategy template: fixed region + evolvable region

Following MadEvolve's `EVOLVE-BLOCK` mechanism (§3.1.10 L1) and `TUNABLE` (L2) — but **enforced in code**, not by prompt.

```python
# ═══ FIXED REGION — the agent may NOT edit this. Hash committed in evaluation.lock.yaml ═══
from core.strategy.base import Signal, Strategy, Bars, Features
from core.strategy.registry import ind            # whitelisted indicators only

class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START ═══
    # TUNABLE: fast = 20,  bounds=(5, 60)
    # TUNABLE: slow = 100, bounds=(40, 300)
    # TUNABLE: k_atr = 2.0, bounds=(1.0, 4.0)

    def indicators(self, bars: Bars) -> Features:
        return {"f": ind.ema(bars.close, self.p.fast),
                "s": ind.ema(bars.close, self.p.slow),
                "atr": ind.atr(bars, 14)}

    def signal(self, x: Features) -> Signal:
        if ind.cross_up(x["f"], x["s"]):
            return Signal("long", 1.0, self.p.k_atr * x["atr"])
        return Signal("flat", 0.0, self.p.k_atr * x["atr"])
    # ═══ EVOLVE-BLOCK-END ═══
```

**Rules enforced at gate ①a** (any violation ⇒ `AST_REJECT`, recorded in `generation_log`):

1. `SHA256(prefix) ‖ SHA256(suffix)` — everything outside the two markers — must **exactly equal** the value in `evaluation.lock.yaml`. If the LLM returns a whole file instead of just the evolvable region, that file is rejected rather than merged (MadEvolve accepts such files — §3.1.10).
2. Every free parameter needs a `TUNABLE` line with finite `bounds`; at most **6** lines. A numeric constant used as a threshold/period without being declared ⇒ reject (stops parameters being hidden to dodge the ≤ 6 rule). Exception: whitelisted constants (0, 1, the standard ATR period 14…).
3. Only whitelisted `ind.*` calls and DSL operators (§3.1.6).

4. 🆕 **Indicator correlation constraint** (checked at gate ③, since it needs data): any two indicator series in the evolvable region with IS |ρ| > `max_indicator_corr` (default 0.9) ⇒ reject. Per MadEvolve §6.3: constraining the number of features and their pairwise correlation gave more consistent IS/OOS results.

**`TUNABLE` declarations are used in three places:** the PBO grid (§3.2), the `n_params` count in the complexity penalty (§3.1.6 #1), and the space for the parameter-calibration step.

> 🔴 **v0.5 — the parameter optimizer is switched off inside the evolution loop.** Version 0.4 let the Coding Team run Optuna on every candidate as long as trials were recorded. The MadEvolve paper (§3.4) **deliberately does not** do this: re-fitting each candidate's continuous parameters to the evaluation period means *"the effective number of trials per candidate would no longer be a handful of LLM mutations but a continuous sweep of each parameter's local optimum"* — `N` balloons and the search drifts toward over-parameterized code. New rule: inside the evolution loop, **parameters are set by the LLM**, like the logic. Optuna calibration runs **once**, for the strategies selected into the portfolio, **before the freeze** (§3.2.1 step 5b).

**🆕 Module-wise evolution (v0.5).** The evolvable region is split into up to three named blocks, each with its own markers:

```python
    # ═══ EVOLVE-BLOCK-START: entry ═══      entry conditions
    # ═══ EVOLVE-BLOCK-START: exit ═══       exits + stop_distance
    # ═══ EVOLVE-BLOCK-START: regime ═══     market-regime filter (trading on/off)
```

`evolve_scope` in `user.yaml` selects which blocks may be edited; the others are locked and count as part of the fixed region when hashing (rule 1). Uses: (a) debugging — lock entry, evolve only exit; (b) ablation — measure each block's contribution. Per MadEvolve Runs 1–5: joint evolution does **not** always beat component-wise evolution, and the widest joint scope showed the largest overfitting gap. Sizing is **never** an evolvable block — it belongs to the Risk layer (P3, P4). Each evolution scope is a separate run configuration; every trial of every scope adds to `N`.

#### 3.3.2. 🆕 Evaluator contract: public / private metrics

Following MadEvolve (§3.1.10 L3). Every evaluation returns exactly one structure:

```python
@dataclass(frozen=True)
class EvaluationReport:
    public:   dict[str, float]   # IS: Sharpe, MDD, trade count, turnover… → MAY go into prompts
    private:  dict[str, float]   # CPCV paths, PBO, robustness, anything beyond IS → ledger ONLY
    feedback: str                # generated AUTOMATICALLY from `public` — never reads `private`
    error:    str | None
```

- The prompt builder receives only `public` + `feedback`. A test asserts that no `private` key appears in any prompt (string match over the prompt log).
- The Evaluation Team (§3.1.2) also sees only `public` — it judges *whether the code implements the hypothesis*, not *whether the strategy passes validation*.

#### 3.3.3. 🆕 Sandbox for generated code

MadEvolve runs LLM code with a plain `subprocess` that inherits the full environment — generated code can read API keys (§3.1.10 X5). We run all generated code (including gate ⓪'s micro-scenarios) with:

| Limit | Value |
|---|---|
| Environment variables | **Empty**, passing only a minimal `PYTHONPATH` |
| Network | **Fully blocked** |
| File system | IS data mounted **read-only**; writes only to the run's temp directory; **no visibility** of `holdout/`, `config/`, `ledger/` |
| Resources | Timeout (default 300 s), RAM limit, CPU limit |
| Output | Returned only as a JSON file following `EvaluationReport`; stdout/stderr are truncated before being fed back |

On local Windows (A6): use a container (Docker with `--network none`, `--read-only`) — do not hand-roll a sandbox in Python.

### 3.4. Risk & Sizing Layer ★

This layer is **more important than the adapter layer** yet routinely treated as an afterthought.

> 🔴 **v0.3 correction — version 0.2 reduced exposure by volatility TWICE.** The old code computed `risk_amount = equity × risk_pct × target_vol / vol_estimate` and then **also divided by `stop_distance`** (= k × ATR). Both `vol_estimate` and ATR scale with volatility, so when volatility doubles, size drops to **~¼** instead of ½ — which is no longer vol targeting, and the 10% portfolio vol target is not achieved. New rule: **volatility enters the size exactly once**; the stop is only a **cap**, never multiplied in.

```python
class PositionSizer:
    def size(self, signal: Signal, instrument: Instrument,
             account: Account, vol_estimate: float,
             n_active: int, idm: float) -> Quantity:
        # 1. Vol targeting — the ONLY place volatility enters the size
        #    Per-instrument vol budget: portfolio target split evenly, times IDM
        #    (instrument diversification multiplier, compensating for correlation < 1)
        inst_target_vol = self.target_vol * idm / n_active
        notional = account.equity * inst_target_vol / vol_estimate * signal.strength
        qty_vol = notional / (instrument.price * instrument.multiplier * fx_rate)

        # 2. Stop-based risk cap — MIN, not multiply
        #    The loss at the stop may not exceed max_risk_pct of equity
        qty_cap = account.equity * self.max_risk_pct / (
            signal.stop_distance * instrument.multiplier * fx_rate)

        qty = min(qty_vol, qty_cap)

        # 3. Convert to venue units: round to lot step, check min/max
        return round_to_lot(qty, instrument)
```

One more step at **portfolio level** (runs on every rebalance, §3.2.1): estimate portfolio vol from the position covariance matrix; if it deviates from target, **scale all positions uniformly** back to `target_vol`, with a leverage cap. IDM is re-estimated at this step.

**Four things this layer must do:**

1. **Vol targeting** — scale every market to the same volatility target (Hurst-Ooi-Pedersen use 10%/yr). **Without this step, BTC at ~60% vol eats the entire portfolio risk budget and VN30F1M at ~20% vol effectively does not exist**
2. **Stop-based risk cap** — `stop_distance` only bounds the maximum loss per trade (`min`), it does not scale a second time
3. **Unit conversion** — notional → lots/shares/contracts using each venue's multiplier
4. **FX conversion** — account for PnL consistently in one base currency (USD, VND, …)

> Mandatory test in phase 1: double both `vol_estimate` and ATR on a synthetic instrument ⇒ size must drop to **exactly ½** (when the stop cap is not binding).

### 3.5. Execution Core & Adapters

**NautilusTrader** — why it was chosen: *"The DataEngine guarantees 100% identical data handling in both backtesting and live trading"*, deploying *"with no code changes"*. That is P5, exactly.

| Adapter | Markets | Status |
|---|---|---|
| Binance, Bybit, OKX | Crypto spot + perp | ✅ official |
| **Interactive Brokers** | **Forex + international equities + global futures** | ✅ official |
| Databento | High-quality futures data | ✅ official |
| **SSI FastConnect** | **HOSE/HNX, VN30F1M** | 🔴 **build it yourself** — 3–6 weeks |

**🆕 Pessimistic fill model (v0.5).** Backtests use NautilusTrader's `FillModel` configured adversarially, locked in `evaluation.lock.yaml`:

- A limit order fills only when price **trades through** the limit (low < limit for a buy), not when it merely touches
- Market/stop orders pay a fixed slippage in ticks, plus the spread
- Fees follow the venue's actual fee schedule

For reference: MadEvolve fills the **entire** quantity at exactly the limit price whenever price trades through, with no queue, on data aggregated across exchanges — the authors themselves write *"still far from being realistic"*; their baseline already shows Sharpe 4.81 for a passive minute-bar BTC strategy. For our low-frequency strategies, market impact is small at retail size; fees, spread and slippage dominate. Cost sensitivity is checked at gate ⑥′.

The Vietnam adapter needs: `InstrumentProvider` + `DataClient` + `ExecutionClient`. The hardest part is **reconciliation on reconnect while positions are open**.

> ⚠️ The Vietnam adapter's phase 1 **supports derivatives only (VN30F1M)**, skipping the cash market. Reasons: the ±7% price limit produces "locks" that break backtest realism, T+2 blocks intraday, and short-selling doesn't exist. This cuts ~60% of the effort. Detail in [[06-MULTI-MARKET-PLATFORM]] §2.3.

---

## 4. Data model

### 4.1. Trial Ledger — single source of truth

> 🆕 **v0.3 — two records, two purposes.** Version 0.2 used `COUNT(*)` over every row as `N`, mixing reviewer calls, compile failures and backtested configurations. They are not equivalent: only a configuration **whose performance was measured on data** is a test that can create selection bias. Recording *everything* is still right (P2) — but the audit log and the statistical inputs must be kept apart.

```sql
-- ═══ RECORD 1: AUDIT LOG — all activity, append-only, NOT used for statistics ═══
CREATE TABLE generation_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    run_id         TEXT NOT NULL,
    campaign_id    TEXT NOT NULL,        -- research campaign (§4.2)
    engine         TEXT NOT NULL,        -- 🆕 quantevolve | simple_loop | random (§3.1.11)
    seed           INTEGER NOT NULL,     -- 🆕 B7
    evolve_scope   TEXT,                 -- 🆕 entry | exit | regime | joint (§3.3.1)
    agent          TEXT NOT NULL,        -- data | research | coding | eval
    model_used     TEXT NOT NULL,        -- model provenance (§3.1.9)
    cell_id        TEXT,                 -- targeted feature-map cell
    event          TEXT NOT NULL,        -- LLM_CALL | PATCH_FAIL | COMPILE_FAIL | AST_REJECT | TEMPLATE_TAMPER | DRIFT_REJECT | SANDBOX_VIOLATION | ...
    strategy_hash  TEXT,                 -- NULL if no code was produced yet
    drift_delta    REAL,                 -- normalized Δ (gate ⓪), if any
    detail         JSON
);

-- ═══ RECORD 2: STATISTICAL TRIALS — only configurations that reached ③ Backtest IS ═══
CREATE TABLE trials (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    run_id         TEXT NOT NULL,
    campaign_id    TEXT NOT NULL,
    engine         TEXT NOT NULL,        -- 🆕 the engine that CREATED the strategy; kept on migration
    seed           INTEGER NOT NULL,
    evolve_scope   TEXT,
    strategy_hash  TEXT NOT NULL,        -- hash of normalized code, catches duplicates
    hypothesis     TEXT,                 -- the hypothesis the LLM wrote BEFORE coding
    params         JSON NOT NULL,
    universe       TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    timerange      TEXT NOT NULL,
    cell_id        TEXT,
    source         TEXT NOT NULL,        -- 🆕 evolution | param_opt | manual (hand-written) — parameter-optimizer evaluations ARE trials too
    sharpe_is      REAL NOT NULL,        -- needed for V[SR] across trials
    returns_path   TEXT NOT NULL,        -- needed for N_eff clustering and DSR
    candidate_id   TEXT NOT NULL,        -- 🆕 ADR-0002: links gate_results; N_eff clusters live in trial_clusters
    gate_failed    TEXT,                 -- NULL if everything passed
    verdict        TEXT NOT NULL         -- PASS | REJECT_<gate> | REJECT_FABRICATION (reviewer veto after backtest)
);

-- Portfolio variants evaluated (§3.2.1) — each row is also a selection
CREATE TABLE portfolio_variants (
    portfolio_hash TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL,
    rule_config    JSON NOT NULL,        -- ρ threshold, K, weights, rebalancing schedule
    members        JSON NOT NULL,        -- list of strategy_hash + weights
    sharpe_is      REAL,
    returns_path   TEXT,
    ts             TIMESTAMP NOT NULL
);

-- 🆕 ADR-0002: N_eff clustering history — replaces trials.cluster_id (the ledger never UPDATEs)
CREATE TABLE clustering_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    method         TEXT NOT NULL,
    n_trials       INTEGER NOT NULL
);
CREATE TABLE trial_clusters (
    clustering_run INTEGER NOT NULL REFERENCES clustering_runs(id),
    trial_id       INTEGER NOT NULL REFERENCES trials(id),
    cluster_id     INTEGER NOT NULL,
    PRIMARY KEY (clustering_run, trial_id)
);

-- 🆕 ADR-0002: every GateResult, pass or fail — gates after ③ cannot update the trials row
CREATE TABLE gate_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    campaign_id    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,
    strategy_hash  TEXT,
    trial_id       INTEGER,              -- NULL before gate ③
    gate           TEXT NOT NULL,
    passed         INTEGER NOT NULL,
    value          REAL,
    reason         TEXT NOT NULL,
    detail         JSON
);

CREATE INDEX idx_hash ON trials(strategy_hash);
CREATE INDEX idx_cell ON trials(cell_id);
CREATE INDEX idx_gen_cell ON generation_log(cell_id);

-- DSR inputs: count statistical trials, every verdict, every run, NEVER reset
CREATE VIEW trial_stats AS
WITH latest AS (SELECT MAX(id) AS run FROM clustering_runs),
     covered AS (SELECT trial_id, cluster_id FROM trial_clusters
                 JOIN latest ON clustering_run = latest.run)
SELECT (SELECT COUNT(*) FROM trials)                  AS n_raw,
       (SELECT COUNT(DISTINCT cluster_id) FROM covered)
         + (SELECT COUNT(*) FROM trials
            WHERE id NOT IN (SELECT trial_id FROM covered)) AS n_eff,  -- uncovered trial = own cluster
       (SELECT AVG(sharpe_is * sharpe_is) - AVG(sharpe_is) * AVG(sharpe_is) FROM trials) AS var_sr;

CREATE VIEW total_portfolio_variants AS SELECT COUNT(*) AS n FROM portfolio_variants;

-- Fill-bias alarm: code-generation failure rate > 50% — read from the AUDIT LOG
--    ⇒ allocate MORE refinement budget; do NOT conclude the region is worthless
CREATE VIEW starved_cells AS
SELECT cell_id,
       SUM(event IN ('COMPILE_FAIL','AST_REJECT')) * 1.0
         / NULLIF(SUM(event = 'LLM_CALL' AND agent = 'coding'), 0) AS fail_rate,
       SUM(event = 'LLM_CALL' AND agent = 'coding') AS attempts
FROM generation_log
WHERE cell_id IS NOT NULL
GROUP BY cell_id
HAVING fail_rate > 0.5;
```

> 🔴 **Inputs to gate ⑤ DSR** = `trial_stats` (`N_eff`, `V[SR]`) + `total_portfolio_variants`, applied to the return series of the **consolidated portfolio**. See the warning box in §3.1.6. This is the easiest thing to get wrong in the entire design.
>
> 🆕 **Hidden trials (MadEvolve lesson X2):** the parameter-calibration step (§3.2.1 step 5b) records **every** evaluated configuration as a row in `trials` with `source = 'param_opt'`. The MadEvolve repo skips exactly these evaluations. (v0.5: the optimizer no longer runs inside the evolution loop — §3.3.1.)
>
> **Estimating `N_eff`:** cluster the return series of all trials by correlation distance (`d = √(½(1−ρ))`), choosing the number of clusters by silhouette — López de Prado's ONC approach. Re-run on every portfolio evaluation. `N_eff` may only *lower* the bar on principled grounds; always report DSR at both `N_raw` and `N_eff`.

### 4.2. Holdout Registry — enforcing P6

> 🔴 **v0.3 correction.** Version 0.2 keyed `holdout_access` on `strategy_hash`. That way strategies A and B **can still both read the holdout, once each** — and we then keep whichever has the better `sharpe_oos`. The holdout has become one more selection round. The correct requirement: the holdout is used **once for the whole selection process**, not once per candidate.

```sql
-- Research campaign: groups every run and every trial before one holdout opening
CREATE TABLE campaigns (
    campaign_id     TEXT PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    holdout_range   TEXT NOT NULL,        -- this campaign's holdout period
    lock_hash       TEXT NOT NULL,        -- 🆕 ADR-0002: SHA256 of evaluation.lock.yaml (§10.1)
    holdout_lock_hash TEXT,               -- 🆕 ADR-0002: SHA256 of holdout.lock
    status          TEXT NOT NULL CHECK (status IN ('OPEN','FROZEN','BURNED'))
);

CREATE TABLE holdout_access (
    campaign_id     TEXT PRIMARY KEY REFERENCES campaigns,  -- each CAMPAIGN opens it once
    portfolio_hash  TEXT NOT NULL REFERENCES portfolio_variants,  -- the frozen portfolio
    frozen_at       TIMESTAMP NOT NULL,   -- must be < accessed_at
    accessed_at     TIMESTAMP NOT NULL,
    timerange       TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('PASS','FAIL')),  -- the only bit returned to research
    sharpe_oos      REAL                  -- recorded for the human-readable report, NEVER returned to the agent
);
```

**Procedure:**

1. While `OPEN`, the agent evolves, validation runs ⓪–⑤, and the portfolio is built per §3.2.1. The holdout **does not exist** for any process in this campaign.
2. `FROZEN`: record `portfolio_hash` (strategy set + weights + rule). After this point **nothing may be added, removed, or re-weighted**.
3. Open the holdout **exactly once** for **exactly one** frozen portfolio. The evaluator returns **PASS/FAIL** against a pre-registered threshold — not a number, so no gradient flows back.
4. The campaign moves to `BURNED`. If it FAILs and you want to keep researching: **start a new campaign with a new holdout** (data *after* the old holdout, or wait for new data to accumulate). The old holdout is merged into the new campaign's IS range and is **never used as a holdout again**.
5. 🆕 `ABANDONED` (ADR-0019): an `OPEN` campaign can be abandoned **without opening the holdout** — with a reason, audited, final. Its trials stay in `N`; its lock is archived unchanged; its holdout was never claimed, so it stays unused. A new campaign opens only once `holdout_pass` (D4) is set.

Enforced by `PRIMARY KEY (campaign_id)`: a second holdout opening within the same campaign — even for a different strategy or portfolio — **throws at the database layer**. 🆕 ADR-0016 (amended): the evaluator **claims** the holdout in one transaction *before* reading it (`holdout_claims`); a claim is refused if an earlier campaign claimed the same `holdout.lock` hash or an overlapping period, and any error after the claim still burns the campaign (FAIL).

> ⚠️ **Practical consequence:** the holdout is a **consumable**, non-renewable resource. With free data of limited depth (A7), the number of campaigns with a clean holdout is **very small** (a handful over the whole project). One more reason not to open it early.

> 🆕 **But `PRIMARY KEY` is still not enough — raise P6 to the operating-system level.**
>
> AgonAlpha identifies the blind spot: in academic research a "holdout" is only a promise, because **the authors still control the entire pipeline**. We are in exactly that position — we write the code that computes DSR *and* the code that decides when the holdout is read.
>
> Three layers of enforcement, weakest to strongest:

| Layer | Mechanism | What it blocks |
|---|---|---|
| 1 | `PRIMARY KEY (campaign_id)` on `holdout_access` | A second opening within the same campaign *through the intended API*, even for a different strategy/portfolio |
| 2 | 🆕 **Holdout files `chmod 0400`, SHA256 hash committed to `holdout.lock` before each campaign starts** | Editing/replacing holdout data; detects any tampering |
| 3 | 🆕 **Evaluator runs in its own process**, receiving only a frozen `portfolio_hash` and returning **exactly one PASS/FAIL bit**. No shared memory with the evolution loop | The evolution loop "accidentally" reading holdout results and using them to pick the next generation. Returning 1 bit instead of a number limits how much information leaks back if there is a later campaign |

> Layer 3 matters most and is the easiest to skip: if the evaluator lives in the same process, a single global variable or one log line is enough for holdout information to leak back into the loop. ⚠️ A separate process does **not by itself** prevent feedback — the human operator still sees PASS/FAIL. What prevents feedback is the rule above: *once a campaign is BURNED, its holdout is never reused*.

---

## 5. Directory structure

```
TradingProject/                   ← Crucible Quant (package: quantcrucible)
├── research_docs/                ← research + this document
├── core/
│   ├── strategy/
│   │   ├── base.py                ← Strategy protocol, Signal
│   │   ├── template.py            ← template: fixed region + EVOLVE-BLOCK (§3.3.1)
│   │   ├── tunable.py             🆕 parses TUNABLE declarations → PBO grid, n_params
│   │   └── registry.py            ← indicator whitelist
│   ├── sizing/
│   │   ├── vol_target.py          ★ vol targeting
│   │   └── position_sizer.py      ← risk → lots/shares/contracts
│   └── zoo/                       ← classic strategies, for AST similarity scoring
├── agent/                         ← QuantEvolve architecture
│   ├── loop.py                    ← Algorithm 1: the generation loop
│   ├── data_agent.py              ← ① schema prompt + category detection
│   ├── research_agent.py          ← ② hypothesis (6 XML tags)
│   ├── coding_team.py             ← ③ code → backtest → refine (subprocess)
│   ├── evaluation_team.py         ← ④ the 5 analysis functions
│   ├── evolution/
│   │   ├── feature_map.py         ← MAP-Elites, 16 bins × 6+ dims
│   │   ├── islands.py             ← N islands + top-10% migration
│   │   ├── sampling.py            ← SampleParent (Eq.1), SampleCousins (Eq.2)
│   │   └── archive.py             ← also stores feature-map-rejected strategies
│   ├── insights.py                ← repository + curation every K=50 gens
│   ├── dsl.py                     🆕 closed DSL + AST rejection of non-whitelisted ops
│   ├── drift.py                   🆕 gate ⓪: SHA256 logic + normalized Levenshtein, anchor trace
│   ├── patcher.py                 🆕 strict SEARCH/REPLACE + block rewrite + retry with error
│   ├── pipeline.py                🆕 LLM producers → prefetch queue → backtest slots
│   ├── routing.py                 🆕 heterogeneous model routing (§3.1.9)
│   ├── engines/                   🆕 §3.1.11
│   │   ├── quantevolve.py         ← engine A (4 agents)
│   │   ├── simple_loop.py         ← engine B (parent + inspirations → 1 LLM call)
│   │   └── random_search.py       ← engine C (no LLM)
│   ├── scheduler.py               🆕 enforces trial-budget shares across engines
│   └── prompts/                   ← templates following the paper's Appendix A
├── validation/
│   ├── gates.py                   ← Gate protocol, pipeline
│   ├── report.py                  🆕 EvaluationReport: public / private / feedback (§3.3.2)
│   ├── sandbox.py                 🆕 Docker wrapper, --network none --read-only (§3.3.3)
│   ├── guardrail.py               ← AST similarity + look-ahead scan
│   ├── statistical.py             ← CPCV, PBO, DSR, MinBTL (purgedcv)
│   ├── portfolio.py               🆕 builds the portfolio by the pre-registered rule (§3.2.1)
│   ├── portfolio_dsr.py           🆕 DSR on the consolidated portfolio, N_eff + V[SR] (§4.1)
│   ├── n_eff.py                   🆕 clusters trial returns → N_eff
│   ├── temporal.py                ← holdout manager, decay
│   └── oracles/                   ← the 4-level leaky oracle suite
├── holdout/                       🔴 chmod 0400 + holdout.lock (committed hash)
│   └── evaluator_proc.py          🆕 separate process, takes portfolio_hash, returns PASS/FAIL
├── config/
│   ├── user.yaml                  🆕 user configuration (§10.1)
│   └── evaluation.lock.yaml       🆕 generated from user.yaml per campaign — read-only, hashed, no agent write path
├── ledger/
│   └── db.py
├── execution/
│   ├── engine.py                  ← NautilusTrader wrapper
│   └── adapters/
│       └── ssi/                   🔴 build it yourself
├── data/
└── tests/
```

> 🆕 **Implementation:** code lives under `src/quantcrucible/` (src layout); the root `holdout/` holds data only, and `evaluator_proc.py` lives in `src/quantcrucible/holdout/`. See [ADR-0001](../implement_docs/adr/0001-src-layout-and-holdout-split.md) and the [up-to-date tree](../implement_docs/04-MODULE-MAP.md).

---

## 6. Tech stack

| Component | Choice | Reason |
|---|---|---|
| Execution core | **NautilusTrader** | Guaranteed backtest ≡ live; multi-asset native; Rust core |
| Agent orchestration | **LangGraph** | Needs state + cycles; already familiar |
| Optimizer | **Optuna** | TPE/GP, the de-facto standard |
| Validation | **`purgedcv`** (PyPI) ⚠️ *API unverified* | PBO + DSR + CPCV + WalkForward + MinBTL/MinTRL in one MIT-licensed library |
| Fast search | **vectorbt** *(optional)* | Parameter sweeps; ⚠️ *"lies about microstructure"* — coarse screening only |
| Ledger | SQLite → Postgres | Start simple |
| **Data** | **Entirely free** | See §6.1 — this is a **hard constraint** |
| Packaging | FastAPI + Docker | Already familiar |

⚠️ **Licenses to check:** `vectorbt` carries a Commons Clause; `pypbo` is AGPL-3.0 (avoided by using MIT-licensed `purgedcv`).

> 🆕 **`purgedcv` is the core of the validation layer but has not been verified.** The signatures of `deflated_sharpe_ratio` and `probability_of_backtest_overfitting` have not been checked against the source, and it is unclear whether it accepts `N_eff` / `V[SR]` as separate inputs (§4.1). First task of **phase 1**: (1) read the source and pin a version; (2) write tests against the numerical examples in the original DSR paper (Bailey & López de Prado 2014), and for PBO (Bailey et al. 2017) against hand-computed fixtures from the definition plus an independent implementation (version recorded); (3) if results differ or parameters are missing → implement DSR/PBO ourselves (~50 lines each) and use `purgedcv` only for CPCV/purging. The architecture does not depend on this library — `validation/statistical.py` is a wrapper.

---

## 6.1. Data — constraint: 100% FREE

### Sources per asset class

| Asset class | Free source | Quality | Problem |
|---|---|---|---|
| **Crypto** | **ccxt** (Binance, Bybit, OKX) | 🟢 **No compromise** | None. Free = full quality |
| **Forex spot** | Dukascopy, HistData, **Stooq** (68 majors + 1,840 other pairs) | 🟢 Good at the daily timeframe | Broker tick data is poor quality |
| **International equities** | **Stooq** (21,000+ global tickers & ETFs), yfinance | 🟡 Decent | 🔴 **Survivorship bias** — no delisted tickers |
| **Futures** | **TurtleTrader** (major US futures from the 1970s, with OHLCV + open interest), QuantConnect free tier | 🔴 **Weakest** | 🔴 **Continuous contracts not rolled** |
| **Vietnam** | **vnstock** free tier | 🟡 Decent | Short history |

### ⚠️ Three prices you pay

**1. 🔴 Rolling continuous contracts becomes your job**

This is the biggest cost. Free futures data is mostly **unadjusted front-month**.

> *"Introducing artificial jumps from roll dates can completely alter any form of data analysis or backtest performed on this time series... they show up in your results as false wins or losses that had nothing to do with your strategy."*

⚠️ **Important caveat:** the "rules must be scale-invariant" principle (§3.1) **does not save you** here. Scale invariance protects you from *back-adjustment distortion*, but an unadjusted roll gap produces a **fabricated return** — and a rule that consumes returns will eat it.

**Solution (free, but real work):** build continuous contracts yourself from individual contract data.

```
TurtleTrader gives you: OHLCV + OPEN INTEREST per individual contract
        ↓
Roll yourself when the back contract's volume/OI exceeds the front contract's
        ↓
Apply ratio adjustment yourself (do NOT use back-adjustment —
see the warning below)
        ↓
Your own continuous contract, fully under your control
```

> 🔴 **Avoid back-adjustment (the Panama method):** *"on sufficiently long crude oil series, backward-adjusted prices can turn negative — which has no economic meaning"*, and it *"introduces a trend bias... a large drift to the prices"*. Use **ratio/proportional adjustment**.

**Estimate: 2–3 weeks** to build and test the roll module. This is exactly what Norgate's $270/yr buys for you.

**2. 🔴 Equity survivorship bias — not fixable with free data**

Stooq/yfinance only carry companies that are **still alive**. This is the #1 backtest inflator for equity strategies.

**Architectural consequence:** for international equities, **trade indices/ETFs only** (SPY, QQQ, EFA…), **never single stocks**. Indices have no instrument-level survivorship bias. This is a binding constraint, not a suggestion.

**3. 🟡 Engineering time instead of money**

You trade **$270/yr** for **2–3 weeks of work plus ongoing maintenance**. That is a sensible trade while you still don't know whether the project produces any edge.

### Should you take data from the broker you trade on?

The argument in favour is **correct in principle**: backtesting and trading on the same venue removes the mismatch between data source and matching engine — same prices, spreads, symbol specs, sessions. It is P5 extended down to the data layer. And it is **free** (you have an account).

**But there are 5 problems, 3 of them hard blockers:**

| # | Problem | Severity | Evidence |
|---|---|---|---|
| 1 | **History far too shallow** | 🔴 Blocker | IB: *"Expired futures data older than two years counting from the future's expiration date"* is unavailable → **you cannot build long futures series**. Bars ≤30s older than 6 months are also unavailable |
| 2 | **Survivorship bias worse than public data** | 🔴 Blocker | A broker only lists what it currently offers — delisted tickers and expired contracts vanish entirely |
| 3 | **Quality varies by broker and cannot be audited** | 🔴 Blocker | Same EURGBP over 10 years, **4 brokers**: some fail to reach 60% history quality, others ~100%. **Same EA: 100% quality → ~$1,300 profit; 10% quality → ~$2,500 profit** — bad data makes the strategy look BETTER |
| 4 | **Pacing limits** | 🟡 | IB: 60 requests/10 min, 6 requests per contract/2 s, BID_ASK counts double. Daily is fine (~50 min for 30 instruments × 10 years); **intraday is hopeless** (~2,520 requests for 10 years of *one* instrument) |
| 5 | **Lock-in** | 🟡 | Switching brokers → the entire research corpus loses comparability, and the ledger's `N` was built on data that no longer exists |

> *"High modeling quality on garbage history just gives you a very smooth, very confident lie."*

**✅ For crypto this question resolves itself.** ccxt pulls data **from Binance itself**, and you also execute on Binance → free data **is already** broker data, already venue-exact. Phases 0–3 are unaffected. It only bites in phases 4–5 (forex/equities/futures).

### 🎯 Decision: a TWO-TIER data strategy

Don't pick a side. Assign roles by gate:

| Gate | Source | Requirement |
|---|---|---|
| **①–⑤** research, thousands of backtests | **Public free** (ccxt, Stooq, TurtleTrader) | Depth of history, bulk download, **reproducibility** |
| **⑥–⑦** holdout + dry-run | **Broker** (IB, crypto exchanges) | **Venue-exact** |

**And here is why this beats a mere compromise — it adds a new gate:**

```
⑥′ DATA-SOURCE ROBUSTNESS  (new gate, inserted before holdout)
    Re-run the strategy on BROKER data instead of public data.
    🔒 Gate: Sharpe must not drop more than 30% when the data source changes
```

> 🔑 If a strategy survives on Stooq's EURUSD but dies on your broker's EURUSD, that is **real information**: the edge was an artifact of how one vendor cleans data. You want to know that **before** risking real money.
>
> This is a form of **robustness test that no paper in [[00-RESEARCH-SYNTHESIS]] performs** — because they all use exactly one data source.

### 🎯 Why this constraint does NOT block you

**Phases 0–4 (roughly the first 6 months) only need crypto and forex/indices — both free with no quality compromise.**

The futures problem only bites in **phase 5**. By then you will know whether the project is worth $270.

> 💡 **Recommendation:** keep the free-data constraint, but **design `DataSource` as an interface** so swapping sources requires no changes above it. If you later want to buy Norgate, you just write one new adapter.

```python
class DataSource(Protocol):
    def bars(self, symbol: str, timeframe: str,
             start: datetime, end: datetime) -> Bars: ...
    def is_continuous(self) -> bool: ...   # futures already rolled?
    def adjustment(self) -> Literal["none", "ratio", "difference"]: ...
```

### Clarification: Futures ≠ Forex

A common confusion. **Futures are not a market, they are a CONTRACT TYPE.** A multi-asset futures basket contains 4 groups:

| Group | Example tickers |
|---|---|
| Commodities | CL (crude), GC (gold), NG (natural gas), ZC (corn) |
| Equity indices | ES (S&P 500), NQ (Nasdaq), FDAX, NK |
| Bonds / rates | ZN (10y T-note), ZB (30y), Bund |
| **Currencies** | 6E (EUR), 6J (JPY), 6B (GBP) ← **this is forex** |

This is exactly the basket Hurst-Ooi-Pedersen use (**29 commodities + 11 indices + 15 bonds + 12 currencies**) to obtain Sharpe **1.60** — see [[04-SCHOOL-B-EFFICACY]].

**Forex spot vs currency futures:**

| | Forex spot (OTC) | Currency futures |
|---|---|---|
| Volume | ❌ No real volume | ✅ Real volume + OI |
| Counterparty | Broker → **B-book** risk | Clearing house |
| Expiry | None | Yes → must roll |
| Free data | 🟢 Stooq/Dukascopy are good | 🔴 Must build continuous series yourself |

> 💡 For trend following, currency futures are usually better than spot (real volume, no B-book). **But under a free-data constraint, forex spot is easier** — Stooq provides clean daily data with no rolling required. → **Use forex spot in the early phases; leave currency futures for phase 5.**

---

## 7. Build roadmap

| Phase | Contents | Gate to advance | Estimate |
|---|---|---|---|
| **0** | **Harness + ledger + oracle suite**. No agent yet. Hand-write one EMA crossover strategy and run it through all 8 steps. 🆕 Template + sandbox + `EvaluationReport` | 🔒 **The pipeline REJECTS all 4 levels of leaky oracle** | 2–3 weeks |
| **1** | Full validation layer (CPCV/PBO/DSR/MinBTL) + Risk & Sizing | 🔒 The hand-written strategy clears every gate; the ledger separates audit/trials correctly, `N_eff` + `V[SR]` are computable; DSR matches a published numerical example in the original paper; PBO matches hand-computed fixtures from the CSCV/PBO definition and the result of an independent implementation on the same input; the ½ sizing test (§3.4) passes | 2–3 weeks |
| **2** | The **QuantEvolve** agent loop (4 agents + feature map + islands), **crypto only** | 🔒 ≥150 generations run autonomously, every `s_new` reaches the ledger, the feature map does not collapse into one bin. 🆕 Island integration test passes (§3.1.5). 🆕 **Multi-engine A/B/C comparison in `isolated` mode** per §3.1.11 — decision rule locked before running (internal validation of K4, §3.1) + Research Agent model A/B (§3.1.9) | **6–8 weeks** |
| **3** | Expand breadth: 15–30 weakly-correlated instruments. 🆕 Switch the engines to `collaborative` | 🔒 Vol targeting works; no instrument holds >20% of the risk | 2–3 weeks |
| **4** | IB adapter → forex + international equities | 🔒 Sessions/calendars correct, no look-ahead introduced | 3–4 weeks |
| **5** | Multi-asset futures — **including building the roll module yourself** from free data (see 6.1) | 🔒 Self-built continuous contracts match a reference source; roll gaps produce no fabricated returns | **5–7 weeks** |
| **6** | 🔴 SSI adapter → VN30F1M | 🔒 Reconciliation correct on reconnect with open positions | 3–6 weeks |

**Total: 6–9 months.**

> 🎯 **Phase 0 is the most important phase and the one most often skipped.** Its gate is not "is the strategy profitable" but **"can the harness reject cheating"**.

---

## 8. Risks

| Risk | Level | Mitigation |
|---|---|---|
| **Agent produces garbage at high speed** | 🔴 High | P1 + P2: harness first, no ledger bypass |
| **`N` undercounted → falsely good DSR** | 🔴 High | The ledger is the only route to a backtest; enforce in code |
| **Holdout viewed more than once** | 🔴 High | `PRIMARY KEY (campaign_id)` on the holdout registry — one opening per research campaign, for one frozen portfolio; a BURNED campaign's holdout is never reused (§4.2) |
| **Self-built roll gaps wrong → fabricated returns** | 🔴 High | Free-data constraint (6.1). Reconcile your continuous series against a reference source before trusting it |
| **Equity survivorship bias** | 🔴 High | Free data has no delisted tickers → **trade indices/ETFs only, never single stocks** |
| **Vietnam adapter reconciliation wrong** | 🟡 Med | Leave it for last; test thoroughly on demo |
| **Session/calendar misalignment → silent look-ahead** | 🟡 Med | Dedicated tests per venue when adding each adapter |
| **No edge is ever found** | 🟡 Med | **This is a valid outcome.** CTA funds running 60+ markets just lost money for 3 years straight |
| **Over-engineering before any edge exists** | 🔴 High | Stop at phase 3 until a strategy clears dry-run |

---

## 9. What this architecture DELIBERATELY does not do

Stated explicitly to prevent scope creep:

- ❌ **No LLM at runtime** — A4
- ❌ **No cross-sectional factor + ML** — that is School A, a different architecture ([[08-LLM-QUANT-RESEARCHER]] — School A accounts for ~18 of the 22 surveyed systems)
- ❌ **No MQL5 EA support** — A3 (decided 21 Sep 2026): live trading through NautilusTrader. Forex goes through **Interactive Brokers**, not an MT5 broker
- ❌ **No HFT / order-book strategies** — low frequency is a survival condition for retail
- ❌ **No custom backtest engine** — NautilusTrader already solved this
- ❌ **No price forecasting with an LLM** — out of scope
- 🆕 ❌ **No meta-evolution prompt genome** (AlgoEvolve). It sounds appealing but the evidence is **negative**: the paper's own table shows that removing it gives a *higher* Sharpe (5.71 > 5.60) and nearly 4× lower drawdown. More importantly — every prompt variant is **a new search axis**, inflating `N` in DSR. We would be spending trials to buy something with no demonstrated value ([[08-LLM-QUANT-RESEARCHER]] §7.4)
- 🆕 ❌ **No RFT / RL fine-tuning** (Alpha-R1, QuantEvolver). The newest direction of 2026 and probably right in the long run, but Alpha-R1 consumed **64×H800 for 120 hours** — violating A6

---

## 10. Decision register — the single source

> 🆕 **v0.3.** Previously the README pointed to 4 questions in [[00-RESEARCH-SYNTHESIS]] §9 while this section held 5 different ones, and the two places disagreed about which question blocks coding. **From this version on, the table below is the only place where decision status is recorded.** Every other document points here.

Status: ✅ **Decided** (changing it means changing the architecture) · 🟡 **Provisional default** (used for coding; adjustable later without breaking the design) · 🔴 **Open, blocking** (must be answered before writing code in the affected area)

| # | Decision | Status | Current value | Source / impact |
|---|---|---|---|---|
| D1 | Markets (00 §9 Q1) | ✅ Decided (confirmed 21 Sep 2026) | Crypto → forex + international equities → Vietnam, in phase order (A2, §7) | Direct requirement. If crypto-only → Freqtrade ([[05-SMOOTH-FLOW]]) |
| D2 | Output: TA rules or ML factors (00 §9 Q2) | ✅ Decided | Deterministic rule-based TA (A1) | Changing it ⇒ an entirely different architecture (RD-Agent(Q) + Qlib) |
| D3 | Is an MQL5 EA required? (00 §9 Q3) | ✅ Decided (21 Sep 2026) | **No.** Live trading through NautilusTrader | Condition: every venue has a live adapter (§3.5) — crypto ✅, IB (forex/equities/futures) ✅, SSI self-built (phase 6). An MT5-only broker ⇒ a bridge adapter, not an EA |
| D4 | Economic threshold for funding (00 §9 Q4) | ✅ **Decided for the holdout** (21 Sep 2026) · 🔴 **live still open** | `holdout_pass = 1.3`: minimum annualized OOS Sharpe of the **whole frozen portfolio**, on the holdout, net of fees + slippage, reference return 0; locked per campaign | ADR-0020. An initial acceptance bar set by the user, not derived from synthetic tests; never adjusted after seeing a holdout result. **PASS only means this bar was cleared — not that the portfolio may trade real money**; the funding criteria for live must still be decided before live |
| D5 | Actual capital at the live stage | 🟡 Provisional default | < $10k | Maximum instrument count |
| D6 | Base currency | 🟡 Provisional default | USD | FX conversion layer |
| D7 | Portfolio vol target | 🟡 Provisional default | 10%/yr (Hurst-Ooi-Pedersen) | Vol targeting (§3.4) |
| D8 | Maximum tolerable drawdown | 🟡 Provisional default | 20% | Kill-switch; part of D4 |
| D9 | Portfolio-construction parameters (ρ, K, rebalancing) | 🟡 Provisional default | 0.5 / 20 / monthly (§3.2.1) | ⚠️ Must be frozen **before the first portfolio evaluation** — after that, every change is a `portfolio_variant` |
| D10 | Drift thresholds Δ | 🟡 Provisional default | 0.05 / 0.15, normalized Δ (§3.1.7) | Calibrate in phase 2 before using them to abort automatically |
| D11 | Research Agent model | 🟡 Provisional default | Non-reasoning, `gpt-oss-120b` (§3.1.9) | Internal A/B in phase 2 |
| D12 | Maximum loss per trade at the stop (`max_risk_pct`) | 🟡 Provisional default | 1% of equity | Risk cap in sizing (§3.4) |
| D13 | PBO parameter grid; number of drift micro-scenarios | 🟡 Provisional default | 3–5 values within ±30%, `M` ≤ 200; ~20 scenarios | §3.2, §3.1.7 |
| D14 | Engine budget shares; mode | 🟡 Provisional default | A 0.5 / B 0.4 / C 0.1; `isolated` in phase 2 | §3.1.11 |
| D15 | Minimum trades / holding time; maximum indicator correlation; seed count | 🟡 Provisional default | 30 IS trades / 1 bar; 0.9; 3 seeds | Gate ③, §3.3.1, §3.1.8 |
| D16 | Evolution scope | 🟡 Provisional default | `joint` (entry + exit + regime) | §3.3.1 |
| D17 | MinBTL target Sharpe (gate ②) | 🟡 Provisional default | 1.5 annualized; may only be lowered | ADR-0002. At 1.0, ~7 years of free IS data cap the search at ~100–200 trials |
| D18 | Research data (phase 0) | 🟡 Provisional default | Binance spot, 1d, BTC/ETH/SOL/BNB/XRP vs USDT from 2018; holdout = last 12 months; second source for ⑥′ = Gate.io (`second_exchange`) | ADR-0002, ADR-0015, §6.1 |

> ✅ **Every 🟡 row is user-configurable** (decided 21 Sep 2026) — the numbers in the table are only the defaults used when the user sets nothing. How to configure, and the limits: §10.1.

**Three kinds of content in this document, kept apart:**

- **Decisions** — only the ✅ rows above.
- **Provisional defaults** — the 🟡 rows, and every number in the body marked "provisional default".
- **Unverified API examples** — every code snippet that calls an external library (especially `purgedcv`, §6) is **illustrative** and has not been checked against the real signatures. See the verification item in [[07-VALIDATION-LAYER]] §7.

### 10.1. User configuration

The user edits **a single file**: `config/user.yaml`. `evaluation.lock.yaml` is never edited by hand — it is **generated** from `user.yaml` at the start of each research campaign (§4.2), then made read-only and hashed.

```yaml
# config/user.yaml — every key is optional; omitted ⇒ the default from the §10 table

operational:            # GROUP A — change any time, does not affect statistical results
  live_capital: 10000           # D5
  base_currency: USD            # D6
  kill_switch_drawdown: 0.20    # D8
  models:                       # D11 — recorded in generation_log.model_used
    research: gpt-oss-120b
    coding: <mid-tier>
    eval: <mid-tier>

research:               # GROUP B — locked per campaign; mid-campaign changes are refused
  target_vol: 0.10              # D7
  max_risk_pct: 0.01            # D12
  portfolio:                    # D9
    max_corr: 0.5
    max_strategies: 20
    rebalance: monthly
  pbo_grid: {values_per_param: 5, range: 0.30, max_configs: 200}   # D13
  drift: {allow_below: 0.05, reject_at: 0.15, n_scenarios: 20}      # D10, D13
  engines: {quantevolve: 0.5, simple_loop: 0.4, random: 0.1}       # D14
  engine_mode: isolated         # D14 — isolated | collaborative
  evolve_scope: joint           # D16 — entry | exit | regime | joint
  constraints: {min_trades: 30, min_holding_bars: 1, max_indicator_corr: 0.9}   # D15
  seeds: 3                      # D15
  minbtl_target_sharpe: 1.5     # D17 — may only be lowered (stricter)
  data: {exchange: binance, second_exchange: gate, symbols: [BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT], timeframe: 1d, start: 2018-01-01, holdout_months: 12}   # D18; second_exchange: the ⑥′ source (ADR-0015)
  calibration: {enabled: true, budget_per_strategy: 50}   # §3.2.1 step 5b
  gates:                        # may only be TIGHTENED, never loosened (see below)
    dsr_min: 0.95
    pbo_max: 0.5
  holdout_pass: 1.3             # D4 (ADR-0020) — min annualized OOS Sharpe of the portfolio, net of costs; missing ⇒ no new campaign, no holdout
```

**Three enforcement rules — so that "configurable" does not become a back door for overfitting:**

| Rule | Mechanism | Why |
|---|---|---|
| **Group B is locked per campaign** | When a campaign opens, `research:` is copied into `evaluation.lock.yaml` + hashed into the `campaigns` table. Editing `user.yaml` mid-campaign ⇒ the system **refuses to run** until the user either reverts or opens a new campaign | Changing parameters after seeing results = one more selection round the ledger does not count |
| **Changing portfolio-construction parameters = a new variant** | Within a campaign, a different `portfolio:` config may be tried **through a dedicated command**; each attempt is recorded in `portfolio_variants` and added to `N` (§3.2.1) | Allows experimentation but makes you pay for it in DSR |
| **Gate thresholds have hard floors** | `dsr_min` may not be < 0.95 and `pbo_max` may not be > 0.5, `minbtl_target_sharpe` may not be > 1.5 — looser values are rejected when the config loads. Stricter is fine | This is the minimum for results to mean anything statistically; loosening it disables the validation layer |

> Group A changes freely because it does not feed into *selecting* strategies: capital, base currency and kill-switch only matter live. Model routing does affect the search but does not bias the statistics — it is traced in `generation_log`.

---

## 11. Realistic expectations

To avoid disappointment halfway through, repeating from [[04-SCHOOL-B-EFFICACY]]:

| Configuration | Expected Sharpe |
|---|---|
| 1 instrument | **~0.4** |
| 15–30 weakly-correlated instruments | 0.6–1.3 |
| 50 markets across asset classes (ceiling) | **~1.6** |

**Three skepticism thresholds:** Sharpe > 1.0 on a single instrument → suspicious · > 2.0 anywhere → almost certainly in-sample · > 3.0 → suspect a methodological error.

> 🆕 **Do not use numbers from LLM-quant papers as your benchmark.** Across the 22-system survey ([[08-LLM-QUANT-RESEARCHER]]): QuantaAlpha reports IR **3.3251**, MadEvolve a test Sharpe of **5.65**, AlgoEvolve **5.60**. Set against an independent survey from the same period: **1 of 19** studies declares a transaction-cost model, **0 of 19** reach the top reproducibility tier. And AlgoEvolve itself admits the 5.60 belongs to an *elite prompt* while the population mean is only **~1.21** — i.e. the headline is best-of-N.
>
> **How to read papers in this field:** when you see Sharpe > 3 at daily/intraday frequency, find all three of these before believing it — (a) is it best-of-N, (b) what transaction cost in bps, (c) how long is the OOS window. Missing any one ⇒ the number is meaningless.
>
> The only figure in the entire survey that is both plausible and decently out-of-sample: **Sharpe 1.55 after 5bp one-way costs, pure OOS 2024–2026** (Crypto Constrained Agents) — and that is cross-sectional long-short, not School B. Our benchmark remains the table above.

Also: professional CTA funds running 60+ markets **just lost an average of −2.3%/yr over the 3 years to Aug 2025**. Prepare psychologically for long negative streaks.

---

## One-sentence summary

> This architecture bets on three things the whole field is missing: **the harness built before the agent**, **a ledger with no bypass**, and **a Risk & Sizing layer treated as more important than the adapter layer**. The LLM-generates-code part is the easy part that many people have already done — the decisive part is the rejection layer.
