"""Portfolio construction — the pre-registered rule of Architecture §3.2.1 (D9, ADR-0013).

Input: every trial of the campaign whose candidate passed gate ④. Then:

1. **Cell representative** — one per ``cell_id`` (a trial without a cell is its own cell): the
   highest *selection score* (PSR against the campaign's deflated benchmark SR₀(N_eff, V[SR]);
   a ranking only — DSR as a gate exists for the consolidated portfolio alone, §3.1.6).
2. **Correlation filter** — representatives in score order; drop one whose IS returns have
   |ρ| > ``max_corr`` with any strategy already accepted (ρ pairwise, on the pair's shared days).
3. **Size cap** — at most ``max_strategies``.
4. **Weights** — naive risk parity: the same vol budget each, w ∝ 1/σ (never optimized on Sharpe),
   on the members' common window — a candidate that was not selected never changes it.
5. **Rebalancing** — back to the weights on the ``rebalance`` schedule.
6. **Freeze** — ``portfolio_hash`` = SHA256 of the members (strategy hash + params + weight) and
   the rule. Every distinct build is a ``portfolio_variants`` row, and each counts in ``N`` at ⑤.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pandas as pd

from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars, Direction, Signal
from quantcrucible.execution.joint_account import SignalReplay, SlotPlan, replay_signals
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import (
    LEGACY_INSTRUMENT,
    PortfolioVariant,
    TrialRow,
    TrialStats,
)
from quantcrucible.validation.gates import G4_PBO
from quantcrucible.validation.statistical import (
    deflated_benchmark,
    probabilistic_sharpe_ratio,
    sharpe_moments,
)

MIN_COMMON_OBS = 30
WEIGHT_DIGITS = 10  # weights are rounded before hashing so the hash is stable across platforms


@dataclass(frozen=True, slots=True)
class PortfolioRule:
    """The pre-registered construction rule (lock: ``research.portfolio``, D9)."""

    max_corr: float = 0.5
    max_strategies: int = 20
    rebalance: str = "monthly"
    selection: str = "dsr_rank"
    weighting: str = "inverse_vol"

    @classmethod
    def from_lock(cls, lock: Mapping[str, Any]) -> PortfolioRule:
        p = lock["research"]["portfolio"]
        return cls(float(p["max_corr"]), int(p["max_strategies"]), str(p["rebalance"]))

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_corr": self.max_corr, "max_strategies": self.max_strategies,
            "rebalance": self.rebalance, "selection": self.selection,
            "weighting": self.weighting,
        }  # fmt: skip


def _required_timeframe(d: Mapping[str, Any]) -> str:
    """A member's timeframe is not optional (P3-14).

    Defaulting a missing one to "1d" would annualise a 4h member six times too small, and nothing
    downstream would notice — the Sharpe would simply be wrong. A variant recorded before the
    field existed is refused rather than guessed.
    """
    try:
        return str(d["timeframe"])
    except KeyError:
        raise KeyError(
            "this portfolio member has no timeframe: it cannot be annualised, and defaulting "
            "would silently mis-scale every metric derived from it"
        ) from None


@dataclass(frozen=True, slots=True)
class Member:
    trial_id: int
    candidate_id: str
    strategy_hash: str
    params: dict[str, Any]
    weight: float
    universe: tuple[str, ...] = ()
    timeframe: str = "1d"
    direction: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {
            "trial_id": self.trial_id, "candidate_id": self.candidate_id,
            "strategy_hash": self.strategy_hash, "params": self.params,
            "weight": round(self.weight, WEIGHT_DIGITS), "universe": list(self.universe),
            "timeframe": self.timeframe,
        }  # fmt: skip
        if self.direction is not None:
            result["direction"] = self.direction
        return result

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Member:
        return cls(
            int(d["trial_id"]), str(d["candidate_id"]), str(d["strategy_hash"]),
            dict(d["params"]), float(d["weight"]), tuple(d.get("universe", ())),
            _required_timeframe(d), str(d["direction"]) if "direction" in d else None,
        )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Portfolio:
    campaign_id: str
    rule: PortfolioRule
    members: tuple[Member, ...]
    returns: pd.Series  # consolidated IS returns
    periods_per_year: float
    steps: dict[str, list[str]] = field(default_factory=dict)  # candidate ids after each step

    @property
    def portfolio_hash(self) -> str:
        return portfolio_hash(self.members, self.rule)

    @property
    def sharpe_is(self) -> float:
        r = self.returns.to_numpy(dtype=np.float64)
        std = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
        return float(np.mean(r) / std * np.sqrt(self.periods_per_year)) if std > 0 else 0.0


def portfolio_hash(members: Sequence[Member], rule: PortfolioRule) -> str:
    """Deterministic: independent of member order; changes with any member, weight or rule."""
    body = {
        "members": sorted((m.as_dict() for m in members), key=lambda d: d["strategy_hash"]),
        "rule": rule.as_dict(),
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_returns(path: str) -> pd.Series:
    df = pd.read_parquet(path, columns=["ts", "ret"])
    return pd.Series(df["ret"].to_numpy(dtype=np.float64), index=pd.to_datetime(df["ts"]))


def eligible_trials(ledger: Ledger, campaign_id: str) -> list[TrialRow]:
    """The latest trial of every candidate, if *that trial* passed gate ④ (ADR-0013).

    The latest measurement of a candidate id decides: a later trial that failed ③ or ④ (e.g. a
    failed calibration confirmation) takes the candidate out — an earlier pass never vouches for
    it, and there is no fallback to an older trial."""
    passed = ledger.passed_trials(campaign_id, G4_PBO)
    latest: dict[str, TrialRow] = {}
    for t in ledger.trials(campaign_id):
        latest[t.candidate_id] = t
    return [t for t in latest.values() if t.id in passed and t.verdict == "PASS"]


def selection_score(returns: pd.Series, sr_benchmark: float) -> float:
    """PSR against the campaign-wide deflated benchmark — for ranking inside §3.2.1 only."""
    return probabilistic_sharpe_ratio(sharpe_moments(returns.to_numpy()), sr_benchmark)


def _period_keys(index: pd.DatetimeIndex, rebalance: str) -> np.ndarray:
    if rebalance == "weekly":
        iso = index.isocalendar()
        years = np.asarray(iso["year"], dtype=np.int64)
        return years * 100 + np.asarray(iso["week"], dtype=np.int64)
    if rebalance == "monthly":
        return (index.year * 100 + index.month).to_numpy(dtype=np.int64)
    if rebalance == "quarterly":
        return (index.year * 10 + (index.month - 1) // 3).to_numpy(dtype=np.int64)
    raise ValueError(f"unknown rebalance schedule {rebalance!r}")


def combine(frame: pd.DataFrame, weights: np.ndarray, rebalance: str) -> pd.Series:
    """Portfolio returns: holdings reset to ``weights`` at the start of every period, drifting
    with each strategy's returns in between."""
    rets = frame.to_numpy(dtype=np.float64)
    keys = _period_keys(pd.DatetimeIndex(frame.index), rebalance)
    holdings = weights.astype(np.float64).copy()
    out = np.empty(len(frame))
    for i in range(len(frame)):
        if i > 0 and keys[i] != keys[i - 1]:
            holdings = weights * holdings.sum()
        value = holdings.sum()
        holdings = holdings * (1.0 + rets[i])
        out[i] = holdings.sum() / value - 1.0
    return pd.Series(out, index=frame.index)


