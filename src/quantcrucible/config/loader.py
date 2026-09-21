"""Load and validate ``config/user.yaml`` (Architecture §10.1).

Every key is optional (missing ⇒ §10 default). Unknown keys are errors: a typo must not silently
fall back to a default. Hard floors are enforced here, at load time.
"""

from __future__ import annotations

import dataclasses
import types
from datetime import date
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

import yaml

from quantcrucible.config.schema import (
    DSR_MIN_FLOOR,
    MINBTL_TARGET_SHARPE_CEILING,
    PBO_MAX_CEILING,
    Research,
    UserConfig,
)


class ConfigError(ValueError):
    """``user.yaml`` is invalid or tries to loosen a hard floor."""


def _build(tp: Any, value: Any, where: str) -> Any:
    origin = get_origin(tp)
    if dataclasses.is_dataclass(tp) and isinstance(tp, type):
        return _build_dataclass(tp, value, where)
    if origin in (Union, types.UnionType):
        options = get_args(tp)
        if value is None and type(None) in options:
            return None
        (inner,) = [o for o in options if o is not type(None)]
        return _build(inner, value, where)
    if origin is Literal:
        if value not in get_args(tp):
            raise ConfigError(f"{where}: {value!r} is not one of {list(get_args(tp))}")
        return value
    if origin is tuple:
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{where}: expected a list of strings")
        return tuple(value)
    if tp is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: expected true/false, got {value!r}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where}: expected an integer, got {value!r}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{where}: expected a number, got {value!r}")
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise ConfigError(f"{where}: expected a string, got {value!r}")
        return value
    if tp is date:
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as e:
            raise ConfigError(f"{where}: expected a date YYYY-MM-DD, got {value!r}") from e
    raise TypeError(f"{where}: unsupported config type {tp!r}")  # programming error


def _build_dataclass(cls: type[Any], value: Any, where: str) -> Any:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where or 'config'}: expected a mapping")
    hints = get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(value) - known)
    if unknown:
        raise ConfigError(f"{where or 'config'}: unknown key(s) {unknown}")
    kwargs = {k: _build(hints[k], v, f"{where}.{k}" if where else k) for k, v in value.items()}
    return cls(**kwargs)


def _validate(cfg: UserConfig) -> None:
    r = cfg.research
    problems: list[str] = []
    if r.gates.dsr_min < DSR_MIN_FLOOR:
        problems.append(f"research.gates.dsr_min must be >= {DSR_MIN_FLOOR} (hard floor)")
    if r.gates.dsr_min >= 1:
        problems.append("research.gates.dsr_min must be < 1")
    if r.gates.pbo_max > PBO_MAX_CEILING:
        problems.append(f"research.gates.pbo_max must be <= {PBO_MAX_CEILING} (hard floor)")
    if r.gates.pbo_max <= 0:
        problems.append("research.gates.pbo_max must be > 0")
    if not 0 < r.minbtl_target_sharpe <= MINBTL_TARGET_SHARPE_CEILING:
        problems.append(
            f"research.minbtl_target_sharpe must be in (0, {MINBTL_TARGET_SHARPE_CEILING}]"
            " (a higher target loosens gate ②)"
        )
    shares = (r.engines.quantevolve, r.engines.simple_loop, r.engines.random)
    if any(s < 0 for s in shares) or abs(sum(shares) - 1.0) > 1e-9:
        problems.append("research.engines shares must be >= 0 and sum to 1")
    if not r.drift.allow_below < r.drift.reject_at:
        problems.append("research.drift.allow_below must be < reject_at")
    positive = {
        "research.target_vol": r.target_vol,
        "research.max_risk_pct": r.max_risk_pct,
        "research.portfolio.max_corr": r.portfolio.max_corr,
        "research.portfolio.max_strategies": r.portfolio.max_strategies,
        "research.pbo_grid.values_per_param": r.pbo_grid.values_per_param,
        "research.pbo_grid.range": r.pbo_grid.range,
        "research.pbo_grid.max_configs": r.pbo_grid.max_configs,
        "research.drift.n_scenarios": r.drift.n_scenarios,
        "research.constraints.min_trades": r.constraints.min_trades,
        "research.constraints.min_holding_bars": r.constraints.min_holding_bars,
        "research.constraints.max_indicator_corr": r.constraints.max_indicator_corr,
        "research.seeds": r.seeds,
        "research.calibration.budget_per_strategy": r.calibration.budget_per_strategy,
        "research.data.holdout_months": r.data.holdout_months,
        "operational.live_capital": cfg.operational.live_capital,
        "operational.kill_switch_drawdown": cfg.operational.kill_switch_drawdown,
    }
    problems += [f"{k} must be > 0" for k, v in positive.items() if v <= 0]
    for name, v in {
        "research.max_risk_pct": r.max_risk_pct,
        "research.portfolio.max_corr": r.portfolio.max_corr,
        "research.constraints.max_indicator_corr": r.constraints.max_indicator_corr,
        "operational.kill_switch_drawdown": cfg.operational.kill_switch_drawdown,
    }.items():
        if v > 1:
            problems.append(f"{name} must be <= 1")
    if not r.data.symbols:
        problems.append("research.data.symbols must not be empty")
    if problems:
        raise ConfigError("; ".join(problems))


def parse_user_config(data: Any) -> UserConfig:
    cfg: UserConfig = _build_dataclass(UserConfig, data, "")
    _validate(cfg)
    return cfg


def load_user_config(path: Path | str) -> UserConfig:
    with Path(path).open(encoding="utf-8") as f:
        return parse_user_config(yaml.safe_load(f))


def research_to_dict(research: Research) -> dict[str, Any]:
    """Plain, JSON-safe dict of Group B — the part locked per campaign."""

    def plain(v: Any) -> Any:
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, tuple):
            return [plain(x) for x in v]
        if isinstance(v, dict):
            return {k: plain(x) for k, x in v.items()}
        return v

    return {k: plain(v) for k, v in dataclasses.asdict(research).items()}
