from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import (
    Event,
    GenerationEvent,
    PortfolioVariant,
    TrialRecord,
)
from quantcrucible.review.app import create_app
from quantcrucible.review.repository import (
    SUPPORTED_SCHEMA,
    ReviewDataError,
    ReviewRepository,
)


def fixture_root(tmp_path: Path) -> Path:
    (tmp_path / "ledger").mkdir()
    with Ledger.open(tmp_path / "ledger" / "crucible.db") as ledger:
        ledger.open_campaign(
            "c-ui", "2025-01-01/2026-01-01", "lock", purpose="harness_test", trial_budget=6
        )
        ledger.log_event(
            GenerationEvent(
                run_id="r",
                campaign_id="c-ui",
                engine="compare",
                seed=0,
                agent="human",
                model_used="none",
                event=Event.PROTOCOL_LOCKED,
                detail={"protocol": {"version": 3}, "sha256": "p"},
            )
        )
        ledger.log_event(
            GenerationEvent(
                run_id="r",
                campaign_id="c-ui",
                engine="gp",
                seed=0,
                agent="engine",
                model_used="none",
                event=Event.CANDIDATE_SUBMITTED,
                strategy_hash="abc",
                detail={"candidate_id": "gp-0", "parents": []},
            )
        )
        ledger.record_trial(
            TrialRecord(
                run_id="r",
                campaign_id="c-ui",
                candidate_id="gp-0",
                engine="gp",
                seed=0,
                strategy_hash="abc",
                params={},
                universe="BTC/USDT",
                timeframe="1d",
                timerange="2020/2025",
                source="evolution",
                sharpe_is=1.2,
                returns_path="missing.parquet",
                verdict="PASS",
                ts=datetime.now(UTC),
            )
        )
    return tmp_path


