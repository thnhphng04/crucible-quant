"""The perpetual input bundle (Architecture §3.5, §6.1, ADR-0032, ADR-0034, P3-18).

A perpetual backtest needs four things a trade bar cannot supply:

============  =====================================================================
mark          the price liquidation is measured on — not the price fills happen at
funding       what the position pays or receives, three times a day on Binance
paths         the intrabar path, which decides *when inside a bar* a level is reached
brackets      the leverage tiers that set initial and maintenance margin
============  =====================================================================

They travel **together**, in one object, because the failure that matters here is not a missing
series — that raises — but a **desynchronised** one. A mark series cut one bar differently from
the trade series marks every position at the wrong price, and nothing anywhere raises. So there
is exactly one slicing operation, it moves all four, and there is no way to cut one of them.

**The conventions, stated once.**

- Timestamps are nanoseconds, UTC, and label a bar's **close** time, the same as :class:`Bars`.
- Windows are half-open ``[start, stop)``, the same as :meth:`Bars.slice`.
- ``bar_ix`` in the funding table indexes *this* bundle, and :meth:`PerpBundle.slice` renumbers
  it from zero. A sliced bundle therefore cannot be read with an unsliced one's indices: the
  alternative is charging funding to a bar offset by the slice, which is invisible in a result.
- Several funding events inside one bar stay separate rows. Summing them to one rate per bar
  would hide the liquidation-price move each settlement causes mid-bar (ADR-0032 decision 6b).
- ``brackets`` is a campaign constant, not a window, so slicing carries it through unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from quantcrucible.core.path_summary import PathSummary, SegmentedPath
from quantcrucible.core.strategy.base import Bars

# The funding table crosses the sandbox boundary as a bare float array, so the column order is
# part of the contract rather than an implementation detail.
FUNDING_COLUMNS = ("bar_ix", "ts", "rate", "mark")

Bracket = Mapping[str, float | int]


@dataclass(frozen=True, slots=True, eq=False)
class PerpBundle:
    """One contract's perpetual inputs, aligned bar for bar with its trade bars."""

    symbol: str
    timeframe: str
    marks: Bars
    funding: npt.NDArray[np.float64]  # (n, 4): see FUNDING_COLUMNS
    paths: tuple[SegmentedPath, ...]
    brackets: tuple[Bracket, ...]

    def __post_init__(self) -> None:
        if len(self.paths) != len(self.marks):
            raise ValueError(
                f"{self.symbol}: one path per bar is required, got {len(self.paths)} paths for "
                f"{len(self.marks)} bars — a short list shifts every later bar's intrabar answer"
            )
        if not self.brackets:
            raise ValueError(f"{self.symbol}: no leverage bracket table, so margin is unknown")
        if self.funding.ndim != 2 or self.funding.shape[1] != len(FUNDING_COLUMNS):
            raise ValueError(
                f"{self.symbol}: funding must be an (n, {len(FUNDING_COLUMNS)}) array of "
                f"{FUNDING_COLUMNS}"
            )
        if len(self.funding):
            top = int(self.funding[:, 0].max())
            if top >= len(self.marks) or int(self.funding[:, 0].min()) < 0:
                raise ValueError(
                    f"{self.symbol}: funding bar_ix {top} is outside a bundle of "
                    f"{len(self.marks)} bars — the series are desynchronised"
                )

    def __len__(self) -> int:
        return len(self.marks)

    def funding_at(self, bar: int) -> npt.NDArray[np.float64]:
        """Every settlement inside one bar, in time order — one row per event, never summed."""
        if not len(self.funding):
            return self.funding
        rows = self.funding[self.funding[:, 0] == bar]
        ordered: npt.NDArray[np.float64] = rows[np.argsort(rows[:, 1], kind="stable")]
        return ordered

    def slice(self, start: int, stop: int) -> PerpBundle:
        """The one windowing operation: all four series, half-open, ``bar_ix`` renumbered."""
        if len(self.funding):
            bar = self.funding[:, 0]
            keep = (bar >= start) & (bar < stop)
        else:
            keep = np.zeros(0, dtype=bool)
        moved = self.funding[keep].copy()
        if len(moved):
            moved[:, 0] -= start
        return PerpBundle(
            symbol=self.symbol,
            timeframe=self.timeframe,
            marks=self.marks.slice(start, stop),
            funding=moved,
            paths=self.paths[start:stop],
            brackets=self.brackets,
        )

    def followed_by(self, later: PerpBundle) -> PerpBundle:
        """Join a window to the one that comes after it, as one continuous bundle.

        The holdout evaluator reads a warm-up bundle from in-sample storage and the out-of-sample
        bundle from the holdout, and has to hand the backtest a single object. ``bar_ix`` is
        re-based onto the joined axis for the same reason :meth:`slice` renumbers it: an index
        that means one thing in its own bundle and another in a joined one charges funding to the
        wrong bar, silently.
        """
        if later.symbol != self.symbol or later.timeframe != self.timeframe:
            raise ValueError(
                f"only windows of the same contract join: {self.symbol} {self.timeframe} and "
                f"{later.symbol} {later.timeframe}"
            )
        if len(self.marks) and len(later.marks) and later.marks.ts[0] <= self.marks.ts[-1]:
            raise ValueError(
                f"{self.symbol}: the second window must come strictly after the first "
                f"({later.marks.ts[0]} is not after {self.marks.ts[-1]})"
            )
        offset = len(self.marks)
        moved = later.funding.copy()
        if len(moved):
            moved[:, 0] += offset
        return PerpBundle(
            symbol=self.symbol,
            timeframe=self.timeframe,
            marks=Bars(
                self.symbol,
                self.timeframe,
                np.concatenate([self.marks.ts, later.marks.ts]),
                *(
                    np.concatenate([getattr(self.marks, f), getattr(later.marks, f)])
                    for f in ("open", "high", "low", "close", "volume")
                ),
            ),
            funding=np.concatenate([self.funding, moved]) if len(moved) else self.funding,
            paths=self.paths + later.paths,
            brackets=self.brackets,
        )

    def aligned_with(self, trades: Bars) -> None:
        """Refuse trade bars this bundle does not line up with, bar for bar.

        Checked rather than assumed because the consequence is silent: a mark series one bar
        short marks every position at its neighbour's price for the rest of the run.
        """
        if trades.symbol != self.symbol or len(trades) != len(self.marks):
            raise ValueError(
                f"{self.symbol}: the perpetual bundle does not line up with the trade bars "
                f"({trades.symbol}, {len(trades)} bars vs {len(self.marks)})"
            )
        if len(trades) and not np.array_equal(trades.ts, self.marks.ts):
            raise ValueError(
                f"{self.symbol}: the perpetual bundle does not line up with the trade bars — "
                "same length, different close timestamps"
            )


