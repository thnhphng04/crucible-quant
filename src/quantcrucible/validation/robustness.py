"""Gate ⑥′ — data-source robustness + cost sensitivity of the portfolio (Architecture §3.2 row
⑥′, §6.1 "two-tier data", ADR-0015).

Every member is re-run in the sandbox from its archived source (ADR-0013):

* **Costs × 2** — fees and slippage doubled on the primary data. The consolidated portfolio must
  keep Sharpe > 0 and its DSR at ``N_eff`` must still clear ``dsr_min`` (the campaign-locked
  threshold; user decision 21 Sep 2026).
* **Second source** — the same code, costs and sizing on the second free source's in-sample bars.
  The portfolio's Sharpe there may not fall more than ``max_sharpe_drop`` (30%, §6.1) below its
  Sharpe on the primary source over the same dates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from quantcrucible.core.strategy.base import Bars
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.gates import GateContext, GateResult
from quantcrucible.validation.is_gates import backtest_options
from quantcrucible.validation.portfolio import Member, Portfolio, combine, load_returns
from quantcrucible.validation.portfolio_dsr import G6P_ROBUSTNESS
from quantcrucible.validation.sandbox import SandboxJob, SandboxRunner
from quantcrucible.validation.statistical import portfolio_dsr

COST_MULTIPLIER = 2.0
MAX_SHARPE_DROP = 0.30


class RerunError(RuntimeError):
    """A member could not be re-run in the sandbox."""


def rerun_member(
    member: Member,
    source: str,
    bars: Mapping[str, Bars],
    options: Mapping[str, Any],
    runner: SandboxRunner,
) -> pd.Series:
    """One member's per-bar returns on ``bars`` (its own universe), via the sandbox."""
    missing = [s for s in member.universe if s not in bars]
    if missing:
        raise RerunError(f"{member.candidate_id}: no data for {missing}")
    universe = {s: bars[s] for s in member.universe}
    res = runner.run(SandboxJob("backtest", source, universe, member.params, options))
    if not res.ok or res.report is None:
        raise RerunError(f"{member.candidate_id}: sandbox {res.error}")
    out: dict[str, Any] = res.report["result"]
    return pd.Series(
        np.asarray(out["returns"], dtype=np.float64), index=pd.to_datetime(out["ts"][1:])
    )


def rerun_portfolio(
    portfolio: Portfolio,
    archive: StrategyArchive,
    bars: Mapping[str, Bars],
    options: Mapping[str, Any],
    runner: SandboxRunner,
) -> pd.DataFrame:
    """Every member re-run; columns = member trial ids, inner-joined on common timestamps."""
    series = {
        m.trial_id: rerun_member(m, archive.get(m.strategy_hash), bars, options, runner)
        for m in portfolio.members
    }
    return pd.concat(series, axis=1, join="inner")


def weights_of(members: Sequence[Member]) -> np.ndarray:
    return np.array([m.weight for m in members], dtype=np.float64)


def annual_sharpe(r: pd.Series, periods_per_year: float) -> float:
    x = r.to_numpy(dtype=np.float64)
    std = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    return float(np.mean(x) / std * np.sqrt(periods_per_year)) if std > 0 else 0.0


def primary_member_returns(ledger: Ledger, members: Sequence[Member]) -> pd.DataFrame:
    paths = {t.id: t.returns_path for t in ledger.trials()}
    return pd.concat({m.trial_id: load_returns(paths[m.trial_id]) for m in members}, axis=1)


class RobustnessGate:
    id = G6P_ROBUSTNESS
    cost = 4

    def check(self, portfolio: Portfolio, ctx: GateContext) -> GateResult:
        runner: SandboxRunner = ctx.services["sandbox"]
        archive: StrategyArchive = ctx.services["archive"]
        dsr_min = float(ctx.lock["research"]["gates"]["dsr_min"])
        settings: Mapping[str, Any] = ctx.lock["derived"].get("robustness", {})
        mult = float(settings.get("cost_multiplier", COST_MULTIPLIER))
        max_drop = float(settings.get("max_sharpe_drop", MAX_SHARPE_DROP))
        base = backtest_options(ctx.lock, seed=0)
        ppy = portfolio.periods_per_year
        weights = weights_of(portfolio.members)
        cols = [m.trial_id for m in portfolio.members]
        problems: list[str] = []

        # ── costs × 2 on the primary source ──────────────────────────────────────────
        stressed_costs = {k: float(v) * mult for k, v in base["costs"].items()}
        stressed = rerun_portfolio(
            portfolio, archive, ctx.services["is_data"], {**base, "costs": stressed_costs},
            runner,
        )  # fmt: skip
        stressed_ret = combine(stressed[cols], weights, portfolio.rule.rebalance)
        sharpe_stressed = annual_sharpe(stressed_ret, ppy)
        dsr = portfolio_dsr(
            stressed_ret.to_numpy(), ctx.ledger.trial_stats(),
            ctx.ledger.total_portfolio_variants(), ppy,
        )  # fmt: skip
        if not sharpe_stressed > 0:
            problems.append(f"Sharpe {sharpe_stressed:.2f} with costs × {mult:g} is not > 0")
        if not dsr.dsr_n_eff >= dsr_min:
            problems.append(f"DSR {dsr.dsr_n_eff:.3f} with costs × {mult:g} < {dsr_min:g}")

        # ── second data source ───────────────────────────────────────────────────────
        second: Mapping[str, Bars] | None = ctx.services.get("second_is_data")
        detail: dict[str, Any] = {
            "cost_multiplier": mult, "sharpe_stressed": sharpe_stressed,
            "dsr_stressed_n_eff": dsr.dsr_n_eff, "dsr_stressed_n_raw": dsr.dsr_n_raw,
        }  # fmt: skip
        if not second:
            problems.append("no second-source data (research.data.second_exchange)")
        else:
            alt = rerun_portfolio(portfolio, archive, second, base, runner)
            primary = primary_member_returns(ctx.ledger, portfolio.members)
            common = alt.index.intersection(primary.dropna().index)
            if len(common) < 60:
                problems.append(f"only {len(common)} common bars with the second source")
            else:
                rebalance = portfolio.rule.rebalance
                s_alt = annual_sharpe(combine(alt.loc[common, cols], weights, rebalance), ppy)
                s_pri = annual_sharpe(combine(primary.loc[common, cols], weights, rebalance), ppy)
                drop = 1.0 - s_alt / s_pri if s_pri > 0 else float("inf")
                detail.update({
                    "sharpe_primary_common": s_pri, "sharpe_second": s_alt, "sharpe_drop": drop,
                    "second_range": f"{common[0].date()}/{common[-1].date()}",
                })  # fmt: skip
                if not drop <= max_drop:
                    problems.append(
                        f"Sharpe {s_pri:.2f} → {s_alt:.2f} on the second source "
                        f"(drop {drop:.0%} > {max_drop:.0%})"
                    )
        summary = (
            f"costs × {mult:g}: Sharpe {sharpe_stressed:.2f}, DSR {dsr.dsr_n_eff:.3f}; "
            f"second source drop {detail.get('sharpe_drop', float('nan')):.0%}"
        )
        return GateResult(
            passed=not problems,
            gate=self.id,
            value=sharpe_stressed,
            reason="; ".join(problems) or summary,
            detail=detail,
        )
