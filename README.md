# Crucible Quant

*An AI quantitative research lab that evolves systematic trading strategies and puts every one through the fire before it trades.*

> **Status:** phase-3 implementation. Fixed SL/TP and maximum holding exits are merged (P3-25); the local Studio UI for configuring, creating and running campaigns is implemented (P3-26); real-source perpetual data is fetched and its holdout carved (P3-24, ADR-0042). No strategy has been validated for live trading. Build plan: [implement_docs/](implement_docs/README.md) · agent instructions: [CLAUDE.md](CLAUDE.md).

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

## Creating and running a campaign in Studio

Studio is the local control UI for the same project root ([ADR-0037](implement_docs/adr/0037-local-studio-control-ui.md)). Build the UI once as above, then start it from the project root:

```bash
uv run python -m quantcrucible.cli studio             # http://127.0.0.1:8765
```

Open **Campaign Studio**. Choose purpose, market, exchanges, symbols, timeframe and UTC date range; set the trial budget and bracket rules. Save a draft, choose **Chuẩn bị dữ liệu** to fetch and pin an immutable dataset, then **Xem trước** to check the lock, dataset and quota. Choose **Tạo campaign** only after the preview is valid. **Chạy campaign** starts or resumes search; **Đánh giá danh mục** is available for research campaigns after search. The job panel shows progress and supports stopping a job. Review screens remain available from Studio; `cli review` remains read-only. Studio binds to `127.0.0.1` and serializes project writes with one writer lock. Fetching real data requires access to the selected exchange; perpetual data needs a read-only Binance key in `.env` (run commands with `uv run --env-file .env`) for the leverage brackets, and `data-preflight` checks coverage before any download.

## Documentation

| | English | Tiếng Việt (source of truth) |
|---|---|---|
| Start here | [research_docs/README.md](research_docs/README.md) | [research_docs_vi/README.md](research_docs_vi/README.md) |
| Architecture | [research_docs/Architecture_Design.md](research_docs/Architecture_Design.md) | [research_docs_vi/Architecture_Design.md](research_docs_vi/Architecture_Design.md) |
| Implementation | [implement_docs/README.md](implement_docs/README.md) (original) | [implement_docs_vi/README.md](implement_docs_vi/README.md) |

The Vietnamese documents are the original; the English ones are a 1:1 translation. The four raw source reports (`90`–`93`) exist in Vietnamese only.

## Disclaimer

Research project. Nothing here is investment advice, and no strategy described here has been validated in live trading.
