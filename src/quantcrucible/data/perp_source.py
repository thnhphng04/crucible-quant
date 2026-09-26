"""Binance USDT-M perpetual data (Architecture §6.1, D18, P3-09).

Three series, not one. A perpetual backtest needs the **trade** price it fills at, the **mark**
price it is liquidated on, and the **funding** it pays or receives — and the last two cannot be
reconstructed from the first. Missing either is refused rather than filled with a default,
because a zero funding rate is a claim ("funding was free that week"), not an absence.

``ccxt`` is already a dependency and ``binanceusdm`` declares everything needed: ``fetchOHLCV``,
``fetchMarkOHLCV``, ``fetchFundingRateHistory``, and ``fetchLeverageTiers`` for the brackets.
That last one is a **signed** endpoint, so :meth:`PerpSource.fetch_brackets` needs a read-only
API key and **refuses** without one; a campaign locks the snapshot it returns and records it as
an assumption, because the venue publishes no bracket history (ADR-0032).

Every endpoint is paged (P3-17). ccxt serves at most 1,000 rows per call, so the earlier
single-call version returned the first 1,000 rows of a five-year window and said nothing about
the rest — which, for funding, looked like a short history and tripped INV-94 for the wrong
reason.

The probe in ``scripts/perp_coverage.py`` measured what the venue actually serves: the binding
listing is SOLUSDT at 2020-09-14, which is what :func:`common_window` computes for a real set.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import numpy as np

from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.source import timeframe_delta

PAGE_LIMIT = 1000
# Funding cadence is not fixed: Binance has changed selected contracts to four-hour
# settlements, so pagination advances by one millisecond after the last actual event.


def _pages[T](
    fetch: Callable[[int], list[T]],
    since_ms: int,
    until_ms: int,
    step_ms: int,
    *,
    key: Callable[[T], int],
) -> list[T]:
    """Walk an exchange endpoint page by page until the window is covered.

    ``fetch`` asks for one page from a timestamp. Five years of one-minute marks is thousands of
    pages, so this is the difference between the real window and its first 1,000 rows — which is
    what the single-page version silently returned.

    Rows are deduplicated and sorted by ``key``, because pages overlap at their boundary. The
    loop ends when a page is short, empty, or fails to advance: a venue that ignores ``since``
    would otherwise serve the same page forever.
    """
    rows: dict[int, T] = {}
    since = since_ms
    while since < until_ms:
        page = fetch(since)
        if not page:
            break
        last = key(page[-1])
        for row in page:
            at = key(row)
            if since_ms <= at < until_ms:
                rows.setdefault(at, row)
        if last < since or len(page) < PAGE_LIMIT:
            break
        since = last + step_ms
    return [rows[at] for at in sorted(rows)]


class CoverageError(RuntimeError):
    """A required series is missing, short, or does not match its manifest."""


class PerpExchange(Protocol):
    """The slice of ccxt this module uses (and that a test can fake)."""

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]: ...

    def fetch_mark_ohlcv(
        self, symbol: str, timeframe: str, since: int, limit: int
    ) -> list[list[float]]: ...

    def fetch_funding_rate_history(
        self, symbol: str, since: int, limit: int
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class PerpData:
    """One instrument's three series over one window."""

    symbol: str
    trades: Bars
    marks: Bars
    funding: np.ndarray  # (timestamp_ns, rate) pairs, one row per funding event


def _is_linear_perp(symbol: str) -> bool:
    """ccxt spells a linear USDT-margined perpetual ``BASE/QUOTE:SETTLE``."""
    return ":" in symbol and symbol.split(":")[-1] == symbol.split("/")[1].split(":")[0]


def _bars(rows: list[list[float]], symbol: str, timeframe: str) -> Bars:
    """ccxt gives bar OPEN times; Bars are indexed by CLOSE time (the project convention)."""
    period = timeframe_delta(timeframe)
    ts = np.array(
        [np.datetime64(int(r[0]) + int(period.total_seconds() * 1000), "ms") for r in rows],
        dtype="datetime64[ns]",
    )
    arr = np.array(rows, dtype=np.float64)
    return Bars(symbol, timeframe, ts, arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5])


