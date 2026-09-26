"""Account equity and its per-slot decomposition, read-only (P3-23, ADR-0035) — INV-80, INV-92b.

The promise this screen makes is arithmetic: **starting equity plus the summed contributions
equals account equity**, at every bar, including while positions are still open. A chart that does
not add up is not a decomposition — it is an invented subaccount, and it would be read as if the
slots were separately funded when they were competing for one balance.

So the endpoint serves the reconciliation *as data*, not as a claim: the residual is computed from
what was stored and returned alongside it, so the UI can show it rather than assert it.

Path confinement is the other half (INV-80). A `portfolio_hash` comes out of the database, so it
must not be able to address a file outside `results/`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from tests.review.test_review_api import fixture_root

from quantcrucible.review.app import create_app
from quantcrucible.review.repository import ReviewDataError, ReviewRepository
from quantcrucible.validation.portfolio import write_account_curve

HASH = "a" * 64
SLOTS = (("BTC/USDT:USDT", "long"), ("ETH/USDT:USDT", "short"))


def curve(root: Path, *, residual: float = 0.0) -> Path:
    """Three bars, two slots, reconciling exactly — or off by `residual` on the last bar."""
    ts = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
    long_c = np.array([0.0, 100.0, 250.0])
    short_c = np.array([0.0, -40.0, 10.0])
    equity = 100_000.0 + long_c + short_c
    equity[-1] += residual
    return write_account_curve(
        root / "results",
        "c-ui",
        HASH,
        ts=ts.to_numpy(),
        equity=equity,
        initial_cash=100_000.0,
        contributions={SLOTS[0]: long_c, SLOTS[1]: short_c},
    )


def test_the_endpoint_reports_the_curve_and_every_slot(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    curve(root)
    data = ReviewRepository(root).account("c-ui", HASH)
    assert data["initial_cash"] == pytest.approx(100_000.0)
    assert len(data["points"]) == 3
    assert [s["slot"] for s in data["slots"]] == [
        "BTC/USDT:USDT-long",
        "ETH/USDT:USDT-short",
    ]
    assert data["slots"][0]["final"] == pytest.approx(250.0)


def test_the_reconciliation_is_served_as_a_measured_residual(tmp_path: Path) -> None:
    """Not a boolean the reader has to trust: the number itself, so a drift is visible."""
    root = fixture_root(tmp_path)
    curve(root)
    data = ReviewRepository(root).account("c-ui", HASH)
    assert data["max_residual"] == pytest.approx(0.0, abs=1e-9)
    assert data["reconciles"] is True


def test_a_curve_that_does_not_add_up_is_reported_rather_than_hidden(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    curve(root, residual=12.5)
    data = ReviewRepository(root).account("c-ui", HASH)
    assert data["max_residual"] == pytest.approx(12.5)
    assert data["reconciles"] is False


def test_a_missing_curve_reads_as_absent_not_as_zero(tmp_path: Path) -> None:
    """`Chưa có`, never `0`: a portfolio built before the account replay existed has no curve,
    and showing it as a flat line at zero would be a claim about the account."""
    root = fixture_root(tmp_path)
    data = ReviewRepository(root).account("c-ui", HASH)
    assert data["status"] == "missing"
    assert data["points"] == [] and data["slots"] == []
    assert data["max_residual"] is None and data["reconciles"] is None


def test_a_hash_cannot_address_a_file_outside_results(tmp_path: Path) -> None:
    """INV-80. The hash comes out of the database."""
    root = fixture_root(tmp_path)
    secret = tmp_path / "outside.parquet"
    pd.DataFrame({"ts": [], "equity": []}).to_parquet(secret, index=False)
    data = ReviewRepository(root).account("c-ui", "../../outside")
    assert data["status"] == "missing"


def test_the_route_answers_over_http(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    curve(root)
    with TestClient(create_app(root)) as client:
        ok = client.get(f"/api/campaigns/c-ui/account/{HASH}")
        assert ok.status_code == 200
        assert ok.json()["reconciles"] is True
        missing = client.get("/api/campaigns/c-ui/account/" + "b" * 64)
        assert missing.status_code == 200 and missing.json()["status"] == "missing"


def test_a_missing_campaign_is_still_a_404(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    with pytest.raises(ReviewDataError, match="c-nope"):
        ReviewRepository(root).account("c-nope", HASH)


def test_reading_the_account_leaves_the_ledger_byte_identical(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    curve(root)
    path = root / "ledger" / "crucible.db"
    before = path.read_bytes()
    ReviewRepository(root).account("c-ui", HASH)
    assert path.read_bytes() == before
