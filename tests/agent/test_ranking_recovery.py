"""Does the ranking steer the search towards returns? (ADR-0030, P2-16 — the calibration bench.)

A market is simulated in which one clause signature pays a little more than the rest. The gates
are fakes, but everything else is real: the grammar, the operators, the islands, the pipeline,
the ledger and the engine rebuilding its state from it. Two things make this the right bench for
judging a ranking, and a poor one for judging a strategy:

* **The answer is known.** Whether the search moved towards the paying clause is a fact about
  the run, not a return to be believed.
* **Backtest noise is removed.** A real gate ③ would add variance that has nothing to do with
  the question, at ≈ 90 s per trial; here a whole run takes seconds and repeats exactly.

The market reproduces the campaign `c-20260923-090957` pathology on purpose. Its Sharpes reach
well below zero, which drives `V[SR]` high enough that the deflated benchmark climbs above the
whole population — the state in which the absolute PSR collapses (measured: an old-form margin
of ≈ 0.01 against the real campaign's 0.59). The auxiliary metrics are drawn from an independent
hash, so they carry no information about the edge whatsoever.

**The edge is deliberately weak and overlapping** (a mean shift of 0.7 Sharpe between two spans
of 1.5). An earlier version of this bench planted a clean needle — the paying clause far above
everything else — and found *no difference between the two ranking forms*. That was the bench
being wrong, and it is worth recording why: compression is monotone, so an extreme top survives
it intact and both forms rank a needle first. The defect bites in the **middle** of a population
of mediocre, neighbouring candidates, which is what the real campaign had and what this is.
"""

from __future__ import annotations

import hashlib
import statistics
import threading
from collections.abc import Callable

import pytest

from quantcrucible.agent.engines.gp_search import GpSearch
from quantcrucible.agent.engines.random_search import Proposal
from quantcrucible.agent.evolution import ranking as ranking_module
from quantcrucible.agent.evolution.archive import Entry, load_entries
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.islands import island_names
from quantcrucible.agent.evolution.ranking import (
    RankContext,
    dsr_rank,
    scores,
    term_dispersion,
)
from quantcrucible.agent.grammar import categories, genome_to_dict, signature
from quantcrucible.agent.pipeline import Outcome, Pipeline
from quantcrucible.agent.scheduler import Key, TrialScheduler
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import (
    G1A_STATIC,
    G3_IS,
    G4_PBO,
    GateContext,
    GatePipeline,
    GateResult,
    StrategyCandidate,
    TrialMeasurement,
)
from quantcrucible.validation.run import FEATURE_MAP
from quantcrucible.validation.statistical import deflated_benchmark
from tests.factories import unit

pytestmark = pytest.mark.slow

TARGET = "thr(rsi>)"  # the clause that pays; reachable from i1, i2 and the open island
GENERATIONS = 60
SEEDS = (7, 11, 23)
TOP = 20  # the selection order's head: what the engine actually breeds from
ISLANDS = len(island_names())
LOCK = {"derived": {"feature_map": FEATURE_MAP}}
PPY = 365.0


