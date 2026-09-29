"""The gate-③/④ backtest report, built from results by pure functions (P3-34, ADR-0038).

The sandbox job runner and the host kernel engine both report through these functions, so the
report a gate reads cannot depend on which engine produced it. The module runs inside the sandbox
container too: it imports nothing the sandbox runner may not (§3.3.3), and the execution layer
only for types.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy.typing as npt

from quantcrucible.core.strategy.base import Signal
from quantcrucible.validation.feature_stats import max_abs_change_corr

if TYPE_CHECKING:
    from quantcrucible.execution.engine import BacktestResult


def timestamps(ts: npt.NDArray[Any]) -> list[str]:
    return [str(t) for t in ts]


def indicator_corr(
    features: Iterable[Mapping[str, npt.ArrayLike]],
) -> tuple[float, tuple[str, str] | None]:
    """The largest |ρ| between two features' changes over every symbol's feature set."""
    corr, pair = 0.0, None
    for symbol_features in features:
        rho, names = max_abs_change_corr(symbol_features)
        if rho > corr:
            corr, pair = rho, names
    return corr, pair


def signals_stream(signals: Mapping[str, Sequence[Signal]]) -> dict[str, list[list[Any]]]:
    """What a portfolio replay sizes from later (ADR-0035), per symbol and bar."""
    return {
        symbol: [[s.direction, s.strength, s.stop_distance, s.take_profit] for s in sigs]
        for symbol, sigs in signals.items()
    }


def backtest_report(
    res: BacktestResult,
    corr: float,
    pair: tuple[str, str] | None,
    stream: dict[str, list[list[Any]]],
) -> dict[str, Any]:
    """The gate-③ report of one backtest."""
    return {
        "public": res.public_metrics(),
        "indicator_corr": corr,
        "indicator_pair": list(pair) if pair else None,
        "ts": timestamps(res.ts),
        "equity": res.equity.tolist(),
        "returns": res.returns.tolist(),
        "n_fills": len(res.fills),
        "denied_orders": res.denied_orders,
        "signal_counts": dict(res.signals),
        "periods_per_year": res.periods_per_year,
        # Perpetual reporting (P3-21). Zero and empty on the spot path, which models neither.
        "funding_paid": res.funding_paid,
        "liquidated": list(res.liquidated),
        "terminated_at": str(res.terminated_at) if res.terminated_at is not None else None,
        "stops_placed": [[s.ts, s.symbol, s.direction, s.trigger, s.qty] for s in res.stops_placed],
        "exits": [[e.bar, e.symbol, e.side, e.price, e.reason] for e in res.exits],
        "ambiguous_bars": res.ambiguous_bars,
        # The stream a portfolio replay needs later: it sizes from signals, not from these fills
        # (ADR-0035), so gate ③ is where it has to be captured.
        "signals_stream": stream,
    }


@dataclass(frozen=True, slots=True)
class GridRow:
    """One configuration of gate ④'s pre-registered set."""

    returns: list[float]
    n_trades: int
    avg_holding_bars: float
    funding_paid: float
    # A liquidation ends that configuration, it never raises: one grid cell must not take the
    # other 199 with it (ADR-0032 decision ⑤).
    terminated_at: str | None


def grid_row(res: BacktestResult) -> GridRow:
    return GridRow(
        res.returns.tolist(),
        res.n_trades,
        res.avg_holding_bars,
        res.funding_paid,
        str(res.terminated_at) if res.terminated_at is not None else None,
    )


def grid_report(ts: list[str], rows: Sequence[GridRow]) -> dict[str, Any]:
    """The gate-④ report: the (n_configs × n_obs) return matrix on the shared bar-close axis."""
    return {
        "ts": ts,
        "returns": [r.returns for r in rows],
        "n_trades": [r.n_trades for r in rows],
        "avg_holding_bars": [r.avg_holding_bars for r in rows],
        "funding_paid": [r.funding_paid for r in rows],
        "terminated_at": [r.terminated_at for r in rows],
    }
