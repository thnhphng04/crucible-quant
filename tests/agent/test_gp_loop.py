"""Engine C-gp driven by the pipeline for ≥ 150 generations (arch §7 phase-2 gate, P2-13).

The gates are fakes with deterministic results derived from each candidate's code and values,
but everything else is real: GatePipeline, the ledger (one connection per slot), the scheduler,
the engine rebuilding its archive, islands and migrations from the ledger at every proposal.
The docker run with the real gates is tests/e2e/test_phase2.py.
"""

from __future__ import annotations

import hashlib
import threading
from collections import Counter

import pytest

from quantcrucible.agent.engines.gp_search import GpSearch
from quantcrucible.agent.engines.random_search import Proposal
from quantcrucible.agent.evolution.archive import trial_cells
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.islands import island_names, migrations
from quantcrucible.agent.grammar import categories, genome_to_dict, signature
from quantcrucible.agent.pipeline import Outcome, Pipeline
from quantcrucible.agent.scheduler import Key, TrialScheduler
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import (
    G1A_STATIC,
    G3_IS,
    G4_PBO,
    GateContext,
    GatePipeline,
    GateResult,
    StrategyCandidate,
    TrialMeasurement,
)
from quantcrucible.validation.run import FEATURE_MAP

pytestmark = pytest.mark.slow

GENERATIONS = 150
ISLANDS = len(island_names())
LOCK = {"derived": {"feature_map": FEATURE_MAP}}


def _u(c: StrategyCandidate, salt: str) -> float:
    """A deterministic number in [0, 1) from the candidate's code and values."""
    key = f"{salt}|{c.source}|{sorted(c.params.items())}".encode()
    return int(hashlib.sha256(key).hexdigest()[:12], 16) / 16**12


class Static:
    id, cost = G1A_STATIC, 1

    def check(self, c: StrategyCandidate, ctx: GateContext) -> GateResult:
        return GateResult(True, self.id, 0.0, "ok")


class InSample:
    id, cost = G3_IS, 3

    def check(self, c: StrategyCandidate, ctx: GateContext) -> GateResult:
        sharpe = 3.5 * _u(c, "s") - 1.0
        public = {
            "sharpe_is": sharpe, "sortino_is": sharpe * 1.3, "max_drawdown": _u(c, "d"),
            "total_return": 4.0 * _u(c, "r") - 0.5, "n_trades": 30 + 400 * _u(c, "n"),
            "sr_obs": sharpe / 19.1, "skew_is": 0.0, "kurtosis_is": 3.0, "n_obs": 2000.0,
        }  # fmt: skip
        return GateResult(
            sharpe > 0, self.id, sharpe, "fake ③", detail={"public": public},
            measurement=TrialMeasurement(sharpe, "fake.parquet"),
        )  # fmt: skip


class Pbo:
    id, cost = G4_PBO, 4

    def check(self, c: StrategyCandidate, ctx: GateContext) -> GateResult:
        public = {"spp_median_sharpe": 2.0 * _u(c, "m"), "plateau": _u(c, "p")}
        return GateResult(
            _u(c, "pbo") < 0.7, self.id, _u(c, "pbo"), "fake ④",
            detail={"public": public, "pbo": _u(c, "pbo")},
        )  # fmt: skip


def _evaluator(path: str) -> object:
    local = threading.local()
    pipeline = GatePipeline([Static(), InSample(), Pbo()])

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        lg = getattr(local, "lg", None) or Ledger.open(path)
        local.lg = lg
        engine, seed = key
        cand = StrategyCandidate(
            candidate_id=cid, source=p.source, params=p.params, universe=("A/USDT",),
            timeframe="1d", timerange="2018-01-01/2024-01-01", run_id="loop", campaign_id="c1",
            engine=engine, seed=seed, island=p.island, parents=p.parents, mutation=p.mutation,
            trial_source="evolution", agent="engine",
            descriptors={"categories": list(categories(p.genome)),
                         "signature": list(signature(p.genome)),
                         "genome": genome_to_dict(p.genome)},
        )  # fmt: skip
        out = pipeline.run(cand, GateContext(lg, {}))
        return Outcome(cid, out.trial_id is not None, out.passed, out.failed_gate)

    return evaluate


@pytest.fixture(scope="module")
def loop(tmp_path_factory: pytest.TempPathFactory) -> tuple[Ledger, GpSearch]:
    path = tmp_path_factory.mktemp("gp") / "ledger.db"
    main = Ledger.open(path)
    main.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    gp = GpSearch(main, "c1", 0, 7, FeatureMap.from_lock(LOCK), 365.0, 0.30, "loop")
    budget = GENERATIONS * ISLANDS
    Pipeline(
        {("gp", 0): gp}, TrialScheduler({("gp", 0): budget}), _evaluator(main.path),  # type: ignore[arg-type]
        workers=2, run_label="loop",
    ).run()  # fmt: skip
    return main, gp


def test_at_least_150_generations_run_unattended(loop: tuple[Ledger, GpSearch]) -> None:
    _, gp = loop
    assert gp.proposals // ISLANDS >= GENERATIONS


def test_every_s_new_reaches_the_ledger(loop: tuple[Ledger, GpSearch]) -> None:
    ledger, gp = loop
    submitted = [e for e, _i, _d in ledger.events_for("c1", engine="gp", seed=0)
                 if e == Event.CANDIDATE_SUBMITTED]  # fmt: skip
    assert len(submitted) == gp.proposals == len(ledger.trials("c1", engine="gp", seed=0))


def test_the_feature_map_does_not_collapse(loop: tuple[Ledger, GpSearch]) -> None:
    ledger, _ = loop
    cells = Counter(trial_cells(ledger, "c1", "gp", 0, FeatureMap.from_lock(LOCK)).values())
    assert len(cells) > 50
    assert max(cells.values()) / sum(cells.values()) < 0.2


def test_evolution_actually_happens_on_every_island(loop: tuple[Ledger, GpSearch]) -> None:
    ledger, _ = loop
    islands = {t.island for t in ledger.trials("c1", engine="gp", seed=0)}
    assert islands == set(island_names())
    kinds = Counter(
        d.get("mutation") for e, _i, d in ledger.events_for("c1", engine="gp", seed=0)
        if e == Event.CANDIDATE_SUBMITTED and d
    )  # fmt: skip
    assert {"param", "point", "subtree", "crossover"} <= set(kinds)
    bred = sum(v for k, v in kinds.items() if k)
    assert kinds["param"] <= 0.30 * bred + 1  # INV-66 over the run
    assert len(migrations(ledger, "c1", "gp", 0)) >= GENERATIONS // 10 - 1


def test_a_restarted_engine_resumes_from_the_ledger(loop: tuple[Ledger, GpSearch]) -> None:
    ledger, gp = loop
    again = GpSearch(ledger, "c1", 0, 7, FeatureMap.from_lock(LOCK), 365.0, 0.30, "loop2")
    assert (again.proposals, again.children, again.param_only) == (
        gp.proposals, gp.children, gp.param_only,
    )  # fmt: skip
    p = again.next()
    assert (p.source, tuple(sorted(p.params.items()))) not in {
        k for k in gp.seen
    }  # never re-proposes a candidate from before the restart
