"""Campaign bootstrap for the `validate` CLI (validation/run.py)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from quantcrucible.config.lock import LockMismatchError, read_lock
from quantcrucible.config.schema import UserConfig
from quantcrucible.core.strategy.template import template_hash
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.run import (
    candidate_pipeline,
    current_campaign,
    make_candidate,
    phase0_pipeline,
)
from tests.factories import make_bars


def test_opens_once_then_resumes(tmp_path: Path) -> None:
    manifest = {"range": "2030-01-01/2031-01-01", "files": {}, "created_at": "x"}
    (tmp_path / "holdout.lock").write_text(json.dumps(manifest), encoding="utf-8")
    ledger = Ledger.open(tmp_path / "ledger.db")
    lock_path = tmp_path / "config" / "evaluation.lock.yaml"
    cfg = UserConfig()
    first = current_campaign(cfg, ledger, lock_path, tmp_path)
    lock = read_lock(lock_path)
    assert lock["holdout_range"] == "2030-01-01/2031-01-01"
    assert lock["derived"]["template_hash"] == template_hash("joint")
    assert set(lock["derived"]["costs"]) == {"fee_rate", "slippage_bps"}
    assert current_campaign(cfg, ledger, lock_path, tmp_path) == first
    changed = replace(cfg, research=replace(cfg.research, seeds=5))
    with pytest.raises(LockMismatchError):
        current_campaign(changed, ledger, lock_path, tmp_path)


def test_candidate_defaults_to_tunable_values() -> None:
    source = (Path(__file__).parents[2] / "src/quantcrucible/core/zoo/ema_crossover.py").read_text(
        encoding="utf-8"
    )
    data = {"A/USDT": make_bars(10, symbol="A/USDT")}
    c = make_candidate(source, "c1", data)
    assert c.params == {"fast": 20, "slow": 100, "k_atr": 2.0}
    assert c.universe == ("A/USDT",) and c.timerange == "2020-01-02/2020-01-11"
    assert [g.id for g in phase0_pipeline().gates] == [
        "g1a_static", "g1b_dynamic", "g2_minbtl", "g3_is",
    ]  # fmt: skip
    assert [g.id for g in candidate_pipeline().gates][-1] == "g4_pbo"
