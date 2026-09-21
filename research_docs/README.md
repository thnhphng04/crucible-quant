# Crucible Quant — Research Documentation

> *Crucible Quant — an AI quantitative research lab that evolves systematic trading strategies and puts every one through the fire before it trades.*

Project goal: build an AI system that develops trading strategies (agent loop: generate strategy → backtest → optimize → validate), where the output strategy is **deterministic and never calls an LLM at runtime**.

> 🌐 **English edition.** The Vietnamese original lives in [`../research_docs_vi/`](../research_docs_vi/) and is the source of truth. File numbers match 1:1 across both folders.

---

## 🏗️ Implementation blueprint

| File | Contents |
|---|---|
| **[Architecture_Design.md](Architecture_Design.md)** | **System design.** Assumptions, 6 principles, layer-by-layer specs, data model, tech stack, 7-phase roadmap, risks. **Read §0 and §10 first** — they contain assumptions that need your confirmation |
| **[../implement_docs/](../implement_docs/README.md)** | **How to build it** (English only): task list with acceptance tests, code conventions, invariant → test map, module map, ADRs. Coding-agent instructions: [../CLAUDE.md](../CLAUDE.md) |

## Synthesis & analysis

| File | Contents | When to read |
|---|---|---|
| [00-RESEARCH-SYNTHESIS.md](00-RESEARCH-SYNTHESIS.md) | **Start here.** Synthesis of the 4 source reports: convergent conclusions, 5-component architecture, recommended stack, hard gate-threshold table | Before doing anything |
| [03-FEASIBILITY-FOR-INDIVIDUALS.md](03-FEASIBILITY-FOR-INDIVIDUALS.md) | Feasibility of both schools for an individual investor: capital barriers, costs, data, regulation | When choosing a direction |
| [04-SCHOOL-B-EFFICACY.md](04-SCHOOL-B-EFFICACY.md) | Evidence on rule-based TA efficacy (136 years, 67 markets) + the CTA industry + retail outcomes | When setting Sharpe expectations |
| [05-SMOOTH-FLOW.md](05-SMOOTH-FLOW.md) | Seam-free pipeline on $0 data. 8 gate steps, ledger, validation. ⚠️ The Freqtrade recommendation here **only holds for a crypto-only scope** | When starting to code (crypto) |
| [06-MULTI-MARKET-PLATFORM.md](06-MULTI-MARKET-PLATFORM.md) | **Multi-market platform architecture.** NautilusTrader + adapters for crypto / forex / international & Vietnamese equities. Build order, effort estimates | When scope goes beyond crypto |
| [07-VALIDATION-LAYER.md](07-VALIDATION-LAYER.md) | **The most important layer.** Purging/embargo, CPCV, PBO, DSR, trial ledger, leaky-oracle test. Real `purgedcv` API + gate ordering | Before trusting any backtest |
| [08-LLM-QUANT-RESEARCHER.md](08-LLM-QUANT-RESEARCHER.md) | **State of the field (Sep 2026).** Survey of 22 papers/repos on LLMs generating strategies. 5 architectures classified by *where knowledge accumulates*, hard evidence on the validation gap, model-backbone ablations, opinion + required fixes | When you need to know where the field stands, or before locking the agent architecture |

## Source reports (raw material)

The four original research reports are **Vietnamese only** — they were not translated, because they are raw input already distilled into [00](00-RESEARCH-SYNTHESIS.md) and are kept for traceability rather than reading.

| File (in `../research_docs_vi/`) | Topic |
|---|---|
| [90-NGUON-KIEN-TRUC-VA-REPO.md](../research_docs_vi/90-NGUON-KIEN-TRUC-VA-REPO.md) | **Architecture & repos** — does any project already implement all 5 components, and what to assemble from |
| [91-NGUON-AI-AGENT-BACKTESTING.md](../research_docs_vi/91-NGUON-AI-AGENT-BACKTESTING.md) | **AI agents for backtesting (Sep 2025 – Sep 2026)** — audits, look-ahead bias, benchmarks |
| [92-NGUON-THI-TRUONG-CHIEN-LUOC.md](../research_docs_vi/92-NGUON-THI-TRUONG-CHIEN-LUOC.md) | **Markets & strategies** — crypto / forex / stocks + Vietnam |
| [93-NGUON-AI-SINH-STRATEGY-MT5.md](../research_docs_vi/93-NGUON-AI-SINH-STRATEGY-MT5.md) | **AI building rule-based strategies** — MT5, LLM code generation |

> Elsewhere in these documents the four are referenced by short number: `90`, `91`, `92`, `93`.

---

## The two schools under consideration

| | **A — Cross-sectional factor + ML** | **B — Rule-based TA** |
|---|---|---|
| Output | Alpha factor + ML model | Entry/exit/SL/TP rules |
| Decision unit | Cross-sectional ranking | Time series per instrument |
| Universe needed | Large basket (50–500 names) | 1 → 50 instruments |
| Expected Sharpe | 1.0–1.7 | 0.4 (1 symbol) → 1.6 (50 markets) |
| Minimum capital | ~$50k (equity) / $2–5k (crypto perp) | $1k |
| Exports to MQL5 EA | ❌ | ✅ |
| Reference papers | RD-Agent(Q), AlphaAgent, QuantaAlpha — see [08](08-LLM-QUANT-RESEARCHER.md) | [04](04-SCHOOL-B-EFFICACY.md) |

---

## The three most important takeaways

