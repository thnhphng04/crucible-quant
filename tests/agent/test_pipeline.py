"""Evaluation pipeline (arch §3.1.8, ADR-0024, P2-08) — INV-61 under the pipeline, INV-69."""

import random
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from quantcrucible.agent.engines.random_search import Proposal, RandomSearch
from quantcrucible.agent.pipeline import Outcome, Pipeline
from quantcrucible.agent.scheduler import Key, TrialScheduler
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent, TrialRecord


def _engines(keys: list[Key]) -> dict[Key, RandomSearch]:
    return {k: RandomSearch(seed=k[1] + 100 * i) for i, k in enumerate(keys)}


def test_quotas_are_used_exactly_under_concurrent_slots() -> None:
    limits = {("gp", 0): 12, ("random", 0): 9, ("random", 1): 7}
    lock = threading.Lock()
    measured: Counter[Key] = Counter()

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        time.sleep(random.random() / 200)
        ok = random.random() < 0.5
        with lock:
            measured[key] += ok
        return Outcome(cid, ok, ok)

    pipe = Pipeline(_engines(list(limits)), TrialScheduler(limits), evaluate, 4, "t")
    stats = pipe.run()
    assert dict(measured) == limits == stats.trials
    assert not stats.starved


def test_the_slots_are_shared_fairly_between_engines_and_seeds() -> None:
    """Every (engine, seed) advances together, so an interrupted run still compares like with
    like: a fixed scan order would spend the whole run on the first key (ADR-0027)."""
    order: list[tuple[int, Key]] = []
    lock = threading.Lock()

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        with lock:
            order.append((int(cid.rsplit("-", 1)[1]), key))
        time.sleep(random.random() / 200)
        return Outcome(cid, True, True)

    limits = {("gp", 0): 30, ("gp", 1): 30, ("random", 0): 30, ("random", 1): 30}
    workers = 4
    Pipeline(_engines(list(limits)), TrialScheduler(limits), evaluate, workers, "t").run()
    dispatched: Counter[Key] = Counter()
    for _n, key in sorted(order):
        dispatched[key] += 1
        assert max(dispatched.values()) - min(dispatched[k] for k in limits) <= workers + 1
    assert dict(dispatched) == limits


def test_an_engine_whose_candidates_never_reach_gate_3_is_stopped() -> None:
    """No infinite loop: a (engine, seed) that only produces pre-③ failures is marked starved."""
    pipe = Pipeline(
        _engines([("random", 0)]), TrialScheduler({("random", 0): 5}),
        lambda k, p, c: Outcome(c, False, False, "g1a_static"), 2, "t", max_attempts_per_trial=10,
    )  # fmt: skip
    stats = pipe.run()
    assert stats.starved == {("random", 0)} and stats.proposed[("random", 0)] >= 10


def test_an_error_aborts_and_kills_first() -> None:
    aborted: list[bool] = []

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        raise RuntimeError("sandbox exploded")

    pipe = Pipeline(
        _engines([("random", 0)]), TrialScheduler({("random", 0): 5}), evaluate, 2, "t",
        on_abort=lambda: aborted.append(True),
    )  # fmt: skip
    with pytest.raises(RuntimeError, match="exploded"):
        pipe.run()
    assert aborted == [True]


def test_candidate_ids_are_unique_and_carry_engine_and_seed() -> None:
    seen: list[str] = []
    lock = threading.Lock()

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        with lock:
            seen.append(cid)
        return Outcome(cid, True, True)

    limits = {("gp", 0): 10, ("random", 2): 10}
    Pipeline(_engines(list(limits)), TrialScheduler(limits), evaluate, 3, "run7").run()
    assert len(seen) == len(set(seen)) == 20
    assert all(c.startswith(("run7-gp-s0-", "run7-random-s2-")) for c in seen)


def test_concurrent_slots_write_the_ledger_without_lock_errors(tmp_path: Path) -> None:
    """Each slot opens its own connection to the same ledger (WAL + busy_timeout)."""
    main = Ledger.open(tmp_path / "ledger.db")
    main.open_campaign("c1", "2027-01-01/2028-01-01", lock_hash="h")
    local = threading.local()

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        lg = getattr(local, "lg", None) or Ledger.open(main.path)
        local.lg = lg
        engine, seed = key
        lg.log_event(GenerationEvent(run_id="r", campaign_id="c1", engine=engine, seed=seed,
                                     agent="engine", model_used="none",
                                     event=Event.CANDIDATE_SUBMITTED))  # fmt: skip
        lg.record_trial(TrialRecord(
            run_id="r", campaign_id="c1", candidate_id=cid, engine=engine, seed=seed,
            strategy_hash="h", params=p.params, universe="BTC/USDT", timeframe="1d",
            timerange="2018-01-01/2024-01-01", source="evolution", sharpe_is=0.1,
            returns_path="x", verdict="PASS",
        ))  # fmt: skip
        return Outcome(cid, True, True)

    limits = {("gp", 0): 40, ("random", 0): 40}
    Pipeline(_engines(list(limits)), TrialScheduler(limits), evaluate, 6, "t").run()
    assert len(main.trials("c1", engine="gp")) == 40
    assert len(main.trials("c1", engine="random")) == 40


SLEEPER = """
import time
time.sleep(120)
"""


@pytest.mark.docker
def test_interrupt_kills_containers(sandbox_image: str) -> None:
    """INV-69: `kill_all` (what the pipeline calls on an interrupt) leaves no container of the
    run behind — Docker Desktop keeps a container alive when its host process dies."""
    from quantcrucible.validation.sandbox import RUN_LABEL, SandboxJob, SandboxRunner
    from tests.factories import make_bars

    label = f"test-{random.randrange(10**9)}"
    runner = SandboxRunner(sandbox_image, label=label)
    job = SandboxJob("signals", SLEEPER, {"A/USDT": make_bars(50, symbol="A/USDT")},
                     timeout_s=300)  # fmt: skip
    thread = threading.Thread(target=runner.run, args=(job,), daemon=True)
    thread.start()

    def running() -> list[str]:
        out = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label={RUN_LABEL}={label}"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        return out.stdout.split()

    deadline = time.monotonic() + 90
    while not running() and time.monotonic() < deadline:
        time.sleep(0.5)
    assert running(), "the sandbox container never started"
    assert runner.kill_all() >= 1
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert running() == []
