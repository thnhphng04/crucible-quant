"""Stop kinds are Group B, locked per campaign (P3-55, INV-116, ADR-0042)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from quantcrucible.config.loader import ConfigError, parse_user_config, research_to_dict
from quantcrucible.config.lock import LockMismatchError, assert_lock_matches, open_campaign
from quantcrucible.config.schema import Exit, UserConfig
from quantcrucible.core.strategy.tunable import lock_stop_kinds
from quantcrucible.ledger.db import Ledger

HOLDOUT = "2025-09-21/2026-09-21"
BRACKET = {"exit_protocol": "bracket_timeout_v1"}


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger.open(tmp_path / "ledger.db")


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "evaluation.lock.yaml"


def _cfg(*kinds: str) -> UserConfig:
    cfg = UserConfig()
    exit_ = dataclasses.replace(cfg.research.exit, stop_kinds=kinds)  # type: ignore[arg-type]
    return dataclasses.replace(cfg, research=dataclasses.replace(cfg.research, exit=exit_))


def _open_pre_p3_55(
    ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch, derived: dict[str, Any]
) -> None:
    """A campaign locked before the key existed: its ``exit`` section has no ``stop_kinds``."""
    from quantcrucible.config.loader import research_to_dict as real

    def old(research: Any) -> dict[str, Any]:
        d = real(research)
        d["exit"].pop("stop_kinds")
        return d

    target = "quantcrucible.config.lock.research_to_dict"
    monkeypatch.setattr(target, old)
    open_campaign(UserConfig(), ledger, "c1", lock_path, HOLDOUT, derived=derived)
    monkeypatch.setattr(target, real)


def test_the_default_is_the_atr_stop_alone_and_it_is_locked() -> None:
    assert Exit().stop_kinds == ("atr",)
    assert research_to_dict(UserConfig().research)["exit"]["stop_kinds"] == ["atr"]
    cfg = parse_user_config({"research": {"exit": {"stop_kinds": ["atr", "bollinger"]}}})
    assert cfg.research.exit.stop_kinds == ("atr", "bollinger")


@pytest.mark.parametrize("kinds", [[], ["donchian"], ["atr", "atr"], "atr"])
def test_an_empty_unknown_or_repeated_kind_is_refused(kinds: Any) -> None:
    with pytest.raises(ConfigError, match="stop_kinds"):
        parse_user_config({"research": {"exit": {"stop_kinds": kinds}}})


@pytest.mark.parametrize("kinds", [("atr",), ("atr", "bollinger")])
def test_an_old_bracket_lock_resumes_under_its_default_or_its_actual_kinds(
    ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch, kinds: tuple[str, ...]
) -> None:
    """Every campaign open when P3-55 landed keeps running: the lock, not user.yaml, decides
    its stops, and they stay what it ran with."""
    _open_pre_p3_55(ledger, lock_path, monkeypatch, BRACKET)
    lock = assert_lock_matches(_cfg(*kinds), lock_path, ledger, "c1")
    assert "stop_kinds" not in lock["research"]["exit"]
    assert lock_stop_kinds(lock) == ("atr", "bollinger")


def test_an_old_lock_refuses_kinds_it_never_ran_with(
    ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_pre_p3_55(ledger, lock_path, monkeypatch, BRACKET)
    with pytest.raises(LockMismatchError, match="exit"):
        assert_lock_matches(_cfg("bollinger"), lock_path, ledger, "c1")


def test_an_old_lock_still_refuses_another_exit_change(
    ledger: Ledger, lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open_pre_p3_55(ledger, lock_path, monkeypatch, BRACKET)
    cfg = UserConfig()
    exit_ = dataclasses.replace(cfg.research.exit, tp_sl_ratio=2.0)
    edited = dataclasses.replace(cfg, research=dataclasses.replace(cfg.research, exit=exit_))
    with pytest.raises(LockMismatchError, match="exit"):
        assert_lock_matches(edited, lock_path, ledger, "c1")


def test_a_new_lock_carries_the_key_and_refuses_a_change(ledger: Ledger, lock_path: Path) -> None:
    open_campaign(_cfg("atr"), ledger, "c1", lock_path, HOLDOUT, derived=BRACKET)
    lock = assert_lock_matches(_cfg("atr"), lock_path, ledger, "c1")
    assert lock["research"]["exit"]["stop_kinds"] == ["atr"]
    assert lock_stop_kinds(lock) == ("atr",)
    with pytest.raises(LockMismatchError, match="exit"):
        assert_lock_matches(_cfg("atr", "bollinger"), lock_path, ledger, "c1")


def test_the_kinds_a_lock_allows() -> None:
    explicit = {"research": {"exit": {"stop_kinds": ["atr", "bollinger"]}}, "derived": BRACKET}
    assert lock_stop_kinds(explicit) == ("atr", "bollinger")
    assert lock_stop_kinds({"research": {"exit": {}}, "derived": BRACKET}) == ("atr", "bollinger")
    assert lock_stop_kinds({"research": {"exit": {}}, "derived": {}}) == ("atr",)  # legacy_flat
    assert lock_stop_kinds({"research": {}, "derived": {}}) == ("atr",)
