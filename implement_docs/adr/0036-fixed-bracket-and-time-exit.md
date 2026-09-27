# ADR-0036 — Fixed bracket and time exit

- **Status:** Accepted
- **Date:** 2026-09-27
- **Task:** P3-25

## Context

The generator currently emits flat outside its entry condition; take_profit is validated but not executed. A fixed-position perpetual stop already stays at the signal close, while spot replaces its stop each bar.

## Decision

New campaign locks use `bracket_timeout_v1`: stop and take-profit levels anchor to the signal bar close, are fixed at entry, and ignore later flat signals. The entry bar counts as held bar one. After bar 100 closes without a price exit, a market order fills at the next open. Existing locks without the version keep `legacy_flat`.

The generator chooses ATR or Bollinger(8, 2) stop distance; the campaign fixes TP distance at 1.1 times that distance by default. Entry rules do not change. Spot OHLC ambiguity takes the stop. Perpetual stop/TP touch uses trade-minute paths, liquidation uses mark-minute paths, and ties take the worse result.

Funding is settled at each segment boundary only while the wallet is open, exactly once per side under hedge mode; a later settlement does not charge a position already closed by SL/TP. Missing or misaligned trade paths fail before pricing.

For intraday histories, gate ④ splits the preregistered configuration grid into sandbox jobs sized below the report limit, then joins return rows in the original order and requires identical timestamp axes. No configuration becomes a separate trial or is discarded.

## Consequences

All price-setting jobs and portfolio replay must receive the version from the campaign lock. The trade-minute path becomes a required companion to the mark path for new perpetual campaigns. Existing archived results retain their original semantics.