**1. The validation layer is the whole field's blind spot.** Across 22 systems surveyed ([08](08-LLM-QUANT-RESEARCHER.md)): **1 of 22 reports DSR**, and an independent survey found **0 of 19** studies reach the top reproducibility tier while **1 of 19** declares a transaction-cost model. This is what we have to build ourselves.
→ ⚠️ **But the bar is harder than we assumed:** the single system that does report DSR (Alpha-R1, Sep 2026) only reaches **0.39–0.85 at 34–158 trials** — i.e. it **fails the 0.95 threshold**. Our plan calls for 4,500–9,000 *LLM calls* — i.e. hundreds to thousands of statistical trials. See [08 §5.2](08-LLM-QUANT-RESEARCHER.md).

**2. DSR and PBO are not enough.** A "leaky oracle" that reads future data and posts a Sharpe of 34.7 **still passes with DSR = 1.00**. Three independent layers are required: structural guardrail (against leakage) + statistical correction (against selection bias) + temporal isolation (against memorization).

**3. Breadth matters more than rule quality.** The same trading rule: Sharpe 0.4 on one market → 1.60 diversified across 50. Do not tune the rule while running a single symbol.

---

## Corrections already applied (20 Sep 2026)

The source reports **keep their original text**; each carries a correction banner at the top of the file.

- [x] **DSR threshold** — `DSR > 0` is **wrong**; DSR is a probability in 0–1. The correct threshold is **DSR > 0.95**. Fixed in [00](00-RESEARCH-SYNTHESIS.md) and [05](05-SMOOTH-FLOW.md); banners in `90`, `91`
- [x] **QuantEvolve code** — `tarsyang/quantevolve` is **not** the code behind arXiv 2510.18569 (Gemini vs Qwen3, no MAP-Elites/Zipline, does not cite the paper). Comparison table added to [00](00-RESEARCH-SYNTHESIS.md) §4; banner in `93`
- [x] **AlphaAgent IC/IR** — not an arithmetic contradiction but a question about *effective breadth* (`IC 0.0056 × √125000 ≈ 1.98`, so IR 1.05 sits *below* what the Fundamental Law permits)
- [x] **PDT rule** — abolished as of **4 Jun 2026**; PIT data is now **$29–49/month**. Banner in `92`
- [x] **"Sharpe 0.3–0.8"** — correct at portfolio level; per single market it is **~0.4** (Hurst-Ooi-Pedersen). Banner in `92`

## Open items

- [ ] **Answer the 🔴 Open decisions in the [decision register](Architecture_Design.md) §10** — the single source for decision status (it absorbs the 4 questions in §9 of [00](00-RESEARCH-SYNTHESIS.md)). Only **D4** remains — the economic threshold for funding (blocks the first holdout opening + live). Decided 21 Sep 2026: markets (D1), output type (D2), **no MQL5 EA — live through NautilusTrader** (D3). All other defaults are user-configurable via `config/user.yaml` (§10.1)
- [ ] Verify the signatures of `deflated_sharpe_ratio` and `probability_of_backtest_overfitting` in `purgedcv` (see [07](07-VALIDATION-LAYER.md) §7). ⚠️ This library is the core of validation but its API is **unverified** — first task of phase 1, with a self-implementation fallback ([Architecture_Design.md](Architecture_Design.md) §6)
- [ ] Read the [`RndmVariableQ/AlphaAgent`](https://github.com/RndmVariableQ/AlphaAgent) code — its README appears to have drifted from the paper (Tushare/AgentScope/CSI 1000 vs Qlib/CSI 500 + S&P 500)
- [x] **Apply the 10 changes from [08 §8](08-LLM-QUANT-RESEARCHER.md) to [Architecture_Design.md](Architecture_Design.md)** — done 21 Sep 2026, version 0.2. Most important: DSR computed on the **consolidated portfolio** (not per cell), gate ⓪ drift detection, heterogeneous model-routing table replacing "use small models", P6 raised to OS level
- [x] **8-finding design review → [Architecture_Design.md](Architecture_Design.md) version 0.3** — done 21 Sep 2026. Holdout locked per research campaign + frozen portfolio; audit log separated from statistical trials (`N_eff`, `V[SR]`); portfolio-construction spec; **PBO correction** (no edge ⇒ PBO ≈ 0.5, not → 1; also fixed in [07](07-VALIDATION-LAYER.md)); **fixed the sizing bug that divided by volatility twice**; drift gate normalized; reasoning-model/MadEvolve conclusions downgraded to hypotheses; §10 is now the single decision register
- [x] **Read the MadEvolve repo → [Architecture_Design.md](Architecture_Design.md) version 0.4** — done 21 Sep 2026. The island model in the repo is dead code (the convergence evidence exists only in the paper). Brought into the spec: fixed-region template + hash-enforced `EVOLVE-BLOCK`, `TUNABLE` declarations, public/private metrics, sandbox, strict patching, asynchronous pipeline (§3.1.10, §3.3.1–3.3.3)
- [x] **Read the full MadEvolve paper → [Architecture_Design.md](Architecture_Design.md) version 0.5** — done 21 Sep 2026. **Multi-engine architecture** (QuantEvolve + simple loop + random search, isolated/collaborative modes, §3.1.11); parameter optimizer switched off inside the evolution loop; module-wise evolution; minimum trade count; indicator correlation; IS→OOS degradation curve on CPCV; pessimistic fill model; multiple seeds. Critical assessment of the paper recorded in [08](08-LLM-QUANT-RESEARCHER.md) §5
- [ ] Cross-check repo ↔ paper for [`QuantaAlpha`](https://github.com/QuantaAlpha/QuantaAlpha), [`Alpha-R1`](https://github.com/FinStep-AI/Alpha-R1), [`QuantEvolver`](https://github.com/QuantLLM/QuantEvolver) — lesson learned from the `tarsyang/quantevolve` incident