@dataclass(frozen=True, slots=True)
class PerpSource:
    exchange_id: str = "binanceusdm"
    exchange: PerpExchange | None = None
    _cached_exchange: PerpExchange | None = field(default=None, init=False, repr=False)

    def _venue(self) -> PerpExchange:
        if self.exchange is not None:
            return self.exchange
        if self._cached_exchange is not None:
            return self._cached_exchange
        import ccxt  # imported lazily: only real downloads need it

        options: dict[str, Any] = {"enableRateLimit": True}
        if self.exchange_id == "binanceusdm":
            key = os.environ.get("BINANCE_API_KEY")
            secret = os.environ.get("BINANCE_API_SECRET")
            if key and secret:
                options.update({"apiKey": key, "secret": secret})
        venue: PerpExchange = getattr(ccxt, self.exchange_id)(options)
        object.__setattr__(self, "_cached_exchange", venue)
        return venue

    def is_continuous(self) -> bool:
        return True  # a perpetual never expires, so there is no roll

    def adjustment(self) -> Literal["none", "ratio", "difference"]:
        return "none"

    def fetch(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> PerpData:
        if not _is_linear_perp(symbol):
            raise ValueError(
                f"{symbol!r} is not a linear perpetual: expected BASE/QUOTE:SETTLE, e.g. "
                "'BTC/USDT:USDT'"
            )
        venue = self._venue()
        since = int(start.timestamp() * 1000)
        until = int(end.timestamp() * 1000)
        step = int(timeframe_delta(timeframe).total_seconds() * 1000)
        trade_rows = _pages(
            lambda s: venue.fetch_ohlcv(symbol, timeframe, s, PAGE_LIMIT),
            since, until, step, key=lambda r: int(r[0]),
        )  # fmt: skip
        if not trade_rows:
            raise CoverageError(f"{symbol}: no trade bars in {start:%Y-%m-%d}/{end:%Y-%m-%d}")
        mark_rows = _pages(
            lambda s: venue.fetch_mark_ohlcv(symbol, timeframe, s, PAGE_LIMIT),
            since, until, step, key=lambda r: int(r[0]),
        )  # fmt: skip
        if not mark_rows:
            raise CoverageError(f"{symbol}: no mark bars — liquidation cannot be resolved")
        funding_rows = _pages(
            lambda s: venue.fetch_funding_rate_history(symbol, s, PAGE_LIMIT),
            since, until, 1, key=lambda r: int(r["timestamp"]),
        )  # fmt: skip
        self._check_funding(symbol, funding_rows, trade_rows)
        funding = np.array(
            [[float(r["timestamp"]) * 1e6, float(r["fundingRate"])] for r in funding_rows],
            dtype=np.float64,
        )
        return PerpData(
            symbol=symbol,
            trades=_bars(trade_rows, symbol, timeframe),
            marks=_bars(mark_rows, symbol, timeframe),
            funding=funding,
        )

    def fetch_raw(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, *, kind: str
    ) -> list[list[float]]:
        """One series of paged OHLCV rows, in ccxt's shape with bar **open** timestamps.

        Used by :mod:`quantcrucible.data.perp_store` for the minute series, where the rows are
        written to disk rather than turned into :class:`Bars`: a five-year minute download is
        resumed from the file, so it is the raw rows that have to survive the round trip.
        """
        return _pages(
            lambda s: self._raw_page(symbol, timeframe, s, kind=kind),
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
            int(timeframe_delta(timeframe).total_seconds() * 1000),
            key=lambda r: int(r[0]),
        )

    def iter_raw_pages(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, *, kind: str
    ) -> Iterator[list[list[float]]]:
        """Yield raw OHLCV pages without accumulating the whole window in memory.

        ``download_minutes`` uses this for checkpointed minute downloads: every yielded page can
        be validated and written before the next request is attempted, so an interrupted
        multi-page fetch resumes from the last durable minute instead of starting over.
        """
        since_ms = int(start.timestamp() * 1000)
        until_ms = int(end.timestamp() * 1000)
        step_ms = int(timeframe_delta(timeframe).total_seconds() * 1000)
        since = since_ms
        while since < until_ms:
            page = self._raw_page(symbol, timeframe, since, kind=kind)
            if not page:
                break
            rows = [row for row in page if since_ms <= int(row[0]) < until_ms]
            if rows:
                yield rows
            last = int(page[-1][0])
            if last < since or len(page) < PAGE_LIMIT:
                break
            since = last + step_ms

    def _raw_page(self, symbol: str, timeframe: str, since: int, *, kind: str) -> list[list[float]]:
        venue = self._venue()
        fetch = {"trade": venue.fetch_ohlcv, "mark": venue.fetch_mark_ohlcv}.get(kind)
        if fetch is None:
            raise ValueError(f"kind must be 'trade' or 'mark', got {kind!r}")
        return fetch(symbol, timeframe, since, PAGE_LIMIT)

    def fetch_brackets(self, symbol: str) -> list[dict[str, float | int]]:
        """Today's leverage brackets for one contract, as plain rows.

        ``GET /fapi/v1/leverageBracket`` is a **signed** endpoint, so this needs a read-only API
        key in ``.env``. Without one it **refuses**: the bracket table decides initial and
        maintenance margin, and therefore the liquidation price, so a default here would be an
        invented venue rule that every result silently depends on. Failing closed means a
        campaign cannot open without a real snapshot.

        Rows rather than a ``BracketTable``: ``data`` may not import ``execution``
        (import-linter), so the execution layer builds the table from these. ccxt keeps the
        maintenance amount — the continuity term between tiers — in the raw ``info`` payload.

        The venue publishes no bracket history, so applying today's snapshot to a 2020 backtest
        is an assumption the campaign lock records, never a reconstruction (ADR-0032).
        """
        venue = self._venue()
        call = getattr(venue, "fetch_market_leverage_tiers", None)
        if call is None:
            raise CoverageError(
                f"{symbol}: this exchange exposes no leverage bracket endpoint, and margin "
                "cannot be assumed"
            )
        try:
            tiers = call(symbol)
        except Exception as exc:  # unauthenticated, rate-limited, endpoint moved — all the same
            raise CoverageError(
                f"{symbol}: could not read the leverage brackets ({type(exc).__name__}). A "
                "read-only Binance API key is required; refusing rather than assuming margin"
            ) from exc
        if not tiers:
            raise CoverageError(f"{symbol}: the venue returned no leverage brackets")
        rows = [
            {
                "cap": float(t["maxNotional"]),
                "max_leverage": int(t["maxLeverage"]),
                "mmr": float(t["maintenanceMarginRate"]),
                "amount": float(t.get("info", {}).get("cum", 0.0)),
            }
            for t in tiers
        ]
        return sorted(rows, key=lambda r: r["cap"])

    @staticmethod
    def _check_funding(
        symbol: str, funding_rows: list[dict[str, Any]], trade_rows: list[list[float]]
    ) -> None:
        """Funding must span the trade window. A short series is refused, never zero-filled:
        a zero rate is a claim about the market, not an absence of data (INV-94)."""
        if not funding_rows:
            raise CoverageError(f"{symbol}: no funding history")
        last_trade_ms = int(trade_rows[-1][0])
        last_funding_ms = int(funding_rows[-1]["timestamp"])
        first_trade_ms = int(trade_rows[0][0])
        first_funding_ms = int(funding_rows[0]["timestamp"])
        if first_funding_ms > first_trade_ms or last_funding_ms < last_trade_ms:
            raise CoverageError(
                f"{symbol}: funding history covers "
                f"{_day(first_funding_ms)}/{_day(last_funding_ms)} but the trade bars run "
                f"{_day(first_trade_ms)}/{_day(last_trade_ms)} — refusing rather than filling"
            )


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def common_window(
    windows: Mapping[str, tuple[datetime, datetime]],
) -> tuple[datetime, datetime]:
    """The window every instrument covers: the latest start and the earliest end.

    The binding listing sets the in-sample start for the whole campaign, which is why one late
    contract shortens everyone's history — SOLUSDT at 2020-09-14 cost the set 2.7 years against
    the spot window (P3-01, ADR-0031).
    """
    if not windows:
        raise CoverageError("no instruments to find a common window for")
    start = max(w[0] for w in windows.values())
    end = min(w[1] for w in windows.values())
    if start >= end - timedelta(days=1):
        raise CoverageError(
            f"no common window: the latest start {start:%Y-%m-%d} is at or after the earliest "
            f"end {end:%Y-%m-%d}"
        )
    return start, end