def select_slots(
    trials: Sequence[Any],
    returns: Mapping[int, pd.Series],
    trial_stats: TrialStats,
    periods_per_year: float,
    max_strategies: int | None = None,
) -> list[Any]:
    """One member per ``(instrument, direction)`` slot — §3.2.1 step 1, as of ADR-0033.

    This **replaces** the cell-representative rule rather than sitting beside it. That rule was
    right when every candidate searched the same five-symbol basket, because two candidates in
    one feature-map cell really were near-duplicates. Once scopes are searched independently it
    is wrong: BTC-long and XRP-short landing in one cell are not duplicates, and deduping would
    silently drop one of them.

    A slot with no candidate that passed gate ④ with a positive IS Sharpe stays **empty**. The
    requirements are explicit that an empty slot is an outcome, not a gap to be filled by a
    neighbour or a post-hoc replacement.

    Ties break on the lowest trial id: deterministic, and it prefers the earlier trial rather
    than whichever happened to sort first.
    """
    if trial_stats.var_sr is None:
        raise ValueError("no trials in the ledger")
    eligible = []
    for t in trials:
        if t.instrument == LEGACY_INSTRUMENT:
            raise ValueError(
                f"trial {t.id} has the legacy scope: it searched the whole basket, so it belongs "
                "to no (instrument, direction) slot"
            )
        if not getattr(t, "passed4", True) or t.sharpe_is <= 0 or t.id not in returns:
            continue
        eligible.append(t)
    if not eligible:
        return []
    # per observation, like build_portfolio: `var_sr` is annualised in the ledger and
    # `selection_score` compares per-observation Sharpes
    var_sr = max(trial_stats.var_sr, 0.0) / periods_per_year
    sr0 = deflated_benchmark(max(trial_stats.n_eff, 1), var_sr)
    score = {t.id: selection_score(returns[t.id], sr0) for t in eligible}
    best: dict[tuple[str, str], Any] = {}
    for t in sorted(eligible, key=lambda t: (-score[t.id], t.id)):
        best.setdefault((t.instrument, t.direction), t)
    chosen = sorted(best.values(), key=lambda t: (-score[t.id], t.id))
    return chosen[:max_strategies] if max_strategies is not None else chosen


