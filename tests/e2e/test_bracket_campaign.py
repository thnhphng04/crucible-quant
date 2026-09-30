"""A bracket campaign opened from user.yaml, end to end (P3-12, P3-25, ADR-0033): each locked symbol
is its own scope and every candidate is measured on that one instrument.

The lock generated from user.yaml names `symbols`, never `instruments`. Read as the legacy
whole-basket scope, a multi-symbol bracket campaign handed five instruments to a replay that takes
exactly one, and every candidate failed gate ③ without a trial — found on a real campaign, not by
any test, because every earlier bracket e2e run had one symbol. Synthetic data only, never the
root holdout/.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("numba")

from quantcrucible.agent.compare import lock_protocol
from quantcrucible.agent.run import evolve
from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import open_campaign, read_lock, sha256_file
from quantcrucible.config.schema import Compute
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.holdout_split import carve
from quantcrucible.data.store import ResearchStore, parse_range
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.engine_router import make_job_runner
from quantcrucible.validation.numerics import numerics_tag
from quantcrucible.validation.research_run import ResearchSession
from quantcrucible.validation.run import derived_settings
from quantcrucible.validation.sandbox import SandboxRunner
from tests.factories import make_trending_bars

pytestmark = [pytest.mark.docker, pytest.mark.slow]

SYMBOLS = ("A/USDT", "B/USDT")
HOLDOUT = (date(2024, 2, 1), date(2025, 2, 1))
CAMPAIGN = "c-bracket"
BUDGET = 8  # 2 symbols x long x 2 engines x 1 seed x 2 trials


@pytest.fixture(scope="module")
def ledger(tmp_path_factory: pytest.TempPathFactory, sandbox_image: str) -> Ledger:
    root = tmp_path_factory.mktemp("bracket")
    cfg = parse_user_config({
        "research": {
            "data": {"symbols": list(SYMBOLS)},
            "seeds": 1,
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
    ledger_path = root / "crucible.db"
    lg = Ledger.open(ledger_path)
    lock_path = root / "config" / "evaluation.lock.yaml"
    derived = derived_settings("joint")  # what validation.run.current_campaign writes
    derived["exit_protocol"] = "bracket_timeout_v1"
    derived["backtest_numerics"] = numerics_tag("float64")
    open_campaign(
        cfg, lg, CAMPAIGN, lock_path, holdout_range=f"{HOLDOUT[0]}/{HOLDOUT[1]}",
        holdout_lock_hash=sha256_file(root / "holdout.lock"), derived=derived,
    )  # fmt: skip
    lock = read_lock(lock_path)
    assert "instruments" not in lock["research"]["data"]  # the shape a real lock has
    store = ResearchStore(root / "data" / "is", [parse_range(f"{HOLDOUT[0]}/{HOLDOUT[1]}")])
    is_data: dict[str, Bars] = {s: store.bars(s, "1d") for s in SYMBOLS}
    box = SandboxRunner(sandbox_image, label="e2e-bracket")
    runner = make_job_runner(box, lock, Compute("cpu_kernel", 0.02), ledger_path, "e2e")
    session = ResearchSession(
        ledger=lg, lock=lock, campaign_id=CAMPAIGN, is_data=is_data, sandbox=runner,
        results_dir=root / "results",
    )  # fmt: skip
    lock_protocol(lg, CAMPAIGN)
    evolve(session, ["gp", "random"], workers=4, run_label="e2e")
    return lg


def test_every_scope_gets_trials_on_its_own_instrument(ledger: Ledger) -> None:
    trials = ledger.trials(CAMPAIGN)
    assert len(trials) == BUDGET
    assert {t.instrument for t in trials} == set(SYMBOLS)
    for t in trials:
        assert t.direction == "long"  # spot is long-only
        assert t.universe == t.instrument


def test_no_candidate_is_handed_the_whole_basket(ledger: Ledger) -> None:
    reasons = [
        str((detail or {}).get("reason", ""))
        for event, _island, detail in ledger.events_for(CAMPAIGN)
        if event == Event.GATE_REJECT
    ]
    assert not any("exactly one scoped instrument" in r for r in reasons), reasons
