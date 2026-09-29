# ADR-0039 — Campaign-locked backtest precision (float64 | float32)

- **Status:** Accepted
- **Date:** 2026-09-30
- **Task:** P3-35 (config), P3-45 (fp32), P3-47 (holdout) · **Arch:** §3.5, §10 D23, §10.1

## Context

The kernel engine (ADR-0038) can run features and signals in float32. On this GPU, fp32 is 32× the fp64 rate, but a float32 indicator can flip an entry at a threshold, so a gate verdict can differ from float64. The user wants both precisions with equal standing. Adding any `research.*` key also makes every existing lock fail `assert_lock_matches`, which requires exact equality.

## Decision

1. **Group B key** `research.backtest.precision: float64 | float32`, default float64, copied into the lock. It is paired with `derived.backtest_numerics` (`fp64_oracle_v1` | `fp32_signals_v1`); an unknown tag, or one that contradicts the key, is refused.
2. **Old locks.** `assert_lock_matches` reads a lock without `backtest` as `{precision: float64}`. An old lock with a float32 config is refused. New locks always write the key.
3. **Scope of fp32.** Only features and signals run in float32; cash, quantity and equity are always float64. fp32 campaigns have equal standing, including freeze and the holdout.
4. **fp32 fails closed.**
   - An fp32 campaign accepts only grammar genomes. Any other strategy is refused and records no trial (ADR-0022).
   - It never falls back to the float64 sandbox.
   - Its audit compares CUDA with the float32 njit build.
5. **Holdout.** The evaluator runs the engine at the lock's precision. It self-tests before `claim()`, and a refusal leaves the holdout unclaimed. A GPU fault mid-run continues on the CPU kernel at the same precision.
6. **E6.** The fp32/fp64 divergence (signal and trade mismatches, ΔSharpe, gate-verdict flips, speed-up) is measured on synthetic data only (P2) and appended here in P3-45.

## Consequences

- A campaign's results are reproducible from its lock alone. Precision cannot be switched mid-campaign to rescue a candidate.
- Existing OPEN campaigns keep running as float64 without a new lock.
- fp32 campaigns cannot take manual or LLM strategies. They need a numba-capable host, at least for the CPU kernel.
- Tests: `tests/config/test_lock_precision.py` (INV-108) and router/holdout tests (INV-110). Studio gains a precision field (P3-35).

## Alternatives considered

- **float64 only:** simplest, but the user wants the fp32 speed.
- **fp32 search with fp64 re-verification of passing candidates:** safer, but the user chose equal standing.
- **fp32 barred from the holdout:** rejected by the user for the same reason.
- **Running non-genome strategies in the fp64 sandbox inside an fp32 campaign:** that mixes precisions within one campaign.
