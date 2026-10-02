"""Real-source perpetual preflight: can the venue serve the window the config asks for? (P3-24)

A perpetual fetch downloads five years of one-minute trade and mark bars per contract and then
carves a write-once holdout. Learning at the end of that download that the brackets need an API
key, or that one contract listed after the configured start, wastes hours; learning it after the
carve wastes a holdout. So before either, each contract is asked one cheap question per series —
**from when, and until when** — for the four inputs a perpetual backtest needs (§6.1, D18):

* trade bars at the research timeframe, and trade one-minute bars (the fill paths);
* mark one-minute bars (the liquidation paths);
* funding settlements;
* today's leverage brackets — a signed endpoint, refused without a read-only key (ADR-0032).

The **common start** is the latest first timestamp of any series of any contract: the binding
listing (SOLUSDT at 2020-09-14 on the real venue, ADR-0031). The **earliest start** the fetch
accepts is that instant rounded up to the coarser of the bar and funding grids, because the
fetch refuses a first bar older than the first funding event, and Binance stamps settlements a
few milliseconds after the boundary.

Nothing here writes: no file, no manifest, no lock, no ledger row. It reports; the caller
refuses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from quantcrucible.data.perp_source import CoverageError, PerpSource, common_window
from quantcrucible.data.source import timeframe_delta

__all__ = ["PerpPreflight", "PerpSource", "preflight_perpetual"]

PROBE_FROM = datetime(2017, 1, 1, tzinfo=UTC)  # before any USDT-M perpetual listing
LAST_PAGE = 50
DAYS_PER_YEAR = 365.25
N_SERIES = 4
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MS = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class Span:
    """The first and the last row a series serves inside the probed window."""

    first: datetime
    last: datetime


@dataclass(frozen=True, slots=True)
class ContractCoverage:
    symbol: str
    series: dict[str, Span]  # "trade <tf>", "trade 1m", "mark 1m", "funding"
    bracket_tiers: int
    max_leverage: int | None

    @property
    def start(self) -> datetime | None:
        """The first instant every series of this contract covers, if all four exist."""
        if len(self.series) < N_SERIES:
            return None
        return max(span.first for span in self.series.values())


@dataclass(frozen=True, slots=True)
class PerpPreflight:
    timeframe: str
    contracts: tuple[ContractCoverage, ...]
    requested_start: datetime
    end: datetime
    holdout_start: datetime
    common_start: datetime | None
    binding: str | None
    earliest_start: datetime | None
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        names = [label for label, _, _ in _series(self.timeframe)]
        lines = [
            "| Contract | " + " | ".join(f"{n} from" for n in names) + " | Brackets | Max lev |",
            "|---" * (len(names) + 3) + "|",
        ]
        for c in self.contracts:
            cells = [_day(c.series[n].first) if n in c.series else "—" for n in names]
            lev = f"{c.max_leverage}x" if c.max_leverage else "—"
            tiers = str(c.bracket_tiers) if c.bracket_tiers else "—"
            lines.append(f"| `{c.symbol}` | " + " | ".join(cells) + f" | {tiers} | {lev} |")
        if self.common_start is not None and self.earliest_start is not None:
            lines.append(
                f"common window starts {_day(self.common_start)} (binding: {self.binding}); "
                f"earliest start the fetch accepts {self.earliest_start:%Y-%m-%d %H:%M} UTC"
            )
            start = max(self.requested_start, self.earliest_start)
            years = (self.holdout_start - start).days / DAYS_PER_YEAR
            lines.append(
                f"in-sample {_day(start)}/{_day(self.holdout_start)} = {years:.2f} years; "
                f"holdout {_day(self.holdout_start)}/{_day(self.end)}"
            )
        lines.append("preflight " + ("PASSED" if self.ok else "REFUSED"))
        return "\n".join(lines) + "\n"


def _series(timeframe: str) -> tuple[tuple[str, str, str], ...]:
    """(label, kind, timeframe) for the four series a perpetual backtest reads."""
    return (
        (f"trade {timeframe}", "trade", timeframe),
        ("trade 1m", "trade", "1m"),
        ("mark 1m", "mark", "1m"),
        ("funding", "funding", "8h"),
    )


def preflight_perpetual(
    source: PerpSource,
    symbols: Sequence[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    holdout_start: datetime,
    *,
    funding_interval_hours: int = 8,
) -> PerpPreflight:
    """Probe every series of every contract and say whether ``[start, end)`` can be fetched."""
    step = timeframe_delta(timeframe)
    interval = timedelta(hours=funding_interval_hours)
    problems: list[str] = []
    contracts: list[ContractCoverage] = []
    for symbol in symbols:
        series: dict[str, Span] = {}
        for label, kind, tf in _series(timeframe):
            # A minute series must reach the window's last minute; funding, its last settlement.
            grid = interval if kind == "funding" else timeframe_delta(tf)
            try:
                span = _span(source, symbol, tf, kind, end, grid)
            except Exception as exc:  # a probe reports every dead endpoint, it does not stop
                problems.append(f"{symbol}: could not read {label} ({type(exc).__name__}: {exc})")
                continue
            if span is None:
                problems.append(f"{symbol}: no {label} history — refusing rather than assuming")
                continue
            if span.last < end - grid:
                problems.append(
                    f"{symbol}: {label} does not reach the window end {end:%Y-%m-%d %H:%M} "
                    f"(its last row before it is {span.last:%Y-%m-%d %H:%M})"
                )
            series[label] = span
        tiers, max_lev = 0, None
        try:
            rows = source.fetch_brackets(symbol)
            tiers, max_lev = len(rows), max(int(r["max_leverage"]) for r in rows)
        except CoverageError as exc:
            problems.append(str(exc))
        contracts.append(ContractCoverage(symbol, series, tiers, max_lev))

    common_start = binding = earliest = None
    starts = {c.symbol: c.start for c in contracts}
    if symbols and all(s is not None for s in starts.values()):
        try:
            common_start, _ = common_window({s: (t, end) for s, t in starts.items() if t})
        except CoverageError as exc:
            problems.append(str(exc))
        else:
            binding = max(starts, key=lambda s: starts[s] or common_start)
            earliest = _ceil(common_start, max(step, interval))
            if start < earliest:
                problems.append(
                    f"research.data.start {start:%Y-%m-%d} is before the data: the binding "
                    f"listing is {binding} on {_day(common_start)} and the first bar every "
                    f"series covers starts {earliest:%Y-%m-%d %H:%M} UTC; set research.data.start "
                    f"to {_day(_ceil(earliest, timedelta(days=1)))} or later"
                )
            if holdout_start <= max(start, earliest):
                problems.append("no in-sample window is left before the holdout")
    return PerpPreflight(
        timeframe=timeframe,
        contracts=tuple(contracts),
        requested_start=start,
        end=end,
        holdout_start=holdout_start,
        common_start=common_start,
        binding=binding,
        earliest_start=earliest,
        problems=tuple(problems),
    )


def _span(
    source: PerpSource, symbol: str, timeframe: str, kind: str, end: datetime, grid: timedelta
) -> Span | None:
    first = source.timestamps(symbol, timeframe, _ms(PROBE_FROM), 1, kind=kind)
    if not first:
        return None
    since = _ms(end - grid * 5)
    tail = [t for t in source.timestamps(symbol, timeframe, since, LAST_PAGE, kind=kind)
            if t < _ms(end)]  # fmt: skip
    # An empty tail means the series stopped earlier; the first row stands in, and fails the
    # caller's end check.
    last = max(tail) if tail else first[0]
    return Span(_at(first[0]), _at(last))


def _ceil(when: datetime, grid: timedelta) -> datetime:
    step = grid // MS
    ms = _ms(when)
    return _at(ms + (-ms % step))


def _ms(when: datetime) -> int:
    return (when - EPOCH) // MS  # integer: a 3 ms settlement stamp must not round away


def _at(ms: int) -> datetime:
    return EPOCH + ms * MS


def _day(when: datetime) -> str:
    return when.date().isoformat()
