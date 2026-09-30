"""The kernel backtest engine: features → signals → replay, and the results gates read
(P3-41, ADR-0038).

:class:`KernelBackend` runs one job — one genome, one instrument, one or many configurations — on
``cpu`` (njit) or ``cuda``. For gate ③ it returns the :class:`BacktestResult` the Python replay
would have produced: fills, exits, stops and turnover are rebuilt from the kernel's trade log with
the engine's own helpers, in the replay's append order, so every derived number matches. For
gate ④ it returns the return rows, trade counts and mean holding per configuration.

:func:`self_test` is the start-up check (audit L0): the CUDA build must reproduce the CPU build on
a fixture genome bit for bit before a process trusts the device. :func:`cpu_check` holds the CPU
build to its pinned result on the same fixture (the holdout's preflight). Nothing here loads
source: the CPU kernels are proved against the Python oracle by the test suite, not at runtime.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from quantcrucible.core.perp_arrays import PerpArrays
from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.genome import (
    Combine,
    Cross,
    Genome,
    Indicator,
    Param,
    Slope,
    Threshold,
    named_params,
)
from quantcrucible.execution.engine import (
    BacktestResult,
    ExitRecord,
    _bracket_turnover,
    _periods_per_year,
    _ratio_returns,
)
from quantcrucible.execution.kernels._numba import Dtype, Target
from quantcrucible.execution.kernels.features import run_features
from quantcrucible.execution.kernels.perp_replay import REASONS as PERP_REASONS
from quantcrucible.execution.kernels.perp_replay import PerpReplay, run_perp_replay
from quantcrucible.execution.kernels.program import Program, compile_program
from quantcrucible.execution.kernels.replay import (
    R_STOP,
    R_TIMEOUT,
    REASONS,
    SpotReplay,
    run_spot_replay,
)
from quantcrucible.execution.kernels.signals import run_signals
from quantcrucible.execution.nautilus_bridge import FillRecord, StopPlacement


@dataclass(frozen=True, slots=True)
class ReplaySpec:
    """The locked account and exit settings of a spot bracket job."""

    fee: float  # float(CostModel.taker_rate)
    lot_step: float
    max_risk_pct: float
    initial_cash: float
    max_holding_bars: int
    tp_sl_ratio: float


@dataclass(frozen=True, slots=True)
class GridResult:
    """Gate ④: one row per configuration, on the axis of ``bars.ts[1:]``."""

    returns: npt.NDArray[np.float64]  # [configs, bars - 1]
    n_trades: list[int]
    avg_holding_bars: list[float]
    funding_paid: list[float] | None = None  # perpetual only
    terminated_at: list[int | None] | None = None  # perpetual: the bar of the first liquidation


@dataclass(frozen=True, slots=True)
class PerpSpec:
    """What a perpetual job adds to :class:`ReplaySpec` (``lot_step`` is unused there)."""

    leverage: int
    max_portfolio_risk_pct: float = 0.10  # replay_signals' defaults, as _run_bracket calls it
    clearance: float = 0.25


@dataclass(frozen=True, slots=True)
class Signals:
    entry: npt.NDArray[np.int8]  # [bars, configs]
    stop: npt.NDArray[Any]  # [bars, configs], 0 where flat


class KernelBackend:
    """``target`` places the features and signals; the replay runs on the CPU either way.

    Stage timings on the development laptop (180-config grid, 67k bars): features 0.017 s on
    CUDA vs 0.092 s on CPU, signals 0.035 vs 0.077 s, but the replay — one sequential walk per
    configuration, which a GPU does poorly — 0.132 s on CUDA vs 0.023 s on CPU. The CUDA replay
    kernel stays available and tested; the engine does not route to it (E2).
    """

    def __init__(self, target: Target, dtype: Dtype = "float64", replay: Target = "cpu") -> None:
        self.target: Target = target
        self.dtype: Dtype = dtype
        self.replay_target: Target = replay

    def signals(self, prog: Program, bars: Bars) -> Signals:
        now, prev = run_features(
            prog.inst_op, prog.inst_period, bars.close, bars.high, bars.low, prog.lookback,
            self.target, self.dtype,
        )  # fmt: skip
        entry, stop = run_signals(prog, now, prev, self.target, self.dtype)
        return Signals(entry, stop)

    def _replay(self, prog: Program, bars: Bars, sig: Signals, spec: ReplaySpec, log: bool) -> Any:
        return run_spot_replay(
            bars.open, bars.high, bars.low, bars.close, sig.entry, sig.stop,
            direction=prog.direction, tp_sl_ratio=spec.tp_sl_ratio, fee=spec.fee,
            lot_step=spec.lot_step, max_risk_pct=spec.max_risk_pct,
            initial_cash=spec.initial_cash, max_holding_bars=spec.max_holding_bars,
            trade_log=log, target=self.replay_target,
        )  # fmt: skip

    def backtest(
        self, prog: Program, bars: Bars, spec: ReplaySpec, sig: Signals | None = None
    ) -> BacktestResult:
        """Gate ③: the one configuration's full result (``sig``: signals already computed)."""
        if prog.n_configs != 1:
            raise ValueError("a single backtest takes exactly one configuration")
        sig = sig if sig is not None else self.signals(prog, bars)
        out = self._replay(prog, bars, sig, spec, log=True)
        return _result(bars, prog, sig, out, spec)

    def grid(
        self, prog: Program, bars: Bars, spec: ReplaySpec, sig: Signals | None = None
    ) -> GridResult:
        sig = sig if sig is not None else self.signals(prog, bars)
        out = self._replay(prog, bars, sig, spec, log=False)
        returns = _ratio_returns_rows(out.equity)
        return GridResult(returns, [int(t) for t in out.trades], _avg_holding(out))

    # ── perpetuals (P3-51): the same signals, the one-slot USDT-M replay K4 ──────────────────
    def _perp_replay(self, prog: Program, bars: Bars, perp: PerpArrays, sig: Signals,
                     spec: ReplaySpec, pspec: PerpSpec, log: bool) -> PerpReplay:  # fmt: skip
        return run_perp_replay(
            bars.open, bars.close, perp, sig.entry, sig.stop, direction=prog.direction,
            tp_sl_ratio=spec.tp_sl_ratio, fee=spec.fee, max_risk_pct=spec.max_risk_pct,
            initial_cash=spec.initial_cash, leverage=pspec.leverage,
            max_holding_bars=spec.max_holding_bars, trade_log=log,
            max_portfolio_risk_pct=pspec.max_portfolio_risk_pct, clearance=pspec.clearance,
        )  # fmt: skip

    def perp_backtest(
        self, prog: Program, bars: Bars, perp: PerpArrays, spec: ReplaySpec, pspec: PerpSpec,
        sig: Signals | None = None,
    ) -> BacktestResult:  # fmt: skip
        """Gate ③ on a perpetual: what ``_run_bracket`` returns from ``replay_signals``."""
        if prog.n_configs != 1:
            raise ValueError("a single backtest takes exactly one configuration")
        sig = sig if sig is not None else self.signals(prog, bars)
        out = self._perp_replay(prog, bars, perp, sig, spec, pspec, log=True)
        return _perp_result(bars, prog, sig, out, spec)

    def perp_grid(
        self, prog: Program, bars: Bars, perp: PerpArrays, spec: ReplaySpec, pspec: PerpSpec,
        sig: Signals | None = None,
    ) -> GridResult:  # fmt: skip
        sig = sig if sig is not None else self.signals(prog, bars)
        out = self._perp_replay(prog, bars, perp, sig, spec, pspec, log=False)
        first = [int(b) if b >= 0 else None for b in out.first_liquidation]
        return GridResult(
            _ratio_returns_rows(out.equity), [int(t) for t in out.trades], _avg_holding(out),
            [float(0 + f) for f in out.funding_paid], first,
        )  # fmt: skip


