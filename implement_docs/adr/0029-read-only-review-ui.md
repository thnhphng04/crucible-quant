# ADR-0029 — A read-only review UI that reads the ledger directly

- **Status:** Accepted
- **Date:** 2026-09-24
- **Task:** — (tooling, no roadmap task) · **Arch:** §4.1 (ledger), §4.2 (campaign lifecycle), §3.1.11 (comparison)

## Context

Reading what a campaign established meant querying `ledger/crucible.db` by hand or re-running `qc compare --report`. The first complete protocol-v2 run has 552 trials, 941 audit events and five gate results per candidate; the parts that matter for review — which gate blocked a candidate, which arm carries a warning, whether the lock on disk still matches the one the campaign opened with — are exactly the parts that are tedious to reconstruct from SQL each time.

A UI over research data is a leakage surface, so two things had to be settled before building one. First, it must not be able to write: P2 makes the ledger API the only way to run a backtest, and any "helpful" mutation from a browser would put a second writer next to it. Second, it must not become a second way to reach `holdout/`.

The `Ledger` class is a writer: it opens the database read-write, holds a run lock and migrates the schema on open. Pointing the UI at it would mean a web process that can write the ledger and that fights the running research for the lock.

## Decision

1. **A separate `quantcrucible.review` package**, served by FastAPI on `127.0.0.1` via `cli review`. It is presentation only.
2. **It opens its own SQLite connection in read-only mode** — `file:…?mode=ro`, `PRAGMA query_only=ON`, every request inside a transaction that ends in `ROLLBACK` — instead of going through `Ledger`. It never migrates: a schema other than the one it was written for (`SUPPORTED_SCHEMA`) is an error shown to the reviewer, not something to fix by opening the file read-write.
3. **Every route is a GET** and the app exposes no write path. An import-linter contract forbids `quantcrucible.review` from importing `agent`, `validation`, `execution`, `core`, `data`, `config` and `holdout`, so the surface cannot grow into the research layers.
4. **Artifacts are served only from inside `results/`**, resolved and checked against the allowed parent before reading, so a `returns_path` or a strategy hash from the database cannot address a file elsewhere on disk.
5. **The UI states research state, never a result.** An incomplete budget reads "Chưa kết luận"; a divergence warning is shown as an audit fact that decides nothing; missing data reads "Chưa có" and is never rendered as zero.

## Consequences

- Review no longer needs SQL, and the read path can never take the ledger's lock or block a running campaign.
- The read model duplicates schema knowledge (table and column names in SQL strings). A ledger migration must bump `SUPPORTED_SCHEMA` and update `repository.py`; until then the UI refuses to open the file rather than showing wrong data.
- `cli review` serves `ui/dist`, so the built front end has to exist. `scripts/review_demo_server.py` builds a throwaway demo ledger for the Playwright run, so end-to-end tests never read the real `ledger/`, `config/` or `holdout/`.
- Front-end conventions now live in the repository too: TypeScript strict, pinned npm versions, Vitest for components and Playwright for the desktop smoke run.

## Alternatives considered

- **Reuse `Ledger` inside the web app** — it opens the database read-write and takes a lock; a reader must not be able to do either.
- **A static HTML report generated per campaign** — no filtering or drill-down, and it goes stale the moment the next trial is written.
- **Expose the UI beyond localhost** — would need authentication and an audit of what it may show; nothing in phase 0–2 needs it.
