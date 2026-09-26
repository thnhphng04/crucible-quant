"""Row types written to and read from the ledger (Architecture §4.1, §4.2, ADR-0002)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

# A trial recorded before P3-11 has NULL scope columns and can never be backfilled: the
# append-only triggers refuse UPDATE on `trials` (INV-02). So the legacy scope is resolved on the
# way out, and these are what it resolves to — the five-symbol spot basket, traded long-or-flat.
LEGACY_INSTRUMENT = "legacy_spot"
LEGACY_DIRECTION = "long"

CampaignStatus = Literal["OPEN", "FROZEN", "BURNED", "ABANDONED"]  # ABANDONED: ADR-0019
CampaignPurpose = Literal["research", "harness_test"]  # harness_test: never frozen (§3.1.11)
TrialSource = Literal["evolution", "param_opt", "manual"]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Event(StrEnum):
    """Audit-log event names (generation_log.event)."""

    LLM_CALL = "LLM_CALL"
    PATCH_FAIL = "PATCH_FAIL"
    COMPILE_FAIL = "COMPILE_FAIL"
    AST_REJECT = "AST_REJECT"
    TEMPLATE_TAMPER = "TEMPLATE_TAMPER"
    DRIFT_REJECT = "DRIFT_REJECT"
    LEAK_REJECT = "LEAK_REJECT"
    MINBTL_REJECT = "MINBTL_REJECT"
    SANDBOX_VIOLATION = "SANDBOX_VIOLATION"
    GATE_ERROR = "GATE_ERROR"
    GATE_REJECT = "GATE_REJECT"
    CANDIDATE_SUBMITTED = "CANDIDATE_SUBMITTED"
    CAMPAIGN_FROZEN = "CAMPAIGN_FROZEN"  # detail: portfolio_hash (ADR-0016)
    HOLDOUT_OPENED = "HOLDOUT_OPENED"  # no numbers in detail — only in holdout_access
    HOLDOUT_CLAIMED = "HOLDOUT_CLAIMED"  # the one-time right to evaluate is taken (ADR-0016)
    HOLDOUT_EVALUATION_FAILED = "HOLDOUT_EVALUATION_FAILED"  # error type only; still consumed
    CAMPAIGN_ABANDONED = "CAMPAIGN_ABANDONED"  # detail: reason (ADR-0019)
    CALIBRATION_FINISHED = "CALIBRATION_FINISHED"  # detail: every attempt (ADR-0017 amendment)
    CALIBRATION_STARTED = "CALIBRATION_STARTED"  # detail: budget, technical retry or not
    CALIBRATION_CONFIRMATION = "CALIBRATION_CONFIRMATION"  # detail: status, params, trial id
    MIGRATION = "MIGRATION"  # engine C-gp: detail from/to island, candidate ids (§3.1.5, P2-12)
    DEGRADATION_CHECKPOINT = "DEGRADATION_CHECKPOINT"  # IS record vs its CPCV-OOS (§3.2, P2-14)
    DEGRADATION_WARNING = "DEGRADATION_WARNING"  # divergence seen but not acted on (ADR-0028)
    PROTOCOL_LOCKED = "PROTOCOL_LOCKED"  # engine comparison protocol, before any trial (P2-15)


@dataclass(frozen=True, slots=True)
class CalibrationRun:
    """A registered calibration run (ADR-0017 amendment 2); ``outcome`` None: never finished."""

    campaign_id: str
    candidate_id: str
    strategy_hash: str
    budget: int
    outcome: str | None
    attempts: int | None


@dataclass(frozen=True, slots=True)
class Campaign:
    campaign_id: str
    started_at: datetime
    holdout_range: str
    lock_hash: str
    holdout_lock_hash: str | None
    status: CampaignStatus


@dataclass(frozen=True, slots=True)
class GenerationEvent:
    run_id: str
    campaign_id: str
    engine: str
    seed: int
    agent: str
    model_used: str
    event: Event
    evolve_scope: str | None = None
    cell_id: str | None = None
    strategy_hash: str | None = None
    drift_delta: float | None = None
    detail: dict[str, Any] | None = None
    island: str | None = None
    instrument: str | None = None  # the scope this event belongs to (P3-11)
    direction: str | None = None
    ts: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TrialRecord:
    run_id: str
    campaign_id: str
    candidate_id: str
    engine: str
    seed: int
    strategy_hash: str
    params: dict[str, Any]
    universe: str
    timeframe: str
    timerange: str
    source: TrialSource
    sharpe_is: float
    returns_path: str
    verdict: str
    evolve_scope: str | None = None
    hypothesis: str | None = None
    cell_id: str | None = None
    gate_failed: str | None = None
    island: str | None = None
    # The (instrument, direction) this candidate was searched for (P3-11, ADR-0033). Unset on a
    # pre-P3 trial, which reads back as the legacy spot basket.
    instrument: str | None = None
    direction: str | None = None
    ts: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TrialRow:
    """A ``trials`` row as read back (``id`` assigned by the database)."""

    id: int
    campaign_id: str
    candidate_id: str
    engine: str
    strategy_hash: str
    params: dict[str, Any]
    universe: str
    timeframe: str
    source: TrialSource
    sharpe_is: float
    returns_path: str
    verdict: str
    hypothesis: str | None
    cell_id: str | None
    seed: int = 0
    island: str | None = None
    timerange: str = ""
    _instrument: str | None = None
    _direction: str | None = None

    @property
    def is_legacy_scope(self) -> bool:
        """Recorded before P3-11, so it belongs to the five-symbol spot basket."""
        return self._instrument is None

    @property
    def instrument(self) -> str:
        return self._instrument if self._instrument is not None else LEGACY_INSTRUMENT

    @property
    def direction(self) -> str:
        return self._direction if self._direction is not None else LEGACY_DIRECTION


@dataclass(frozen=True, slots=True)
class GateResultRecord:
    campaign_id: str
    candidate_id: str
    gate: str
    passed: bool
    reason: str
    value: float | None = None
    strategy_hash: str | None = None
    trial_id: int | None = None
    detail: dict[str, Any] | None = None
    ts: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class PortfolioVariant:
    portfolio_hash: str
    campaign_id: str
    rule_config: dict[str, Any]
    members: list[dict[str, Any]]
    sharpe_is: float | None = None
    returns_path: str | None = None
    ts: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class HoldoutAccess:
    campaign_id: str
    portfolio_hash: str
    frozen_at: datetime
    accessed_at: datetime
    timerange: str
    verdict: Literal["PASS", "FAIL"]
    sharpe_oos: float | None = None


@dataclass(frozen=True, slots=True)
class TrialStats:
    """DSR inputs (§4.1). ``var_sr`` is None while there are no trials."""

    n_raw: int
    n_eff: int
    var_sr: float | None


@dataclass(frozen=True, slots=True)
class StarvedCell:
    cell_id: str
    fail_rate: float
    attempts: int
