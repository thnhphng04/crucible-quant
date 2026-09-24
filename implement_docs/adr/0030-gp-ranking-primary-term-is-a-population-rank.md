# ADR-0030 — The GP ranking's primary term is a population rank

- **Status:** Accepted
- **Date:** 2026-09-24
- **Task:** P2-16 · **Arch:** §3.1.6 #1, §3.1.11 · **Amends:** [ADR-0026](0026-gp-operators-and-ranking-score.md)

## Context

The first complete comparison run (campaign `c-20260923-090957`, protocol v2) produced an inversion. C-gp passed gate ④ far more often than C-random — 72.5% against 47.0% of the candidates that cleared gate ③, 145 archive-eligible entries against 65 — yet the portfolio built from its candidates scored a Sharpe of 0.466 against C-random's 1.096, and a DSR of 0.0012 against 0.0988.

The cause is in `ranking.py`. Its primary term, ADR-0026's DSR-rank, is the PSR of a candidate against `deflated_benchmark(N_eff, V[SR])`. That benchmark climbs with the trial count: at `N_eff = 145` and `V[SR] = 0.3355` it reached **1.540 annualised Sharpe**, above the population's 90th percentile of 1.22 and well above its median of 0.71. PSR therefore collapsed — median **0.0187**, against a plateau term of 0.0486, an SPP term of 0.0321 and a novelty penalty reaching 0.10. The term carrying an implicit λ of 1.0 was outweighed more than four to one by terms carrying λ = 0.05.

C-gp spent twenty generations selecting for flat parameters, novel structure and few TUNABLEs. The measurement confirms it: its selected population has a **lower** mean `sharpe_is` (0.595) than C-random's unselected draws (0.664). Worse, `plateau` and `SPP median` measure roughly what gate ④'s PBO measures, so the search was being trained on its own examiner.

Two hypotheses were tested against the ledger and rejected. Correlation between portfolio members does not explain the gap: C-gp's candidate pool is *less* correlated than C-random's (mean |ρ| 0.312 against 0.347) and its members only marginally more (0.129 against 0.097), both far inside the `max_corr` of 0.5. Crowding is real — 145 eligible entries dedupe to 108 representatives against C-random's 65 to 63 — but §3.2.1 removes it before the portfolio. What remains is quality: C-gp's members are simply worse.

## Decision

1. **The primary term is the population rank of the DSR-rank**, normalised to [0, 1], with ties sharing their average rank and a single entry scoring the midpoint 0.5. PSR is a monotone transform of the Sharpe moments, so ranking keeps every ordering it carries while discarding the compression.
2. **No λ moves.** The primary term now spans 1.0 against the auxiliary terms' combined 0.245, which is the emphasis ADR-0026 intended. Retuning is a separate decision with its own evidence.
3. **The rank is taken over the entries handed to `scores()`** — one (engine, seed) — matching the scope of the novelty term, which already compares across islands. Ranking per island would give a freshly re-seeded island of two entries a primary term of exactly {0.0, 1.0}.
4. **Ties share the average rank, compared by exact equality.** `dsr_rank` returns a literal 0.0 for every entry without IS moments; ordinal ranking would spread that block across the bottom of the interval in trial order and reward whichever ran first.
5. **`term_dispersion` and INV-79** expose the failure mode: the primary term's spread must exceed the combined spread of the auxiliary terms. It is a diagnostic, reported per (engine, seed) as `ranking_margin`; it reaches neither `decide` nor `EarlyStop`, because a comparison may not let a measured quantity set its stopping time ([ADR-0028](0028-comparison-protocol-v3-monitor-warns.md)).
6. **The spread is an interquartile range, not a standard deviation.** This was measured, not assumed. The pathology is a bulk squashed against zero while a few top entries keep a large PSR, and an sd is dominated by exactly that tail: on the real campaign the old form scores sd-margins of 1.36 and 1.08 and so **passes** a check it must fail. The same populations give IQR-margins of 0.59 and 0.44 against the rank form's 4.56 and 3.21.
7. **The protocol carries the ranking (INV-80), protocol v4.** The λ are hashed as data, read at lock time like the divergence rule; `ranking_fingerprint()` hashes the selection order and the rounded scores over `RANKING_SCENARIOS` as a secondary check. The ranking is what distinguishes C-gp from C-random, so a silent change to it is a larger hole than the monitor one ADR-0028 closed.

## Consequences

- Campaign `c-20260923-090957` keeps its standing from ADR-0028: **no engine decision**, and its 552 trials stay in `N`. Its trial-efficiency and coverage figures now also carry a known cause, so they should not be read as evidence about evolution as such.
- The comparison restarts under protocol v4 (`0cafe016ef5f…`, ranking fingerprint `77597182279a…`). This is the third restart; like the first two it was bought by a defect found before any result was read.
- Every future λ retune forces a new campaign. That is correct, and it mechanises ADR-0026's rule that the λ change only with a recorded reason.
- The rank form removes the benchmark's influence on **scale**, not on **ordering**. PSR still divides by √(T−1) and by the moments, so ADR-0026's penalty on short, skewed and fat-tailed records survives intact and `RankContext` still changes who outranks whom. No test may assert the score is invariant to `N_eff`.
- Scores are no longer comparable across populations: 0.8 among 20 entries is not 0.8 among 145. Every current consumer compares within one call; a future per-seed "best score" diagnostic would be wrong.
- `monitor.py` now imports `ranking`. The flow is one-way and `load_entries` strips `private` before either sees an entry, so INV-68 holds; `test_ranking_ignores_private` moved with the other ranking tests into `tests/agent/evolution/test_ranking.py` and keeps guarding it.
- A calibration bench (`tests/agent/test_ranking_recovery.py`) runs the whole engine against a simulated market whose Sharpes reproduce the compression, with the auxiliary metrics drawn from an independent hash. It records a finding worth keeping: an earlier version planted a **clean needle** — one clause paying far above everything else — and measured *no difference between the two forms*. Compression is monotone, so an extreme top survives it and both forms rank a needle first. The defect bites in the **middle** of a population of mediocre, neighbouring candidates. With a weak overlapping edge (a mean shift of 0.7 Sharpe across spans of 1.5) the rank form breeds from a better head on every seed of three. Anyone retuning the λ should start there, in a ledger of its own.
- Arch §3.1.6 #1 still reads `Score = DSR(returns, N_eff) − λ₁·AST_sim − λ₂·n_params`. ADR-0026 already replaced the literal `DSR(...)`, which INV-42 forbids per strategy; this refines the same substitution and changes no term, λ, gate or threshold. If the architecture is read as requiring an absolute probability rather than an ordering, the change is architectural and the user's call.

## Alternatives considered

- **Raise the auxiliary λ, or lower them.** Rejected: the λ that would restore the balance depend on where the benchmark lands, which is a property of the campaign's own `N_eff` and `V[SR]`. Any value chosen today is stale by the next campaign, and ADR-0026 requires a recorded reason that is not derived from the data.
- **Set `SR₀ = 0`.** Rejected: the compression returns at the low end whenever a population sits near zero Sharpe, and it discards ADR-0013's deflated-benchmark vocabulary that portfolio construction shares.
- **Z-score the PSR instead of ranking it.** Rejected: unbounded, so a single outlier re-compresses everyone else — the same failure in a new costume.
- **Drop `plateau` and `SPP` from the score (D20).** Not taken here. They do overlap with what gate ④ measures, and the Goodhart loop survives the rescaling; but D20 is in the decision register and reopening it is the user's call, not an implementation decision.
