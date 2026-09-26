# ADR-0026 — Engine C-gp: typed operators and the ranking score

- **Status:** Accepted · **Amended by** [ADR-0030](0030-gp-ranking-primary-term-is-a-population-rank.md) (the primary term is a population rank)
- **Date:** 2026-09-23
- **Task:** P2-11 · **Arch:** §3.1.11 (C-gp), §3.1.6 #1, §3.3.1, D20 · **Open item:** —

## Context

Arch v0.6 makes C-gp — typed GP over the grammar — the main engine, and §3.1.6 #1 ranks the search by `DSR(returns, N_eff) − λ₁·AST_sim − λ₂·n_params`, a ranking and not a gate. D20 adds the gate-④ grid's SPP median and plateau as a secondary term and caps parameter-only children. Two constraints shape the implementation: INV-42 allows no per-strategy DSR (only gate ⑤ computes DSR), and INV-68 lets the ranking read `public` metrics only.

## Decision

1. **Operators** (`agent/evolution/operators.py`) act on typed genomes: `param` (one TUNABLE moves within its bounds — same code, same `strategy_hash`), `point` (flip a comparison, direction or and/or; swap sma↔ema or rsi↔zscore, re-drawing an oscillator's level on its own scale), `subtree` (a clause re-sampled), `crossover` (a clause taken from a second parent, replaced or added up to 3 clauses; the stop from either parent). Every child keeps ≤ 6 TUNABLE; an operator that cannot comply raises `OperatorFailed`.
2. **Parameter-only cap:** `choose_kind` picks `param` with probability 0.3 but never when that would push the parameter-only share of the offspring above `gp.param_only_max` (INV-66).
3. **Ranking** (`agent/evolution/ranking.py`): `DSR-rank − 0.10·novelty − 0.05·n_params/6 + 0.05·tanh(SPP median) + 0.05·plateau`. The DSR-rank follows ADR-0013: the PSR of the candidate's IS returns against the campaign-wide deflated benchmark SR₀(`N_eff`, `V[SR]`) — a ranking, never compared with `dsr_min`. Novelty = the largest clause-signature Jaccard with any earlier entry (trial order), so the score is a function of the ledger alone.
4. **Inputs:** gate ③ now also reports the IS return moments (`sr_obs`, `skew_is`, `kurtosis_is`, `n_obs`) as `public`; submissions record the genome's clause `signature` with its categories. Nothing from gate ④'s `private` detail is read.

## Consequences

- The DSR term dominates (range [0, 1]); the other terms, each ≤ 0.1, break ties and push diversity. The λ are provisional; retune only with a recorded reason, never on holdout results.
- A crossover can reuse a parent's parameter object: the renderer declares it once.

## Alternatives considered

- **Call `deflated_sharpe_ratio` per candidate:** rejected — it would reintroduce a per-strategy DSR API that INV-42 forbids; the ADR-0013 convention gives the same ordering.
- **Raw IS Sharpe as fitness:** rejected — it rewards short, lucky records (§3.1.6 #1).
