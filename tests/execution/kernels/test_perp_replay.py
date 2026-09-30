"""Perpetual replay kernel K4 reproduces ``replay_signals`` for one slot bit for bit (P3-50,
INV-105): equity, every open and close (bar, quantity, price, reason, leverage), funding paid,
liquidations, denials and ambiguous bars — on synthetic 4h bundles with minute paths, mid-bar
settlements and a tiered leverage table (``tests/perp_fixtures.py``)."""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_arrays import pack_bundle
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import FLAT, Bars, Signal
from quantcrucible.execution.exit_policy import ExitPolicy
from quantcrucible.execution.joint_account import SignalReplay, SlotPlan, replay_signals
from quantcrucible.execution.kernels.features import KernelInputError
from quantcrucible.execution.kernels.perp_replay import REASONS, PerpReplay, run_perp_replay
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings
from tests.perp_fixtures import MINUTES, perp_market

RATIO = 1.1
COSTS = CostModel()


@dataclasses.dataclass(frozen=True)
class Case:
    seed: int
    side: str
    p: float  # signal probability per bar
    lo: float  # stop distance range, as a fraction of the close
    hi: float
    pct: float  # max_risk_pct
    lev: int
    maxh: int
    cash: float = 100_000.0
    mark_noise: float = 0.002


CASES = [
    Case(1, "long", 0.15, 0.004, 0.02, 0.01, 5, 12),
    Case(2, "short", 0.15, 0.004, 0.02, 0.01, 5, 12),
    Case(3, "long", 0.3, 0.002, 0.008, 0.05, 20, 6, mark_noise=0.008),  # liquidations
    Case(4, "short", 0.3, 0.002, 0.008, 0.05, 20, 6, mark_noise=0.008),
    Case(5, "long", 0.4, 0.001, 0.01, 0.2, 50, 2),  # risk cap denials, one-bar holds
    Case(6, "short", 0.2, 0.003, 0.03, 0.03, 3, 30, cash=2_000.0),  # small balance
    Case(7, "long", 0.25, 0.0005, 0.004, 0.02, 10, 4, mark_noise=0.004),
    Case(8, "long", 0.3, 0.08, 0.15, 0.01, 5, 2),  # wide brackets: timeouts
    Case(9, "short", 0.3, 0.08, 0.15, 0.01, 5, 3),
    # a small balance forces the highest leverage, so the mark path can reach liquidation
    Case(10, "long", 0.5, 0.004, 0.006, 0.1, 50, 3, cash=2_000.0, mark_noise=0.03),
    Case(11, "short", 0.5, 0.004, 0.006, 0.1, 50, 3, cash=2_000.0, mark_noise=0.03),
]


