"""Gates ② MinBTL and ③ in-sample backtest (Architecture §3.2, 07-VALIDATION-LAYER §4.4, D15).

Both read their settings from the campaign lock (never ``user.yaml``) and their data from
``ctx.services['is_data']`` — in-sample bars by symbol, already checked against the holdout by
``ResearchStore``. ③ is where a candidate becomes a statistical trial: its result carries a
:class:`TrialMeasurement`, so the pipeline writes a ``trials`` row whatever the verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars
from quantcrucible.validation.artifacts import measurement_path, write_parquet_once
from quantcrucible.validation.gates import (
    G2_MINBTL,
    G3_IS,
    GateContext,
    GateResult,
    StrategyCandidate,
    TrialMeasurement,
)
from quantcrucible.validation.report import EvaluationReport
from quantcrucible.validation.sandbox import SandboxJob, SandboxRunner, sandbox_failure
from quantcrucible.validation.statistical import min_btl_years, sharpe_moments

DAYS_PER_YEAR = 365.25


def universe_bars(candidate: StrategyCandidate, ctx: GateContext) -> dict[str, Bars]:
    data: Mapping[str, Bars] = ctx.services["is_data"]
    missing = [s for s in candidate.universe if s not in data or not len(data[s])]
    if missing or not candidate.universe:
        raise ValueError(f"no IS data for {missing or 'an empty universe'}")
    return {s: data[s] for s in candidate.universe}


def is_perpetual(symbol: str) -> bool:
    """ccxt spells a perpetual ``BASE/QUOTE:SETTLE``. Read off the symbol rather than a config
    flag, so the requirement cannot be switched off by a lock that forgot to name the market —
    and so a spot campaign, which has no mark and no funding, is unaffected."""
    return ":" in symbol


def perp_inputs(candidate: StrategyCandidate, ctx: GateContext) -> dict[str, PerpBundle] | None:
    """The mark, funding, path and bracket bundle for a perpetual candidate, or ``None`` for spot.

    Fails closed (INV-94): a perpetual candidate with no bundle would be backtested on a market
    with no funding cost and no liquidation, and would report a Sharpe that looks like every
    other Sharpe.
    """
    perps = [s for s in candidate.universe if is_perpetual(s)]
    if not perps:
        return None
    available: Mapping[str, PerpBundle] = ctx.services.get("perp_data") or {}
    missing = [s for s in perps if s not in available]
    if missing:
        raise ValueError(
            f"no perpetual inputs for {', '.join(missing)}: mark, funding, intrabar paths and "
            "leverage brackets are required, and refusing is the only honest answer"
        )
    return {s: available[s] for s in perps}


def write_signal_stream(returns_path: Path, stream: Mapping[str, Any]) -> Path | None:
    """Persist the signal stream beside the returns curve, under the same unique id.

    A portfolio replay sizes from signals rather than from fills (ADR-0035), so this is where the
    stream has to be captured. Sharing the returns file's id means the ledger's ``returns_path``
    locates it with no new column and no ambiguity when a candidate is measured twice.
    """
    if not stream:
        return None
    rows = [
        (
            symbol,
            bar,
            str(sig[0]),
            float(sig[1]),
            float(sig[2]),
            # `Signal.take_profit` is optional; a float column carries the absence as NaN, and
            # `load_signal_stream` turns it back into None. Writing 0.0 would be a *value*, and
            # `Signal` rejects a take profit of zero.
            float("nan") if sig[3] is None else float(sig[3]),
        )
        for symbol, signals in stream.items()
        for bar, sig in enumerate(signals)
    ]
    path = signals_path_for(returns_path)
    write_parquet_once(
        path,
        pd.DataFrame(
            rows, columns=["symbol", "bar", "direction", "strength", "stop_distance", "take_profit"]
        ),
    )
    return path


def signals_path_for(returns_path: Path) -> Path:
    """Where a trial's signal stream lives, given the returns path the ledger recorded."""
    parts = list(returns_path.parts)
    parts[parts.index("returns")] = "signals"
    return Path(*parts)


def risk_settings(lock: Mapping[str, Any]) -> dict[str, Any]:
    """Sizing locked for the campaign: ``max_risk_pct`` from Group B (D12).

    Two locks are refused rather than mixed, because a campaign's trials must all be sized the
    same way: one written before P1-06, which has no ``derived.sizing`` at all, and one written
    under vol targeting, which carries ``max_leverage`` (ADR-0031 retired P4)."""
    derived: Mapping[str, Any] = lock["derived"]
    if "sizing" not in derived:
        raise ValueError(
            "this campaign's lock predates the Risk layer (no derived.sizing): its trials were "
            "sized differently — open a new campaign"
        )
    sizing: Mapping[str, Any] = derived["sizing"]
    if "max_leverage" in sizing or "idm_cap" in sizing:
        raise ValueError(
            "this campaign's lock was written under vol targeting (derived.sizing.max_leverage): "
            "its trials were sized differently — open a new campaign"
        )
    research: Mapping[str, Any] = lock["research"]
    return {"max_risk_pct": float(research["max_risk_pct"])}


