# 04 — Module map

Where code goes and what it may import. The tree is arch §5 moved under a src layout ([ADR-0001](adr/0001-src-layout-and-holdout-split.md)). Most modules below don't exist yet — the last column is the task that creates them.

## Tree

```
TradingProject/
├── CLAUDE.md                      agent instructions
├── config/
│   ├── user.yaml                  the only file the user edits (§10.1)
│   └── evaluation.lock.yaml       generated per campaign — gitignored, read-only, hashed
├── holdout/                       🔴 DATA ONLY — gitignored, read-only, never read except by the evaluator process
├── holdout.lock                   SHA256 of the holdout files (§4.2 layer 2)
├── data/                          parquet cache of free data — gitignored
├── docker/                        sandbox image (planned, P0-08)
├── scripts/                       dev tooling (check_doc_mirror.py)
├── src/quantcrucible/
│   ├── cli.py                     data-fetch(-second), holdout-reharden, validate,     §7      P0-09,
│   │                              portfolio, freeze (never the holdout)                        P1-12
│   ├── config/                    loader, schema, campaign lock                        §10.1   P0-03
│   ├── core/                      ── bottom layer ──
│   │   ├── strategy/              base (Signal, Strategy), registry (ind), template,    §3.3    P0-04/05
│   │   │                          tunable
│   │   ├── sizing/                vol_target, position_sizer                           §3.4    P1-06
│   │   └── zoo/                   hand-written (ema, sma, rsi v1+v2); AST            §3.1.6  P1-12
│   │                              similarity set                                               phase 2
│   ├── data/                      DataSource protocol, ccxt/Stooq sources, store,      §6.1    P0-09
│   │                              holdout_split
│   ├── ledger/                    schema.sql, db.py                                    §4      P0-02
│   ├── validation/
│   │   ├── gates.py, report.py    Gate pipeline, EvaluationReport                      §3.2    P0-06
│   │   ├── guardrail.py           gate ①a static + ①b dynamic (leak_check.py)          §3.2    P0-07/11
│   │   ├── sandbox.py             Docker runner (host) + sandbox_runner.py (in-container) §3.3.3 P0-08
│   │   ├── statistical.py         MinBTL, PSR, DSR, portfolio_dsr                      §3.2    P0-12, P1-02
│   │   ├── pbo.py, pbo_gate.py    CSCV/PBO (vectorized) + gate ④ configuration set      §3.2    P1-03
│   │   ├── cpcv.py                CPCV (purge/embargo) + IS→OOS degradation check       §3.2    P1-04
│   │   ├── is_gates.py, run.py    gates ② ③; the phase-0 pipeline + `cli validate`     §3.2    P0-12
│   │   ├── n_eff.py               ONC clustering → N_eff                               §4.1    P1-05
│   │   ├── portfolio.py           §3.2.1 construction + portfolio_hash                 §3.2.1  P1-07
│   │   ├── archive.py             source archive by strategy_hash (re-runs at ⑥′, ⑥)    §3.2.1  P1-07
│   │   ├── portfolio_dsr.py       portfolio pipeline ⑤ → ⑥′ + gate ⑤                   §3.2    P1-08
│   │   ├── robustness.py          gate ⑥′: costs × 2 + second source; member re-runs    §3.2    P1-09
│   │   ├── freeze.py              OPEN → FROZEN on a validated portfolio (research side) §4.2    P1-10
│   │   ├── calibration.py         step 5b: Optuna, every evaluation a param_opt trial   §3.2.1  P1-11
│   │   ├── research_run.py        pre-freeze workflow: submit → build → ⑤⑥′ → 5b        §3.2.1  P1-12
│   │   ├── artifacts.py           write-once measurement files (returns, PBO matrix)    §4.1    P1-12
│   │   ├── temporal.py            decay monitoring                                     §5      later
│   │   └── oracles/               leaky-oracle suite, levels 1–4                       07 §9   P0-11
│   ├── agent/                     ── the ONLY place an LLM is called (A4) ──           §3.1    phase 2
│   │   ├── loop.py, data_agent.py, research_agent.py, coding_team.py, evaluation_team.py
│   │   ├── evolution/             feature_map, islands, sampling, archive, ranking
│   │   ├── engines/               quantevolve, simple_loop, random_search              §3.1.11
│   │   ├── dsl.py, drift.py, patcher.py, pipeline.py, routing.py, runlock.py, scheduler.py, insights.py
│   │   └── prompts/
│   ├── holdout/                   evaluator_proc.py (own process), campaign.py         §4.2    P1-10
│   ├── review/                    read-only read model + FastAPI app (`cli review`) ADR-0029  tooling
│   └── execution/                 engine.py (NautilusTrader), nautilus_bridge.py, risk.py §3.5 P0-10, P1-06
│       └── adapters/ssi/          VN30F1M adapter                                      §3.5    phase 6
├── ui/                            React + Vite front end: src/, e2e/ (Playwright)    ADR-0029  tooling
└── tests/                         mirrors src/quantcrucible/; e2e/ for phase gates
```

