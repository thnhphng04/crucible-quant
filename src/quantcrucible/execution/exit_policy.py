"""Versioned, deterministic exit rules shared by standalone and portfolio replays."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from quantcrucible.core.path_summary import SegmentedPath
from quantcrucible.core.strategy.base import ScopeDirection, Signal

ExitMode = Literal["legacy_flat", "bracket_timeout_v1"]
ExitReason = Literal["stop", "take_profit", "timeout", "liquidation"]


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    mode: ExitMode = "legacy_flat"
    tp_sl_ratio: float | None = None
    max_holding_bars: int | None = None

    def validate_signal(self, signal: Signal) -> None:
        if self.mode != "bracket_timeout_v1" or signal.direction == "flat":
            return
        if self.tp_sl_ratio is None or signal.take_profit is None:
            raise ValueError("bracket campaign requires take_profit on every entry signal")
        if not math.isclose(
            signal.take_profit, self.tp_sl_ratio * signal.stop_distance, rel_tol=1e-9
        ):
            raise ValueError("signal take_profit differs from the campaign's locked TP/SL ratio")

    @classmethod
    def from_lock(cls, lock: Mapping[str, Any]) -> ExitPolicy:
        derived = lock.get("derived", {})
        version = derived.get("exit_protocol")
        if version is None:
            return cls()
        if version != "bracket_timeout_v1":
            raise ValueError(f"unknown locked exit protocol {version!r}")
        values = lock["research"]["exit"]
        ratio, bars = float(values["tp_sl_ratio"]), int(values["max_holding_bars"])
        if not math.isfinite(ratio) or ratio <= 0 or bars <= 0:
            raise ValueError("invalid locked bracket exit policy")
        return cls("bracket_timeout_v1", ratio, bars)


@dataclass(frozen=True, slots=True)
class Bracket:
    side: ScopeDirection
    stop: float
    take_profit: float

    def accepts_entry(self, entry: float) -> bool:
        if not all(math.isfinite(v) and v > 0 for v in (entry, self.stop, self.take_profit)):
            return False
        return (
            self.stop < entry < self.take_profit
            if self.side == "long"
            else self.take_profit < entry < self.stop
        )

    def stop_fill(self, bar_open: float) -> float:
        return min(self.stop, bar_open) if self.side == "long" else max(self.stop, bar_open)


def bracket_at_signal(side: ScopeDirection, close: float, signal: Signal) -> Bracket:
    if signal.direction != side or signal.take_profit is None:
        raise ValueError("bracket entry requires a matching signal with take_profit")
    d, tp = signal.stop_distance, signal.take_profit
    if not all(math.isfinite(v) and v > 0 for v in (close, d, tp)):
        raise ValueError("bracket prices require finite positive signal values")
    if side == "long":
        return Bracket(side, close - d, close + tp)
    return Bracket(side, close + d, close - tp)


def resolve_ohlc(
    bracket: Bracket, bar_open: float, high: float, low: float
) -> tuple[ExitReason | None, bool]:
    """Use the worse touch when a spot OHLC bar hides intrabar ordering."""
    stop = low <= bracket.stop if bracket.side == "long" else high >= bracket.stop
    tp = high > bracket.take_profit if bracket.side == "long" else low < bracket.take_profit
    if stop:
        return "stop", tp
    return ("take_profit", False) if tp else (None, False)


def resolve_paths(
    bracket: Bracket,
    trade: SegmentedPath,
    mark: SegmentedPath,
    liquidations: list[float],
) -> tuple[ExitReason | None, int | None, bool]:
    """Earliest minute wins; same-minute uncertainty is settled pessimistically."""
    above_stop = bracket.side == "short"
    touches: list[tuple[int, int, ExitReason]] = []
    stop = trade.first_touch(bracket.stop, above=above_stop)
    # A take-profit limit needs trade-through, not a mere touch at its quoted price.
    through = math.nextafter(bracket.take_profit, math.inf if bracket.side == "long" else -math.inf)
    tp = trade.first_touch(through, above=not above_stop)
    liquidation = mark.first_touch_stepwise(liquidations, above=above_stop)
    events: tuple[tuple[int | None, int, ExitReason], ...] = (
        (liquidation, 0, "liquidation"),
        (stop, 1, "stop"),
        (tp, 2, "take_profit"),
    )
    for minute, priority, reason in events:
        if minute is not None:
            touches.append((minute, priority, reason))
    if not touches:
        return None, None, False
    touches.sort()
    first = touches[0]
    return first[2], first[0], len(touches) > 1 and touches[1][0] == first[0]


def expires(entry_bar: int, current_bar: int, max_holding_bars: int) -> bool:
    """The entry bar is bar one; exit is sent after the Nth bar has closed."""
    return current_bar - entry_bar + 1 >= max_holding_bars
