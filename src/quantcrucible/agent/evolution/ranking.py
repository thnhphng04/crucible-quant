"""Ranking score of engine C-gp's archive (arch §3.1.6 #1, D20, ADR-0026, ADR-0029, P2-11).

    score = primary  : rank of the DSR-rank within the scored population — [0, 1], ADR-0029
          − λ_sim    · novelty penalty                    — clause-signature Jaccard with the
                                                             most similar earlier entry
          − λ_params · n_params / 6                        — complexity
          + λ_spp    · tanh(SPP median Sharpe)             — D20, secondary: the grid's median
          + λ_plat   · plateau                             — D20, secondary: flat neighbourhood

A **ranking** for the search, not a gate (§3.1.6): no per-strategy DSR exists (INV-42) — the
DSR-rank is the PSR of the candidate's IS returns against the campaign-wide deflated benchmark,
exactly as portfolio construction ranks cell representatives (ADR-0013), and it is never compared
with ``dsr_min``; gate ⑤ tests the portfolio. It reads only ``public`` metrics (IS-only,
§3.3.2) and the ledger's trial statistics — never PBO, CPCV or any other ``private`` value
(INV-68). The λ are ADR-0026's provisional defaults, left unchanged by ADR-0029.

**Why the primary term is a rank (ADR-0029).** The PSR *value* measures a candidate against
SR₀(N_eff, V[SR]), which climbs with the trial count. In campaign ``c-20260923-090957`` it
reached ≈ 1.54 annualised against a population whose 90th percentile was 1.22, so the PSR
collapsed — median 0.0187 against auxiliary terms summing to 0.081 — and the "secondary" terms
chose every parent. Ranking is invariant to that collapse while preserving the PSR ordering, so
the emphasis ADR-0026 intended survives wherever the benchmark lands. It removes SR₀'s influence
on the **scale**, not on the **ordering**: PSR still divides by the moments and by √(T−1), so
short, skewed and fat-tailed records stay penalised and ``RankContext`` still changes who
outranks whom.

⚠️ Scores are **not comparable across populations**: 0.8 among 20 entries is not 0.8 among 145.
Only ever compare scores produced by one call — every consumer does.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
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


@dataclass(frozen=True, slots=True)
class Terms:
    """The five signed contributions ``scores`` adds up, kept apart so the dispersion check
    measures the terms the scorer actually sums (ADR-0029)."""

    primary: float
    similarity: float
    params: float
    spp: float
    plateau: float

    @property
    def total(self) -> float:
        return self.primary + self.similarity + self.params + self.spp + self.plateau


def primary_rank(entries: Sequence[Entry], ctx: RankContext) -> dict[str, float]:
    """candidate_id → the DSR-rank's rank within ``entries``, normalised to [0, 1].

    Ties share their average rank. That is not a detail: ``dsr_rank`` returns a literal 0.0 for
    every entry without IS moments, so ordinal ranking would spread that block across the bottom
    of the interval **in trial order** and reward whichever one ran first. A single entry scores
    the midpoint, 0.5, being neither the best nor the worst of anything.
    """
    values = {e.candidate_id: dsr_rank(e, ctx) for e in entries}
    n = len(values)
    if n == 0:
        return {}
    if n == 1:
        return dict.fromkeys(values, 0.5)
    order = sorted(values, key=lambda cid: (values[cid], cid))
    out: dict[str, float] = {}
    lo = 0
    while lo < n:
        hi = lo
        while hi + 1 < n and values[order[hi + 1]] == values[order[lo]]:
            hi += 1
        share = ((lo + hi) / 2) / (n - 1)
        for k in range(lo, hi + 1):
            out[order[k]] = share
        lo = hi + 1
    return out


def terms(entries: Sequence[Entry], ctx: RankContext) -> dict[str, Terms]:
    """candidate_id → its five signed terms. Novelty compares each entry with the entries before
    it (trial order), so the ranking is a function of the ledger alone."""
    primary = primary_rank(entries, ctx)
    out: dict[str, Terms] = {}
    seen: list[tuple[str, ...]] = []
    for e in sorted(entries, key=lambda x: x.trial_id):
        sim = max((jaccard(e.signature, s) for s in seen), default=0.0) if e.signature else 0.0
        p = e.public
        out[e.candidate_id] = Terms(
            primary=primary[e.candidate_id],
            similarity=-LAMBDA_SIM * sim,
            params=-LAMBDA_PARAMS * len(e.params) / MAX_TUNABLES,
            spp=LAMBDA_SPP * math.tanh(float(p.get("spp_median_sharpe", 0.0))),
            plateau=LAMBDA_PLATEAU * float(p.get("plateau", 0.0)),
        )
        if e.signature:
            seen.append(e.signature)
    return out


def scores(entries: Sequence[Entry], ctx: RankContext) -> dict[str, float]:
    """candidate_id → score. Only comparable within one call (see the module docstring)."""
    return {cid: t.total for cid, t in terms(entries, ctx).items()}


@dataclass(frozen=True, slots=True)
class Dispersion:
    """Interquartile range of each signed term across a scored population."""

    primary: float
    similarity: float
    params: float
    spp: float
    plateau: float

    @property
    def auxiliary(self) -> float:
        """How much the penalties and bonuses move the score, combined."""
        return self.similarity + self.params + self.spp + self.plateau

    @property
    def margin(self) -> float:
        """> 1 ⇔ the term the score is nominally about moves it more than the rest together."""
        return self.primary / self.auxiliary if self.auxiliary else math.inf


def term_dispersion(entries: Sequence[Entry], ctx: RankContext) -> Dispersion:
    """The check that would have caught the compressed-benchmark defect (INV-79, ADR-0029).

    **The spread is an IQR, not an sd**, and the difference decides whether the check works. The
    pathology is a *bulk* squashed against zero while a handful of top entries keep a large PSR,
    and a standard deviation is dominated by exactly that tail: on campaign ``c-20260923-090957``
    the absolute-PSR form scores sd-margins of 1.36 (gp) and 1.08 (random) and so **passes** a
    check it ought to fail. The same populations give IQR-margins of 0.59 and 0.44 — a clear
    failure — against 4.56 and 3.21 under the rank form.

    Diagnostic only. Nothing stops on it and nothing is decided by it: a comparison run may not
    let a measured quantity set its stopping time (ADR-0028), so it never reaches ``decide`` or
    ``EarlyStop`` — it is reported per (engine, seed) and read after the run.
    """
    rows = list(terms(entries, ctx).values())

    def iqr(pick: Callable[[Terms], float]) -> float:
        if len(rows) < 2:
            return 0.0
        q = statistics.quantiles([pick(t) for t in rows], n=4, method="inclusive")
        return q[2] - q[0]

    return Dispersion(
        primary=iqr(lambda t: t.primary),
        similarity=iqr(lambda t: t.similarity),
        params=iqr(lambda t: t.params),
        spp=iqr(lambda t: t.spp),
        plateau=iqr(lambda t: t.plateau),
    )