## Dependency rules

| Package | May import (internal) |
|---|---|
| `core` | — (bottom layer) |
| `ledger` | — |
| `data` | `core` |
| `config` | `ledger` |
| `execution` | `core`, `data` |
| `validation` | `core`, `data`, `ledger`, `config`, `execution` |
| `agent` | everything except `holdout` |
| `holdout` | `core`, `data`, `ledger`, `config`, `execution`, `validation` — and **nobody imports it** |
| `review` | — (reads the ledger file directly in read-only mode; imports no research package) |

The forbidden directions are enforced by import-linter contracts in `pyproject.toml`:

| Contract | Why |
|---|---|
| `core` imports nothing from `agent`, `validation`, `execution`, `ledger`, `holdout` | Strategies and sizing stay venue- and research-agnostic (P3, §3.3 "hard architectural boundary") |
| `validation` never imports `agent` | The harness judges the agent; it must not depend on it (P1) |
| `execution` never imports `agent`, `validation`, `holdout` | The live path must be the backtest path, with no research machinery (P5) |
| Nothing imports `holdout` | The evaluator is reached only by launching its process (§4.2 layer 3) |
| `data` imports nothing from `agent`, `validation`, `execution` | Data sources are swappable (§6.1 `DataSource`) |
| `agent` imports no `execution`, sandbox or leak-check code | It reaches backtests only through `GatePipeline`, which writes the ledger (P2) |
| `validation.sandbox_runner` imports no `ledger`, `config`, `agent`, `holdout`, gate or host-sandbox code | It runs inside the container with generated code; it can only return a report (§3.3.3) |
| `ledger` imports no other `quantcrucible` package; `config` imports only `ledger` | The ledger is the single source of truth (P2) and must not depend on what it records |
| `review` imports no `agent`, `validation`, `execution`, `core`, `data`, `config` or evaluator package | The review surface reads and shows; it must never grow a way into research ([ADR-0029](adr/0029-read-only-review-ui.md)) |

Plus one AST test for third-party imports: LLM SDKs (`openai`, `anthropic`, `litellm`, `langchain`, `langgraph`, …) only under `agent/` (A4).

When a new module needs an import these rules forbid, the design is probably wrong — raise it before adding an exception.

## Runtime process boundaries

| Process | Sees | Never sees |
|---|---|---|
| Research (agent loop + validation ⓪–⑥′) | IS data, ledger, lock | `holdout/` (loader refuses the range; hook blocks the coding agent) |
| Sandbox container (generated code) | IS data (read-only), tmp dir | network, env vars, `holdout/`, `config/`, `ledger/` |
| Holdout evaluator | frozen `portfolio_hash`, holdout data, ledger | the agent loop's memory; returns 1 token |
| Live (gate ⑧) | `core` + `execution` + frozen strategies | LLMs, `agent/`, `validation/` |
| Review UI (`cli review`, localhost only) | the ledger read-only, artifacts under `results/` | any write path, the evaluator's data, files outside `results/` |