def _avg_holding(out: SpotReplay | PerpReplay) -> list[float]:
    """``np.mean`` of the integer holding list: exact, so the integer sum over the count."""
    return [float(h) / int(t) if t else 0.0 for h, t in zip(out.hold_sum, out.trades, strict=True)]


def _ratio_returns_rows(equity: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """``engine._ratio_returns`` for every row at once: the same element-wise division and
    subtraction, so each row is bit-identical to calling it per configuration."""
    if equity.shape[1] < 2:
        return np.zeros((equity.shape[0], 0))
    previous = equity[:, :-1]
    out = np.zeros_like(previous)
    alive = previous > 0
    # in place with where=: no gather/scatter of the masked elements (was 0.16 s per 180 rows)
    np.divide(equity[:, 1:], previous, out=out, where=alive)
    np.subtract(out, 1.0, out=out, where=alive)
    return out


def _ts(bars: Bars, i: int) -> int:
    return int(bars.ts[i].astype("datetime64[ns]").astype("int64"))


def _result(bars: Bars, prog: Program, sig: Signals, out: Any, spec: ReplaySpec) -> BacktestResult:
    """Rebuild what ``_run_spot_bracket`` records, in the order it appends it."""
    symbol, fee = bars.symbol, spec.fee
    fills: list[FillRecord] = []
    stops: list[StopPlacement] = []
    exits: list[ExitRecord] = []
    holding: list[int] = []
    for k in range(int(out.logged[0])):
        entry_bar, exit_bar, reason = (int(v) for v in out.ilog[0, k])
        qty, entry_px, exit_px, stop = (float(v) for v in out.flog[0, k])
        notional = qty * entry_px
        fills.append(FillRecord(_ts(bars, entry_bar), symbol, "BUY", qty, entry_px, notional * fee))
        stops.append(StopPlacement(_ts(bars, entry_bar), symbol, "long", stop, qty))
        if exit_bar < 0:
            continue  # still open at the last bar
        proceeds = qty * exit_px
        fills.append(
            FillRecord(
                _ts(bars, exit_bar), symbol, "SELL", qty, exit_px, proceeds * fee,
                from_stop=reason == R_STOP,
            )
        )  # fmt: skip
        exits.append(ExitRecord(exit_bar, symbol, "long", exit_px, REASONS[reason]))
        holding.append(exit_bar - entry_bar + (0 if reason == R_TIMEOUT else 1))
    equity = out.equity[0]
    ppy = _periods_per_year(bars.timeframe)
    entries = int((sig.entry[:, 0] == 1).sum())
    side = "long" if prog.direction == 1 else "short"
    counts = {"long": 0, "short": 0, "flat": len(bars) - entries}
    counts[side] = entries
    return BacktestResult(
        ts=bars.ts,
        equity=equity,
        returns=_ratio_returns(equity),
        fills=tuple(fills),
        n_trades=len(exits),
        avg_holding_bars=float(np.mean(holding)) if holding else 0.0,
        turnover=_bracket_turnover(fills, equity, ppy),
        signals=counts,
        denied_orders=int(out.denied[0]),
        periods_per_year=ppy,
        stops_placed=tuple(stops),
        exits=tuple(exits),
        ambiguous_bars=int(out.ambiguous[0]),
    )


def _perp_result(
    bars: Bars, prog: Program, sig: Signals, out: PerpReplay, spec: ReplaySpec
) -> BacktestResult:
    """Rebuild what ``engine._run_bracket`` makes of a one-slot ``replay_signals``: every open's
    fill and stop, then every close's fill, sorted by time (stably), in that append order."""
    assert out.ilog is not None and out.flog is not None
    symbol, fee = bars.symbol, spec.fee
    side = "long" if prog.direction == 1 else "short"
    entries = int((sig.entry[:, 0] == 1).sum())
    if entries == 0:
        side = "long"  # _run_bracket's default when the strategy never enters
    buy, sell = ("BUY", "SELL") if side == "long" else ("SELL", "BUY")
    leg = "LONG" if side == "long" else "SHORT"
    fills: list[FillRecord] = []
    stops: list[StopPlacement] = []
    exits: list[ExitRecord] = []
    holding: list[int] = []
    k_all = int(out.logged[0])
    for k in range(k_all):
        ts = _ts(bars, int(out.ilog[0, k, 0]))
        qty, price, stop = (float(out.flog[0, k, j]) for j in (0, 1, 3))
        fills.append(FillRecord(ts, symbol, buy, qty, price, qty * price * fee, leg))
        stops.append(StopPlacement(ts, symbol, side, stop, qty))
    for k in range(k_all):
        bar = int(out.ilog[0, k, 1])
        if bar < 0:
            continue
        reason = PERP_REASONS[int(out.ilog[0, k, 2])]
        qty, price = float(out.flog[0, k, 0]), float(out.flog[0, k, 2])
        commission = 0.0 if reason == "liquidation" else qty * price * fee
        fills.append(
            FillRecord(_ts(bars, bar), symbol, sell, qty, price, commission, leg, reason == "stop")
        )
        exits.append(ExitRecord(bar, symbol, side, price, reason))
        holding.append(bar - int(out.ilog[0, k, 0]) + (0 if reason == "timeout" else 1))
    equity = out.equity[0]
    ppy = _periods_per_year(bars.timeframe)
    counts = {"long": 0, "short": 0, "flat": len(bars) - entries}
    counts["long" if prog.direction == 1 else "short"] = entries
    first = int(out.first_liquidation[0])
    return BacktestResult(
        ts=bars.ts,
        equity=equity,
        returns=_ratio_returns_rows(equity[None, :])[0],
        fills=tuple(sorted(fills, key=lambda f: f.ts)),
        n_trades=len(exits),
        avg_holding_bars=float(np.mean(holding)) if holding else 0.0,
        turnover=_bracket_turnover(fills, equity, ppy),
        signals=counts,
        denied_orders=int(out.denied[0]),
        periods_per_year=ppy,
        stops_placed=tuple(stops),
        funding_paid=0 + float(out.funding_paid[0]),
        # _run_bracket unpacks each liquidated (instrument, side) slot and keeps the instrument
        liquidated=(symbol,) if int(out.liquidations[0]) else (),
        terminated_at=bars.ts[first] if first >= 0 else None,
        exits=tuple(exits),
        ambiguous_bars=int(out.ambiguous[0]),
    )


# ── start-up self-test (audit L0) ─────────────────────────────────────────────────────────────
def _fixture() -> tuple[Genome, Bars]:
    def n(v: int) -> Param:
        return Param("period", 2, 300, v)

    genome = Genome(
        Combine(
            "or",
            (
                Cross(Indicator("ema", n(12)), True, Indicator("sma", n(40))),
                Threshold(
                    Indicator("rsi", Param("period", 2, 100, 14)), ">", Param("level", 50, 90, 62.5)
                ),
                Slope(Indicator("zscore", Param("period", 5, 300, 30)), True),
            ),
        ),
        Param("mult", 0.5, 5.0, 2.0),
        stop_kind="bollinger",
    )  # fmt: skip
    rng = np.random.default_rng(20260930)
    size = 2_000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, size)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, size)))
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, size + 1) * np.timedelta64(1, "h")
    return genome, Bars("BTC/USDT", "1h", ts, open_, high, low, close, np.ones(size))


