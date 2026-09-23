"""One evolve run per campaign (INV-72)."""

from pathlib import Path

import pytest

from quantcrucible.agent.compare import lock_protocol
from quantcrucible.agent.run import EvolveError, evolve
from quantcrucible.agent.runlock import RunLockError, campaign_run_lock, lock_path
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.research_run import ResearchSession
from tests.agent.test_compare import LOCK


def test_a_second_run_of_the_same_campaign_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "ledger" / "crucible.db"
    with campaign_run_lock(db, "c1", "run-a") as held:
        assert held == lock_path(db, "c1")
        with (
            pytest.raises(RunLockError, match="already being evolved"),
            campaign_run_lock(db, "c1", "run-b"),
        ):
            pass
        with campaign_run_lock(db, "c2", "run-c"):  # another campaign is free to run
            pass
    with campaign_run_lock(db, "c1", "run-d"):  # released with the block
        pass


def test_evolve_refuses_while_another_run_holds_the_campaign(tmp_path: Path) -> None:
    """Two evolve runs each start their scheduler from the ledger, so both would spend the same
    quota: the second is refused rather than allowed to overshoot N."""
    lg = Ledger.open(tmp_path / "l.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h", purpose="harness_test",
                     trial_budget=6)  # fmt: skip
    lock_protocol(lg, "c1")
    session = ResearchSession(
        ledger=lg, lock=LOCK, campaign_id="c1", is_data={},
        sandbox=None, results_dir=tmp_path,  # type: ignore[arg-type]
    )  # fmt: skip
    with (
        campaign_run_lock(lg.path, "c1", "other"),
        pytest.raises(EvolveError, match="already being evolved"),
    ):
        evolve(session, ["random"])


def test_the_lock_is_released_when_the_run_fails(tmp_path: Path) -> None:
    db = tmp_path / "crucible.db"
    with pytest.raises(ZeroDivisionError), campaign_run_lock(db, "c1", "run-a"):
        _ = 1 / 0
    with campaign_run_lock(db, "c1", "run-b"):
        pass