def build_portfolio(
    trials: Sequence[TrialRow],
    returns: Mapping[int, pd.Series],
    rule: PortfolioRule,
    trial_stats: TrialStats,
    periods_per_year: float,
    campaign_id: str,
) -> Portfolio:
    """Steps 1–5 of §3.2.1 on already-loaded IS returns (by trial id)."""
    if not trials:
        raise ValueError("no candidate passed gate ④: nothing to build a portfolio from")
    if trial_stats.var_sr is None:
        raise ValueError("no trials in the ledger")
    var_sr = max(trial_stats.var_sr, 0.0) / periods_per_year
    sr0 = deflated_benchmark(max(trial_stats.n_eff, 1), var_sr)
    score = {t.id: selection_score(returns[t.id], sr0) for t in trials}
    ordered = sorted(trials, key=lambda t: (-score[t.id], t.id))
    # 1. one representative per cell
    reps: dict[str, TrialRow] = {}
    for t in ordered:
        reps.setdefault(t.cell_id or f"_own:{t.id}", t)
    step1 = sorted(reps.values(), key=lambda t: (-score[t.id], t.id))
    # 2. correlation filter, in score order. ρ is pairwise, on each pair's own shared days, so a
    # candidate that is dropped never shapes what the members are measured on (ADR-0013); a pair
    # with too few shared days has no measurable ρ, and the lower-ranked one is dropped.
    everyone = pd.concat({t.id: returns[t.id] for t in step1}, axis=1, join="outer")
    corr = everyone.corr(min_periods=MIN_COMMON_OBS).to_numpy(dtype=np.float64)
    pos = {t.id: i for i, t in enumerate(step1)}
    accepted: list[TrialRow] = []
    for t in step1:
        rho = [corr[pos[t.id], pos[a.id]] for a in accepted]
        if all(abs(r) <= rule.max_corr for r in rho):  # NaN (too little overlap) fails
            accepted.append(t)
    # 3. size cap
    chosen = accepted[: rule.max_strategies]
    # 4. naive risk parity — on the members' common window only
    frame = pd.concat({t.id: returns[t.id] for t in chosen}, axis=1, join="inner")
    if len(frame) < MIN_COMMON_OBS:
        raise ValueError(f"only {len(frame)} common observations across the members")
    vols = frame.std(ddof=1).to_numpy(dtype=np.float64)
    inv = np.where(vols > 0, 1.0 / np.where(vols > 0, vols, 1.0), 0.0)
    if not inv.sum() > 0:
        raise ValueError("every selected strategy has zero IS volatility")
    weights = inv / inv.sum()
    members = tuple(
        Member(
            t.id, t.candidate_id, t.strategy_hash, dict(t.params), float(w),
            tuple(s for s in t.universe.split(",") if s), t.timeframe,
            t.direction if t.instrument != LEGACY_INSTRUMENT else None,
        )
        for t, w in zip(chosen, weights, strict=True)
    )  # fmt: skip
    # 5. rebalanced consolidated returns
    combined = combine(frame, weights, rule.rebalance)
    steps = {
        "eligible": [t.candidate_id for t in ordered],
        "representatives": [t.candidate_id for t in step1],
        "decorrelated": [t.candidate_id for t in accepted],
        "capped": [t.candidate_id for t in chosen],
    }
    return Portfolio(campaign_id, rule, members, combined, periods_per_year, steps)


