# ADR-0010 — Risk & Sizing in the execution path

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-06 · **Arch:** §3.4, D7, D12, P4, P5 · **Open item:** O11 (closed) · **Amended by:** [ADR-0031](0031-position-size-is-risk-over-stop-distance.md)

## Context

§3.4 fixes the sizing formula (vol leg × strength, stop cap as `min`, lot rounding, FX) and a portfolio step (uniform scale back to `target_vol`, leverage cap, IDM re-estimated on rebalance), but not the volatility estimator, the leverage cap's value, when the portfolio step runs inside a bar-by-bar backtest, or how a campaign's lock pins these choices. Phase 0 sized with a fixed-notional `PlaceholderSizer` (O11).

## Decision

1. **`core/sizing/`** (pure, no venue code): `PositionSizer.size(signal, instrument, account, vol_estimate, n_active, idm, portfolio_scale=1)` exactly as §3.4 — `portfolio_scale` multiplies the vol leg only, so the stop cap stays a hard bound; `round_to_lot` floors to the step, drops below `min_qty`, clips at `max_qty`; `InstrumentSpec.multiplier × fx_rate` converts to the base currency. `ewma_vol` (span 25 bars, zero-mean squared simple returns, annualized, ≥ 10 returns else no position), `instrument_diversification_multiplier` (1/√(wᵀCw), equal weights, ρ < 0 floored at 0, cap 2.5), `portfolio_scale` (target / estimated vol, then gross ≤ `max_leverage`).
2. **`execution/risk.py` `RiskSizer`** is what the NautilusTrader bridge calls every bar (`TargetSizer` protocol: symbol, signal, closed-bar window, account equity). `n_active` = the universe size; IDM and the portfolio scale are re-estimated at each `rebalance` boundary (Group B `portfolio.rebalance`) from the last 250 bars of returns; the gross-leverage cap is enforced on every bar given the other symbols' current targets. Equity = quote cash + positions at their last close. Spot cash account ⇒ `max_leverage = 1.0`.
3. **Locked per campaign:** `target_vol`, `max_risk_pct`, `rebalance` come from Group B; `vol_span`, `max_leverage`, `idm_cap` are written to the lock's `derived.sizing` when the campaign opens (like costs and lookback). No new `user.yaml` keys and no §10 change.
4. **A lock without `derived.sizing` is refused at gate ③** ("open a new campaign"): its existing trials were sized by the placeholder, and one campaign must not mix two sizing rules.
5. **`PlaceholderSizer` deleted.** Execution-mechanics tests (next-open fills, stops, costs; P0-10) use a test-only `FixedNotional` sizer, so their golden values are unchanged; sizing has its own tests.

## Consequences

- INV-05 enforced: `test_half_size_when_vol_doubles`, `test_stop_cap_is_min_not_multiplier` (core) and `test_half_size_through_the_risk_sizer` (execution).
- The phase-0 campaign `c-20260921-104445` cannot run gate ③ any more; a new campaign is needed (no CLI for that yet — P1-10 adds the campaign commands).
- The portfolio scale can raise positions when few instruments are held (the arch rule "scale back to target"); the stop cap and the leverage cap bound it.
- IS Sharpe values change versus phase 0 (different position sizes); only runs in a new campaign are comparable.

## Alternatives considered

- **Portfolio step outside the backtest (post-hoc scaling of returns):** rejected — backtest ≠ live (P5).
- **New Group-B keys for `vol_span` / `max_leverage`:** deferred — they are implementation constants today; promoting them is an arch §10 change for the user.
- **Keep running old campaigns with default sizing:** rejected — silently mixes sizing rules within one set of trials.
