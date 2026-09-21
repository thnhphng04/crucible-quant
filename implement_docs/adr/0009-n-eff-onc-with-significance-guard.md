# ADR-0009 — `N_eff`: ONC with a significance guard

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-05 · **Arch:** §4.1 ("Estimating `N_eff`"), §3.1.6 · **Open item:** O10 (closed)

## Context

§4.1 asks for `N_eff` from ONC clustering of the trials' return series (distance `√(½(1−ρ))`, cluster count by silhouette) and warns that `N_eff` may only lower the bar on principled grounds. The first implementation of plain ONC failed that warning in tests: k-means assigns **every** trial to some cluster, so 12 independent noise series came out as 3–4 clusters, and a new unrelated trial was absorbed into an existing cluster — both would lower the DSR benchmark without evidence.

## Decision

1. **Own implementation** in `validation/n_eff.py` (no library ships ONC): `clusterKMeansBase` (k = 2…min(n−1, 50), `n_init = 3` — bounded for speed in P1-08; the guard can only split further, quality = mean/std of silhouettes) + `clusterKMeansTop` (re-cluster the clusters whose silhouette t-stat is below the mean; keep the split only if it improves them), after López de Prado & Lewis (2019). scikit-learn (`KMeans`, `silhouette_samples`, BSD-3, already pulled in by `purgedcv`) does the k-means. Seeded ⇒ deterministic.
2. **Significance guard** after ONC: a trial stays in its cluster only while its mean correlation with the other members exceeds `3/√T` (T = mean common observations; independent series have ρ ~ N(0, 1/T)). Weakest member first, until stable; a removed trial is its own cluster.
3. **Conservative edges:** fewer than 3 trials ⇒ each its own cluster (ONC needs k ≥ 2 and a silhouette); identical series ⇒ one cluster. Correlations use common timestamps; fewer than 30 common observations, or a flat series, ⇒ ρ = 0.
4. `update_n_eff(ledger)` clusters **every** trial (all campaigns) and appends one `clustering_runs` row (`method = 'onc-v1'`). It is re-run on every portfolio evaluation (P1-07/P1-08). New `Ledger.trials()` read feeds it.

## Consequences

- Tests: k ∈ {2, 4, 7} planted sources recovered exactly; 12 noise series ⇒ 12 clusters; an unrelated new trial stays separate after re-clustering.
- Cost: ≤ n_init · 50 k-means fits per ONC level — ~8 s for 200 trials. For thousands (phase 2), cluster incrementally; revisit then.
- The guard is an addition to the published ONC; it can only raise `N_eff`, never lower it.

## Alternatives considered

- **Plain ONC:** rejected — lowers `N_eff` on noise (see Context).
- **`purgedcv.effective_n_trials`:** rejected in ADR-0007 — depends on trial order, not return correlation.
- **Hierarchical clustering with a fixed ρ cut:** rejected — the cut is a free parameter §4.1 does not give; the silhouette choice is the specified one.
