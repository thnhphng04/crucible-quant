"""Ranking score of engine C-gp's archive (arch §3.1.6 #1, D20, ADR-0026, P2-11).

    score = DSR-rank: PSR(IS returns vs SR₀(N_eff, V[SR]))— in [0, 1], ADR-0013's convention
          − λ_sim    · novelty penalty                    — clause-signature Jaccard with the
                                                             most similar earlier entry
          − λ_params · n_params / 6                        — complexity
          + λ_spp    · tanh(SPP median Sharpe)             — D20, secondary: the grid's median
          + λ_plat   · plateau                             — D20, secondary: flat neighbourhood

A **ranking** for the search, not a gate (§3.1.6): no per-strategy DSR exists (INV-42) — the
DSR-rank is the PSR of the candidate's IS returns against the campaign-wide deflated benchmark,
exactly as portfolio construction ranks cell representatives (ADR-0013), and it is never compared
with ``dsr_min``; gate ⑤ tests the portfolio. It reads only
``public`` metrics (IS-only, §3.3.2) and the ledger's trial statistics — never PBO, CPCV or any
other ``private`` value (INV-68). The λ are provisional defaults (ADR-0026).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from quantcrucible.agent.evolution.archive import Entry
from quantcrucible.core.strategy.tunable import MAX_TUNABLES
from quantcrucible.validation.statistical import (
    SharpeMoments,
    deflated_benchmark,
    probabilistic_sharpe_ratio,
)

LAMBDA_SIM = 0.10
LAMBDA_PARAMS = 0.05
LAMBDA_SPP = 0.05
LAMBDA_PLATEAU = 0.05


@dataclass(frozen=True, slots=True)
class RankContext:
    """The ledger-wide statistics DSR deflates with (``trial_stats``), taken once per ranking."""

    n_trials: int  # N_eff
    var_sr: float  # variance of the trials' annualized IS Sharpe
    periods_per_year: float


def dsr_rank(entry: Entry, ctx: RankContext) -> float:
    """PSR of the entry's IS returns against SR₀(N_eff, V[SR]) — 0 without IS moments."""
    p = entry.public
    if not {"sr_obs", "skew_is", "kurtosis_is", "n_obs"} <= set(p):
        return 0.0
    m = SharpeMoments(p["sr_obs"], p["skew_is"], p["kurtosis_is"], int(p["n_obs"]))
    var_sr = max(ctx.var_sr, 0.0) / ctx.periods_per_year
    try:
        value = probabilistic_sharpe_ratio(m, deflated_benchmark(max(ctx.n_trials, 1), var_sr))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def scores(entries: Sequence[Entry], ctx: RankContext) -> dict[str, float]:
    """candidate_id → score. Novelty compares each entry with the entries before it (trial
    order), so the ranking is a function of the ledger alone."""
    out: dict[str, float] = {}
    seen: list[tuple[str, ...]] = []
    for e in sorted(entries, key=lambda x: x.trial_id):
        sim = max((jaccard(e.signature, s) for s in seen), default=0.0) if e.signature else 0.0
        p = e.public
        value = (
            dsr_rank(e, ctx)
            - LAMBDA_SIM * sim
            - LAMBDA_PARAMS * len(e.params) / MAX_TUNABLES
            + LAMBDA_SPP * math.tanh(float(p.get("spp_median_sharpe", 0.0)))
            + LAMBDA_PLATEAU * float(p.get("plateau", 0.0))
        )
        out[e.candidate_id] = value
        if e.signature:
            seen.append(e.signature)
    return out
