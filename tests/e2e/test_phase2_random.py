"""Engine C-random end to end (arch §3.1.11, P2-09) — INV-60: real containers, a real ledger,
a harness-test campaign with a small trial budget. Synthetic data only, never the root holdout/.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from quantcrucible.agent.compare import lock_protocol
from quantcrucible.agent.evolution.archive import trial_cells
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.run import evolve, run_quotas
from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import open_campaign, read_lock, sha256_file
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.holdout_split import carve
from quantcrucible.data.store import ResearchStore, parse_range
from quantcrucible.ledger.db import Ledger, LedgerError
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import G1A_STATIC
from quantcrucible.validation.research_run import ResearchSession
from quantcrucible.validation.run import derived_settings
from quantcrucible.validation.sandbox import SandboxRunner
from tests.factories import make_trending_bars

pytestmark = [pytest.mark.docker, pytest.mark.slow]

SYMBOLS = ("A/USDT", "B/USDT")
HOLDOUT = (date(2024, 2, 1), date(2025, 2, 1))
BUDGET = 6  # 2 seeds × 3 trials of C-random


class Phase2Random:
    def __init__(self, root: Path, image: str) -> None:
        cfg = parse_user_config({
            "research": {
                "data": {"symbols": list(SYMBOLS)},
                "engines": {"gp": 0.0, "random": 1.0},
                "seeds": 2,
                "campaign": {"purpose": "harness_test", "trial_budget": BUDGET},
                "pbo_grid": {"values_per_param": 3, "range": 0.3, "max_configs": 6},
                "holdout_pass": 0.5,
            }
        })  # fmt: skip
        full = {s: make_trending_bars(2_600, seed=11 + i, symbol=s, regime=100)
                for i, s in enumerate(SYMBOLS)}  # fmt: skip
        carve(
            full, HOLDOUT[0], HOLDOUT[1], in_sample_dir=root / "data" / "is",
            holdout_dir=root / "holdout", lock_path=root / "holdout.lock", harden=False,
        )  # fmt: skip
        self.ledger = Ledger.open(root / "crucible.db")
        lock_path = root / "config" / "evaluation.lock.yaml"
        open_campaign(
            cfg, self.ledger, "c-p2", lock_path, holdout_range=f"{HOLDOUT[0]}/{HOLDOUT[1]}",
            holdout_lock_hash=sha256_file(root / "holdout.lock"),
            derived=derived_settings("joint"),
        )  # fmt: skip
        store = ResearchStore(root / "data" / "is", [parse_range(f"{HOLDOUT[0]}/{HOLDOUT[1]}")])
        is_data: dict[str, Bars] = {s: store.bars(s, "1d") for s in SYMBOLS}
        self.session = ResearchSession(
            ledger=self.ledger, lock=read_lock(lock_path), campaign_id="c-p2", is_data=is_data,
            sandbox=SandboxRunner(image, label="e2e-p2"), results_dir=root / "results",
        )  # fmt: skip
        lock_protocol(self.ledger, "c-p2")  # a harness-test campaign needs it first (INV-70)
        self.first = evolve(self.session, ["random"], workers=4, run_label="e2e1")
        self.second = evolve(self.session, ["random"], workers=4, run_label="e2e2")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory, sandbox_image: str) -> Phase2Random:
    return Phase2Random(tmp_path_factory.mktemp("phase2r"), sandbox_image)


def test_every_candidate_has_ledger_rows(run: Phase2Random) -> None:
    """INV-60: each proposal went through submit → gates → ledger: a submission event and a
    gate-①a result at least; every trial carries its engine, seed and run."""
    events = run.ledger.events_for("c-p2", engine="random")
    submitted = [d["candidate_id"] for e, _i, d in events if e == Event.CANDIDATE_SUBMITTED and d]
    assert len(submitted) == sum(run.first.proposed.values())
    for cid in submitted:
        gates = [g for g, _passed, _reason in run.ledger.gate_results(cid)]
        assert gates and gates[0] == G1A_STATIC
    for t in run.ledger.trials("c-p2"):
        assert t.engine == "random" and t.seed in (0, 1) and t.source == "evolution"


def test_the_run_stops_exactly_at_its_quotas(run: Phase2Random) -> None:
    limits = run_quotas(run.session, ["random"])
    assert limits == {("random", 0): 3, ("random", 1): 3}
    for (engine, seed), limit in limits.items():
        n = len(run.ledger.trials("c-p2", engine=engine, seed=seed))
        assert n == limit or (engine, seed) in run.first.starved


def test_a_second_run_resumes_from_the_ledger(run: Phase2Random) -> None:
    """The quotas were used: the second call proposes nothing and adds no trial."""
    assert sum(run.second.proposed.values()) == 0
    assert len(run.ledger.trials("c-p2")) <= BUDGET


def test_the_search_covers_more_than_one_cell(run: Phase2Random) -> None:
    fm = FeatureMap.from_lock(run.session.lock)
    cells = {c for seed in (0, 1)
             for c in trial_cells(run.ledger, "c-p2", "random", seed, fm).values()}  # fmt: skip
    assert len(cells) > 1


def test_the_harness_test_campaign_cannot_freeze(run: Phase2Random) -> None:
    with pytest.raises(LedgerError, match="never frozen"):
        run.ledger.transition("c-p2", "FROZEN")
