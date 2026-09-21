# 08 — LLM as Quant Researcher: an architectural overview

> **Survey of 22 papers/repos, updated September 20, 2026.**
> Scope: systems where an **LLM generates an artifact** (factor, expression, or strategy code) and that artifact is **evaluated by backtest**. Systems that call an LLM at trading-decision runtime are excluded.

---

## 0. TL;DR

1. **Architecture has converged; validation has not.** Over 18 months, the field has converged on 5 architectural types. They differ on exactly one question: *where accumulated knowledge lives*. Yet **0/19** studies reach the highest reproducibility level, and **1/19** reports a transaction-cost model.
2. **Only one system reports DSR — and it fails.** Alpha-R1 (9/2026) honestly reports DSR **0.39–0.85**, below the 0.95 threshold, with only **34–158 trials**. Your project is expected to make 4,500–9,000 LLM calls — i.e. hundreds to thousands of statistical trials.
3. **Sharpe figures of 5.6–8.8 in several 2026 papers should not be trusted.** See section 5.4 — one paper has a simple baseline with *higher* Sharpe than the proposed system.
4. **Almost the entire literature belongs to School A.** Only 2/22 systems generate executable strategies with entry/exit rules (AlgoEvolve, MadEvolve). You are entering a region with little precedent.
5. **New data reverses the intuition on model size:** the reasoning model (o3) is **worst** among 6 tested backbones; Llama3-8B produces **negative RankIC**. Size is not the right axis.
6. **The most credible validation design is not a statistical formula, but *who controls the evaluator*.** AgonAlpha pushes scoring to a third party the authors cannot touch.

---

## 1. Scope and classification axis

### 1.1 Inclusion / exclusion criteria

|                  |                                                                                     |
| ---------------- | ----------------------------------------------------------------------------------- |
| ✅ **In** | LLM generates factor/expression/code → backtest scores it → improvement loop |
| ❌ **Out** | LLM reads news/charts and **directly decides buy/sell actions** |

The excluded group includes the most famous repos by star count: **TradingAgents** (~80k★), **AI Hedge Fund** (~59k★), **Vibe-Trading** (~31.5k★, HKU Data Intelligence Lab, 4/2026), **FinMem**, **FinAgent**, **FinCon**, **FinRL**.

The reason for excluding them is not that they are bad, but that they violate your project's **assumption A4** (*the LLM never runs at runtime*). They also carry a structural risk: every decision depends on a model that can be changed or killed by the provider, and honest backtesting is impossible because today's model already knows what happened in 2023.

> ⚠️ **Do not be fooled by star counts.** TradingAgents' 80k★ reflects the appeal of the idea "simulate an investment fund with LLMs", not profitability. The repos paired with serious papers in this report often have only a few hundred stars.

### 1.2 Classification axis: where does accumulated knowledge live?

This question distinguishes systems better than any other taxonomy (by number of agents, output type, or market). After 200 iterations, **what in the system has improved?**

```
K1  Human-in-the-loop   →  in the human's head
K2  R&D loop            →  in the prompt / memory store
K3  Tree search         →  in tree-node statistics
K4  Population evolution → in the archive / population
K5  Weight fine-tuning  →  in model parameters
```

Each step pushes knowledge one layer deeper — and costs one step more. This also matches the chronological path the field has taken: 2023 → 2026.

---

## 2. The five architectures

### Overview table

|                             | **K1** Interactive | **K2** R&D loop                | **K3** Tree (MCTS)       | **K4** Population                       | **K5** Weights          |
| --------------------------- | ------------------------ | ------------------------------------------ | ------------------------------ | --------------------------------------------- | -------------------------------- |
| Where knowledge lives       | Human                    | Prompt/memory                              | Tree node                      | Archive                                       | Model parameters                 |
| Candidates alive at once    | 1                        | 1                                          | 1 branch                       | Hundreds                                      | 1 (model)                        |
| Diversity mechanism         | Human                    | ❌ or weak                                  | UCB exploration                | ✅ MAP-Elites + island                        | ✅ diversity reward              |
| Cost                        | Very low                 | Low                                        | Medium                         | **High** (~10×)                               | **Very high** (GPU cluster)      |
| Main weakness               | Does not scale           | **Early convergence**, context explosion   | Tree bloat, still one direction | Expensive, complex                            | Infeasible for individuals       |
| Representatives             | Alpha-GPT                | RD-Agent(Q), AlphaAgent, XALPHA            | Alpha Jungle, AgonAlpha        | **QuantEvolve**, MadEvolve, QuantaAlpha       | Alpha-R1, QuantEvolver           |
| Appeared                    | 2023                     | 2025                                       | 2025–26                        | 2025–26                                      | **2026**                         |

---

### K1 — Human-in-the-loop (Alpha-GPT, 7/2023)

The LLM acts as a **translator**: the user says "I think a stock with a sudden volume spike after a down streak will rebound"; the LLM translates that into a mathematical expression; the backtest runs; the human reads the result and edits the idea.

Alpha-GPT's original thesis: **do not fully automate the process**. Humans retain economic intuition; AI handles disciplined testing.

> **Why this still matters in 2026:** this is the only architecture whose number of trials is naturally limited by human effort. Every later architecture removes that limit — and that is the source of the entire selection-bias problem in section 5.

---

### K2 — Linear R&D loop

**Members:** RD-Agent(Q), AlphaAgent, Chain-of-Alpha, AlphaMemo, XALPHA, Crypto Constrained Agents.

A chain: `hypothesis → code → backtest → feedback → new hypothesis`. This is exactly the "Generator/Critic/Scheduler" architecture already discussed.

The evolutionary direction of this group in 2026 is **increasingly structured memory**:

