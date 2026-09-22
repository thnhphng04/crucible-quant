"""config/user.yaml loading (Architecture §10.1) — INV-20."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from quantcrucible.config.loader import ConfigError, load_user_config, parse_user_config
from quantcrucible.config.schema import Campaign, Research, UserConfig

REPO_USER_YAML = Path(__file__).resolve().parents[2] / "config" / "user.yaml"


def test_repo_user_yaml_loads_and_matches_defaults() -> None:
    cfg = load_user_config(REPO_USER_YAML)
    # the committed file restates the §10 defaults — except D4, which the user sets explicitly:
    # the schema default stays None so an omitted threshold can never open a campaign (ADR-0020)
    # and the phase-2 comparison campaign (ADR-0027): harness_test with a 600-trial budget
    assert cfg.research == replace(
        Research(), holdout_pass=1.3, campaign=Campaign("harness_test", 600)
    )
    assert Research().holdout_pass is None
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
        ({"research": {"engines": {"gp": 0.9}}}, "sum to 1"),
        ({"research": {"engines": {"gp": 0.4, "quantevolve": 0.1}}}, "deferred"),
        ({"research": {"engines": {"gp": 0.5, "random": 0.4, "simple_loop": 0.1}}}, "deferred"),
        ({"research": {"gp": {"param_only_max": 1.5}}}, "param_only_max"),
        ({"research": {"gp": {"param_only_max": -0.1}}}, "param_only_max"),
        ({"research": {"gp": {"plateau_threshold": 0}}}, "plateau_threshold"),
        ({"research": {"gp": {"plateau_threshold": 1.2}}}, "plateau_threshold"),
        ({"research": {"campaign": {"purpose": "production"}}}, "not one of"),
        ({"research": {"campaign": {"trial_budget": 0}}}, "trial_budget"),
        ({"research": {"drift": {"allow_below": 0.2}}}, "allow_below"),
        ({"research": {"target_vol": 0}}, "> 0"),
        ({"research": {"max_risk_pct": 2}}, "<= 1"),
        ({"research": {"data": {"start": "yesterday"}}}, "date"),
        ({"research": {"data": {"symbols": []}}}, "symbols"),
        ({"research": {"data": {"second_exchange": "binance"}}}, "second_exchange"),
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


def test_engine_c_is_the_focus_by_default() -> None:
    """Arch v0.6 (D14, D19, D20): C-gp and C-random share phase 2; A/B are deferred at 0."""
    r = parse_user_config({}).research
    assert (r.engines.gp, r.engines.random) == (0.5, 0.5)
    assert (r.engines.quantevolve, r.engines.simple_loop) == (0.0, 0.0)
    assert (r.gp.param_only_max, r.gp.plateau_threshold) == (0.30, 0.5)
    assert r.campaign.purpose == "research" and r.campaign.trial_budget is None


def test_campaign_purpose_and_budget() -> None:
    r = parse_user_config(
        {"research": {"campaign": {"purpose": "harness_test", "trial_budget": 1200}}}
    ).research
    assert r.campaign.purpose == "harness_test" and r.campaign.trial_budget == 1200
