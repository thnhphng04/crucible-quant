"""Backtest precision is Group B, locked per campaign (P3-35, INV-108, ADR-0039)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from quantcrucible.config.loader import ConfigError, parse_user_config, research_to_dict
from quantcrucible.config.lock import LockMismatchError, assert_lock_matches, open_campaign
from quantcrucible.config.schema import Backtest, UserConfig
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.numerics import Numerics, numerics_tag

HOLDOUT = "2025-09-21/2026-09-21"


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger.open(tmp_path / "ledger.db")


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "evaluation.lock.yaml"


def _cfg(precision: str) -> UserConfig:
    cfg = UserConfig()
    return dataclasses.replace(
        cfg,
        research=dataclasses.replace(cfg.research, backtest=Backtest(precision)),  # type: ignore[arg-type]
    )


def _open_pre_p3_35(ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A campaign locked before the key existed: its research section has no ``backtest``."""
    from quantcrucible.config.loader import research_to_dict as real

    def old(research: Any) -> dict[str, Any]:
        d = real(research)
        d.pop("backtest")
        return d

    target = "quantcrucible.config.lock.research_to_dict"
    monkeypatch.setattr(target, old)
    open_campaign(UserConfig(), ledger, "c1", lock_path, HOLDOUT)
    monkeypatch.setattr(target, real)


def test_the_default_is_float64_and_it_is_locked() -> None:
    assert research_to_dict(UserConfig().research)["backtest"] == {"precision": "float64"}
    cfg = parse_user_config({"research": {"backtest": {"precision": "float32"}}})
    assert cfg.research.backtest.precision == "float32"


def test_an_unknown_precision_is_refused() -> None:
    with pytest.raises(ConfigError):
        parse_user_config({"research": {"backtest": {"precision": "float16"}}})


def test_the_audit_rate_can_only_be_raised() -> None:
    assert parse_user_config({"operational": {"compute": {"audit_rate": 0.5}}})
    for bad in (0.01, 1.5):
        with pytest.raises(ConfigError, match="audit_rate"):
            parse_user_config({"operational": {"compute": {"audit_rate": bad}}})


def test_a_float32_campaign_cannot_be_forced_onto_the_float64_sandbox() -> None:
    with pytest.raises(ConfigError, match="float32"):
        parse_user_config(
            {
                "operational": {"compute": {"engine": "sandbox"}},
                "research": {"backtest": {"precision": "float32"}},
            }
        )


def test_an_old_lock_reads_as_float64(
    ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_pre_p3_35(ledger, lock_path, monkeypatch)
    lock = assert_lock_matches(UserConfig(), lock_path, ledger, "c1")
    assert "backtest" not in lock["research"]
    assert Numerics.from_lock(lock) == Numerics("float64", "fp64_oracle_v1")


def test_an_old_lock_refuses_a_float32_config(
    ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_pre_p3_35(ledger, lock_path, monkeypatch)
    with pytest.raises(LockMismatchError, match="backtest"):
        assert_lock_matches(_cfg("float32"), lock_path, ledger, "c1")


def test_a_new_lock_carries_the_key_and_refuses_a_change(ledger: Ledger, lock_path: Path) -> None:
    open_campaign(_cfg("float32"), ledger, "c1", lock_path, HOLDOUT)
    lock = assert_lock_matches(_cfg("float32"), lock_path, ledger, "c1")
    assert lock["research"]["backtest"] == {"precision": "float32"}
    with pytest.raises(LockMismatchError, match="backtest"):
        assert_lock_matches(_cfg("float64"), lock_path, ledger, "c1")


def test_numerics_follow_the_lock_and_refuse_a_contradiction() -> None:
    research = {"backtest": {"precision": "float32"}}
    ok = {"research": research, "derived": {"backtest_numerics": numerics_tag("float32")}}
    assert Numerics.from_lock(ok) == Numerics("float32", "fp32_signals_v1")
    for tag in ("fp64_oracle_v1", "fp16_v9"):
        with pytest.raises(ValueError, match="numerics"):
            Numerics.from_lock({"research": research, "derived": {"backtest_numerics": tag}})
    with pytest.raises(ValueError, match="precision"):
        Numerics.from_lock({"research": {"backtest": {"precision": "float16"}}, "derived": {}})
    # A float32 lock must name its numerics: none is only valid for pre-P3-35 (float64) locks.
    with pytest.raises(ValueError, match="numerics"):
        Numerics.from_lock({"research": research, "derived": {}})