| System      | Date         | What memory is                                                                                                                                                                                        |
| ----------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RD-Agent(Q) | NeurIPS 2025 | Knowledge base + bandit scheduler (Thompson sampling)                                                                                                                                                 |
| AlphaMemo   | 6/2026       | **Search-process memory** — stores the whole *search trajectory*, not only results: which step led to which step, and why each failure happened |
| XALPHA      | 7/2026       | **Three brains**: Macro (cycle plan) / Micro (factor-search loop) / Cross (maps factors to mechanism "archetypes"). Loads financial analyst reports into tiered A/B/C memory |

**XALPHA is the most notable in gate design.** It does not use one threshold, but two tiers:

```
Normal:      RankIC ≥ 0.005,  positive-ratio ≥ 0.50,  percentile 60%
Elite:       RankIC ≥ 0.01,   positive-ratio ≥ 0.55,  percentile 80%
Selection:   70% train alpha + 30% OOS evolution score
Dedup:       maximum correlation 0.60 when loading into the library (40 factors)
```

CSI300 result (train 2011–2020 / valid 2021 / test 2022–2025, 10-day open-to-open return target): IC **0.0619**, RankIC 0.0748, ICIR 0.3703, IR **1.5368** — beating AlphaAgent (IR 0.9516) and CogAlpha (IC 0.0366). Backbone: **gpt-oss-120b**.

**Crypto Constrained Agents** (4/2026) is the only system in this group focused on crypto, and its constraint design is well worth copying:

> The LLM proposes hypotheses, but **a deterministic engine** owns everything else: point-in-time data, a **restricted DSL** (only cross-sectional rank, time-series transforms, and nonlinear functions), **predefined** selection gates, and an **append-only** experiment trace. The LLM **cannot edit evaluation rules or the data split during a session**.

Result: equal-weight long-short, pure 2024–2026 OOS, after 5bp one-way fees → **AR 44.55%, Sharpe 1.55**. Best single factor Sharpe > 2.4. Daily crypto universe filtered by liquidity and trading history; train 2020–22 / valid 2023 / OOS 2024–26; 1-day execution lag.

**The intrinsic weakness of K2** is named precisely by the RFT paper (section K5): *"context explosion and feedback drift"* and *"search stagnation"*. The prompt grows longer, the model begins drifting away from old feedback, and the whole loop gets stuck in one direction.

---

### K3 — Tree search (MCTS)

**Members:** Alpha Jungle (AAAI 2026), Tree-structured Thoughts (8/2025), AgonAlpha (8/2026).

Instead of a linear chain, the search space is a **tree**: each node is a factor expression, and each branch is a refinement. UCB balances exploitation/exploration; the backtest is the reward.

**Alpha Jungle** adds *frequent-subtree avoidance* — it detects subtrees that repeat too often and avoids them, a cheap anti-convergence trick.

**AgonAlpha (8/2026) has the best validation design in this entire survey** (details in section 5.3). Its architecture has three parts:

| Component                           | Role                                                                                                                                                  |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Proposer**                        | Generates 16 candidate formulas, runs a knockout tournament 16→8→4→2→1, and submits the survivor |
| **Fresh-Context Reviewer**          | Independently audits the frozen artifact, **has veto power** if it detects fabricated numbers, and can rerun evaluation |
| **Pending-Aware MCTS Scheduler**    | Allocates evaluation budget across research lines, **accounts for work already pending**, and prevents starvation during parallel runs |

Its "prompt economy" philosophy: **the whole system uses only 101 lines of prompt** (57 proposer + 44 reviewer), reused for every node. Result: 60 submissions across 5 deployments → **17 SPECTACULAR-ranked alphas**, best Sharpe 3.48, best Fitness 9.50 — scored by WorldQuant BRAIN on US TOP3000, 2019–2023, 1-day data delay.

> 🔑 **The most important detail:** *the reviewer has a fresh context*. It does not see the search process or the failed attempts — so it cannot be persuaded by the story the proposer tells itself. This is **temporal isolation applied to the agent itself**, not only to data.

---

### K4 — Population evolution / quality-diversity

**Members:** QuantEvolve, MadEvolve, AlgoEvolve, QuantaAlpha, CogAlpha, AlphaPROBE, EFS.

This is the architecture you chose. **Good news: several independent groups converged on this design in 2026** — evidence of feasibility, not yet of efficacy (see §7.1).

**MadEvolve** (5/2026, Kvasiuk–Li–Colegrove–Münchmeyer) reconstructs something almost identical to QuantEvolve without citing it:

```
Parent sampling from population database
  → Inspiration retrieval (global best + recent top + diverse neighbors)   ← exactly "cousins"
  → LLM mutation (~70% differential patch, ~30% full rewrite)
  → Backtest fitness
  → Update database
```

Plus a **MAP-Elites grid** (partitioned by code complexity, diversity, performance) + **5-island model, migration every 5 generations, 10% rate** + global elite archive. The 10% migration figure matches QuantEvolve exactly.

