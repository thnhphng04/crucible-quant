"""Monitoring and early stop (arch §3.2 IS→OOS curve, §3.1.11, P2-14)."""

from pathlib import Path

import pytest

from quantcrucible.agent.engines.random_search import Proposal, RandomSearch
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.monitor import (
    EarlyStop,
    already_stopped,
    checkpoints,
    engine_report,
    record_checkpoint,
    record_holder,
    stopped_keys,
    warned_keys,
)
from quantcrucible.agent.pipeline import Outcome, Pipeline
from quantcrucible.agent.scheduler import Key, TrialScheduler
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.run import FEATURE_MAP
from tests.agent.evolution.test_feature_map import _candidate

FM = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP}})


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    lg = Ledger.open(tmp_path / "l.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    return lg


def _trial(lg: Ledger, cid: str, is_sharpe: float, oos: list[float] | None) -> None:
    detail = {"pbo": 0.1, "cpcv_path_sharpes": oos} if oos is not None else None
    _candidate(lg, cid, "gp", 0, is_sharpe, ["trend"], g4_pass=True if oos else None,
               g4_detail=detail)  # fmt: skip


def test_the_record_holder_is_the_best_is_sharpe_that_reached_gate_4(ledger: Ledger) -> None:
    assert record_holder(ledger, "c1", "gp", 0) is None
    _trial(ledger, "a", 1.0, [0.2, 0.4, 0.6])
    _trial(ledger, "b", 3.0, None)  # never reached ④: not a record holder
    _trial(ledger, "c", 1.5, [0.1, 0.0, 0.3])
    assert record_holder(ledger, "c1", "gp", 0) == ("c", 1.5, 0.1)


def test_diverging_is_and_oos_stop_the_engine(ledger: Ledger) -> None:
    """IS record rising, its CPCV-OOS falling: the p-hacking signal of §3.2."""
    stop = EarlyStop(ledger, "c1", "run", every=1)
    verdicts = []
    for k, (is_sr, oos) in enumerate([(1.0, 0.8), (1.5, 0.5), (2.0, 0.2)]):
        _trial(ledger, f"x{k}", is_sr, [oos, oos, oos])
        verdicts.append(stop(("gp", 0), k + 1))
    assert verdicts == [False, False, True]
    assert [p.oos_median_sharpe for p in checkpoints(ledger, "c1", "gp", 0)] == [0.8, 0.5, 0.2]


def test_in_warn_mode_a_diverging_engine_keeps_running(ledger: Ledger) -> None:
    """ADR-0028: in a comparison campaign the stopping time must not depend on the result being
    measured, so divergence is recorded and the arm runs on to its quota."""
    warn = EarlyStop(ledger, "c1", "run", every=1, mode="warn")
    verdicts = []
    for k, (is_sr, oos) in enumerate([(1.0, 0.8), (1.5, 0.5), (2.0, 0.2), (2.5, 0.1)]):
        _trial(ledger, f"x{k}", is_sr, [oos, oos, oos])
        verdicts.append(warn(("gp", 0), k + 1))
    assert verdicts == [False, False, False, False]
    keys: list[Key] = [("gp", 0), ("random", 0)]
    assert warned_keys(ledger, "c1", keys) == {("gp", 0)}  # once, not once per checkpoint
    assert len(checkpoints(ledger, "c1", "gp", 0)) == 4


def test_is_and_oos_rising_together_do_not_stop(ledger: Ledger) -> None:
    stop = EarlyStop(ledger, "c1", "run", every=1)
    for k, (is_sr, oos) in enumerate([(1.0, 0.2), (1.5, 0.4), (2.0, 0.7)]):
        _trial(ledger, f"y{k}", is_sr, [oos])
        assert not stop(("gp", 0), k + 1)


def test_checkpoints_only_every_n_trials(ledger: Ledger) -> None:
    _trial(ledger, "a", 1.0, [0.3])
    stop = EarlyStop(ledger, "c1", "run", every=25)
    assert not stop(("gp", 0), 10)
    assert checkpoints(ledger, "c1", "gp", 0) == []
    stop(("gp", 0), 25)
    assert len(checkpoints(ledger, "c1", "gp", 0)) == 1
    assert record_checkpoint(ledger, "c1", "random", 0, "run") is None  # no trial yet


def test_a_stopped_engine_stays_stopped_after_a_restart(ledger: Ledger) -> None:
    """The stop decision lives in the ledger's checkpoints, not in the run that made it: a new
    run must not hand the engine one more trial before deciding again."""
    stop = EarlyStop(ledger, "c1", "run", every=1)
    for k, (is_sr, oos) in enumerate([(1.0, 0.8), (1.5, 0.5), (2.0, 0.2)]):
        _trial(ledger, f"x{k}", is_sr, [oos, oos, oos])
        stop(("gp", 0), k + 1)
    assert already_stopped(ledger, "c1", ("gp", 0))
    assert not already_stopped(ledger, "c1", ("random", 0))

    proposed: list[Key] = []

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        proposed.append(key)
        return Outcome(cid, True, True)

    limits = {("gp", 0): 5, ("random", 0): 5}
    stats = Pipeline(
        {k: RandomSearch(seed=i) for i, k in enumerate(limits)}, TrialScheduler(limits),
        evaluate, 1, "t2", stopped=stopped_keys(ledger, "c1", limits),
    ).run()  # fmt: skip
    assert ("gp", 0) not in proposed and stats.stopped == {("gp", 0)}
    assert stats.trials[("random", 0)] == 5


def test_engine_report(ledger: Ledger) -> None:
    _trial(ledger, "a", 1.0, [0.3])
    _trial(ledger, "b", 0.5, [0.1])
    _candidate(ledger, "c", "gp", 0, 2.0, ["breakout"], g4_pass=False)
    r = engine_report(ledger, "c1", "gp", 0, FM)
    assert (r["trials"], r["passed_gate4"]) == (3, 2)
    assert r["trial_efficiency"] == pytest.approx(200 / 3)
    assert r["archive_cells"] >= 1 and r["search_cells"] >= 2
    assert r["proposals_per_trial"] == 1.0 and r["diverging"] is False


def test_the_pipeline_stops_an_engine_its_monitor_flags() -> None:
    calls: list[Key] = []

    def monitor(key: Key, trials: int) -> bool:
        calls.append(key)
        return key == ("gp", 0) and trials >= 3

    limits = {("gp", 0): 20, ("random", 0): 5}
    engines = {k: RandomSearch(seed=i) for i, k in enumerate(limits)}
    stats = Pipeline(
        engines, TrialScheduler(limits),
        lambda k, p, c: Outcome(c, True, True), 1, "t", monitor=monitor,
    ).run()  # fmt: skip
    assert stats.stopped == {("gp", 0)}
    assert stats.trials[("gp", 0)] == 3 and stats.trials[("random", 0)] == 5
    assert isinstance(next(iter(engines.values())).next(), Proposal)