def _fixture_configs(genome: Genome) -> list[dict[str, Any]]:
    base: dict[str, Any] = {name: p.value for name, p in named_params(genome)}
    return [base, {**base, "n1": 20}, {**base, "k_stop": 3.0}]


def _fixture_job() -> tuple[Program, Bars, ReplaySpec]:
    """The fixture as the campaign defaults would run it: fee + slippage 0.0015, BTC's lot."""
    genome, bars = _fixture()
    prog = compile_program(genome, "long", 1.1, _fixture_configs(genome), 300)
    return prog, bars, ReplaySpec(0.0015, 1e-8, 0.01, 100_000.0, 100, 1.1)


# sha256 of the fixture grid's return matrix, as the oracle-verified CPU build computes it: in
# float64 it is also what run_backtest gives (tests/execution/kernels/test_backend.py).
FIXTURE_DIGESTS = {
    "float64": "2081b8f05f04a87132cabe81088f63a026111948dbcfdec8a5f31a2bdeee7ac8",
    "float32": "fa6fd70f031cfbedc7a3d2957f802c4bccba5d53aad03d75c04437600221a9e4",
}


def fixture_digest(dtype: Dtype) -> str:
    prog, bars, spec = _fixture_job()
    grid = KernelBackend("cpu", dtype).grid(prog, bars, spec)
    return hashlib.sha256(np.ascontiguousarray(grid.returns).tobytes()).hexdigest()


def cpu_check(dtype: Dtype = "float64") -> bool:
    """True when the CPU build — the fallback nothing else backs in float32 — compiles, runs
    and reproduces its pinned result on the fixture job. The holdout asks before it claims."""
    return fixture_digest(dtype) == FIXTURE_DIGESTS[dtype]


def self_test(dtype: Dtype = "float64") -> bool:
    """True when the CUDA build reproduces the CPU build bit for bit on the fixture job."""
    prog, bars, spec = _fixture_job()
    cpu = KernelBackend("cpu", dtype).grid(prog, bars, spec)
    gpu = KernelBackend("cuda", dtype).grid(prog, bars, spec)
    return (
        np.array_equal(cpu.returns, gpu.returns)
        and cpu.n_trades == gpu.n_trades
        and cpu.avg_holding_bars == gpu.avg_holding_bars
        and sum(cpu.n_trades) > 0
    )
