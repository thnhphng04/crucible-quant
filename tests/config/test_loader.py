"""config/user.yaml loading (Architecture §10.1) — INV-20."""

from pathlib import Path
from typing import Any

import pytest

from quantcrucible.config.loader import ConfigError, load_user_config, parse_user_config
from quantcrucible.config.schema import Research, UserConfig

REPO_USER_YAML = Path(__file__).resolve().parents[2] / "config" / "user.yaml"


def test_repo_user_yaml_loads_and_matches_defaults() -> None:
    cfg = load_user_config(REPO_USER_YAML)
    assert cfg.research == Research()  # the committed file restates the §10 defaults
    assert cfg.operational.models.research == "gpt-oss-120b"


def test_empty_config_uses_defaults() -> None:
    assert parse_user_config(None) == UserConfig()
    assert parse_user_config({}) == UserConfig()


def test_partial_override_keeps_other_defaults() -> None:
    cfg = parse_user_config({"research": {"portfolio": {"max_strategies": 10}}})
    assert cfg.research.portfolio.max_strategies == 10
    assert cfg.research.portfolio.max_corr == 0.5


@pytest.mark.parametrize(
    "data",
    [
        {"research": {"gates": {"dsr_min": 0.9}}},
        {"research": {"gates": {"pbo_max": 0.6}}},
        {"research": {"minbtl_target_sharpe": 2.0}},
    ],
)
def test_hard_floors(data: dict[str, Any]) -> None:
    with pytest.raises(ConfigError, match=r"hard floor|loosens"):
        parse_user_config(data)


def test_tightening_is_allowed() -> None:
    cfg = parse_user_config(
        {"research": {"gates": {"dsr_min": 0.99, "pbo_max": 0.3}, "minbtl_target_sharpe": 1.0}}
    )
    assert cfg.research.gates.dsr_min == 0.99
    assert cfg.research.minbtl_target_sharpe == 1.0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"research": {"sedds": 3}}, "unknown key"),
        ({"reserch": {}}, "unknown key"),
        ({"research": {"engine_mode": "parallel"}}, "not one of"),
        ({"research": {"seeds": "three"}}, "integer"),
        ({"research": {"seeds": True}}, "integer"),
        ({"research": {"engines": {"quantevolve": 0.9}}}, "sum to 1"),
        ({"research": {"drift": {"allow_below": 0.2}}}, "allow_below"),
        ({"research": {"target_vol": 0}}, "> 0"),
        ({"research": {"max_risk_pct": 2}}, "<= 1"),
        ({"research": {"data": {"start": "yesterday"}}}, "date"),
        ({"research": {"data": {"symbols": []}}}, "symbols"),
        ({"research": {"calibration": {"enabled": "yes"}}}, "true/false"),
    ],
)
def test_invalid_values_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_user_config(data)


def test_holdout_pass_optional_until_used() -> None:
    assert parse_user_config({}).research.holdout_pass is None
    assert parse_user_config({"research": {"holdout_pass": 0.5}}).research.holdout_pass == 0.5


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    "path",
    [
        ("constraints", "max_indicator_corr"),
        ("gates", "dsr_min"),
        ("gates", "pbo_max"),
        ("minbtl_target_sharpe",),
        ("holdout_pass",),
        ("target_vol",),
    ],
)
def test_non_finite_numbers_rejected(bad: float, path: tuple[str, ...]) -> None:
    """NaN compares false with everything, so `corr > NaN` would silently pass a gate."""
    research: dict[str, Any] = {}
    node = research
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = bad
    with pytest.raises(ConfigError, match="finite"):
        parse_user_config({"research": research})


def test_yaml_nan_literal_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "user.yaml"
    cfg.write_text("research:\n  constraints: {max_indicator_corr: .nan}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="finite"):
        load_user_config(cfg)
