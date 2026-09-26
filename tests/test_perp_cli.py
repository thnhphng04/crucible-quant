"""Perpetual CLI preflight never opens a campaign or inspects holdout prices."""

from __future__ import annotations

import json
from pathlib import Path

from quantcrucible.cli import campaign_dryrun, main
from quantcrucible.data.manifest import write_manifest
from quantcrucible.ledger.db import Ledger


def _project(root: Path) -> Path:
    config = root / "perp.yaml"
    config.write_text(
        """research:
  holdout_pass: 0.5
  data:
    exchange: binance
    market: usdt_m_perpetual
    symbols: [BTC/USDT:USDT]
    start: 2020-09-14
    second_exchange: gate
""",
        encoding="utf-8",
    )
    is_dir = root / "data" / "perp"
    is_dir.mkdir(parents=True)
    sample = is_dir / "sample.parquet"
    sample.write_bytes(b"IS fixture, not a price")
    write_manifest(
        is_dir / "manifest.json",
        [sample],
        "binanceusdm",
        {"BTC/USDT:USDT": "2020-09-14/2029-01-01"},
    )
    second_dir = root / "data" / "perp-second-gate"
    second_dir.mkdir(parents=True)
    second_sample = second_dir / "sample.parquet"
    second_sample.write_bytes(b"second IS fixture, not a price")
    write_manifest(
        second_dir / "manifest.json",
        [second_sample],
        "gate",
        {"BTC/USDT:USDT": "2020-09-14/2029-01-01"},
    )
    lock = root / "holdout" / "perp.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps({"range": "2029-01-01/2030-01-01", "files": {}, "created_at": "x"}),
        encoding="utf-8",
    )
    return config


def test_dryrun_leaves_lock_ledger_and_holdout_claim_untouched(tmp_path: Path) -> None:
    config = _project(tmp_path)
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    ledger_path = ledger_dir / "crucible.db"
    ledger = Ledger.open(ledger_path)
    assert ledger.campaigns() == []
    ledger.close()
    holdout_lock = tmp_path / "holdout" / "perp.lock"
    before = holdout_lock.read_bytes()

    assert campaign_dryrun(config, tmp_path) == 0
    assert holdout_lock.read_bytes() == before
    assert not (tmp_path / "config" / "evaluation.lock.yaml").exists()
    ledger = Ledger.open(ledger_path)
    assert ledger.campaigns() == []
    assert ledger.holdout_collision("2029-01-01/2030-01-01", None) is None
    ledger.close()


def test_market_flag_must_match_locked_config(tmp_path: Path) -> None:
    config = _project(tmp_path)
    assert (
        main(["data-fetch", "--market", "spot", "--config", str(config), "--root", str(tmp_path)])
        == 2
    )
