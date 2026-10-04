"""Perpetual CLI preflight never opens a campaign or inspects holdout prices."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_the_dryrun_previews_the_lock_a_real_open_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P3-24 on real data found the preview without the protocol tags every new campaign is
    locked with (exit, numerics, portfolio, stop period, resolved data end)."""
    import yaml

    from quantcrucible.config.loader import load_user_config
    from quantcrucible.validation.run import new_campaign_derived

    config = _project(tmp_path)
    assert campaign_dryrun(config, tmp_path) == 0
    preview = yaml.safe_load(capsys.readouterr().out)
    expected = new_campaign_derived(load_user_config(config))
    for key, value in expected.items():
        assert preview["derived"][key] == value, key
    assert preview["derived"]["resolved_data_end"] == "2029-12-31"


def test_mark_fills_are_recorded_for_the_in_sample_window_only(tmp_path: Path) -> None:
    """ADR-0042: the record sits beside the IS bundle (and so inside its manifest) and says
    nothing about the holdout window."""
    from datetime import UTC, datetime

    from quantcrucible.cli import _write_mark_fills
    from quantcrucible.data.perp_pipeline import PerpCoverage

    def ms(text: str) -> int:
        return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)

    start, end = datetime(2020, 9, 15, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)
    coverage = {
        "BTC/USDT:USDT": PerpCoverage(
            "BTC/USDT:USDT", "1h", start, end, 0, 0, 0, 0,
            mark_fills=(ms("2020-12-17 07:32"), ms("2020-12-17 07:33"), ms("2026-01-05 10:00")),
        ),
        "ETH/USDT:USDT": PerpCoverage("ETH/USDT:USDT", "1h", start, end, 0, 0, 0, 0),
    }  # fmt: skip
    counts = _write_mark_fills(tmp_path, coverage, datetime(2025, 10, 2, tzinfo=UTC))
    assert counts == {"BTC/USDT:USDT": 2, "ETH/USDT:USDT": 0}
    record = json.loads((tmp_path / "mark_fills.json").read_text(encoding="utf-8"))
    assert record == {
        "BTC/USDT:USDT": ["2020-12-17 07:32", "2020-12-17 07:33"],
        "ETH/USDT:USDT": [],
    }


def test_market_flag_must_match_locked_config(tmp_path: Path) -> None:
    config = _project(tmp_path)
    assert (
        main(["data-fetch", "--market", "spot", "--config", str(config), "--root", str(tmp_path)])
        == 2
    )