# ── crossing the sandbox boundary ─────────────────────────────────────────────────────
#
# The container reads files, not objects, so a bundle is written as sidecars beside the trade
# bars. All four go to files, including the brackets: one mechanism is easier to verify than a
# parquet-and-job.json split, and `job.json` only records the names.
#
# The path table is long rather than nested — one row per breakpoint — because that is what
# parquet stores well and what reconstructs without a bespoke codec. `side` is 0 for the running
# minimum and 1 for the running maximum.

MARK_COLUMNS = ("ts", "open", "high", "low", "close", "volume")
PATH_COLUMNS = ("bar_ix", "segment_ix", "start_minute", "side", "minute", "price")


def _safe(symbol: str) -> str:
    """A filename stem for a ccxt perpetual symbol. The colon must go: on NTFS it opens an
    alternate data stream instead of failing (see ``data.store.file_name``)."""
    return symbol.replace("/", "-").replace(":", "-")


def write_bundle(directory: Path, bundle: PerpBundle) -> dict[str, str]:
    """Write one bundle's sidecars into ``directory``; returns role → file name."""
    import pandas as pd

    stem = f"{_safe(bundle.symbol)}_{bundle.timeframe}"
    names = {
        "mark": f"{stem}.mark.parquet",
        "funding": f"{_safe(bundle.symbol)}.funding.parquet",
        "paths": f"{stem}.paths.parquet",
        "brackets": f"{_safe(bundle.symbol)}.brackets.json",
    }
    directory.mkdir(parents=True, exist_ok=True)

    m = bundle.marks
    pd.DataFrame(
        {"ts": m.ts, "open": m.open, "high": m.high, "low": m.low, "close": m.close,
         "volume": m.volume}
    ).to_parquet(directory / names["mark"], index=False)  # fmt: skip

    pd.DataFrame(bundle.funding, columns=list(FUNDING_COLUMNS)).to_parquet(
        directory / names["funding"], index=False
    )

    rows: list[tuple[int, int, int, int, int, float]] = []
    for bar_ix, sp in enumerate(bundle.paths):
        for segment_ix, (start, summary) in enumerate(zip(sp.starts, sp.segments, strict=True)):
            for side, series in ((0, summary.lows), (1, summary.highs)):
                rows += [
                    (bar_ix, segment_ix, start, side, minute, price) for minute, price in series
                ]
    pd.DataFrame(rows, columns=list(PATH_COLUMNS)).to_parquet(
        directory / names["paths"], index=False
    )

    (directory / names["brackets"]).write_text(
        json.dumps([dict(b) for b in bundle.brackets], sort_keys=True), encoding="utf-8"
    )
    return names


