# ADR-0031 — Position size is risk over stop distance

- **Status:** Accepted
- **Date:** 2026-09-25
- **Task:** P3-02 · **Arch:** §3.4, §7, §10 (D7, D12, D18) · **Retires:** P4, INV-05

## Context

`PLAN-PER-INSTRUMENT-STRATEGIES.en.md`, frozen by the user on 2026-09-25, specifies that every position targets a price-move loss to its stop of 1% of account equity at entry, and that the summed risk budget of open positions, pending orders and new entries may not exceed 10% of equity.

That formula is already in the code. `PositionSizer.size` computes `qty_cap = equity × max_risk_pct / (stop_distance × unit_value)` and returns `round_to_lot(min(qty_vol, qty_cap), instrument)`. The requirement differs from today's behaviour in exactly one token: the surrounding `min`. Keeping it means each position risks **at most** 1%; removing it means **exactly** 1%.

The user was shown that distinction, with the note that keeping the `min` would preserve hard invariant P4 and need no architectural change, and chose to remove it.

This is not an implementation decision. P4 is in the architecture's principle table (§1) and in `CLAUDE.md`'s hard-invariant table; INV-05 guards it with three named tests; D7 (`target_vol` = 10%/yr) exists only to feed the vol-targeting leg. Retiring it is the user's call, recorded here.

The first campaign under the new rule is also the first on Binance USDT-M perpetuals, which moves D18. The perpetual coverage probe (P3-01, `scripts/perp_coverage.py`, run 2026-09-25) found the binding listing is SOLUSDT at 2020-09-14, giving **5.03 years** of in-sample history once a 12-month holdout is carved off — against 7.72 years on spot. At `minbtl_target_sharpe = 1.5` that caps MinBTL at **1,475** trials, against 36,724 on the spot window, with `trial_stats.n_eff` already at 290.

## Decision

1. **Position size is `Q = R / d`**, with `R = max_risk_pct × equity` and `d = Signal.stop_distance`, converted through `instrument.multiplier × fx_rate` and rounded **down** to the lot step. Volatility still enters the size exactly once, through `d` (typically k × ATR), never through a vol-targeting leg.
2. **Rounding is one-sided.** `round_to_lot` floors, so realized risk lands in `[R − ε, R]` where ε is what lot and tick rounding give up. The spec's "±1%" is wrong in sign: the error can only be downward. An entry whose rounded risk falls further below `R` than a declared tolerance is **rejected with a reason**, never reported as a full allocation.
3. **`target_vol`, `idm` and `portfolio_scale` are deleted, not deprecated.** `instrument_diversification_multiplier` and `portfolio_scale` leave `core/sizing/vol_target.py`; `target_vol`, `idm_cap` and `max_leverage` leave `RiskSettings`. Dead code that used to be load-bearing is the most dangerous kind: it keeps compiling until someone re-wires it.
4. **`Signal.strength` is pinned to 1.0** for perpetual campaigns, enforced as a gate ①a guardrail rule. The field stays on the contract (P3, INV-03), but it must not scale `R`: the portfolio cap is defined on `R`, and a strength-scaled `R` makes that cap non-binding in a way nothing tracks. The grammar already renders `1.0` literally.
5. **D7 changes meaning rather than disappearing.** It becomes `max_portfolio_risk_pct` = 10% of equity — the summed commitment at the stop across open positions, pending orders and new entries, with at most 10 positions. Because `q` and `d` both freeze at entry, each position's commitment is a constant in USDT, so the cap is arithmetic rather than a separate hand-written breach rule.
6. **The portfolio step becomes an admission rule.** At each timestamp: settle exits, stops, liquidations and funding; snapshot equity once; then consider entries in canonical order, every entry in the batch using the same `E` and `R`. Never widen a stop, never add margin automatically, never close an older position to make room. The ordering is hashed into the campaign protocol.
7. **INV-05 is replaced, not deleted.** INV-90 asserts risk at the stop lands in `[R − ε, R]`; its companion tests assert that doubling `vol_estimate` does **not** change the size — the direct negation that makes the retirement provable rather than assumed — and that doubling `d` halves it.
8. **D18 records the measured window.** Binance USDT-M perpetual, five contracts, `timeframe` configurable from the lock, IS from 2020-09-14, holdout the last 12 months under its own write-once lock.

## Consequences

- **Portfolio volatility becomes an outcome, not a target.** The architecture's warning at §3.4 — "BTC at ~60% vol eats the entire portfolio risk budget and VN30F1M at ~20% vol effectively does not exist" — no longer applies the same way, because risk is equalised at the stop rather than at the volatility estimate. What is genuinely lost is IDM's correlation credit: five correlated crypto perpetuals each risking 1% are not five independent 1% bets, and nothing in the new rule compensates. The portfolio cap bounds the arithmetic sum, not the correlated sum. Whoever revisits this should start there.
- **A campaign lock written under vol targeting cannot be mixed with one written under `Q = R/d`.** `is_gates.risk_settings` already refuses a lock that predates the Risk layer rather than mixing two sizing rules inside one campaign's trials; the same refusal now covers a lock carrying `derived.sizing.max_leverage`. Five campaigns are currently OPEN and keep their meaning as records; none can be resumed.
- **Adding fields to `Research` breaks `assert_lock_matches` for those campaigns.** `research_to_dict` dumps the whole dataclass and the guard compares dicts exactly, so a new key makes `current != locked`. The read-only `review` path does not go through this guard, so old campaigns stay readable; running one again is what stops being possible.
- **The MinBTL headroom is now finite and countable.** 1,475 − 290 = 1,185 trials on this data, ever. At the measured rate — the v4 campaign's 600 raw trials moved `n_eff` from 145 to 290 — that is roughly five more 600-trial campaigns before gate ② closes. `_check_trial_budget` is stricter still, since it adds the raw budget to `n_eff`. A 3-year window was considered and is arithmetically impossible: it caps N at 121, below the 290 already recorded.
- **Timeframe is a genuine knob and stays unset.** `periods_per_year` is derived in both `execution/engine.py` and `validation/pbo_gate.py`; `span_years` and the archive's `_years` use calendar days, so gate ② is timeframe-independent by construction. What is not yet safe: `Member.from_dict` defaults a missing timeframe to `"1d"` instead of refusing, `DEFAULT_LOOKBACK = 400` counts bars so its meaning changes with the timeframe, and D15's `min_trades` / `min_holding_bars` are bar-denominated. P3-14 closes those.
- **`RANKING_CTX` in `compare.py` keeps its hardcoded `periods_per_year=365.0`.** It is a frozen scenario for the ranking fingerprint (INV-82) and must not drift with configuration.
- The ½-size test that §3.4 called mandatory in phase 1, and that caught a real double-counting bug in v0.2, is gone. Its replacement must be at least as adversarial, which is why INV-90 includes the negation rather than only the positive form.

## Alternatives considered

- **Keep `min(qty_vol, qty_cap)` — risk ≤ 1%.** This was recommended and declined. It preserves P4, INV-05, D7 and the architecture unchanged, and satisfies the spec's intent read as a bound rather than a target. The user chose the exact-1% reading.
- **Keep the `min` but reject entries whose vol leg is far below the risk cap.** Preserves P4 while removing very small positions, at the price of one more threshold to calibrate and hash into the protocol. Declined with the option above.
- **Retire P4 but keep `portfolio_scale` as an advisory diagnostic.** Rejected under decision 3: a dormant scaling factor that once governed sizing is an invitation to re-enable it without an ADR.
- **Scale `R` by `Signal.strength`.** Rejected under decision 4: it reintroduces a second risk lever that the 10% portfolio cap does not account for.