def backtest_options(lock: Mapping[str, Any], seed: int) -> dict[str, Any]:
    """Everything a sandbox backtest job needs from the campaign lock: costs, lookback, sizing."""
    derived: Mapping[str, Any] = lock["derived"]
    data: Mapping[str, Any] = lock["research"].get("data", {})
    return {
        "costs": dict(derived["costs"]),
        "lookback": int(derived.get("lookback", 400)),
        "risk": risk_settings(lock),
        "leverage": int(data.get("leverage", 5)),
        "seed": seed,
    }


def span_years(bars: Bars) -> float:
    span = (bars.ts[-1] - bars.ts[0]) / np.timedelta64(1, "D")
    return float(span) / DAYS_PER_YEAR


class MinBtlGate:
    """Gate ②: reject when the IS history is shorter than MinBTL for the trials run so far.

    ``N`` is the campaign-independent ``trial_stats.n_eff`` (N_eff; N_raw until clustering runs)
    plus this candidate. The shortest series in the universe sets the available length.
    """

    id = G2_MINBTL
    cost = 1

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        target = float(ctx.lock["research"]["minbtl_target_sharpe"])
        years = min(span_years(b) for b in universe_bars(candidate, ctx).values())
        n = ctx.ledger.trial_stats().n_eff + 1
        need = min_btl_years(n, target)
        verdict = f"IS {years:.2f} y vs MinBTL {need:.2f} y (N={n}, target Sharpe {target:g})"
        detail = {"is_years": years, "min_btl_years": need, "n_trials": n}
        return GateResult(years >= need, self.id, years, verdict, detail=detail)


def _moments(returns: Any) -> dict[str, float]:
    """Per-observation Sharpe, skew, kurtosis and length of the IS returns (IS-only, public);
    empty when they are undefined (fewer than 4 returns, zero variance, non-finite values)."""
    try:
        m = sharpe_moments(returns)
    except ValueError:
        return {}
    return {"sr_obs": m.sr, "skew_is": m.skew, "kurtosis_is": m.kurtosis, "n_obs": float(m.n_obs)}


class InSampleGate:
    """Gate ③: IS backtest in the sandbox + the D15 constraints (trades, holding, indicator ρ)."""

    id = G3_IS
    cost = 3

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        runner: SandboxRunner = ctx.services["sandbox"]
        results_dir = Path(ctx.services["results_dir"])
        limits: Mapping[str, Any] = ctx.lock["research"]["constraints"]
        options = backtest_options(ctx.lock, candidate.seed)
        try:
            perp = perp_inputs(candidate, ctx)
        except ValueError as exc:
            return GateResult(passed=False, gate=self.id, value=None, reason=str(exc))
        job = SandboxJob(
            "backtest", candidate.source, universe_bars(candidate, ctx), candidate.params,
            options, perp=perp,
        )  # fmt: skip
        res = runner.run(job)
        if not res.ok or res.report is None:
            return sandbox_failure(self.id, res)
        out: dict[str, Any] = res.report["result"]
        public = {k: float(v) for k, v in out["public"].items()}
        public.update(_moments(out["returns"]))  # for the engines' DSR ranking (§3.1.6 #1)
        path = measurement_path(
            results_dir / "returns" / candidate.campaign_id, candidate.candidate_id
        )
        write_parquet_once(
            path,
            pd.DataFrame(
                {
                    "ts": pd.to_datetime(out["ts"][1:]),
                    "ret": np.asarray(out["returns"], dtype=float),
                }
            ),
        )
        write_signal_stream(path, out.get("signals_stream") or {})
        problems: list[str] = []
        # written as `not (ok)` so a NaN on either side fails closed instead of passing
        if not public["n_trades"] >= limits["min_trades"]:
            problems.append(f"{public['n_trades']:.0f} trades < min_trades {limits['min_trades']}")
        if not public["avg_holding_bars"] >= limits["min_holding_bars"]:
            problems.append(
                f"average holding {public['avg_holding_bars']:.2f} bars"
                f" < min_holding_bars {limits['min_holding_bars']}"
            )
        corr = float(out["indicator_corr"])
        if not corr <= limits["max_indicator_corr"]:
            pair = " / ".join(out["indicator_pair"] or [])
            problems.append(
                f"indicators {pair} |ρ| {corr:.3f} > max_indicator_corr"
                f" {limits['max_indicator_corr']}"
            )
        sharpe = public["sharpe_is"]
        summary = f"IS Sharpe {sharpe:.2f}, {public['n_trades']:.0f} trades"
        return GateResult(
            passed=not problems,
            gate=self.id,
            value=sharpe,
            reason="; ".join(problems) or summary,
            # `public` (IS-only, §3.3.2) is kept so an engine's archive rebuilds from the ledger
            detail={
                "indicator_corr": corr,
                "denied_orders": out["denied_orders"],
                "public": public,
            },
            report=EvaluationReport.build(public=public),
            measurement=TrialMeasurement(sharpe, str(path)),
        )
