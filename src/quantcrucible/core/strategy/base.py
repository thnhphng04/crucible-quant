"""The strategy contract (Architecture §3.3, P3).

A strategy speaks only in relative units — :class:`Signal` — and knows nothing about lots, shares,
contracts or currency. Every per-bar evaluation goes through :func:`step`, the single entry point
shared by the signal generator, the dynamic leak gate ①b and the NautilusTrader bridge, so the
paths cannot diverge.

``signal()`` receives a :class:`FeatureView`: each feature is visible only up to the current bar,
so a strategy cannot read the future through its features (look-ahead is still possible inside
``indicators()``, which is what gates ①a/①b check).
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal, SupportsFloat

import numpy as np
import numpy.typing as npt

Direction = Literal["long", "short", "flat"]
FloatArray = npt.NDArray[np.float64]
Features = dict[str, FloatArray]


class LookAheadError(RuntimeError):
    """A strategy asked for a value after the current bar."""


@dataclass(frozen=True, slots=True)
class Signal:
    direction: Direction
    strength: float  # ∈ [0, 1] — degree of participation; NOT a quantity, NOT % of capital
    stop_distance: float  # price units (typically k × ATR); ignored when flat
    take_profit: float | None = None

    def __post_init__(self) -> None:
        if self.direction not in ("long", "short", "flat"):
            raise ValueError(f"direction must be long/short/flat, got {self.direction!r}")
        strength = float(self.strength)
        if not 0.0 <= strength <= 1.0:
            raise ValueError(f"strength must be in [0, 1], got {self.strength!r}")
        if self.direction == "flat":
            if strength != 0.0:
                raise ValueError("a flat signal must have strength 0")
        else:
            stop = float(self.stop_distance)
            if not (math.isfinite(stop) and stop > 0):
                raise ValueError(
                    f"stop_distance must be finite and > 0, got {self.stop_distance!r}"
                )
            if self.take_profit is not None:
                tp = float(self.take_profit)
                if not (math.isfinite(tp) and tp > 0):
                    raise ValueError(f"take_profit must be finite and > 0, got {tp!r}")
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "stop_distance", float(self.stop_distance))


FLAT = Signal("flat", 0.0, 0.0)


@dataclass(frozen=True, slots=True, eq=False)
class Bars:
    """OHLCV for one instrument, indexed by bar CLOSE time (UTC)."""

    symbol: str
    timeframe: str
    ts: npt.NDArray[np.datetime64]
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    volume: FloatArray

    def __post_init__(self) -> None:
        n = len(self.ts)
        for name in ("open", "high", "low", "close", "volume"):
            arr = getattr(self, name)
            if arr.ndim != 1 or len(arr) != n:
                raise ValueError(f"Bars.{name} must be 1-D with {n} values")
        if n > 1 and not bool(np.all(self.ts[1:] > self.ts[:-1])):
            raise ValueError("Bars.ts must be strictly increasing")

    def __len__(self) -> int:
        return len(self.ts)

    def slice(self, start: int, stop: int) -> Bars:
        return Bars(
            self.symbol,
            self.timeframe,
            self.ts[start:stop],
            self.open[start:stop],
            self.high[start:stop],
            self.low[start:stop],
            self.close[start:stop],
            self.volume[start:stop],
        )

    def window(self, end: int, lookback: int) -> Bars:
        """Bars ``[end - lookback + 1, end]`` — everything a strategy may see at bar ``end``."""
        if not 0 <= end < len(self):
            raise IndexError(f"bar {end} out of range")
        return self.slice(max(0, end - lookback + 1), end + 1)


def _value(x: object) -> float:
    return x.now if isinstance(x, SeriesView) else float(x)  # type: ignore[arg-type]


class SeriesView:
    """One feature seen at bar ``t``: arithmetic and comparisons act on the current value."""

    __slots__ = ("_t", "_values")

    def __init__(self, values: FloatArray, t: int) -> None:
        self._values = values
        self._t = t

    @property
    def now(self) -> float:
        return float(self._values[self._t])

    def ago(self, k: int) -> float:
        """Value ``k`` bars before the current one (``k >= 0``); NaN before the series starts."""
        if isinstance(k, bool) or not isinstance(k, int):
            raise TypeError("ago() takes an int")
        if k < 0:
            raise LookAheadError(f"ago({k}) would read the future")
        i = self._t - k
        return float(self._values[i]) if i >= 0 else math.nan

    def __float__(self) -> float:
        return self.now

    def __bool__(self) -> bool:
        raise TypeError("a feature has no truth value; compare it explicitly")

    def __repr__(self) -> str:
        return f"SeriesView(now={self.now!r})"

    def __add__(self, o: SupportsFloat | SeriesView) -> float:
        return self.now + _value(o)

    def __radd__(self, o: SupportsFloat) -> float:
        return _value(o) + self.now

    def __sub__(self, o: SupportsFloat | SeriesView) -> float:
        return self.now - _value(o)

    def __rsub__(self, o: SupportsFloat) -> float:
        return _value(o) - self.now

    def __mul__(self, o: SupportsFloat | SeriesView) -> float:
        return self.now * _value(o)

    def __rmul__(self, o: SupportsFloat) -> float:
        return _value(o) * self.now

    def __truediv__(self, o: SupportsFloat | SeriesView) -> float:
        return self.now / _value(o)

    def __rtruediv__(self, o: SupportsFloat) -> float:
        return _value(o) / self.now

    def __neg__(self) -> float:
        return -self.now

    def __abs__(self) -> float:
        return abs(self.now)

    def __lt__(self, o: SupportsFloat | SeriesView) -> bool:
        return self.now < _value(o)

    def __le__(self, o: SupportsFloat | SeriesView) -> bool:
        return self.now <= _value(o)

    def __gt__(self, o: SupportsFloat | SeriesView) -> bool:
        return self.now > _value(o)

    def __ge__(self, o: SupportsFloat | SeriesView) -> bool:
        return self.now >= _value(o)

    __hash__ = None  # type: ignore[assignment]


class FeatureView(Mapping[str, SeriesView]):
    """All features of a strategy, seen at bar ``t``."""

    def __init__(self, features: Features, t: int) -> None:
        self._features = features
        self._t = t

    def __getitem__(self, name: str) -> SeriesView:
        return SeriesView(self._features[name], self._t)

    def __iter__(self) -> Iterator[str]:
        return iter(self._features)

    def __len__(self) -> int:
        return len(self._features)


class Params:
    """Read-only attribute access to a strategy's parameters (``self.p.fast``)."""

    __slots__ = ("_values",)
    _values: dict[str, float]

    def __init__(self, values: Mapping[str, float]) -> None:
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, name: str) -> float:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(f"no parameter {name!r}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("strategy parameters are read-only")

    def as_dict(self) -> dict[str, float]:
        return dict(self._values)


class Strategy:
    """Base class for strategies. Venue-agnostic; subclasses implement the two methods."""

    def __init__(self, params: Mapping[str, float] | None = None) -> None:
        self.p = Params(params or {})

    def indicators(self, bars: Bars) -> Features:
        raise NotImplementedError

    def signal(self, x: FeatureView) -> Signal:
        raise NotImplementedError


def step(strategy: Strategy, window: Bars) -> Signal:
    """Evaluate ``strategy`` on the last bar of ``window`` — the ONLY per-bar entry point."""
    if len(window) == 0:
        raise ValueError("empty window")
    features = strategy.indicators(window)
    for name, arr in features.items():
        if np.shape(arr) != (len(window),):
            raise ValueError(f"feature {name!r} must have one value per bar")
    sig = strategy.signal(FeatureView(features, len(window) - 1))
    if not isinstance(sig, Signal):
        raise TypeError(f"signal() must return a Signal, got {type(sig).__name__}")
    return sig


def generate_signals(strategy: Strategy, bars: Bars, lookback: int) -> list[Signal]:
    """One signal per bar, each computed from the ``lookback`` bars ending at that bar."""
    return [step(strategy, bars.window(t, lookback)) for t in range(len(bars))]
