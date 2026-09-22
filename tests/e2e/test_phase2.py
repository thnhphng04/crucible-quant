"""Phase-2 gate, the docker half (arch §7 v0.6, P2-13): C-gp and C-random side by side through
the real gates, containers and ledger, in a harness-test campaign. The ≥ 150-generation run is
tests/agent/test_gp_loop.py (fake gates, same pipeline); here a small budget proves that the real
path works end to end for both arms. Synthetic data only, never the root holdout/.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from quantcrucible.agent.evolution.islands import island_names
from quantcrucible.agent.run import evolve
from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import open_campaign, read_lock, sha256_file
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.holdout_split import carve
from quantcrucible.data.store import ResearchStore, parse_range
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.research_run import ResearchSession
from quantcrucible.validation.run import derived_settings
from quantcrucible.validation.sandbox import SandboxRunner
from tests.factories import make_trending_bars

pytestmark = [pytest.mark.docker, pytest.mark.slow]

SYMBOLS = ("A/USDT", "B/USDT")
HOLDOUT = (date(2024, 2, 1), date(2025, 2, 1))
BUDGET = 12  # 6 trials per engine, one seed


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory, sandbox_image: str) -> Ledger:
    root = tmp_path_factory.mktemp("phase2")
    cfg = parse_user_config({
        "research": {
            "data": {"symbols": list(SYMBOLS)},
            "engines": {"gp": 0.5, "random": 0.5},
            "seeds": 1,
            "campaign": {"purpose": "harness_test", "trial_budget": BUDGET},
            "pbo_grid": {"values_per_param": 3, "range": 0.3, "max_configs": 6},
            "holdout_pass": 0.5,
        }
    })  # fmt: skip
    full = {s: make_trending_bars(2_600, seed=21 + i, symbol=s, regime=100)
            for i, s in enumerate(SYMBOLS)}  # fmt: skip
    carve(
        full, HOLDOUT[0], HOLDOUT[1], in_sample_dir=root / "data" / "is",
        holdout_dir=root / "holdout", lock_path=root / "holdout.lock", harden=False,
    )  # fmt: skip
    ledger = Ledger.open(root / "crucible.db")
    lock_path = root / "config" / "evaluation.lock.yaml"
    open_campaign(
        cfg, ledger, "c-p2", lock_path, holdout_range=f"{HOLDOUT[0]}/{HOLDOUT[1]}",
        holdout_lock_hash=sha256_file(root / "holdout.lock"), derived=derived_settings("joint"),
    )  # fmt: skip
    store = ResearchStore(root / "data" / "is", [parse_range(f"{HOLDOUT[0]}/{HOLDOUT[1]}")])
    is_data: dict[str, Bars] = {s: store.bars(s, "1d") for s in SYMBOLS}
    session = ResearchSession(
        ledger=ledger, lock=read_lock(lock_path), campaign_id="c-p2", is_data=is_data,
        sandbox=SandboxRunner(sandbox_image, label="e2e-p2gp"), results_dir=root / "results",
    )  # fmt: skip
    evolve(session, ["gp", "random"], workers=4, run_label="e2e")
    return ledger


def test_both_arms_reach_their_quotas_through_the_real_gates(run: Ledger) -> None:
    for engine in ("gp", "random"):
        assert len(run.trials("c-p2", engine=engine, seed=0)) == BUDGET // 2


def test_gp_candidates_carry_island_genome_and_lineage(run: Ledger) -> None:
    subs = [(i, d) for e, i, d in run.events_for("c-p2", engine="gp", seed=0)
            if e == Event.CANDIDATE_SUBMITTED and d]  # fmt: skip
    assert subs and all(i in island_names() for i, _d in subs)
    assert all("genome" in d["descriptors"] and "signature" in d["descriptors"] for _i, d in subs)
    kinds = Counter(d.get("mutation") for _i, d in subs)
    assert None in kinds  # islands are seeded before anything is bred