def _signals(bars: Bars, case: Case, m: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(case.seed * 31 + m)
    n = len(bars)
    entry = (rng.random(n) < case.p).astype(np.int8)
    stop = np.where(entry == 1, bars.close * rng.uniform(case.lo, case.hi, n), 0.0)
    return entry, stop


def _oracle(bars: Bars, bundle: PerpBundle, entry: Any, stop: Any, case: Case) -> SignalReplay:
    sigs = tuple(
        Signal(case.side, 1.0, float(s), RATIO * float(s)) if e == 1 else FLAT  # type: ignore[arg-type]
        for e, s in zip(entry, stop, strict=True)
    )
    return replay_signals(
        [SlotPlan(bars.symbol, case.side, sigs)],  # type: ignore[arg-type]
        {bars.symbol: bars}, {bars.symbol: bundle}, case.cash, RiskSettings(case.pct), COSTS,
        leverage=case.lev, exit_policy=ExitPolicy("bracket_timeout_v1", RATIO, case.maxh),
    )  # fmt: skip


def _kernel(bars: Bars, bundle: PerpBundle, entry: Any, stop: Any, case: Case) -> PerpReplay:
    return run_perp_replay(
        bars.open, bars.close, pack_bundle(bundle, bars), entry, stop,
        direction=1 if case.side == "long" else -1, tp_sl_ratio=RATIO,
        fee=float(COSTS.taker_rate), max_risk_pct=case.pct, initial_cash=case.cash,
        leverage=case.lev, max_holding_bars=case.maxh, trade_log=True,
    )  # fmt: skip


def _assert_same(got: PerpReplay, c: int, want: SignalReplay, where: str) -> None:
    assert got.equity[c].tobytes() == want.equity.tobytes(), f"{where}: equity"
    assert got.ilog is not None and got.flog is not None
    k = int(got.logged[c])
    opened = [
        (int(got.ilog[c, i, 0]), float(got.flog[c, i, 0]), float(got.flog[c, i, 1]),
         float(got.flog[c, i, 3]), int(got.ilog[c, i, 3]))
        for i in range(k)
    ]  # fmt: skip
    assert opened == [(o.bar, o.qty, o.price, o.stop, o.leverage) for o in want.opened], where
    closed = [
        (int(got.ilog[c, i, 1]), float(got.flog[c, i, 0]), float(got.flog[c, i, 2]),
         REASONS[int(got.ilog[c, i, 2])])
        for i in range(k) if got.ilog[c, i, 1] >= 0
    ]  # fmt: skip
    assert closed == [(x.bar, x.qty, x.price, x.reason) for x in want.closed], where
    assert int(got.trades[c]) == len(want.closed)
    assert int(got.denied[c]) == len(want.denied), f"{where}: denied"
    assert int(got.ambiguous[c]) == want.ambiguous_bars, f"{where}: ambiguous"
    assert int(got.liquidations[c]) == len(want.liquidations), f"{where}: liquidations"
    paid = sum(want.funding_paid.values())
    assert np.float64(got.funding_paid[c]).tobytes() == np.float64(paid).tobytes(), where
    holding = [x.bar - o.bar + (0 if x.reason == "timeout" else 1)
               for x, o in zip(want.closed, want.opened, strict=False)]  # fmt: skip
    assert int(got.hold_sum[c]) == sum(holding)


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"seed{c.seed}-{c.side}")
def test_the_kernel_reproduces_the_one_slot_replay(case: Case) -> None:
    bars, bundle = perp_market(160, seed=case.seed, mark_noise=case.mark_noise)
    configs = [_signals(bars, case, m) for m in range(4)]
    entry = np.stack([e for e, _ in configs], axis=1)
    stop = np.stack([s for _, s in configs], axis=1)
    got = _kernel(bars, bundle, entry, stop, case)
    for m, (e, s) in enumerate(configs):
        _assert_same(got, m, _oracle(bars, bundle, e, s, case), f"{case} config {m}")


def test_the_cases_exercise_every_exit_and_denial() -> None:
    reasons: set[str] = set()
    denied = ambiguous = funded = 0
    for case in CASES:
        bars, bundle = perp_market(160, seed=case.seed, mark_noise=case.mark_noise)
        for m in range(4):
            e, s = _signals(bars, case, m)
            want = _oracle(bars, bundle, e, s, case)
            reasons |= {x.reason for x in want.closed}
            denied += len(want.denied)
            ambiguous += want.ambiguous_bars
            funded += any(v != 0 for v in want.funding_paid.values())
    assert reasons == {"stop", "take_profit", "timeout", "liquidation"}
    assert denied and ambiguous and funded


def test_an_inconsistent_bar_with_a_position_open_fails_like_the_replay() -> None:
    case = CASES[0]
    bars, bundle = perp_market(12, seed=9)
    assert bundle.trade_paths is not None
    flat = [20_000.0] * MINUTES
    uncut = segment_bar(list(range(MINUTES)), flat, flat, [])
    skewed = dataclasses.replace(
        bundle, trade_paths=(*bundle.trade_paths[:3], uncut, *bundle.trade_paths[4:])
    )
    entry = np.zeros((len(bars), 1), dtype=np.int8)
    entry[1] = 1  # enters at bar 2's open, so it is open at bar 3
    stop = np.where(entry == 1, bars.close[:, None] * 0.02, 0.0)
    with pytest.raises(ValueError, match="funding cuts"):
        _oracle(bars, skewed, entry[:, 0], stop[:, 0], case)
    with pytest.raises(KernelInputError, match="disagree"):
        _kernel(bars, skewed, entry, stop, case)
    flat_entry = np.zeros_like(entry)  # never open there: both run through
    _assert_same(
        _kernel(bars, skewed, flat_entry, stop * 0, case), 0,
        _oracle(bars, skewed, flat_entry[:, 0], stop[:, 0] * 0, case), "flat",
    )  # fmt: skip
