"""Portfolio-level gates ⑤ → ⑥′ and gate ⑤ itself: DSR of the consolidated portfolio
(Architecture §3.2 rows ⑤–⑥′, §3.1.6, §4.1, ADR-0014).

From ⑤ on the object under test is the **portfolio**, not a candidate. The portfolio pipeline
mirrors the candidate one: explicit order, stop at the first failure, fail closed on an
exception, and every result — pass or fail — written to ``gate_results`` with
``candidate_id = portfolio_hash``, together with the ledger snapshot (trial and variant counts)
the run started from: a result is only current while the snapshot is (the freeze checks it).

Gate ⑤ re-estimates ``N_eff`` (ONC over every trial), then deflates the portfolio's IS Sharpe
with ``trial_stats`` (``N_eff``, ``V[SR]``) plus ``total_portfolio_variants`` — the only inputs it
reads. There is no per-cell or per-strategy DSR anywhere (§3.1.6).
"""

from __future__ import annotations

import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from quantcrucible.ledger.records import GateResultRecord
from quantcrucible.validation.gates import GateContext, GateResult
from quantcrucible.validation.n_eff import update_n_eff
from quantcrucible.validation.portfolio import Portfolio
from quantcrucible.validation.statistical import portfolio_dsr

G5_DSR = "g5_dsr"
G6P_ROBUSTNESS = "g6p_robustness"
PORTFOLIO_GATE_ORDER: tuple[str, ...] = (G5_DSR, G6P_ROBUSTNESS)
SNAPSHOT_KEY = "ledger_snapshot"


class PortfolioGate(Protocol):
    id: str
    cost: int

    def check(self, portfolio: Portfolio, ctx: GateContext) -> GateResult: ...


@dataclass(frozen=True, slots=True)
class PortfolioOutcome:
    portfolio_hash: str
    passed: bool
    results: tuple[GateResult, ...]

    @property
    def failed_gate(self) -> str | None:
        return next((r.gate for r in self.results if not r.passed), None)


class PortfolioPipeline:
    def __init__(self, gates: Sequence[PortfolioGate]) -> None:
        ids = [g.id for g in gates]
        if any(i not in PORTFOLIO_GATE_ORDER for i in ids) or ids != sorted(
            ids, key=PORTFOLIO_GATE_ORDER.index
        ):
            raise ValueError(f"portfolio gates must follow {PORTFOLIO_GATE_ORDER}, got {ids}")
        self.gates = tuple(gates)

    def run(self, portfolio: Portfolio, ctx: GateContext) -> PortfolioOutcome:
        p_hash = portfolio.portfolio_hash
        snapshot = ctx.ledger.snapshot()
        results: list[GateResult] = []
        for gate in self.gates:
            try:
                result = gate.check(portfolio, ctx)
                if result.gate != gate.id:
                    raise RuntimeError(f"gate {gate.id} returned a result labelled {result.gate}")
            except Exception as e:  # fail closed (§3.2)
                result = GateResult(
                    False, gate.id, None, f"exception in gate: {type(e).__name__}: {e}",
                    detail={"traceback": traceback.format_exc(limit=20)},
                )  # fmt: skip
            results.append(result)
            ctx.ledger.record_gate_result(
                GateResultRecord(
                    campaign_id=portfolio.campaign_id, candidate_id=p_hash, gate=gate.id,
                    passed=result.passed, reason=result.reason, value=result.value,
                    detail={**(result.detail or {}), SNAPSHOT_KEY: snapshot},
                )
            )  # fmt: skip
            if not result.passed:
                return PortfolioOutcome(p_hash, False, tuple(results))
        return PortfolioOutcome(p_hash, True, tuple(results))


class DsrGate:
    """Gate ⑤: DSR(N_eff) of the consolidated portfolio ≥ ``gates.dsr_min``; DSR(N_raw) is
    always reported next to it (§4.1)."""

    id = G5_DSR
    cost = 4

    def check(self, portfolio: Portfolio, ctx: GateContext) -> GateResult:
        dsr_min = float(ctx.lock["research"]["gates"]["dsr_min"])
        clustering = update_n_eff(ctx.ledger, seed=0)
        report = portfolio_dsr(
            portfolio.returns.to_numpy(),
            ctx.ledger.trial_stats(),
            ctx.ledger.total_portfolio_variants(),
            portfolio.periods_per_year,
        )
        reason = (
            f"DSR {report.dsr_n_eff:.3f} at N_eff={report.n_eff} "
            f"(N_raw={report.n_raw}: {report.dsr_n_raw:.3f}); min {dsr_min:g}"
        )
        return GateResult(
            passed=report.dsr_n_eff >= dsr_min,
            gate=self.id,
            value=report.dsr_n_eff,
            reason=reason,
            detail={
                "dsr_n_eff": report.dsr_n_eff, "dsr_n_raw": report.dsr_n_raw,
                "n_eff": report.n_eff, "n_raw": report.n_raw, "var_sr": report.var_sr,
                "sr_benchmark_n_eff": report.sr_benchmark_n_eff,
                "sr_benchmark_n_raw": report.sr_benchmark_n_raw,
                "sr": report.moments.sr, "skew": report.moments.skew,
                "kurtosis": report.moments.kurtosis, "n_obs": report.moments.n_obs,
                "clustering_run": clustering.run_id,
            },
        )  # fmt: skip
