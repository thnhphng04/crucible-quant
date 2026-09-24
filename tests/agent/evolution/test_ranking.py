"""Ranking score of engine C-gp (arch §3.1.6 #1, ADR-0026, ADR-0029) — INV-68, INV-79.

The fixture is the real IS-moment distribution of the ``gp`` arm of campaign
``c-20260923-090957`` (145 archive-eligible entries, 20 paired rows at its Sharpe quantiles),
frozen here so the regression never opens a ledger. That campaign is where the defect showed:
with ``n_eff = 145`` and ``V[SR] = 0.3355`` the deflated benchmark sits at ≈ 1.54 annualised
Sharpe, above the population's 90th percentile, so the PSR that ADR-0026 made the primary term
collapsed to a tie-breaker and the search selected on parameter flatness instead of returns.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from pathlib import Path

import pytest

from quantcrucible.agent.evolution.archive import Entry, load_entries
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.ranking import (
    RankContext,
    dsr_rank,
    primary_rank,
    scores,
    term_dispersion,
    terms,
)
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.run import FEATURE_MAP
from quantcrucible.validation.statistical import deflated_benchmark
from tests.agent.evolution.test_feature_map import _candidate

# (sr_obs, skew_is, kurtosis_is, spp_median_sharpe, plateau, n_params), ascending by sr_obs.
CAMPAIGN_ROWS: tuple[tuple[float, float, float, float, float, int], ...] = (
    (-0.055952, -0.7838, 27.1982, -0.9766, 0.0000, 3),
    (-0.015435, 4.4220, 130.3586, -0.2473, 0.0000, 2),
    (-0.003188, 0.0285, 26.9943, -0.1023, 0.0000, 2),
    (0.001390, -4.6329, 241.7006, 0.0337, 0.5600, 3),
    (0.002862, -0.0523, 27.2902, 0.0420, 0.5578, 5),
    (0.009055, 0.7067, 15.0090, 0.1926, 1.0000, 2),
    (0.013976, 6.3838, 229.2636, 0.4207, 0.6281, 4),
    (0.021190, 0.9645, 20.2080, 0.3887, 0.9583, 2),
    (0.028352, 1.0447, 80.2822, 0.5756, 0.8492, 4),
    (0.036129, 1.4590, 104.0604, 0.7258, 1.0000, 2),
    (0.039071, 0.1501, 15.4709, 0.6969, 1.0000, 3),
    (0.044469, -4.2929, 185.1864, 0.9193, 1.0000, 2),
    (0.049168, 1.2680, 50.3589, 0.9587, 1.0000, 4),
    (0.050994, -3.2840, 162.7688, 0.9698, 1.0000, 2),
    (0.052265, 2.3662, 84.4032, 0.8293, 0.8191, 5),
    (0.055944, 0.4750, 24.6497, 1.1573, 0.9948, 3),
    (0.058680, 0.6844, 24.3248, 1.1593, 0.9950, 3),
    (0.063464, 2.3216, 49.9416, 1.0594, 1.0000, 2),
    (0.070258, 0.2130, 19.2892, 1.1566, 0.7547, 3),
    (0.088780, 2.5624, 63.4483, 1.2687, 0.9286, 3),
)
# Clause signatures whose induced novelty penalties reproduce that arm's own distribution: of
# its 145 entries only 57 signatures are distinct, and 91 children repeat an earlier one exactly,
# so `sim` runs [0, 0.5, 1.0, 1.0, 1.0] across the quartiles. Leaving the fixture's signatures
# unique would zero the largest auxiliary term and hide what the regression has to show.
CAMPAIGN_SIGNATURES: tuple[tuple[str, ...], ...] = (
    ("A",), ("B",), ("C",), ("A",), ("A",), ("B",), ("A", "B"), ("A",), ("C",), ("A", "B"),
    ("A", "D"), ("A",), ("B",), ("C",), ("A", "B"), ("A",), ("B", "C"), ("A",), ("C",),
    ("A", "B"),
)  # fmt: skip
N_OBS = 2819.0  # every trial of that campaign measured the same IS window
CAMPAIGN_CTX = RankContext(n_trials=145, var_sr=0.3355, periods_per_year=365.0)
CTX = RankContext(n_trials=100, var_sr=0.5, periods_per_year=365.0)
TANH_1 = 0.7615941559557649  # written out so a λ change cannot hide behind math.tanh


def _entry(
    cid: str,
    tid: int,
    sr: float,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    spp: float = 0.0,
    plateau: float = 0.0,
    n_params: int = 3,
    signature: tuple[str, ...] = (),
    n_obs: float = N_OBS,
    moments: bool = True,
) -> Entry:
    public = {"sharpe_is": sr * math.sqrt(365.0), "spp_median_sharpe": spp, "plateau": plateau}
    if moments:
        public |= {"sr_obs": sr, "skew_is": skew, "kurtosis_is": kurtosis, "n_obs": n_obs}
    params = {f"p{i}": 1 for i in range(n_params)}
    return Entry(cid, tid, f"h{cid}", params, None, ("trend",), public, (0,), signature)


def campaign_population() -> list[Entry]:
    """The 20 frozen rows as entries, trial order ascending in ``sr_obs``."""
    out = []
    for i, (sr, skew, kurt, spp, plat, n_params) in enumerate(CAMPAIGN_ROWS):
        entry = _entry(
            f"c{i}", i, sr, skew=skew, kurtosis=kurt, spp=spp, plateau=plat,
            n_params=n_params, signature=CAMPAIGN_SIGNATURES[i],
        )  # fmt: skip
        out.append(entry)
    return out


def _iqr(values: Sequence[float]) -> float:
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[2] - q[0]


def _absolute_psr_margin(entries: Sequence[Entry], ctx: RankContext) -> float:
    """The dispersion margin of the **pre-ADR-0029** score, whose primary term was the PSR value
    itself. Kept in the test only: the absolute form is not a maintained code path."""
    return _iqr([dsr_rank(e, ctx) for e in entries]) / term_dispersion(entries, ctx).auxiliary


# ── the premise ──────────────────────────────────────────────────────────────────────────────
def test_the_deflated_benchmark_sits_above_the_populations_ninetieth_percentile() -> None:
    """Why the absolute PSR collapsed: the bar was set above almost every candidate.

    SR₀ follows Bailey et al. (2014); ``tests/validation/test_statistical.py`` pins that formula
    against the paper independently, so the reference here is its annualised value.
    """
    sr0 = deflated_benchmark(145, 0.3355 / 365.0) * math.sqrt(365.0)
    assert sr0 == pytest.approx(1.540, abs=1e-3)
    annualised = sorted(row[0] * math.sqrt(365.0) for row in CAMPAIGN_ROWS)
    assert sr0 > annualised[17]  # the 90th percentile of the population, ≈ 1.21


def test_the_deflated_benchmark_rises_with_the_trial_count() -> None:
    hi = _entry("b", 2, 0.08)
    assert dsr_rank(hi, CTX) > dsr_rank(hi, RankContext(10_000, 0.5, 365.0))


# ── the primary term ─────────────────────────────────────────────────────────────────────────
def test_the_primary_term_is_the_population_rank_of_the_psr() -> None:
    population = [_entry(f"c{i}", i, sr) for i, sr in enumerate((0.01, 0.02, 0.03, 0.04, 0.05))]
    primary = primary_rank(population, CAMPAIGN_CTX)
    assert [primary[f"c{i}"] for i in range(5)] == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_tied_psr_shares_the_average_rank_rather_than_the_trial_order() -> None:
    """Positions 1 and 2 of four share ((1 + 2) / 2) / 3 = 0.5, whichever ran first."""
    low, mid_a, mid_b, high = (
        _entry("low", 1, 0.01), _entry("a", 2, 0.03), _entry("b", 3, 0.03), _entry("high", 4, 0.06)
    )  # fmt: skip
    primary = primary_rank([low, mid_a, mid_b, high], CAMPAIGN_CTX)
    assert primary["low"] == 0.0 and primary["high"] == 1.0
    assert primary["a"] == 0.5 and primary["b"] == 0.5
    swapped = primary_rank(
        [low, _entry("a", 3, 0.03), _entry("b", 2, 0.03), high], CAMPAIGN_CTX
    )  # fmt: skip
    assert swapped == primary


def test_entries_without_is_moments_all_share_the_bottom_rank() -> None:
    """``dsr_rank`` returns a literal 0.0 for them, so ordinal ranks would order them by trial id
    and reward whichever ran first. Three of eight take positions 0–2: ((0 + 2) / 2) / 7."""
    scored = [_entry(f"ok{i}", i, sr) for i, sr in enumerate((0.03, 0.04, 0.05, 0.06, 0.07))]
    blind = [_entry(f"na{i}", 5 + i, 0.09, moments=False) for i in range(3)]
    primary = primary_rank([*scored, *blind], CAMPAIGN_CTX)
    assert [primary[f"na{i}"] for i in range(3)] == [pytest.approx(1.0 / 7)] * 3
    assert primary["ok0"] == pytest.approx(3.0 / 7)  # the worst entry that does have moments


def test_a_single_entry_scores_the_midpoint_rank() -> None:
    only = _entry("only", 1, 0.05, spp=1.0, plateau=0.5, n_params=3)
    assert primary_rank([only], CAMPAIGN_CTX) == {"only": 0.5}
    expected = 0.5 - 0.05 * (3 / 6) + 0.05 * TANH_1 + 0.05 * 0.5
    assert scores([only], CAMPAIGN_CTX)["only"] == pytest.approx(expected)


def test_the_primary_term_always_spans_the_unit_interval_whatever_the_benchmark() -> None:
    """What the rank form buys: scale invariance. **Not** order invariance — PSR divides by
    √(T−1) and by the moments, so a different SR₀ genuinely reorders records of unequal length.
    """
    population = campaign_population()
    for n_trials in (10, 10_000):
        primary = primary_rank(population, RankContext(n_trials, 0.3355, 365.0))
        assert min(primary.values()) == 0.0
        assert max(primary.values()) == 1.0
        assert _iqr(list(primary.values())) == pytest.approx(0.5)
    collapsed = [_iqr([dsr_rank(e, RankContext(n, 0.3355, 365.0)) for e in population])
                 for n in (10, 10_000)]  # fmt: skip
    assert collapsed[1] < collapsed[0] / 2  # the absolute PSR shrinks as the bar climbs


# ── INV-79 ───────────────────────────────────────────────────────────────────────────────────
def test_the_primary_term_out_disperses_the_auxiliary_terms() -> None:
    """INV-79: what the score is nominally about must move it more than everything else."""
    assert term_dispersion(campaign_population(), CAMPAIGN_CTX).margin > 1.0


def test_the_absolute_psr_form_fails_the_dispersion_invariant_on_a_compressed_population() -> None:
    """The regression. On the very population that exposed the defect the old primary term is
    out-dispersed by its own tie-breakers; the rank form clears the bar with room to spare."""
    population = campaign_population()
    assert _absolute_psr_margin(population, CAMPAIGN_CTX) < 1.0
    assert term_dispersion(population, CAMPAIGN_CTX).margin > 1.0


def test_selection_prefers_the_higher_sharpe_over_a_flatter_neighbourhood() -> None:
    """The defect, stated behaviourally (ADR-0029).

    ``winner`` earns 0.974 annualised Sharpe — the 14th of 20 — while carrying every penalty the
    score can impose: a duplicated signature, the full TUNABLE budget, no plateau and no SPP.
    ``flat`` earns 0.027, near the bottom, but is maximally flat, maximally novel and lean.
    A score whose primary term is about returns must still prefer ``winner``.

    Under the absolute-PSR form it does not: the two candidates' PSR differ by 0.082 while the
    auxiliary terms differ by 0.24, so ``flat`` wins on tie-breakers alone.
    """
    population = campaign_population()
    winner = _entry(
        "winner", 13, CAMPAIGN_ROWS[13][0], skew=CAMPAIGN_ROWS[13][1],
        kurtosis=CAMPAIGN_ROWS[13][2], spp=0.0, plateau=0.0, n_params=6,
        signature=population[0].signature,  # duplicates an earlier entry: jaccard 1.0
    )  # fmt: skip
    flat = _entry(
        "flat", 3, CAMPAIGN_ROWS[3][0], skew=CAMPAIGN_ROWS[3][1], kurtosis=CAMPAIGN_ROWS[3][2],
        spp=2.0, plateau=1.0, n_params=1, signature=("unique",),
    )  # fmt: skip
    rest = [e for e in population if e.candidate_id not in {"c13", "c3"}]
    s = scores([*rest, winner, flat], CAMPAIGN_CTX)
    assert s["winner"] > s["flat"]


# ── the auxiliary terms ──────────────────────────────────────────────────────────────────────
def test_the_auxiliary_terms_keep_their_published_weights() -> None:
    """A λ drift changes no ordering here, so only a hand-written reference catches it."""
    first = _entry("a", 1, 0.02, spp=1.0, plateau=0.5, signature=("x", "y"))
    second = _entry("b", 2, 0.06, spp=1.0, plateau=0.5, signature=("y", "z"))
    s = scores([first, second], CAMPAIGN_CTX)
    common = -0.05 * (3 / 6) + 0.05 * TANH_1 + 0.05 * 0.5
    assert s["a"] == pytest.approx(0.0 + common)
    assert s["b"] == pytest.approx(1.0 - 0.10 * (1 / 3) + common)  # jaccard({x,y},{y,z}) = 1/3


def test_higher_sharpe_ranks_higher() -> None:
    lo, hi = _entry("a", 1, 0.03, signature=("x",)), _entry("b", 2, 0.08, signature=("y",))
    assert scores([lo, hi], CTX)["b"] > scores([lo, hi], CTX)["a"]


def test_novelty_and_complexity_are_penalized() -> None:
    first = _entry("a", 1, 0.06, signature=("cmp(ema>sma)",))
    copy = _entry("b", 2, 0.06, signature=("cmp(ema>sma)",))
    fresh = _entry("c", 3, 0.06, signature=("brk(up)",))
    s = scores([first, copy, fresh], CTX)
    assert s["b"] < s["a"] and s["b"] < s["c"]
    lean = _entry("d", 1, 0.06, signature=("q",), n_params=1)
    heavy = _entry("e", 2, 0.06, signature=("r",), n_params=6)
    s2 = scores([lean, heavy], CTX)
    assert s2["d"] > s2["e"]


# ── INV-68 ───────────────────────────────────────────────────────────────────────────────────
def test_ranking_ignores_private(tmp_path: Path) -> None:
    """INV-68: gate ④'s private detail (PBO, CPCV paths) never changes an entry or its score."""
    fm = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP}})
    runs = []
    for private in ({"pbo": 0.1, "cpcv_path_sharpes": [1.0]},
                    {"pbo": 0.4, "cpcv_path_sharpes": [-3.0]}):  # fmt: skip
        lg = Ledger.open(tmp_path / f"{private['pbo']}.db")
        lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
        _candidate(lg, "a", "gp", 0, 1.0, ["trend"], g4_detail=private)
        [entry] = load_entries(lg, "c1", "gp", 0, fm)
        assert "pbo" not in entry.public and "cpcv_path_sharpes" not in entry.public
        runs.append(scores([entry], CTX))
    assert runs[0] == runs[1]


def test_the_terms_add_up_to_the_score() -> None:
    """``term_dispersion`` must measure the terms the scorer actually sums."""
    population = campaign_population()
    assert {c: t.total for c, t in terms(population, CAMPAIGN_CTX).items()} == scores(
        population, CAMPAIGN_CTX
    )
