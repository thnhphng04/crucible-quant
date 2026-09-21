# Crucible Quant

*An AI quantitative research lab that evolves systematic trading strategies and puts every one through the fire before it trades.*

> **Status:** starting implementation (phase 0 — harness first). The design is done; the code under `src/` is a scaffold. Build plan: [implement_docs/](implement_docs/README.md) · agent instructions: [CLAUDE.md](CLAUDE.md).

## What it is

An LLM-driven research loop that generates **deterministic, rule-based trading strategies** and validates them before any capital is at risk:

- **Search** — a QuantEvolve-style quality-diversity evolution (MAP-Elites + island model), running side by side with a simpler MadEvolve-style loop and a random-search control.
- **Validation** — the part the field mostly skips: structural guardrails against look-ahead, a trial ledger that counts every test, CPCV + PBO, Deflated Sharpe Ratio on the consolidated portfolio, and a write-once holdout enforced at the database and OS level.
- **Execution** — multi-market (crypto, forex, international and Vietnamese equities/futures) on NautilusTrader, with backtest ≡ live.
- **No LLM at runtime** — the LLM only writes strategy code; the deployed strategy is plain deterministic code.

## Documentation

| | English | Tiếng Việt (source of truth) |
|---|---|---|
| Start here | [research_docs/README.md](research_docs/README.md) | [research_docs_vi/README.md](research_docs_vi/README.md) |
| Architecture | [research_docs/Architecture_Design.md](research_docs/Architecture_Design.md) | [research_docs_vi/Architecture_Design.md](research_docs_vi/Architecture_Design.md) |
| Implementation | [implement_docs/README.md](implement_docs/README.md) | — (English only) |

The Vietnamese documents are the original; the English ones are a 1:1 translation. The four raw source reports (`90`–`93`) exist in Vietnamese only.

## Disclaimer

Research project. Nothing here is investment advice, and no strategy described here has been validated in live trading.