def test_all_review_routes_are_get_only_and_do_not_change_the_ledger(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    path = root / "ledger" / "crucible.db"
    before = path.read_bytes()
    repository = ReviewRepository(root)
    repository.campaigns()
    repository.overview("c-ui")
    repository.comparison("c-ui")
    repository.candidates("c-ui", {}, 1, 50)
    repository.candidate("c-ui", "gp-0")
    repository.portfolios("c-ui")
    repository.archive("c-ui")
    repository.events("c-ui", 1, 50, None)
    repository.lock("c-ui")
    api_routes = [
        r for r in create_app(root).routes if isinstance(r, APIRoute) and r.path.startswith("/api/")
    ]
    assert api_routes and all(set(r.methods or ()) <= {"GET", "HEAD"} for r in api_routes)
    assert path.read_bytes() == before


def test_every_route_answers_over_http(tmp_path: Path) -> None:
    """The repository being correct is not enough: a wiring mistake in the app made every
    data route demand a query parameter, and only an HTTP call sees that."""
    root = fixture_root(tmp_path)
    with TestClient(create_app(root)) as client:
        for path in [
            "/api/health",
            "/api/campaigns",
            "/api/campaigns/c-ui/overview",
            "/api/campaigns/c-ui/comparison",
            "/api/campaigns/c-ui/candidates",
            "/api/campaigns/c-ui/candidates?engine=gp&seed=0&q=gp",
            "/api/campaigns/c-ui/candidates/gp-0",
            "/api/campaigns/c-ui/portfolios",
            "/api/campaigns/c-ui/archive",
            "/api/campaigns/c-ui/events",
            "/api/campaigns/c-ui/events?event=PROTOCOL_LOCKED",
            "/api/campaigns/c-ui/lock",
        ]:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert response.json() is not None


def test_a_missing_campaign_is_a_404_not_a_server_error(tmp_path: Path) -> None:
    with TestClient(create_app(fixture_root(tmp_path))) as client:
        response = client.get("/api/campaigns/c-nope/overview")
        assert response.status_code == 404
        assert "c-nope" in response.json()["detail"]
        assert client.get("/api/campaigns/c-ui/unknown-route").status_code == 404


def test_incomplete_equal_budget_comparison_is_not_called_a_winner(tmp_path: Path) -> None:
    overview = ReviewRepository(fixture_root(tmp_path)).overview("c-ui")
    assert overview["verdict"]["label"] == "Chưa kết luận"
    assert "1/6 trial" in overview["verdict"]["reason"]


def test_events_list_their_own_names_so_the_filter_needs_no_typing(tmp_path: Path) -> None:
    repository = ReviewRepository(fixture_root(tmp_path))
    data = repository.events("c-ui", 1, 50, None)
    assert data["names"] == ["CANDIDATE_SUBMITTED", "PROTOCOL_LOCKED"]
    filtered = repository.events("c-ui", 1, 50, "PROTOCOL_LOCKED")
    assert filtered["total"] == 1
    assert filtered["names"] == ["CANDIDATE_SUBMITTED", "PROTOCOL_LOCKED"]


def test_candidate_pagination_is_stable(tmp_path: Path) -> None:
    data = ReviewRepository(fixture_root(tmp_path)).candidates("c-ui", {}, 1, 50)
    assert data["total"] == 1 and data["items"][0]["candidate_id"] == "gp-0"


def test_portfolio_staleness_counts_only_this_campaign(tmp_path: Path) -> None:
    """The snapshot a variant was built from is per campaign, so a second campaign's
    trials must not mark it stale (a cross-campaign COUNT(*) would)."""
    root = fixture_root(tmp_path)
    with Ledger.open(root / "ledger" / "crucible.db") as ledger:
        ledger.record_portfolio_variant(
            PortfolioVariant(
                portfolio_hash="p" * 64,
                campaign_id="c-ui",
                rule_config={"snapshot": {"trials": 1}},
                members=[{"candidate_id": "gp-0"}],
                sharpe_is=1.2,
            )
        )
        ledger.open_campaign("c-other", "2025-01-01/2026-01-01", "lock2")
        ledger.record_trial(
            TrialRecord(
                run_id="r",
                campaign_id="c-other",
                candidate_id="other-0",
                engine="random",
                seed=0,
                strategy_hash="def",
                params={},
                universe="BTC/USDT",
                timeframe="1d",
                timerange="2020/2025",
                source="evolution",
                sharpe_is=0.1,
                returns_path="missing.parquet",
                verdict="PASS",
                ts=datetime.now(UTC),
            )
        )
    data = ReviewRepository(root).portfolios("c-ui")
    assert data["ledger_trials"] == 1
    assert data["items"][0]["stale"] is False


def test_artifacts_outside_results_are_refused(tmp_path: Path) -> None:
    """A `returns_path` or a strategy hash comes out of the database; neither may be able
    to address a file outside `results/` (INV-80)."""
    root = fixture_root(tmp_path)
    secret = tmp_path / "outside.py"
    secret.write_text("# must never be served", encoding="utf-8")
    (root / "results" / "strategies").mkdir(parents=True)
    (root / "results" / "strategies" / "abc.py").write_text("# in place", encoding="utf-8")

    with Ledger.open(root / "ledger" / "crucible.db") as ledger:
        ledger.record_trial(
            TrialRecord(
                run_id="r",
                campaign_id="c-ui",
                candidate_id="escape-0",
                engine="gp",
                seed=0,
                strategy_hash="../../outside",
                params={},
                universe="BTC/USDT",
                timeframe="1d",
                timerange="2020/2025",
                source="evolution",
                sharpe_is=0.5,
                returns_path=str(tmp_path / "outside.parquet"),
                verdict="PASS",
                ts=datetime.now(UTC),
            )
        )
    repository = ReviewRepository(root)
    escaping = repository.candidate("c-ui", "escape-0")
    assert escaping["source"]["status"] == "missing"
    assert escaping["source"]["text"] is None
    assert escaping["measurements"][0]["curve"]["status"] == "missing"

    # The same reader does serve a hash that really is inside results/strategies.
    assert repository.candidate("c-ui", "gp-0")["source"]["status"] == "ok"


def test_an_unsupported_schema_is_refused_instead_of_migrated(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    path = root / "ledger" / "crucible.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {SUPPORTED_SCHEMA + 1}")
    before = path.read_bytes()

    with pytest.raises(ReviewDataError, match=f"v{SUPPORTED_SCHEMA + 1}"):
        ReviewRepository(root).campaigns()
    assert path.read_bytes() == before