def _u(key: str) -> float:
    """A deterministic number in [0, 1) — the simulated market has no RNG of its own."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) / 16**12


def _sharpe(c: StrategyCandidate) -> float:
    """The paying clause shifts the mean by 0.7 Sharpe; the two spans overlap heavily, so no
    candidate is obviously best and the ranking has to integrate over the whole population."""
    paid = TARGET in (c.descriptors or {}).get("signature", ())
    noise = _u(f"sr|{c.source}|{sorted(c.params.items())}")
    return (-0.3 if paid else -1.0) + 1.5 * noise


class Static:
    id, cost = G1A_STATIC, 1

    def check(self, c: StrategyCandidate, ctx: GateContext) -> GateResult:
        return GateResult(True, self.id, 0.0, "ok")


class InSample:
    id, cost = G3_IS, 3

    def check(self, c: StrategyCandidate, ctx: GateContext) -> GateResult:
        sharpe = _sharpe(c)
        public = {
            "sharpe_is": sharpe, "sortino_is": sharpe * 1.3,
            "max_drawdown": _u(f"d|{c.source}"), "total_return": sharpe * 0.8,
            "n_trades": 30 + 400 * _u(f"n|{c.source}"),
            "sr_obs": sharpe / 19.1, "skew_is": 0.0, "kurtosis_is": 3.0, "n_obs": 2000.0,
        }  # fmt: skip
        return GateResult(
            True, self.id, sharpe, "fake ③", detail={"public": public},
            measurement=TrialMeasurement(sharpe, "fake.parquet"),
        )  # fmt: skip


class Pbo:
    id, cost = G4_PBO, 4

    def check(self, c: StrategyCandidate, ctx: GateContext) -> GateResult:
        """Flatness that knows nothing about the edge: an independent hash of the code."""
        public = {
            "spp_median_sharpe": 2.0 * _u(f"m|{c.source}"),
            "plateau": _u(f"p|{c.source}"),
        }
        return GateResult(True, self.id, 0.0, "fake ④", detail={"public": public, "pbo": 0.1})


def _evaluator(path: str) -> Callable[[Key, Proposal, str], Outcome]:
    local = threading.local()
    pipeline = GatePipeline([Static(), InSample(), Pbo()])

    def evaluate(key: Key, p: Proposal, cid: str) -> Outcome:
        lg = getattr(local, "lg", None) or Ledger.open(path)
        local.lg = lg
        engine, seed = key.engine, key.seed
        cand = StrategyCandidate(
            candidate_id=cid, source=p.source, params=p.params, universe=("A/USDT",),
            timeframe="1d", timerange="2018-01-01/2024-01-01", run_id="rec", campaign_id="c1",
            engine=engine, seed=seed, island=p.island, parents=p.parents, mutation=p.mutation,
            trial_source="evolution", agent="engine",
            descriptors={"categories": list(categories(p.genome)),
                         "signature": list(signature(p.genome)),
                         "genome": genome_to_dict(p.genome)},
        )  # fmt: skip
        out = pipeline.run(cand, GateContext(lg, {}))
        return Outcome(cid, out.trial_id is not None, out.passed, out.failed_gate)

    return evaluate


def _absolute_psr(entries: list[Entry], ctx: RankContext) -> dict[str, float]:
    """The pre-ADR-0030 primary term: the PSR value itself, not its rank in the population."""
    return {e.candidate_id: dsr_rank(e, ctx) for e in entries}


def _run(tmp_path_factory: pytest.TempPathFactory, rng_seed: int, absolute: bool) -> Ledger:
    """One evolution against the simulated market, in a ledger of its own.

    A separate ledger is not tidiness: ``Ledger.trial_stats`` counts the whole file, so a
    calibration run sharing `ledger/crucible.db` would add to the `N` that every later campaign
    is deflated against. Under ``absolute`` the engine *breeds* with the old primary term, so
    the two runs differ in their whole trajectory, not merely in how the result is scored.
    """
    path = tmp_path_factory.mktemp(f"rec{rng_seed}{absolute:d}") / "ledger.db"
    main = Ledger.open(path)
    main.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    gp = GpSearch(main, "c1", unit(), rng_seed, FeatureMap.from_lock(LOCK), PPY, 0.30, "rec")
    pipeline = Pipeline(
        {unit(): gp}, TrialScheduler({unit(): GENERATIONS * ISLANDS}),
        _evaluator(main.path), workers=2, run_label="rec",
    )  # fmt: skip
    with pytest.MonkeyPatch.context() as patch:
        if absolute:
            patch.setattr(ranking_module, "primary_rank", _absolute_psr)
        pipeline.run()
    return main


def _entries_and_context(ledger: Ledger) -> tuple[list[Entry], RankContext]:
    entries = load_entries(ledger, "c1", unit(), FeatureMap.from_lock(LOCK))
    stats = ledger.trial_stats()
    return entries, RankContext(max(stats.n_eff, 1), stats.var_sr or 0.0, PPY)


def _head_quality(ledger: Ledger) -> tuple[float, float]:
    """Of the ``TOP`` entries the engine would breed from: their mean IS Sharpe, and the share
    of them carrying the paying clause."""
    entries, ctx = _entries_and_context(ledger)
    score = scores(entries, ctx)
    head = sorted(entries, key=lambda e: -score[e.candidate_id])[:TOP]
    mean_sharpe = statistics.fmean(e.public["sharpe_is"] for e in head)
    return mean_sharpe, sum(1 for e in head if TARGET in e.signature) / len(head)


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[tuple[float, float]]]:
    """Both ranking forms over ``SEEDS``, on the same market — the whole bench, run once."""
    return {
        form: [_head_quality(_run(tmp_path_factory, s, form == "absolute")) for s in SEEDS]
        for form in ("rank", "absolute")
    }


def test_the_simulated_market_reproduces_the_compression(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The premise. Without it the bench would not be testing the condition the defect needs:
    a deflated benchmark above the whole population, and a primary term that loses to its own
    tie-breakers under the old form."""
    entries, ctx = _entries_and_context(_run(tmp_path_factory, SEEDS[0], absolute=False))
    benchmark = deflated_benchmark(ctx.n_trials, max(ctx.var_sr, 0.0) / PPY) * PPY**0.5
    assert max(e.public["sharpe_is"] for e in entries) < benchmark
    dispersion = term_dispersion(entries, ctx)
    old = statistics.quantiles([dsr_rank(e, ctx) for e in entries], n=4, method="inclusive")
    assert (old[2] - old[0]) / dispersion.auxiliary < 1.0  # the old form fails INV-81 here
    assert dispersion.margin > 1.0  # the rank form holds it


def test_the_rank_form_breeds_from_better_candidates(
    bench: dict[str, list[tuple[float, float]]],
) -> None:
    """The bench's headline, and the claim ADR-0030 actually makes: with the returns term
    restored, the head of the selection order — what the engine breeds from — carries a higher
    IS Sharpe. Asserted per seed as well as on the mean, because a mean that holds on one seed
    of three would say nothing (§3.1.11's own discipline, applied to the bench)."""
    rank = [sharpe for sharpe, _share in bench["rank"]]
    absolute = [sharpe for sharpe, _share in bench["absolute"]]
    assert all(r > a for r, a in zip(rank, absolute, strict=True))
    assert statistics.fmean(rank) > statistics.fmean(absolute)


def test_the_rank_form_holds_more_of_the_paying_clause(
    bench: dict[str, list[tuple[float, float]]],
) -> None:
    """The same result read through the planted clause rather than through the Sharpe it pays:
    the head of the selection order holds at least as much of it under every seed."""
    rank = [share for _sharpe, share in bench["rank"]]
    absolute = [share for _sharpe, share in bench["absolute"]]
    assert all(r >= a for r, a in zip(rank, absolute, strict=True))
    assert statistics.fmean(rank) > statistics.fmean(absolute)