def record_variant(ledger: Ledger, portfolio: Portfolio, results_dir: Path) -> bool:
    """Step 6: write the variant (and its IS returns) once. Returns False if this exact
    portfolio — same members, weights and rule — was already recorded (not a new selection).

    A recorded hash must carry the same returns as its artifact: the gates never evaluate data
    other than what the variant stored (review 2, ADR-0013)."""
    p_hash = portfolio.portfolio_hash
    recorded = [v for v in ledger.portfolio_variants() if v.portfolio_hash == p_hash]
    if recorded:
        stored = load_returns(recorded[0].returns_path or "")
        same = (
            len(stored) == len(portfolio.returns)
            and np.array_equal(
                stored.index.to_numpy(dtype="datetime64[ns]"),
                portfolio.returns.index.to_numpy(dtype="datetime64[ns]"),
            )
            and np.allclose(stored.to_numpy(), portfolio.returns.to_numpy(), rtol=0.0, atol=1e-12)
        )
        if not same:
            raise ValueError(
                f"portfolio {p_hash[:12]}… differs from its recorded artifact "
                f"({len(stored)} vs {len(portfolio.returns)} observations): refusing to evaluate"
            )
        return False
    campaign = ledger.campaign(portfolio.campaign_id)
    if campaign is None or campaign.status != "OPEN":
        raise ValueError(f"campaign {portfolio.campaign_id} is not OPEN: no new portfolio variants")
    path = results_dir / "portfolios" / portfolio.campaign_id / f"{p_hash}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts": portfolio.returns.index, "ret": portfolio.returns.to_numpy()}).to_parquet(
        path, index=False
    )
    ledger.record_portfolio_variant(
        PortfolioVariant(
            portfolio_hash=p_hash,
            campaign_id=portfolio.campaign_id,
            rule_config=portfolio.rule.as_dict(),
            members=[m.as_dict() for m in portfolio.members],
            sharpe_is=portfolio.sharpe_is,
            returns_path=str(path),
        )
    )
    return True


def build_and_record(
    ledger: Ledger,
    campaign_id: str,
    rule: PortfolioRule,
    periods_per_year: float,
    results_dir: Path,
) -> Portfolio:
    trials = eligible_trials(ledger, campaign_id)
    returns = {t.id: load_returns(t.returns_path) for t in trials}
    portfolio = build_portfolio(
        trials, returns, rule, ledger.trial_stats(), periods_per_year, campaign_id
    )
    record_variant(ledger, portfolio, results_dir)
    return portfolio


# ── the perpetual path: one account replay, not a weighted sum (P3-21, ADR-0035) ───────


def load_signal_stream(path: Path) -> list[SlotPlan]:
    """Read one trial's signal stream back as slot plans, one per instrument it traded.

    The slot's side is taken from the signals themselves rather than passed in: INV-91 guarantees
    a strategy only ever emits its own side, so the stream *is* the declaration. A stream carrying
    both sides for one instrument is that invariant broken upstream, and it is refused here rather
    than resolved by picking one.

    Written by gate ③ beside the returns curve under the same id, so the ledger's ``returns_path``
    locates it (``is_gates.signals_path_for``).
    """
    frame = pd.read_parquet(path).sort_values(["symbol", "bar"])
    plans: list[SlotPlan] = []
    for symbol, rows in frame.groupby("symbol", sort=True):
        directions: list[Direction] = []
        for value in rows["direction"]:
            name = str(value)
            if name not in ("long", "short", "flat"):
                raise ValueError(f"{path.name}: {symbol} has an unknown direction {name!r}")
            directions.append(cast("Direction", name))
        strengths = rows["strength"].to_numpy(dtype=np.float64)
        stops = rows["stop_distance"].to_numpy(dtype=np.float64)
        # NaN is how "no take profit" survives a float column; `Signal` wants None back.
        profits = rows["take_profit"].to_numpy(dtype=np.float64)
        signals = tuple(
            Signal(
                directions[i],
                float(strengths[i]),
                float(stops[i]),
                None if not math.isfinite(float(profits[i])) else float(profits[i]),
            )
            for i in range(len(directions))
        )
        sides = {s.direction for s in signals if s.direction != "flat"}
        if len(sides) > 1:
            raise ValueError(
                f"{path.name}: {symbol} emits {sorted(sides)} — a scope trades one side (INV-91)"
            )
        if not sides:
            continue  # never traded; it contributes no slot rather than an empty long one
        side = sides.pop()
        plans.append(SlotPlan(str(symbol), "long" if side == "long" else "short", signals))
    return plans