def read_bundle(
    directory: Path, symbol: str, timeframe: str, names: Mapping[str, str]
) -> PerpBundle:
    """Read back what :func:`write_bundle` wrote, losslessly.

    Lossless matters concretely: a dropped segment boundary turns a level that moves mid-bar
    into one that does not, which changes which bar liquidates.
    """
    import pandas as pd

    frame = pd.read_parquet(directory / names["mark"])
    marks = Bars(
        symbol,
        timeframe,
        frame["ts"].to_numpy(dtype="datetime64[ns]"),
        *(frame[c].to_numpy(dtype=np.float64) for c in MARK_COLUMNS[1:]),
    )

    funding = pd.read_parquet(directory / names["funding"]).to_numpy(dtype=np.float64)
    if not funding.size:
        funding = np.zeros((0, len(FUNDING_COLUMNS)), dtype=np.float64)

    table = pd.read_parquet(directory / names["paths"])
    paths: list[SegmentedPath] = []
    for bar_ix in range(len(marks)):
        bar = table[table["bar_ix"] == bar_ix]
        starts: list[int] = []
        segments: list[PathSummary] = []
        for segment_ix in sorted(bar["segment_ix"].unique()):
            part = bar[bar["segment_ix"] == segment_ix]
            starts.append(int(part["start_minute"].iloc[0]))
            segments.append(
                PathSummary(
                    lows=_breakpoints(part[part["side"] == 0]),
                    highs=_breakpoints(part[part["side"] == 1]),
                )
            )
        paths.append(SegmentedPath(starts=tuple(starts), segments=tuple(segments)))

    brackets = json.loads((directory / names["brackets"]).read_text(encoding="utf-8"))
    return PerpBundle(
        symbol=symbol,
        timeframe=timeframe,
        marks=marks,
        funding=funding,
        paths=tuple(paths),
        brackets=brackets_of(brackets),
    )


def _breakpoints(part: object) -> tuple[tuple[int, float], ...]:
    rows = part.sort_values("minute")  # type: ignore[attr-defined]
    return tuple((int(m), float(p)) for m, p in zip(rows["minute"], rows["price"], strict=True))


PerpInputs = Mapping[str, PerpBundle]


def slice_inputs(inputs: PerpInputs, start: int, stop: int) -> dict[str, PerpBundle]:
    """Window every contract's bundle by the same bar range."""
    return {symbol: bundle.slice(start, stop) for symbol, bundle in inputs.items()}


def assert_aligned(inputs: PerpInputs, bars: Mapping[str, Bars]) -> None:
    """Every traded symbol has a bundle, and every bundle lines up with its trade bars."""
    missing = sorted(set(bars) - set(inputs))
    if missing:
        raise ValueError(
            f"no perpetual inputs for {', '.join(missing)}: refusing rather than treating the "
            "mark as the trade price and funding as zero (INV-94)"
        )
    for symbol, series in bars.items():
        inputs[symbol].aligned_with(series)


def brackets_of(rows: Sequence[Bracket]) -> tuple[Bracket, ...]:
    """Normalize a bracket table to the tuple the bundle stores, sorted by notional cap."""
    return tuple(sorted(rows, key=lambda r: float(r["cap"])))
