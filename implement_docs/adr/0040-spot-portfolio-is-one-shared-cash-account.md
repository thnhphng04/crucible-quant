# ADR-0040 — A spot portfolio is one shared cash account

- **Status:** Accepted
- **Date:** 2026-10-01
- **Task:** P3-52 · **Arch:** §3.2.1, §3.4 · **Extends:** ADR-0033 (slots), ADR-0035 (account replay)

## Context

Since ADR-0033 a bracket campaign searches each `(instrument, direction)` on its own, and a perpetual portfolio is one account replay of its members' signals (ADR-0035). The spot path never followed. `build_and_record` still ran the v0.3 rule of §3.2.1:

- one representative per feature-map cell, whose id carries no instrument, so BTC-long and ETH-long in one cell eliminated each other (the dedup ADR-0033 decision 5 scoped for perpetuals only);
- a correlation filter, then w ∝ 1/σ, a monthly rebalance, and a weighted sum of standalone 100k accounts;
- no portfolio risk cap, although §3.4 makes one the portfolio-level rule and ADR-0031 retired the volatility leg that 1/σ re-introduces;
- a common window, so a member listed later (SOL, 2020-08) cut every member's history.

The architecture comparison of 2026-10-01 found it. The user chose the shared account and both details below.

## Decision

1. **A new lock tag, `derived.portfolio_protocol: shared_account_v1`**, written by the CLI and Studio for every new campaign. A lock without it keeps the rule it was registered under; an unknown tag is refused. The open harness campaign `c-20260930-170212` and the comparison protocol hash are unchanged.
2. **Selection is `select_slots`, as for perpetuals:** one member per slot, the best PSR against SR₀(N_eff, V[SR]), IS Sharpe > 0, at most `max_strategies`. **No correlation filter** (user, 2026-10-01): it was built to drop near-duplicates of one basket search; spot crypto pairs correlate ≈ 0.7–0.8, so it would leave one or two members, and the 10% cap already bounds the total risk. `max_corr` stays in the lock and is not read on this path.
3. **The portfolio is `replay_spot_signals`** (`execution/spot_account.py`): one cash account, long slots, `bracket_timeout_v1` only. Per step:
   1. timeouts sell at the open;
   2. entries fill at the open, oldest snapshot first, then in canonical order. Each is sized `Q = R/d` from its signal bar's equity snapshot, admitted by `admit_batch` against Σ q·d ≤ 10% of equity after the open's exits, then floored to what the cash left can buy;
   3. SL/TP resolve on OHLC, the worse touch first;
   4. one equity snapshot at the close.
4. **The axis is the union of the members' timestamps** (user, 2026-10-01). A slot waits for its instrument's first bar; a held instrument missing a bar is marked at its last close.
5. **One member reproduces its gate-③ backtest bit for bit** (`_run_spot_bracket`, INV-112). The portfolio adds only what sharing the account adds.
6. **Every consumer replays the same account:** the build (`research_run.account_portfolio`, also for the per-engine portfolio of a tagged harness campaign), gate ⑥′ (`robustness.account_returns`), and the holdout. The holdout picks the path from the frozen variant's `rule_config.weighting == "risk_per_slot"` and asks the engine for `signals` jobs, as for perpetuals. The account curve is written for review and Studio.

## Consequences

- Spot and perpetual portfolios now share one rule: slots, one snapshot, the 10% cap, no weights. A spot portfolio no longer loses history to its youngest member.
- Portfolio returns start at the earliest member's first bar. Before a later member lists, the account holds cash for it, which dilutes nothing: every entry still risks exactly `R`.
- `max_corr` and `rebalance` are dead keys on the account path. Removing them from Group B would need its own ADR.
- Legacy spot locks keep cells and 1/σ forever, as P2 and pre-registration require.
- Arch edits (VI first, then EN): §3.2.1 describes both rules and which lock selects which; §3.4 no longer calls the portfolio step a vol budget; the changelog records it.

## Alternatives considered

- **Keep the weighted sum and only dedup per slot.** Rejected by the user: it would still re-weight by 1/σ, which ADR-0031 retired, and still have no portfolio cap.
- **Intersect the axes, as the perpetual replay does.** Rejected: an intersection cuts BTC, ETH and BNB to SOL's listing date. Perpetuals need one axis because funding and liquidation are resolved per bar; spot has neither.
- **Keep the correlation filter on slots.** Rejected, see decision 2.
- **Change the rule for every lock.** Rejected: rebuilding an open campaign's portfolio under a rule it was not registered with is selection after the fact.