ACCOUNT_COLUMNS = ("ts", "equity")


def write_account_curve(
    results_dir: Path,
    campaign_id: str,
    portfolio_hash: str,
    *,
    ts: npt.NDArray[np.datetime64],
    equity: npt.NDArray[np.float64],
    initial_cash: float,
    contributions: Mapping[tuple[str, str], npt.NDArray[np.float64]],
) -> Path:
    """Persist the account curve and its per-slot decomposition for the review UI (P3-23).

    One column per slot, named ``"<instrument>-<direction>"``, alongside the account's own equity
    and the balance it started from. All three are needed because the screen's promise is
    arithmetic — ``initial_cash + Σ contributions == equity`` at every bar — and a reader that has
    to infer the starting balance cannot check it.
    """
    frame = pd.DataFrame({"ts": ts, "equity": equity})
    for (instrument, direction), series in sorted(contributions.items()):
        frame[f"{instrument}-{direction}"] = series
    frame.attrs.clear()
    path = results_dir / "account" / campaign_id / f"{portfolio_hash}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    # The opening balance travels as a column of its own rather than in metadata: parquet
    # key-value metadata is easy to drop on a rewrite, and losing it would silently turn the
    # reconciliation into an unanswerable question.
    frame["initial_cash"] = float(initial_cash)
    frame.to_parquet(path, index=False)
    return path


def consolidate_on_account(
    members: Sequence[Member],
    streams: Mapping[int, Path],
    bars: Mapping[str, Bars],
    inputs: Mapping[str, PerpBundle],
    *,
    settings: RiskSettings,
    costs: CostModel,
    initial_cash: float,
    leverage: int,
    max_portfolio_risk_pct: float = 0.10,
) -> SignalReplay:
    """§3.2.1 steps 4–5 on the perpetual path: replace the weighted sum with one account replay.

    Under ``Q = R/d`` every slot risks exactly 1% and the 10% cap bounds the total, so there is
    nothing for naive risk parity to weight — and a weighted blend of standalone streams would
    describe positions that never competed for the same margin, the same cap or the same ten
    slots. The account replay *is* the portfolio.

    A member's stream is matched to it by **instrument**, not by list position: a silent mismatch
    would replay one strategy's signals against another strategy's market.
    """
    plans: list[SlotPlan] = []
    seen: set[tuple[str, str]] = set()
    for member in members:
        if member.direction not in ("long", "short"):
            raise ValueError(f"{member.candidate_id}: perpetual member has no locked direction")
        path = streams.get(member.trial_id)
        if path is None:
            raise ValueError(
                f"{member.candidate_id}: no signal stream for trial {member.trial_id}; a "
                "perpetual portfolio is replayed from signals, not returns (ADR-0035)"
            )
        for plan in load_signal_stream(path):
            if plan.direction != member.direction:
                raise ValueError(
                    f"{member.candidate_id}: signal direction {plan.direction} differs from "
                    f"locked {member.direction} direction"
                )
            if plan.instrument not in member.universe:
                raise ValueError(
                    f"{member.candidate_id}: its signal stream names {plan.instrument}, which is "
                    f"not in its universe {list(member.universe)}"
                )
            if plan.instrument not in bars or len(plan.signals) != len(bars[plan.instrument]):
                raise ValueError(
                    f"{member.candidate_id}: signal stream length does not match "
                    f"{plan.instrument} bars"
                )
            if plan.slot in seen:
                raise ValueError(f"duplicate perpetual slot {plan.slot}: one strategy per side")
            seen.add(plan.slot)
            plans.append(plan)
    return replay_signals(
        plans,
        bars,
        inputs,
        initial_cash=initial_cash,
        settings=settings,
        costs=costs,
        leverage=leverage,
        max_portfolio_risk_pct=max_portfolio_risk_pct,
    )
