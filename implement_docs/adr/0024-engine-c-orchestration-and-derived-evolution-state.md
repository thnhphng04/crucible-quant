# ADR-0024 — Engine C orchestration: plain Python, evolution state derived from the ledger

- **Status:** Accepted
- **Date:** 2026-09-22
- **Task:** P2-01 · **Arch:** §3.1.11 (v0.6), §3.1.8, §4.1, §6 (tech stack) · **Open item:** —

## Context

Architecture v0.6 (D19) makes the no-LLM engine C the focus of phase 2: C-gp (typed GP) and C-random (i.i.d. samples), with engines A/B deferred. §6 names LangGraph for agent orchestration, chosen for the LLM agents' state and cycles. Engine C has no LLM and runs one fixed algorithm (sample or breed → submit → archive), so the orchestration question is open again. The loop must also survive interruption without losing or duplicating trials.

## Decision

1. **Plain Python orchestration.** Producers, a prefetch queue and K evaluation slots use the standard library (`threading`, `queue`, `concurrent.futures`). No LangGraph in phase 2. Revisit when engines A/B are reopened.
2. **The ledger is the only state.** Feature map, per-engine archive, islands and parent pools are rebuilt from `trials`, `gate_results`, `generation_log` and the strategy archive (`results/strategies/<hash>.py`). No evolution database of its own; a restarted run continues from the ledger.
3. **Engines produce source only.** Every candidate goes through `research_run.submit` → `candidate_pipeline().run`; engines never import the backtester or the sandbox (import-linter already forbids it).
4. **Scheduling counts statistical trials.** Quotas per (engine, seed) are checked against `trials` rows, never against calls or wall-clock time.
5. **No gate ⓪ drift in phase 2.** Drift guards LLM refinement passes against a hypothesis; engine C has neither. Its gate IDs stay reserved.

## Consequences

- Deterministic, testable loop; nothing to reconcile after a crash.
- Rebuilding state costs a ledger read per generation — fine for thousands of trials; revisit with O9 if it shows up.
- Arch §6 lists LangGraph: when A/B return, either this ADR is superseded or §6 is edited (VI first) to say LangGraph applies to the LLM engines only.

## Alternatives considered

- **LangGraph now:** rejected — a dependency for state machines engine C does not have.
- **A separate evolution database:** rejected — a second source of truth next to the ledger (P2) that can drift from it.
