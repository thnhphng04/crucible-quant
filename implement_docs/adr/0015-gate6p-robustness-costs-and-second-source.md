# ADR-0015 — Gate ⑥′: cost stress and a second data source

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-09 · **Arch:** §3.2 row ⑥′, §6.1 ("two-tier data", 30% rule), §10.1 · **Open item:** —

## Context

⑥′ re-runs the portfolio with fees × 2 and slippage × 2 ("Sharpe > 0 and DSR must not drop past a campaign-locked threshold") and on another data source ("Sharpe must not drop more than 30%"). For crypto the research source (Binance via ccxt) *is* the broker, so "broker data" gives no second opinion; the threshold for the stressed DSR is not given.

## Decision

1. **User decisions (21 Sep 2026):** the second source is **another exchange via ccxt**; the cost-stress bar is **stressed DSR at `N_eff` ≥ `dsr_min`** (no new knob) plus Sharpe > 0.
2. **`research.data.second_exchange`** (Group B, default `gate` = Gate.io). A ccxt probe on 21 Sep 2026 found Gate.io the only free venue with all five symbols from roughly Binance's start (BTC, ETH, XRP 2018; BNB 2019; SOL 2020); OKX, KuCoin, HTX, MEXC, Bitfinex, Kraken and Bybit each lack a symbol or years of history. Must differ from `research.data.exchange`.
3. **Download:** `cli data-fetch-second` → `data/is-<exchange>/`, requests ending before the holdout start, bars inside any holdout range dropped before writing, read back through `ResearchStore` (which refuses the holdout again). The second vendor's holdout-period prices are never stored.
4. **Re-runs:** every member is re-run in the sandbox from the strategy archive (ADR-0013), with the campaign's sizing; stressed returns and second-source returns are combined with the portfolio's weights and rebalance rule. The stressed DSR uses the same `trial_stats` + variants as ⑤ (a robustness check is not a new selection).
5. **Second-source rule:** Sharpe on the second source vs the primary members' IS returns **on the same dates** (≥ 60 common bars), `drop = 1 − S₂/S₁ ≤ 0.30`. Missing second-source data ⇒ reject (fail closed).
6. Multiplier 2 and the 30% drop are written to the lock's `derived.robustness`.

## Consequences

- Tests: `tests/validation/test_robustness.py` (robust ⇒ pass; edge eaten by 2× costs ⇒ fail; edge halves on the second source ⇒ fail; no second data ⇒ fail; tampered archive ⇒ error); `tests/data/test_second_source.py` (nothing from the holdout on disk).
- Cost: 2 sandbox backtests per member.
- **Arch follow-up (user's call):** §10.1's `user.yaml` example and D18 do not list `second_exchange` yet.

## Alternatives considered

- **Same exchange, other endpoint (e.g. futures):** rejected — a different instrument, not a second vendor of the same one.
- **A new `cost_dsr_max_drop` knob:** rejected by the user in favour of reusing `dsr_min`.
- **Compare against the portfolio's full IS Sharpe:** rejected — the second source's history is shorter; only the common dates compare like with like.
