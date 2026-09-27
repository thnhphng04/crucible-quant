# Crucible Quant

*An AI quantitative research lab that evolves systematic trading strategies and puts every one through the fire before it trades.*

> **Status:** phase-3 implementation. Fixed SL/TP and maximum holding exits are merged (P3-25); real-source perpetual coverage remains open (P3-24). No strategy has been validated for live trading. Build plan: [implement_docs/](implement_docs/README.md) · agent instructions: [CLAUDE.md](CLAUDE.md).

## What it is

A research loop that generates **deterministic, rule-based trading strategies** and validates them before any capital is at risk. The current search engine is non-LLM C-gp with C-random as its control; LLM engines are deferred:

- **Search** — typed-grammar GP with MAP-Elites and islands, paired with a random-search control.
- **Validation** — the part the field mostly skips: structural guardrails against look-ahead, a trial ledger that counts every test, CPCV + PBO, Deflated Sharpe Ratio on the consolidated portfolio, and a write-once holdout enforced at the database and OS level.
- **Execution** — crypto spot and perpetual research currently uses NautilusTrader for legacy fills and Python replay for fixed brackets; backtest/live equivalence remains a goal to prove before deployment (ADR-0032, ADR-0036).
- **No LLM at runtime** — the LLM only writes strategy code; the deployed strategy is plain deterministic code.

## Reviewing a campaign

A local, read-only web UI over the ledger — it explains what a campaign established and never writes anything ([ADR-0029](implement_docs/adr/0029-read-only-review-ui.md)):

```bash
cd ui && npm install && npm run build && cd ..        # once, after a checkout
uv run python -m quantcrucible.cli review             # http://127.0.0.1:8765
```

While working on the UI itself, `npm run dev` proxies `/api` to a running review server. `npm test` runs the component tests and `npm run test:e2e` the Playwright smoke run, which serves a throwaway demo ledger instead of your own.

## Documentation

| | English | Tiếng Việt (source of truth) |
|---|---|---|
| Start here | [research_docs/README.md](research_docs/README.md) | [research_docs_vi/README.md](research_docs_vi/README.md) |
| Architecture | [research_docs/Architecture_Design.md](research_docs/Architecture_Design.md) | [research_docs_vi/Architecture_Design.md](research_docs_vi/Architecture_Design.md) |
| Implementation | [implement_docs/README.md](implement_docs/README.md) (original) | [implement_docs_vi/README.md](implement_docs_vi/README.md) |

The Vietnamese documents are the original; the English ones are a 1:1 translation. The four raw source reports (`90`–`93`) exist in Vietnamese only.

## Disclaimer

Research project. Nothing here is investment advice, and no strategy described here has been validated in live trading.
