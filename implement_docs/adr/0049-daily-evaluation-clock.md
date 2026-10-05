# ADR-0049 — A daily evaluation clock

- **Status:** Accepted
- **Date:** 2026-10-05
- **Task:** P3-60 · **Arch:** §3.1.6, §3.2 rows ⑤ ⑥′, §4.1, §4.2, §10 D4, D26 · **Open item:** —

## Context

Every statistic read the backtest's per-bar returns and annualised with `√periods_per_year`: on 1h bars that is √8760 and `T ≈ 50 800` observations for 5.8 years. Hourly returns are not independent — a position held for hours correlates neighbouring bars — so the annualised Sharpe is biased (Lo 2002), and PSR/DSR, which treat every bar as an independent observation, are too sure of themselves. A 1h campaign would also be judged on a different scale from the 1d campaigns whose results set D4 (`holdout_pass: 1.3`) and D17 (MinBTL 1.5). Campaigns move to 1h (2026-10-04).

## Decision

- New locks record `derived.evaluation_clock: daily_v1` (`validation/clock.py`). A lock without the key keeps the per-bar clock; an unknown tag is refused.
- Under `daily_v1` per-bar returns are **compounded** per UTC day, `Π(1 + r) − 1` — the end-of-day equity ratio, exact where a sum is not. A bar belongs to the day its interval lies in: `Bars.ts` is the close, so the label is `ts.ceil("D")` and an hourly day lines up with a daily bar. A day with one bar passes its return through untouched, so a 1d series is the same bit for bit.
- On that clock, annualised with 365: gate ③'s `sharpe_is`, `sortino_is` and the ranking moments (`sr_obs`, `skew_is`, `kurtosis_is`, `n_obs`) — hence `trials.sharpe_is`, `V[SR]`, the feature map, the archive, calibration; the portfolio's selection score and `sharpe_is`; gate ⑤ (DSR with `V[SR]/365`, `T` = days); gate ⑥′ (costs × 2, second source on common bars compounded per day, 60 common days); the holdout's Sharpe against `holdout_pass`, read from the verified lock before the claim; the GP ranking's `RankContext`.
- Gate ③ keeps the per-bar Sharpe as the descriptive `sharpe_bar`; no gate reads it.
- `N_eff` correlates every trial on daily returns whatever its lock (`onc-v2`): it spans the whole ledger, where a bar-by-bar join of an hourly and a daily series meets only at midnight. On the ledger of 2026-10-05 (2,525 trials, all 1d) it is the same number.
- Per bar, unchanged: the backtest, the `returns_path` artifact (`reproduce.py` checks it byte for byte), `min_trades`, gate ④ (PBO, CPCV, `spp_median_sharpe`), MinBTL (years), the kernel ≡ sandbox audit.

## Consequences

- A 1h and a 1d campaign are judged on one scale; D4 and D17 keep their meaning (INV-124).
- The IS/holdout cut is at close ≥ 00:00 of the first holdout day, so the bar closing then — the last hour of the day before — opens the holdout as a one-bar day, and the IS ends with a 23-bar day. Each side compounds only its own bars: nothing crosses the cut.
- `spp_median_sharpe` (the ranking's `tanh` term) and `monitor.record_holder`'s CPCV median stay per bar beside a daily `sharpe_is`: a scale difference in a secondary term and a diagnostic, accepted.
- Arch: §3.1.6, §3.2 ⑤, §4.1 and §10 (D4 amended, D26 added) say which clock the statistics read. ADR-0008's per-observation units now mean per day under `daily_v1`; ADR-0020's Sharpe is the daily one.

## Alternatives considered

- Summed daily returns — an approximation of the compound return for no gain.
- Monthly returns — ~70 observations in 5.8 years: too few for skew, kurtosis and the `N_eff` correlations.
- Per-bar returns with a Newey-West or Lo correction — corrects the Sharpe but leaves `N_eff`, PSR and the 1h/1d scale apart, and adds a lag choice to lock.
