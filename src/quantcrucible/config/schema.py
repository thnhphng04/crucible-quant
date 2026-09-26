"""Typed settings for ``config/user.yaml`` (Architecture §10, §10.1, ADR-0002).

Defaults are the §10 decision-register values. Group A (``operational``) may change any time;
Group B (``research``) is locked per campaign into ``evaluation.lock.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Rebalance = Literal["weekly", "monthly", "quarterly"]
EngineMode = Literal["isolated", "collaborative"]
EvolveScope = Literal["entry", "exit", "regime", "joint"]
CampaignPurpose = Literal["research", "harness_test"]
DEFERRED_ENGINES = ("quantevolve", "simple_loop")  # engines A/B, deferred by D19 (arch v0.6)

# Hard floors (§10.1): may only be tightened.
DSR_MIN_FLOOR = 0.95
PBO_MAX_CEILING = 0.5
MINBTL_TARGET_SHARPE_CEILING = 1.5  # a lower target means a longer MinBTL, i.e. stricter


@dataclass(frozen=True, slots=True)
class Models:
    research: str | None = "gpt-oss-120b"
    coding: str | None = None
    eval: str | None = None


@dataclass(frozen=True, slots=True)
class Operational:
    live_capital: float = 10_000.0
    base_currency: str = "USD"
    kill_switch_drawdown: float = 0.20
    models: Models = field(default_factory=Models)


@dataclass(frozen=True, slots=True)
class Portfolio:
    max_corr: float = 0.5
    max_strategies: int = 20
    rebalance: Rebalance = "monthly"


@dataclass(frozen=True, slots=True)
class PboGrid:
    values_per_param: int = 5
    range: float = 0.30
    max_configs: int = 200


@dataclass(frozen=True, slots=True)
class Drift:
    allow_below: float = 0.05
    reject_at: float = 0.15
    n_scenarios: int = 20


@dataclass(frozen=True, slots=True)
class Engines:
    """Trial-budget shares (D14). Arch v0.6 (D19): engine C first — C-gp and C-random;
    the LLM engines A (quantevolve) and B (simple_loop) are deferred and must stay at 0."""

    gp: float = 0.5
    random: float = 0.5
    quantevolve: float = 0.0
    simple_loop: float = 0.0


@dataclass(frozen=True, slots=True)
class Gp:
    """Parameters in engine C-gp (D20)."""

    param_only_max: float = 0.30  # max share of offspring that only change TUNABLE values
    plateau_threshold: float = 0.5  # neighbour counts as plateau if its IS Sharpe >= this x own


@dataclass(frozen=True, slots=True)
class Campaign:
    """What a campaign is for. A ``harness_test`` campaign (phase-2 engine comparison, §3.1.11)
    never builds a portfolio, freezes or opens a holdout; ``trial_budget`` caps its trials."""

    purpose: CampaignPurpose = "research"
    trial_budget: int | None = None


@dataclass(frozen=True, slots=True)
class Constraints:
    min_trades: int = 30
    min_holding_bars: int = 1
    max_indicator_corr: float = 0.9


@dataclass(frozen=True, slots=True)
class Calibration:
    enabled: bool = True
    budget_per_strategy: int = 50


@dataclass(frozen=True, slots=True)
class Gates:
    dsr_min: float = DSR_MIN_FLOOR
    pbo_max: float = PBO_MAX_CEILING


@dataclass(frozen=True, slots=True)
class Data:
    exchange: str = "binance"
    symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")
    timeframe: str = "1d"
    start: date = date(2018, 1, 1)
    holdout_months: int = 12
    second_exchange: str | None = "gate"  # gate ⑥′ data-source robustness (P1-09, ADR-0015)


@dataclass(frozen=True, slots=True)
class Research:
    target_vol: float = 0.10
    max_risk_pct: float = 0.01
    portfolio: Portfolio = field(default_factory=Portfolio)
    pbo_grid: PboGrid = field(default_factory=PboGrid)
    drift: Drift = field(default_factory=Drift)
    engines: Engines = field(default_factory=Engines)
    gp: Gp = field(default_factory=Gp)
    campaign: Campaign = field(default_factory=Campaign)
    engine_mode: EngineMode = "isolated"
    evolve_scope: EvolveScope = "joint"
    constraints: Constraints = field(default_factory=Constraints)
    seeds: int = 3
    minbtl_target_sharpe: float = MINBTL_TARGET_SHARPE_CEILING
    data: Data = field(default_factory=Data)
    calibration: Calibration = field(default_factory=Calibration)
    gates: Gates = field(default_factory=Gates)
    holdout_pass: float | None = None  # D4 — required before the holdout can be opened


@dataclass(frozen=True, slots=True)
class UserConfig:
    operational: Operational = field(default_factory=Operational)
    research: Research = field(default_factory=Research)
