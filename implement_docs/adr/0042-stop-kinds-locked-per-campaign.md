# ADR-0042 — Stop kinds locked per campaign; Bollinger off by default

- **Status:** Accepted
- **Date:** 2026-10-04
- **Task:** P3-55 · **Arch:** §3.3.1, §10 D21, D24, D25 · **Open item:** —

## Context

Under `bracket_timeout_v1` engine C sampled a Bollinger stop half the time. P3-54 measured that this stop is `<= 0` — so the `ready` guard refuses the signal — exactly when mean-reversion clauses fire: long, band 20, `zscore(20) < −2` ⇒ 100% of those bars (the two conditions are the same inequality); band 14 ⇒ 67%; `rsi(14) < 30` ⇒ 35–49%. The user decided (D25) to turn the Bollinger stop off for new campaigns, through a config key so it can come back without a code change.

## Decision

- Group B key `research.exit.stop_kinds: [atr | bollinger, …]`, default `[atr]`; empty, unknown or repeated kinds are refused at load time.
- `lock_stop_kinds(lock)` (`core/strategy/tunable.py`): the locked key, or for a lock written before it, what its exit ran with — `[atr, bollinger]` under `bracket_timeout_v1`, `[atr]` before it.
- `grammar_config` sets `boll_stop_probability` to 0.5 only when the lock allows `bollinger` (and the exit is bracket). With 0, `point` never flips the stop kind and crossover copies only stops of the same population.
- Gate ①a refuses any `ind.boll_upper/boll_lower` call where the lock leaves `bollinger` out: the grammar uses bands only as a stop.
- An open campaign whose lock lacks the key matches `user.yaml` at the new default `[atr]` or at its actual kinds, nothing else; its stops are still the lock's (`config/lock.py::_comparable`).

## Consequences

- New campaigns search ATR stops only (INV-116); the `n_stop` gene (ADR-0041) still tunes the ATR period.
- The eight campaigns open on 2026-10-04 resume unchanged; their genomes keep both stop kinds.
- `config` may not import `core`, so `_comparable` repeats the legacy rule of `lock_stop_kinds`; a test pins both.
- Arch edits (done with this ADR): §3.3.1 (`exit` names the stop kinds), D25, changes 0.11 → 0.12.

## Alternatives considered

- Hard-wire ATR-only into new locks — re-enabling would need code and an ADR; the user preferred a key.
- Keep Bollinger but skip `stop <= 0` bars differently — changes the exit semantics of ADR-0036, not asked for.
