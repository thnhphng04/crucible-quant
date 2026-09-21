# ADR-0003 — NautilusTrader fill semantics for bar backtests

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P0-10 · **Arch:** §3.5, §3.3, §3.4 · **Open item:** O3

## Context

§3.5 requires a pessimistic fill model: a signal computed on a **closed** bar executes at the **next** bar's open, limits fill only when price trades through them, and slippage is charged. The architecture's NautilusTrader snippets are unverified (§10). A spike against the installed `nautilus_trader` 1.231.0 (wheels exist for `cp312-win_amd64` and manylinux) showed:

- A market order submitted in `on_bar` fills at **that bar's close** — the same bar that produced the signal. With `LatencyModel(base_latency_nanos=1)` alone it fills at bar t+1's **close**. Neither is the next open.
- `FillModel(prob_fill_on_limit=0)` gives trade-through limits: a buy limit at 115 against a low of exactly 115 does not fill; one at 116 fills at 116.
- `FillModel` has no fixed-tick slippage for bar data. Crypto tick sizes (0.01 on BTC/USDT) make "N ticks" meaningless anyway.
- On a `CASH` account, an order that would overdraw the balance stops the whole backtest with `AccountBalanceNegative` — **silently**: `engine.run()` returns normally.

## Decision

1. **Next-open fills:** every bar t+1 is preceded by a synthetic `TradeTick` at its open price, stamped 1 ns after bar t closed, and the venue has 1 ns of latency. An order submitted on bar t's close reaches the matching engine at that tick and fills at the open of t+1. Bars are stamped at their close time (P0-09).
2. **Limits:** `FillModel(prob_fill_on_limit=0, prob_slippage=0)`, seeded.
3. **Slippage = extra taker fee:** `CostModel(fee_rate=0.001, slippage_bps=5)`; the instrument's maker and taker fee are both `fee_rate + slippage_bps/10⁴`. Gate ⑥′ scales both via `CostModel.scaled`.
4. **Spot, long or flat:** `CASH` account, `NETTING`. A `short` signal means flat; it is counted in `BacktestResult.signals` and never traded. Shorts need a margin venue (phase 2+).
5. **One protective stop per position:** a `reduce_only` stop-market at `close − stop_distance`, cancelled and re-issued on every bar. A gap through the stop fills at the gap's open, not at the trigger.
6. **Rebalance band:** an open position is resized only when the target quantity moves by more than 25%, so a fixed-notional target does not trade every bar.
7. **`PlaceholderSizer`** (until P1-06): `50% of cash / n_symbols × strength / close`. The unused half pays for next-open gaps above the signal close.
8. **Truncation is an error:** `run_backtest` counts the bars the strategy saw; fewer than supplied raises `BacktestAbortedError`.
9. **Equity** is rebuilt from fills and marked at each bar's close; per-bar returns are annualized with 365 periods per year (crypto trades every day).

## Consequences

- Oracle level 2 (acting on the bar that produced the signal) cannot profit from its own bar's close: the earliest fill is the next open.
- Costs are a single number per campaign, recorded in the lock with the rest of Group B.
- Tests: `tests/execution/test_engine.py` (INV-35) pins every rule above with hand-computed expectations.
- A later Nautilus upgrade must re-run the spike tests; the pin is `>=1.231,<1.232`.

## Alternatives considered

- **Post-process fills** (let Nautilus fill at the close, then re-price at the next open): rejected — the live path would differ from the backtest path (P5).
- **Custom `FillModel` subclass with tick slippage:** rejected — it does not change when a bar-driven order reaches the book, and ticks mean little on crypto.
- **Margin account to allow shorts now:** deferred — phase 0 only needs the harness to run one long-only reference strategy.
