"""Strategy source archive (ADR-0013): what later stages re-run is exactly what was tested."""

from __future__ import annotations

from pathlib import Path

import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.archive import ArchiveError, StrategyArchive
from quantcrucible.validation.gates import GateContext, GatePipeline, strategy_hash
from tests.validation.test_pbo_gate import ZOO, cand


def test_put_get_round_trip(tmp_path: Path) -> None:
    archive = StrategyArchive(tmp_path / "strategies")
    h = archive.put(ZOO)
    assert h == strategy_hash(ZOO) and archive.get(h) == ZOO
    assert archive.put(ZOO) == h  # idempotent


def test_tampered_or_missing_source_is_refused(tmp_path: Path) -> None:
    archive = StrategyArchive(tmp_path)
    h = archive.put(ZOO)
    archive.path(h).write_text(ZOO.replace("1.0", "0.5"), encoding="utf-8")
    with pytest.raises(ArchiveError, match="no longer matches"):
        archive.get(h)
    with pytest.raises(ArchiveError, match="not in the archive"):
        archive.get("0" * 64)


def test_the_pipeline_archives_every_submitted_candidate(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2030/2031", lock_hash="h")
    archive = StrategyArchive(tmp_path / "strategies")
    GatePipeline([]).run(cand(), GateContext(ledger, {}, {"archive": archive}))
    assert archive.get(strategy_hash(ZOO)) == ZOO