> 🔍 **Repo cross-check (21 Sep 2026) — [`tianyi-stack/MadEvolve`](https://github.com/tianyi-stack/MadEvolve), commit `8b881d3`, 3 Mar 2026.** The repo is a **general-purpose framework** with no evaluator, data, or Bitcoin domain code — the Sharpe 5.65 figure **cannot be reproduced** from it. The repo ↔ paper link is unverified (the README does not cite the paper). More importantly: in the public code the **island model is dead code** — `HybridPopulationManager.get_selection_pool()` always returns `[]`, so the parent is always drawn from the **global top-20** (`artifact_store.get_top_programs(20)`); `sample_from_island()` is never called. The MAP-Elites grid is used only to fetch "diverse inspirations". ⇒ The evidence of "independent convergence on the island model" lives only in the **paper's description**, not in runnable code. Details: [Architecture_Design.md](Architecture_Design.md) §3.1.

> Two independent groups, two different domains (alpha factor vs Bitcoin execution), same architecture. **This is strong evidence that MAP-Elites + island is the right solution for this class of problem**, not an arbitrary choice unique to the QuantEvolve paper.

**QuantaAlpha** (2/2026, SUFE/Tsinghua/Peking/CMU/HKUST alliance) — the prettiest numbers in the whole survey:

| CSI 300, test 2022–2025, GPT-5.2 |                  |
| --------------------------------- | ---------------- |
| IC                                | **0.1501** |
| ICIR                              | 0.9110           |
| ARR                               | **27.75%** |
| MDD                               | −7.98%           |
| IR                                | **3.3251** |

Zero-shot transfer: CSI 500 **+160%**, S&P 500 **+137%** cumulative excess return over 4 years. Code: [`QuantaAlpha/QuantaAlpha`](https://github.com/QuantaAlpha/QuantaAlpha).

Control mechanism: AST as the intermediate representation; verifies **semantic consistency** across hypothesis ↔ description ↔ expression ↔ code; limits symbolic length to ≤250 and ≤6 base features; deduplicates via AST isomorphism. Mutation = self-reflection to identify the weak decision node and **rewrite only the local segment** of the trajectory; crossover = combine effective segments from multiple parents.

Fitness uses a complexity penalty:

```
R(τ) = ℒ(f_τ(X), y) − λ·ℛ(f_τ)
```

**AlgoEvolve** (6/2026, Sharma & Shroff) adds an orthogonal axis — **meta-evolution**:

```
Inner loop:  LLM (Gemini Flash) generates Python strategy
             ← receives top-2 best + top-2 worst from previous generation
Outer loop:  Meta-LLM (Gemini Pro) evolves the PROMPT itself
             "Prompt Genome" = 4 genes: mutation style / creative focus
                                       / constraints / reasoning framework
             + uniform crossover between elite genomes
```

This is the **cheapest idea to copy** in the whole report: once you already have an evolution loop, adding a prompt-evolution layer costs almost no extra infrastructure. The paper claims evolved prompts **always outperform** human-designed instructions. (But see section 5.4 — AlgoEvolve's reported numbers have issues.)

**AlphaPROBE** (2/2026) tries a variant: replace the MAP-Elites grid with a **graph**, using graph-biased evolution plus principled retrieval. Code: [`gta0804/AlphaPROBE`](https://github.com/gta0804/AlphaPROBE). There is not enough evidence yet to conclude that it beats a grid.

**CogAlpha** (China Mobile JiuTian + HKU) goes the opposite way — instead of using a feature map to create diversity, it **imposes diversity through organizational structure**: **21 agents arranged in 7 layers** by financial theme (market structure → tail risk → price-volume → price-volatility → multi-scale complexity → regime-gating → geometric/fusion), plus a multi-agent quality checker (code quality / repair / judge / logic improvement) and **time-leakage unit tests embedded directly in the checker**. CSI300 10-day result: IC 0.0591, ICIR 0.3410, IR **1.8999**. Cost ~5–9 seconds/factor, ~1 hour/generation on one H100.

---

### K5 — Weight updates (RFT/RL) — the newest 2026 direction

**Members:** QuantEvolver (5/2026), Alpha-R1 (updated 9/9/2026).

This is the year's biggest conceptual leap. Instead of stuffing backtest feedback into a prompt, **turn backtest feedback into gradients that update model weights**.

**QuantEvolver** — `DiCo` reward (Diversity-Complementarity):

| Reward component | Content                                                                               |
| ---------------- | ------------------------------------------------------------------------------------- |
| Prediction       | Directional accuracy, IC, RankIC                                                      |
| Shaping          | Penalizes exact repetition, rewards structural-family diversity, behavioral complementarity score |

Base model: **Qwen3-14B**. Result: RankIC 0.0586 on an hourly cross-sectional benchmark (**+109.5%** versus the strongest baseline), 0.1923 on daily equity — beating AlphaBench, QuantaAlpha, R&D-Agent, Alpha-Jungle. Code: [`QuantLLM/QuantEvolver`](https://github.com/QuantLLM/QuantEvolver).

> 🔑 **Their counterintuitive argument, and why it matters for your project:** they **intentionally choose small models (≤30B)** because very large models have *"stable generation preferences"* — stable generative preferences — that make search stagnate. Over-large models are too confident in their canonical answers.

**Alpha-R1** (Qwen3-8B, 64×H800 for ~120 hours) reports AR 47.87% on S&P 500 and 40.57% on CSI 300, Sharpe 1.62 / 2.23 — versus the strongest LLM baseline (DeepSeek-R1) at only 21.94% / 14.66%. Code: [`FinStep-AI/Alpha-R1`](https://github.com/FinStep-AI/Alpha-R1).

**But Alpha-R1 matters for a completely different reason — see section 5.2.**

---

## 3. Full system summary table

| System                    | Date            | K             | Output                         | Market                       | Main result                        | Code       |
| ------------------------- | --------------- | ------------- | ------------------------------ | ---------------------------- | ---------------------------------- | ---------- |
| Alpha-GPT                 | 7/2023          | K1            | Expression                     | A-share                      | —                                  | ❌         |
| AlphaAgent                | KDD 2025        | K2            | Factor                         | CSI500, S&P500               | IR 1.488 / 1.0545                  | ✅         |
| RD-Agent(Q)               | NeurIPS 2025    | K2            | Factor + ML model              | CSI300                       | IC 0.0532, IR 1.7382               | ✅ 14.7k★ |
| Alpha Jungle              | AAAI 2026       | K3            | Expression                     | A-share                      | —                                  | ✅         |
| EFS                       | 7/2025          | K4            | Factor (sparse portfolio)      | —                            | —                                  | —          |
| Chain-of-Alpha            | 8/2025          | K2            | Factor                         | A-share                      | —                                  | —          |
| Tree-structured Thoughts  | 8/2025          | K3            | Factor                         | A-share                      | —                                  | —          |
| **QuantEvolve**           | 10/2025         | **K4**        | **Strategy**                   | Multi                        | SR 1.52, CR 256% @gen150           | ❌         |
| CogAlpha                  | 11/2025→4/2026  | K4            | Factor                         | CSI300/500, S&P500, HSI/HSCI | IC 0.0591, IR 1.8999               | ❌         |
| Alpha-R1                  | 12/2025→9/2026  | **K5**        | Factor screening               | S&P500, CSI300               | AR 47.87%, **DSR 0.39–0.85**       | ✅         |
| QuantaAlpha               | 2/2026          | K4            | Factor                         | CSI300/500, S&P500           | IC 0.1501, IR 3.3251               | ✅         |
| AlphaPROBE                | 2/2026          | K4 (graph)    | Factor                         | A-share                      | —                                  | ✅         |
| **SysTradeBench**         | 4/2026          | *benchmark*   | —                              | —                            | 17 LLM, 61.8% pass                 | ✅         |
| Crypto Constrained        | 4/2026          | K2 + DSL      | Factor                         | **Crypto**                   | AR 44.55%, SR 1.55 OOS             | ❌         |
| QuantEvolver (RFT)        | 5/2026          | **K5**        | Factor                         | 3 benchmarks                 | RankIC +109.5%                     | ✅         |
| **Agentic Trading**       | 5/2026          | *survey*      | —                              | —                            | **0/19 reach R3**                  | —          |
| MadEvolve                 | 5/2026          | K4            | **Strategy + execution**       | **Bitcoin**                  | Test SR 5.65 ⚠️                    | ✅         |
| AlphaMemo                 | 6/2026          | K2 (memory)   | Factor                         | Qlib                         | vs AlphaGen/Forge/QCM              | ✅         |
| AlgoEvolve                | 6/2026          | K4 + meta     | **Python strategy**            | Equity intraday 5m           | Sharpe 5.60 ⚠️                     | ❌         |
| XALPHA                    | 7/2026          | K2 (memory)   | Factor                         | CSI300                       | IC 0.0619, IR 1.5368               | ❌         |
| AgonAlpha                 | 8/2026          | K3            | Expression                     | US TOP3000                   | 17 SPECTACULAR, SR 3.48            | ✅         |
| Survey FITEE              | 11/2025         | *survey*      | —                              | —                            | Taxonomy of LLM roles              | —          |

---

## 4. Imbalance: the literature is almost entirely School A

Counted by output:

| Output                                                                 | Number of systems | Note                                                                    |
| ---------------------------------------------------------------------- | ----------------- | ----------------------------------------------------------------------- |
| **Factor / expression** (School A)                                      | ~18/22            | Almost always CSI300/CSI500/S&P500, Qlib, 1–10 day return prediction |
| **Executable strategy** with entry/exit (School B)                      | **2/22**          | AlgoEvolve (equity intraday), MadEvolve (Bitcoin)                       |
| Benchmark / survey                                                     | 3/22              |                                                                         |

**This has two implications for you:**

✅ **Good side** — little competition. If School B really is effective for individuals as [04](04-SCHOOL-B-EFFICACY.md) argues, then almost nobody in academia is exploiting it with LLMs.

❌ **Bad side** — few shoulders to stand on. All standard metrics (IC, ICIR, RankIC), all baseline sets, and all lessons about overfitting in the literature belong to cross-sectional factors. When you need a reference point for "is my TA strategy good?", the literature does not give you one. You have to return to [04](04-SCHOOL-B-EFFICACY.md) (Hurst-Ooi-Pedersen: Sharpe ~0.4/market) as the benchmark.

Also note: **QuantEvolve is the only K4 system that generates full strategies and does not publish code.** You are rebuilding something nobody has publicly rebuilt.

---

## 5. Validation gap — hard evidence

This is the most important part of the report, and it **confirms the central thesis** of [07-VALIDATION-LAYER.md](07-VALIDATION-LAYER.md) with data, not speculation.

### 5.1 "Agentic Trading" survey (5/2026) — brutal numbers

Among 19 studies that meet the minimum criteria (Action Output + Closed-Loop Evaluation):

| Criterion                                                        | Passed            |
| ---------------------------------------------------------------- | ----------------- |
| Has a temporally consistent, extractable data split               | **2 / 19**        |
| Clearly reports a transaction-cost model                         | **1 / 19**        |
| Handles universe / survivorship bias                             | **1 / 19**        |
| Lowest reproducibility level (R0)                                | **15 / 19**       |
| Highest reproducibility level (R3)                               | **0 / 19**        |

The survey's verbatim conclusion: *"architectural experimentation is expanding rapidly, while comparable evaluation protocols, execution semantics, and reproducible artifacts remain the field's immediate bottlenecks."*

And on exactly the problem of K3/K4 architectures:

> *"Search algorithms (MCTS, ToT) that evaluate thousands of candidate strategies inherently face the multiple testing problem — finding a profitable trajectory purely by chance."*

The survey recommends mandatory reporting of **"Search Budget"** — exactly the **trial ledger** you designed in [Architecture_Design.md](Architecture_Design.md).

### 5.2 Alpha-R1 — the only system reporting DSR, and it fails

In the entire survey, **only one system discloses Deflated Sharpe Ratio**. The figures:

| Metric                                | Value                 |
| ------------------------------------- | --------------------- |
| PSR                                   | 0.96 – 0.996          |
| **DSR (N = 101–158)**                 | **0.39 – 0.85**       |
| DSR (N = 34, narrower estimate)       | 0.84 – 0.89           |
| PBO (CSCV)                            | 0.016 – 0.133         |

The authors admit directly: DSR *"remains below the conventional 0.95 bar"*, explain that this is *"an inherent property of a single-year window under conservative multiple-testing correction"*, and warn that *"The current evidence is limited to a single 12-month test window."*

> 🔴 **This is the most important number in the whole report.**
>
> A system trained on **64 H800 GPUs for 120 hours**, reporting **AR 47.87%** and **Sharpe 1.62**, with only **34–158 trials** — still **cannot pass the DSR 0.95 threshold**.
>
> Your plan is 4,500–9,000 *LLM calls* — the statistical trial count is lower, and the number of independent trials (`N_eff`) lower still, but still in the hundreds to thousands *(correction 21 Sep 2026: the previous version mistook LLM calls for trials; see [Architecture_Design.md](Architecture_Design.md) §4.1)*. DSR penalizes by the number of independent trials. If Alpha-R1 fails at 158 trials, the Sharpe threshold you need to pass DSR at 9,000 trials will be **substantially** higher.
>
> This does not mean your project is impossible. It means the **trial ledger is not paperwork — it is the tightest constraint in the whole system**, and you need to design for *trial efficiency*, not for *running many trials*.

Direct design consequence: every mechanism that reduces the required number of trials (insight repository, meta-evolution prompt, DSL that constrains the search space) is worth **twice as much** — it saves money and lowers the DSR threshold.

### 5.3 AgonAlpha — the lesson is not statistical

AgonAlpha does not compute DSR. But its design is more credible for one structural reason:

> **The evaluator is outside the authors' reach.** WorldQuant BRAIN decides the data, simulator, metrics, and scoring scale. The authors *cannot* tune anything there.

The paper explicitly states the difference from academic holdouts: in academic holdouts, **the authors still control the whole pipeline** — so the "holdout" is only a promise.

Add a 5-dimensional Evidence Integrity Audit (expression–metric match, sign-logic consistency, justification for constants, year-by-year stability, detection of selection-induced duplicates), self-correlation gate 0.85, and a reviewer with **permission to zero out a result if it detects fabricated numbers**.

> 🔑 **Takeaway for you:** the principle of **P6 (write-once holdout)** is pointing in the right direction, but it is weak because *you are enforcing a promise to yourself*. To make it more real: lock the holdout at the **operating-system level** (read-only file, precommitted hash, `holdout_access` with `PRIMARY KEY` as you designed), and consider a **separate evaluator process** that shares no state with the evolution loop.

### 5.4 Warning: read result tables before trusting them

**AlgoEvolve** reports Sharpe **5.60**. But its own table says:

| Method                           | Mean (%)          | Vol (%) | **Ann. Sharpe** | Max DD (%) |
| -------------------------------- | ----------------- | ------- | ---------------- | ---------- |
| AlgoEvolve (6 MG)                | 0.31              | 0.88    | **5.60**         | 1.59       |
| Standard Evol (inner loop only)  | 0.10              | 0.29    | **5.71** ⬅       | 0.42       |
| RF baseline                      | **0.51** ⬅        | 1.52    | 5.24             | 7.27       |
| LSTM baseline                    | −0.05             | 0.68    | −1.11            | 3.79       |
| Seed heuristic                   | −0.78             | 1.06    | −11.75           | 17.56      |

Two observations:

1. **Meta-evolution does NOT improve Sharpe** — removing it (Standard Evol) yields **higher** Sharpe (5.71 > 5.60) with drawdown almost 4× lower. It only raises mean return by raising risk.
2. **Random Forest has higher mean return** (0.51% > 0.31%).

The paper also admits: the elite prompt reaches Sharpe 5.60, but the **population average is only ~1.21** — meaning the headline figure is *best-of-N*, selection bias in its purest form.

**MadEvolve** reports test Sharpe 5.65 on Bitcoin, but handles the issue more honestly: it devotes a whole section to *"Research or P-Hacking? The Central Question"*, uses the Bailey et al. (2014) framework, tracks the **IS→OOS degradation ratio** versus what multiple-testing theory predicts for pure p-hacking, and admits that for forecasting tasks, *"the results are more nuanced... the optimal forecasting model also exhibits some degree of overfitting"*.

> 🔍 **Full-text reading of the MadEvolve paper (21 Sep 2026) — revising the "more honest" assessment above.** Section §7 exists, but is weaker than it looks:
> - **The null is too weak:** it compares against a "p-hacking around the baseline" procedure (PnL ~ Gaussian around baseline performance), not a "no edge" null; no DSR or PBO, and the trial count is not used.
> - **The test set is not clean in the P6 sense:** the 2025 test set is measured for **every** IS champion throughout evolution (Figures 10–11) and shared by all 5 runs + the Claude Code runs. It is not used to *select*, but the authors looked at it thousands of times and chose which run to report.
> - **Optimistic backtest:** a limit order fills 100% at exactly its price whenever price trades through, no queue; minute data aggregated across exchanges; impact applied post hoc. The baseline already shows Sharpe 4.81. The authors themselves write *"still far from being realistic"*.
> - **Joint does not always win:** Run 3 (joint strategy) does not beat Run 2 (order placement only); Run 5 (joint features + strategy) has the best OOS Sharpe but keeps only 39% of validation PnL on test, and win rate drops 68.6% → 49.8%.
> - **Prompt sensitivity:** in the Claude Code comparison, one run gave +627% OOS and another, with a slightly changed prompt, +44%.
> - **Worth learning:** a parameter budget (15–20, declared as UPPER_CASE constants, penalized when exceeded) added after unconstrained pilots overfit; **deliberately no** inner parameter-optimization loop because it inflates the trial count; PnL chosen over Sharpe because Sharpe can be inflated by trading less. Brought into [Architecture_Design.md](Architecture_Design.md) version 0.5.

> **Rule for reading papers in this field:** when you see Sharpe > 3 on daily/intraday data, look for 3 things before trusting it — (a) whether it is best-of-N, (b) how many bp of transaction costs were applied, and (c) how long the OOS period is. If any one is missing, the number is meaningless.

### 5.5 SysTradeBench — LLMs write data leakage with their own hands

Benchmark 4/2026, **17 LLM × 12 strategies**, scored on 4 dimensions: D1 spec fidelity / D2 risk discipline / D3 reliability & audit / D4 OOS indicators.

Pass rate across all validity gates: **61.8%** (despite 78.4% parse success).

Observed **failure modes** — read the second carefully:

- Missing/incomplete audit log even when functional tests pass
- **Future-data access through negative shift (`df.shift(-1)`)**
- Nondeterministic execution due to unseeded randomness or dict ordering
- Incorrect NaN handling in rolling-indicator warmup windows
- Misreporting constraint violations, missing severity

> 🔴 **`df.shift(-1)` is exactly the leaky oracle in [07](07-VALIDATION-LAYER.md) — but the LLM writes it spontaneously without being told.**
>
> You designed `leaky_oracle.py` as an **intentional test** for checking guardrails. SysTradeBench proves that LLMs **generate it spontaneously** when asked to write strategies. That means leakage guardrails are not defensive overengineering — they are something that will fire often in real operation.

The second finding is very important for your roadmap — **correlation between strategy complexity and validity rate: Spearman ρ = −0.68 (p < 0.05)**:

| Strategy            | Validity rate |
| ------------------- | ------------- |
| Double MA Crossover | **88.2%**     |
| ...                 | ...           |
| Index Enhancement   | **35.3%**     |

And the anti-spec-drift mechanism (**cheap, should be copied as-is**):

```
Layer 1 — static checksum: SHA256 over core logic fields (entry/exit rule, parameters)
Layer 2 — trace regression: Levenshtein distance Δ over action sequence
                         (normalized Δ = Levenshtein / max(len) ∈ [0,1]; compared against the anchor
                          trace of the first implementation — full spec: Architecture_Design §3.1.7)

   Δ < 0.05         → bugfix, allowed
   0.05 ≤ Δ < 0.15  → flagged, requires human review
   Δ ≥ 0.15         → suspicious divergence, D1 = 0, abort loop
```

---

## 6. Backbone model: new data reverses intuition

This section **revises** my earlier recommendation about small models.

### 6.1 CogAlpha ablation (CSI300)

| Model                    | IC               | RankIC             | ICIR             | RankICIR           | IR               |
| ------------------------ | ---------------- | ------------------ | ---------------- | ------------------ | ---------------- |
| Llama3 8B                | 0.0121           | **−0.0074**        | 0.0972           | **−0.0540**        | 0.5077           |
| Llama3 70B               | 0.0205           | 0.0229             | 0.1786           | 0.1915             | 0.6312           |
| gpt-oss-20B              | 0.0061           | 0.0075             | 0.0613           | 0.0680             | 0.4885           |
| **gpt-oss-120B**         | **0.0300**       | **0.0318**         | **0.2501**       | **0.2595**         | **0.8015**       |
| GPT-4.1                  | 0.0118           | 0.0114             | 0.1069           | 0.1037             | 0.3628           |
| **o3** (reasoning)       | **0.0019**       | **−0.0050**        | 0.0203           | −0.0475            | **0.2278**       |

Three surprises:

1. **Llama3-8B produces NEGATIVE RankIC.** Small models are not merely weaker — they generate factors with the *wrong sign systematically*. That is worse than doing nothing.
2. **gpt-oss-20B is worse than Llama3-8B on IC.** Parameter count is not the right axis.
3. **o3 — the strongest reasoning model in the list — is worst.** The paper's exact wording: *"the reasoning-oriented model achieving the worst performance among all evaluated LLMs."*

### 6.2 Why reasoning models are bad here

Combined with QuantEvolver's argument — that very large models have **"stable generation preferences"** that cause search stagnation — we get a *plausible* explanation (a hypothesis, not yet tested):

> This task is **not** about finding the right answer. It is about **generating many diverse hypotheses, most of which will be wrong**, so the selection mechanism can do its job.
>
> Reasoning models are optimized to converge toward the best answer. Here, convergence *is* failure — it is mode collapse. The more "certain" the model is, the emptier the feature map becomes.

This also explains why K4 needs MAP-Elites and K5 needs diversity reward: **both are mechanisms that fight the LLM's natural tendency to converge.**

> ⚠️ **Limits of the evidence** *(added 21 Sep 2026)*. CogAlpha is a **cross-sectional factor-generation benchmark on CSI300** (school A), one run per model. The paper does **not** test causes — "mode collapse" is my interpretation, not a measured result. And nothing yet shows this ranking holds for multi-market rule-based TA. Hence: *prefer* non-reasoning as the default, **do not ban** reasoning models — A/B internally before concluding.

### 6.3 Model tiers from SysTradeBench (strategy-code generation — closest to your task)

| Tier             | Validity         | Score     | Model                                                      |
| ---------------- | ---------------- | --------- | ---------------------------------------------------------- |
| **Top**          | 91.7%            | 7.29–7.85 | GPT-5.2, GPT-5.1, o3, Grok-4 Fast                          |
| **Mid**          | 75–91.7%         | 6.94–7.44 | GLM-4.6, DeepSeek-V3, Claude variants                      |
| **Budget**       | **8–58%**        | 5.38–6.26 | GLM-4.7, Grok-4, Gemini Flash, Gemini-2.5 Pro, DeepSeek-R1 |

The Mid tier reaches **90–95% of Top-tier quality at 40–50% of the cost** — this is the sweet spot.

Note the interesting contradiction: **o3 is Top tier in SysTradeBench (writes code to spec), but dead last in CogAlpha (finds good factors).** This is not a contradiction — these are two different skills, and it confirms the **heterogeneous model-routing** strategy already discussed.

### 6.4 Revised recommendation

| Role                                          | Model                                                                                                              | Reason                                                |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| **Research Agent** (hypothesis generation)    | Strong model, **non-reasoning by default**. `gpt-oss-120b` is a starting point with evidence (on a school-A benchmark) | Needs diversity, not convergence. Avoid o3-class |
| **Coding Team** (code writing)                | Code-specialized model, Mid tier by SysTradeBench                                                                   | 90–95% quality, 40–50% cost                    |
| **Evaluation Team**                           | Mid tier + constrained decoding                                                                                   | Scores according to a fixed schema              |
| **Data Agent**                                | Largest context                                                                                                  | Reads schema                                    |

**Do not** use a dense <10B model without fine-tuning in any role (negative RankIC). **Do not** do RFT (Alpha-R1: 64×H800 × 120h).

---

## 7. My view

### 7.1 You chose the right architecture, and now there is independent evidence

MadEvolve rebuilds MAP-Elites + 5 islands + 10% migration without knowing QuantEvolve. Two groups, two domains, one design. That is evidence K4 is **feasible and natural** — *not* evidence it is effective for multi-market rule-based TA (both are self-reported, with no random-search control). Treat it as a working hypothesis, validated internally in phase 2 *(conclusion strength adjusted 21 Sep 2026)*.

At the same time, the K2 weaknesses we inferred ("early convergence") now have official names in the literature — *context explosion*, *feedback drift*, *search stagnation* — and they are the force pushing the whole field toward K4/K5.

### 7.2 But you are solving the wrong hardest problem

The hardest problem is **not architecture**. The field has already solved that part — 5 patterns, public, copyable.

The hardest problem is: **you will make 4,500–9,000 LLM calls — i.e. hundreds to thousands of statistical trials — and Alpha-R1 failed DSR with 158 trials.**

This should change your priority order. The old framing was *"build a strong agent loop, then add validation"*. The better framing is:

> **Trial count is the project's scarcest budget. The agent architecture should be chosen by "good strategies per trial", not by "many strategies generated".**

The paradox is that K4 — your chosen architecture — is the most trial-hungry architecture. It trades trials for diversity.

That is still right, but only for the reason discussed earlier: **the feature map is the diversified portfolio**, and breadth is the decisive variable (Sharpe 0.4 → 1.6). You are not using 9,000 trials to find one strategy — you are using them to fill 20 low-correlation cells. **As long as you validate those 20 cells as one combined portfolio, not as 20 separate candidates.**

> This is where one concrete design choice becomes crucial: **DSR should be computed on the merged portfolio returns, with N = total project trials.** Do not compute DSR separately for each cell and then choose the best cell — that reintroduces selection bias through the back door.

### 7.3 Three low-cost things to copy now

| What to copy                                                                                                                 | From                      | Why                                                                                                                              |
| ---------------------------------------------------------------------------------------------------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Restricted DSL + deterministic engine owns evaluation rules; LLM cannot edit data split during a session**                 | Crypto Constrained Agents | Narrows search space → fewer trials needed → easier DSR. And it is a structural guardrail, not a hope |
| **2-layer drift detection: SHA256 over core logic + Levenshtein over action trace**                                          | SysTradeBench             | Nearly free. Catches the agent secretly changing the spec between refinement rounds |
| **Fresh-context reviewer with veto power**                                                                                    | AgonAlpha                 | The second agent does not see search history → cannot be persuaded by the self-narrative |

### 7.4 One thing NOT to copy even though it sounds good

**Meta-evolution prompt genome (AlgoEvolve).** I was going to recommend it because the idea is elegant and cheap. But the table in section 5.4 shows that **removing it gives higher Sharpe and 4× lower drawdown**. The evidence in favor is currently negative.

More importantly: each prompt variant is a **new search axis**, and each new axis expands `N` in the DSR formula. You are spending trials on something whose value has not been proven.

### 7.5 Do not use these papers' numbers as expectation anchors

QuantaAlpha IR 3.3251, MadEvolve Sharpe 5.65, AlgoEvolve Sharpe 5.60. Put them next to the survey: **1/19 papers report transaction costs**.

The right expectation anchor for you is still the one in [04](04-SCHOOL-B-EFFICACY.md): **Sharpe ~0.4 per market, ~1.6 with good diversification.** The only number in this survey that is close to plausible and has decent OOS testing is **Crypto Constrained Agents: Sharpe 1.55 after 5bp fees on pure 2024–2026 OOS** — and that is cross-sectional long-short, not School B.

### 7.6 Your competitive advantage is confirmed, but narrower

In [00](00-RESEARCH-SYNTHESIS.md) we concluded "no system has DSR/PBO — this is the competitive advantage". After this survey, that should be revised:

- ✅ **Still basically true**: 1/22 systems report DSR, 0/19 reach R3 reproducibility.
- ⚠️ **But it is no longer empty territory**: Alpha-R1 has DSR+PBO+PSR (9/2026), AgonAlpha has external evaluator + 5-dimensional audit (8/2026), Crypto Constrained Agents has deterministic engine + predefined gates (4/2026), CogAlpha embeds time-leakage unit tests.

The field is moving in the direction you predicted, just slowly and in fragments. Your real advantage now is **combining all three defense layers in one system** — nobody has done that — rather than merely "knowing about DSR".

### 7.7 What worries me most in the current plan

`ρ = −0.68` between strategy complexity and valid-code generation rate. Double MA Crossover: 88.2%. Index Enhancement: 35.3%.

Your feature map has **strategy category** as one dimension, and its purpose is to push the system into increasingly diverse strategy types — meaning increasingly complex ones. But valid-code generation rate **falls as complexity rises**.

Consequence: cells corresponding to complex strategies will stay **empty much longer** than simple cells, not because they are bad, but because the code does not run. The feature map will *look like* it has explored fully while actually filling only the easy region.

**Recommendation:** add `gen_attempts` and `gen_failures` columns to the ledger per cell. If a cell has a code-generation failure rate > 50%, that is a signal to **allocate more refinement budget to that cell**, not to conclude that the region has no good strategies.

---

## 8. Concrete impact on Architecture_Design.md

| Item                      | Proposed change                                                                                                                                   | Source       |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| **3.1** Agent layer       | Keep the QuantEvolve architecture — MadEvolve independently converged on the same design (hypothesis, not proof of efficacy)                                                                           | §7.1         |
| **3.1.7** Model           | Replace the "use small model" recommendation with the heterogeneous-routing table in §6.4. **Add warning: avoid reasoning models for Research Agent by default** (hypothesis, A/B in phase 2) | §6           |
| **New gate ⑦**            | 2-layer drift detection (SHA256 + Levenshtein) between refinement rounds                                                                           | §5.5         |
| **Guardrail**             | Add restricted DSL + deterministic engine owning evaluation rules; agent cannot edit split                                                         | §7.3         |
| **Evaluation Team**       | Split into a **fresh-context reviewer**, not given search history, with veto power                                                                  | §5.3         |
| **Validation**            | **Compute DSR on the merged portfolio with N = total project trials**, not separately per cell                                                      | §7.2         |
| **Ledger**                | Add `model_used`, `gen_attempts`, `gen_failures` per cell                                                                                          | §6.4, §7.7   |
| **P6 holdout**            | Promote to OS level: read-only file + precommitted hash + separate evaluator process                                                               | §5.3         |
| **Expectation**           | Do not use paper IR 3.3 / Sharpe 5.6 as targets. Keep the Sharpe 0.4→1.6 anchor                                                                    | §7.5         |
| **DO NOT do**             | Meta-evolution prompt genome; RFT/RL fine-tuning                                                                                                  | §7.4, §6.4   |

---

## 9. What I could NOT verify

Recorded here so these are not reused as verified facts:

- **All figures in this report come from paper abstracts/HTML through an automatic summarization tool.** I did not rerun any backtest and did not read the code of any repo.
- **I did not check whether repos match their papers.** This is exactly the error already found with `tarsyang/quantevolve` (see [00](00-RESEARCH-SYNTHESIS.md), section 4). The QuantaAlpha, Alpha-R1, QuantEvolver, AlphaPROBE, AlphaMemo, and SysTB repos **have not been cross-checked**.
- **Survey FITEE** ("A survey on large language model-based alpha mining", Springer, 11/2025) is blocked behind login. Only the abstract was readable: taxonomy of LLM roles (miner / evaluator / interactive assistant) and 6 named challenges — *oversimplified performance evaluation, poor numerical understanding, lack of diversity and originality, weak exploration incentives, temporal data leakage, black-box and compliance risk*.
- **Alpha Jungle, Tree-structured Thoughts, EFS, Chain-of-Alpha, AlphaMemo, AlphaPROBE** — I could only obtain architecture at the conceptual level, with no results table.
- **GitHub star counts** are taken from search results and have not been directly verified on GitHub.
- **I have not found** any system that generates School B strategies for **forex** or the **Vietnamese market**. It may not exist, or I may simply not have found it.

---

## Sources

**Surveys & benchmarks**

- [Agentic Trading: When LLM Agents Meet Financial Markets](https://arxiv.org/abs/2605.19337) — arXiv 2605.19337, 5/2026
- [SysTradeBench](https://arxiv.org/pdf/2604.04812) — arXiv 2604.04812, 4/2026 · [`YgcCoder/SysTB`](https://github.com/YgcCoder/SysTB)
- [A survey on LLM-based alpha mining](https://link.springer.com/article/10.1631/FITEE.2500386) — FITEE, 11/2025

**K1–K3 architectures**

- [Alpha-GPT](https://arxiv.org/pdf/2308.00016) — arXiv 2308.00016, 7/2023
- [Navigating the Alpha Jungle](https://arxiv.org/abs/2505.11122) — arXiv 2505.11122, AAAI 2026
- [From Flat to Hierarchical](https://arxiv.org/pdf/2508.16334) — arXiv 2508.16334, 8/2025
- [AgonAlpha](https://arxiv.org/html/2608.11250) — arXiv 2608.11250, 8/2026
- [Chain-of-Alpha](https://arxiv.org/pdf/2508.06312) — arXiv 2508.06312, 8/2025
- [AlphaMemo](https://arxiv.org/pdf/2606.20625) — arXiv 2606.20625, 6/2026 · [`jarrettyu/AlphaMemo`](https://github.com/jarrettyu/AlphaMemo)
- [XALPHA](https://arxiv.org/html/2607.08332v2) — arXiv 2607.08332, 7/2026
- [From Hypotheses to Factors: Constrained LLM Agents in Cryptocurrency Markets](https://arxiv.org/html/2604.26747v1) — arXiv 2604.26747, 4/2026

**K4 architectures**

- [QuantaAlpha](https://arxiv.org/html/2602.07085v2) — arXiv 2602.07085, 2/2026 · [`QuantaAlpha/QuantaAlpha`](https://github.com/QuantaAlpha/QuantaAlpha)
- [MadEvolve](https://arxiv.org/html/2605.23007v1) — arXiv 2605.23007, 5/2026 · [madevolve.org](https://madevolve.org)
- [AlgoEvolve](https://arxiv.org/html/2606.26173) — arXiv 2606.26173, 6/2026
- [CogAlpha](https://arxiv.org/html/2511.18850v2) — arXiv 2511.18850, 11/2025→4/2026
- [AlphaPROBE](https://arxiv.org/pdf/2602.11917) — arXiv 2602.11917, 2/2026 · [`gta0804/AlphaPROBE`](https://github.com/gta0804/AlphaPROBE)
- [EFS](https://arxiv.org/pdf/2507.17211) — arXiv 2507.17211, 7/2025

**K5 architectures**

- [Alpha-R1](https://arxiv.org/html/2512.23515) — arXiv 2512.23515, version 9/9/2026 · [`FinStep-AI/Alpha-R1`](https://github.com/FinStep-AI/Alpha-R1)
- [QuantEvolver / RFT](https://arxiv.org/html/2605.15412v1) — arXiv 2605.15412, 5/2026 · [`QuantLLM/QuantEvolver`](https://github.com/QuantLLM/QuantEvolver)

**Evolutionary foundations**

- [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) — DeepMind
- [CodeEvolve](https://arxiv.org/html/2510.14150v1) — arXiv 2510.14150, open-source version, confirms MAP-Elites (CVT variant) is necessary to outperform AlphaEvolve

**Excluded from scope** (LLM at runtime)

- [TradingAgents](https://github.com/tauricresearch/tradingagents) · [Vibe-Trading](https://www.coddykit.com/pages/blog-detail?id=512921) · AI Hedge Fund · FinMem · FinAgent · FinCon · FinRL
