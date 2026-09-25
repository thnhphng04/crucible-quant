"""Probe what Binance USDT-M perpetual history is actually available (task P3-01).

Answers the one question that gates the whole perpetual migration: is there enough data, and
from which date. For each instrument it reports

  * the first daily trade bar the venue will serve,
  * how far back ``fetchFundingRateHistory`` reaches,
  * whether 1-minute mark-price bars exist, and how many rows a full history would be,
  * the current leverage brackets (a SNAPSHOT — the venue publishes no history for these).

The script is read-only: it downloads nothing to ``data/``, writes no manifest and touches no
ledger. Its output is a table to paste into ADR-0031, not an artifact the pipeline consumes.

Usage:  uv run python scripts/perp_coverage.py
        uv run python scripts/perp_coverage.py --symbols BTC/USDT:USDT ETH/USDT:USDT
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import ccxt

# ccxt spells a linear USDT-margined perpetual "BASE/QUOTE:SETTLE".
DEFAULT_SYMBOLS = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
)
PROBE_FROM = datetime(2017, 1, 1, tzinfo=UTC)  # earlier than any USDT-M perpetual listing
MINUTES_PER_DAY = 1440


@dataclass(frozen=True, slots=True)
class Coverage:
    """What one instrument offers, as the venue reports it today.

    Each series is probed on its own: Binance serves trade bars, funding and mark prices from
    public endpoints, but ``leverageBracket`` is signed, so a missing API key must not hide the
    three answers that do not need one.
    """

    symbol: str
    first_daily_bar: datetime | None
    daily_bars: int
    first_funding: datetime | None
    funding_rows: int
    first_mark_minute: datetime | None
    mark_minutes_estimate: int
    leverage_tiers: int
    max_leverage: float | None
    errors: tuple[str, ...] = ()

    @property
    def public_complete(self) -> bool:
        """Every series a backtest needs, ignoring the signed bracket endpoint."""
        return (
            self.first_daily_bar is not None
            and self.first_funding is not None
            and self.first_mark_minute is not None
        )


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _at(stamp: float | int | None) -> datetime | None:
    if stamp is None:
        return None
    return datetime.fromtimestamp(float(stamp) / 1000.0, tz=UTC)


def _first_daily(exchange: Any, symbol: str) -> tuple[datetime | None, int]:
    """The earliest daily bar, and how many daily bars exist from there until now."""
    rows = exchange.fetch_ohlcv(symbol, "1d", since=_ms(PROBE_FROM), limit=1)
    if not rows:
        return None, 0
    first = _at(rows[0][0])
    if first is None:
        return None, 0
    return first, (datetime.now(UTC) - first).days


def _first_funding(exchange: Any, symbol: str) -> tuple[datetime | None, int]:
    """How far back funding history reaches. Binance pays every 8 h, so 3 events a day."""
    rows = exchange.fetch_funding_rate_history(symbol, since=_ms(PROBE_FROM), limit=1)
    if not rows:
        return None, 0
    first = _at(rows[0].get("timestamp"))
    if first is None:
        return None, 0
    return first, (datetime.now(UTC) - first).days * 3


def _first_mark_minute(exchange: Any, symbol: str) -> tuple[datetime | None, int]:
    """The earliest 1-minute mark bar, and how many minutes a full history would hold.

    The row estimate is the number that matters: it decides whether the mark series can be
    staged into the sandbox at all (the container reads one parquet per instrument).
    """
    rows = exchange.fetch_mark_ohlcv(symbol, "1m", since=_ms(PROBE_FROM), limit=1)
    if not rows:
        return None, 0
    first = _at(rows[0][0])
    if first is None:
        return None, 0
    return first, (datetime.now(UTC) - first).days * MINUTES_PER_DAY


def _brackets(exchange: Any, symbol: str) -> tuple[int, float | None]:
    """Leverage brackets as they stand TODAY. The venue publishes no historical brackets, so
    applying these to a 2020 backtest is an assumption, never a reconstruction."""
    tiers = exchange.fetch_market_leverage_tiers(symbol)
    if not tiers:
        return 0, None
    highest = max(float(t.get("maxLeverage") or 0.0) for t in tiers)
    return len(tiers), highest or None


def _attempt[T](label: str, call: Callable[[], T], fallback: T, errors: list[str]) -> T:
    """Run one probe. A failure is recorded and reported, never raised: the point of this
    script is to say what IS available, and one dead endpoint must not hide the rest."""
    try:
        return call()
    except Exception as exc:  # a probe reports failures in its table, it does not raise
        errors.append(f"{label}: {type(exc).__name__}")
        return fallback


def probe(exchange: Any, symbol: str) -> Coverage:
    errors: list[str] = []
    no_series: tuple[datetime | None, int] = (None, 0)
    no_brackets: tuple[int, float | None] = (0, None)
    first_daily, daily = _attempt("1d", lambda: _first_daily(exchange, symbol), no_series, errors)
    first_funding, funding = _attempt(
        "funding", lambda: _first_funding(exchange, symbol), no_series, errors
    )
    first_mark, marks = _attempt(
        "mark", lambda: _first_mark_minute(exchange, symbol), no_series, errors
    )
    tiers, max_leverage = _attempt(
        "brackets", lambda: _brackets(exchange, symbol), no_brackets, errors
    )
    return Coverage(
        symbol=symbol,
        first_daily_bar=first_daily,
        daily_bars=daily,
        first_funding=first_funding,
        funding_rows=funding,
        first_mark_minute=first_mark,
        mark_minutes_estimate=marks,
        leverage_tiers=tiers,
        max_leverage=max_leverage,
        errors=tuple(errors),
    )


def _day(when: datetime | None) -> str:
    return when.date().isoformat() if when else "—"


def render(rows: list[Coverage]) -> str:
    """A markdown table, ready to paste into the ADR."""
    out = [
        "| Instrument | First 1d bar | 1d bars | Funding from | Funding rows | "
        "Mark 1m from | Mark rows (est.) | Brackets | Max lev |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lev = f"{r.max_leverage:g}x" if r.max_leverage else "—"
        brackets = str(r.leverage_tiers) if r.leverage_tiers else "—"
        out.append(
            f"| `{r.symbol}` | {_day(r.first_daily_bar)} | {r.daily_bars:,} | "
            f"{_day(r.first_funding)} | {r.funding_rows:,} | {_day(r.first_mark_minute)} | "
            f"{r.mark_minutes_estimate:,} | {brackets} | {lev} |"
        )
    return "\n".join(out)


def verdict(rows: list[Coverage]) -> str:
    """The one line the ADR needs: the common in-sample start, or the blocker."""
    missing = [r.symbol for r in rows if not r.public_complete]
    if missing:
        return f"BLOCKED: no complete trade/funding/mark coverage for {', '.join(missing)}."
    starts = [
        d
        for r in rows
        for d in (r.first_daily_bar, r.first_funding, r.first_mark_minute)
        if d is not None
    ]
    common = max(starts)
    # A campaign also needs a 12-month holdout plus the indicator warm-up before it.
    usable_is_years = ((datetime.now(UTC) - timedelta(days=365)) - common).days / 365.25
    return (
        f"Common start (latest of every series): {common.date().isoformat()} — "
        f"{usable_is_years:.2f} years of in-sample once the 12-month holdout is carved off."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args(argv)

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    rows = [probe(exchange, s) for s in args.symbols]

    print(render(rows))
    print()
    print(verdict(rows))
    print()
    print(
        "Leverage brackets are a snapshot taken "
        f"{datetime.now(UTC).date().isoformat()}; the venue publishes no history for them."
    )
    failures = sorted({e for r in rows for e in r.errors})
    if failures:
        print()
        print("Endpoints that did not answer: " + ", ".join(failures))
    return 0 if all(r.public_complete for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
