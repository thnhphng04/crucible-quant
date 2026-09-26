"""The phase-2 engine comparison: protocol locked before running, report, decision (arch §3.1.11
v0.6, ADR-0027, ADR-0028, P2-15).

The protocol — metrics, the spread that counts as "beats", the decision rule, and what the
monitor may do — is written into the campaign's audit log (``PROTOCOL_LOCKED``, with its hash)
before the campaign's first trial; ``evolve`` refuses to run a harness-test campaign without it,
and the report refuses a campaign whose protocol does not predate its first trial (INV-70). The
hash includes the monitor's thresholds and declared algorithm version directly (INV-76).
``monitor_fingerprint`` additionally checks behaviour on fixed checkpoint shapes; it cannot
detect every arithmetic rewrite, so such changes still require a version bump.

Primary metric: **trial efficiency** — strategies passing gate ④ per 100 trials — per
(engine, seed). C-gp beats C-random when the difference of the seed means exceeds the
seed-to-seed spread, taken as the larger of the two engines' sample standard deviations across
seeds. Supporting metrics (reported, not decisive): archive and search coverage, proposals per
trial, gate-④ backtests per passing strategy (CPU cost), IS→OOS divergence, and the DSR of a
portfolio built per §3.2.1 from each engine's candidates alone at the same ``N`` (each such
portfolio is recorded as a variant, as every built portfolio is). The raw IS Sharpe of the best
strategy is never compared (best-of-N).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from quantcrucible.agent.evolution.archive import Entry, scope_args, trial_cells
from quantcrucible.agent.evolution.feature_map import FeatureMap, cell_id
from quantcrucible.agent.evolution.ranking import (
    LAMBDA_PARAMS,
    LAMBDA_PLATEAU,
    LAMBDA_SIM,
    LAMBDA_SPP,
    RankContext,
    scores,
)
from quantcrucible.agent.monitor import (
    CHECKPOINT_EVERY,
    MonitorMode,
    engine_report,
    warned_keys,
)
from quantcrucible.agent.scheduler import Key, campaign_scopes, quotas
from quantcrucible.core.strategy.tunable import MAX_TUNABLES
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.cpcv import (
    DEFAULT_DIVERGENCE,
    DegradationPoint,
    DivergenceRule,
    is_oos_diverging,
)
from quantcrucible.validation.gates import G4_PBO
from quantcrucible.validation.n_eff import update_n_eff
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.portfolio import (
    PortfolioRule,
    build_portfolio,
    eligible_trials,
    load_returns,
    record_variant,
)
from quantcrucible.validation.research_run import ResearchSession
from quantcrucible.validation.statistical import portfolio_dsr

MEANINGFUL_EFFICIENCY = 1.0  # passing ④ per 100 trials; below it for both arms ⇒ investigate
MIN_SEEDS = 3  # §3.1.11: the seed-to-seed spread needs at least three seeds

MONITOR_SCENARIOS: tuple[tuple[DegradationPoint, ...], ...] = tuple(
    tuple(DegradationPoint(f"p{k}", is_sr, oos) for k, (is_sr, oos) in enumerate(seq))
    for seq in (
        ((1.0, 0.8), (1.5, 0.5), (2.0, 0.2)),  # textbook divergence: IS up, OOS down
        ((1.0, 0.2), (1.5, 0.4), (2.0, 0.7)),  # both rising: healthy search
        ((1.0, 0.3), (1.0, 0.3), (1.0, 0.3)),  # flat line: arithmetic noise, never divergence
        ((1.0, 0.5), (1.5, 0.4), (1.5, 0.4)),  # one record change, then repeats (ADR-0028)
        ((1.0, 0.5), (1.0, 0.5), (1.5, 0.4)),  # repeats, then one record change
        ((1.0, 0.8), (1.5, 0.8), (2.0, 0.8)),  # IS up, OOS exactly flat
        ((1.0, 0.8), (1.5, 0.5)),  # below min_points: never a verdict
        ((2.0, 0.2), (1.5, 0.5), (1.0, 0.8)),  # IS falling: not the signal
        (),  # no checkpoints
        ((1.0, 0.8),),  # one checkpoint
        ((0.0, 0.0), (0.5e-9, -1.0), (1.0e-9, -2.0)),  # IS below tolerance
        ((0.0, 0.0), (2.0e-9, -1.0), (4.0e-9, -2.0)),  # IS above tolerance
        ((0.0, 0.0), (1.0, 0.5e-9), (2.0, 1.0e-9)),  # tiny OOS rise is flat
        ((0.0, 0.0), (1.0, 2.0e-9), (2.0, 4.0e-9)),  # material OOS rise
        ((1.0, 0.8), (1.5, 0.5), (2.0, 0.2), (2.5, 3.0)),  # recovery at point 4
        ((1.0, 0.8), (1.5, 0.5), (1.5, 0.5), (1.5, 0.5)),  # longer plateau
        # Endpoints rise, but OLS over all five OOS points falls: no endpoint shortcut.
        ((1.0, 0.0), (1.5, 4.0), (2.0, 0.0), (2.5, -4.0), (3.0, 1.0)),
    )
)
"""Fixed probes near the tolerance and across different curve shapes (ADR-0028).
These detect some behavioural changes, not every possible rewrite. Keep the values fixed
rather than deriving them from the active rule, which would move the probes with the threshold.
"""


def _probe(
    cid: str,
    tid: int,
    sr: float,
    *,
    spp: float = 0.0,
    plateau: float = 0.0,
    n_params: int = 3,
    signature: tuple[str, ...] = (),
    moments: bool = True,
) -> Entry:
    """One fixed entry for ``RANKING_SCENARIOS``; only the fields the ranking reads matter."""
    public: dict[str, float] = {"spp_median_sharpe": spp, "plateau": plateau}
    if moments:
        public |= {"sr_obs": sr, "skew_is": 0.0, "kurtosis_is": 3.0, "n_obs": 2819.0}
    params = {f"p{i}": 1 for i in range(n_params)}
    return Entry(cid, tid, f"h{cid}", params, None, ("trend",), public, (0,), signature)


RANKING_CTX = RankContext(n_trials=145, var_sr=0.3355, periods_per_year=365.0)
RANKING_SCENARIOS: tuple[tuple[Entry, ...], ...] = (
    # A population squashed far below the deflated benchmark, with the auxiliary terms pulling
    # the other way: the shape that made the absolute-PSR form rank on flatness (ADR-0029).
    (
        _probe("a", 1, 0.001, spp=1.2, plateau=1.0, n_params=1, signature=("X",)),
        _probe("b", 2, 0.004, spp=0.8, plateau=0.9, n_params=2, signature=("X",)),
        _probe("c", 3, 0.007, spp=0.2, plateau=0.4, n_params=4, signature=("Y",)),
        _probe("d", 4, 0.010, spp=0.0, plateau=0.0, n_params=6, signature=("X",)),
    ),
    # A population spread across the benchmark: the primary term carries real spread either way.
    (
        _probe("a", 1, 0.010, spp=0.1, plateau=0.2, n_params=2, signature=("X",)),
        _probe("b", 2, 0.035, spp=0.5, plateau=0.6, n_params=3, signature=("Y",)),
        _probe("c", 3, 0.060, spp=0.9, plateau=0.9, n_params=4, signature=("X", "Y")),
        _probe("d", 4, 0.090, spp=1.3, plateau=1.0, n_params=5, signature=("Z",)),
    ),
    # Every PSR identical: the whole population shares one average rank and only the auxiliary
    # terms separate the candidates.
    (
        _probe("a", 1, 0.030, spp=0.5, plateau=1.0, n_params=1, signature=("X",)),
        _probe("b", 2, 0.030, spp=0.5, plateau=0.0, n_params=6, signature=("X",)),
        _probe("c", 3, 0.030, spp=0.5, plateau=0.5, n_params=3, signature=("Y",)),
    ),
    # Entries without IS moments score a literal 0.0 and must share the bottom rank.
    (
        _probe("a", 1, 0.020, spp=0.4, plateau=0.7, signature=("X",)),
        _probe("b", 2, 0.050, spp=0.6, plateau=0.8, signature=("Y",)),
        _probe("c", 3, 0.000, spp=0.9, plateau=1.0, signature=("Z",), moments=False),
        _probe("d", 4, 0.000, spp=0.3, plateau=0.2, signature=("Z",), moments=False),
    ),
    # Two entries: the primary term is exactly {0.0, 1.0}.
    (
        _probe("a", 1, 0.015, spp=0.7, plateau=0.9, n_params=2, signature=("X",)),
        _probe("b", 2, 0.075, spp=0.1, plateau=0.1, n_params=5, signature=("X",)),
    ),
    # One entry: the midpoint rank, and no division by zero.
    (_probe("a", 1, 0.040, spp=0.6, plateau=0.6, n_params=3, signature=("X",)),),
)
"""Fixed populations spanning the ranking's decision boundary (ADR-0029): compressed, spread,
fully tied, missing moments, two entries, one entry. Literal data for the same reason
``MONITOR_SCENARIOS`` is — probes derived from the live λ would move with them.
"""

PROTOCOL: dict[str, Any] = {
    "version": 5,
    "arms": ["gp", "random"],
    "primary_metric": "trial_efficiency = strategies passing gate 4 per 100 trials",
    "unit": "(instrument, direction, engine, seed); >= 3 seeds; identical trial quota per unit",
    "beats": "mean(d) > sd(d), where d = eff(gp) - eff(random) paired on "
    "(instrument, direction, seed). Pooling instead of pairing admits between-instrument "
    "variance and the spread swallows any real effect (ADR-0033)",
    "scope_verdicts": "none: an individual (instrument, direction) is exploratory and carries "
    "no win or loss. Reporting one requires a new protocol version, not a reinterpretation",
    "complete": "every unit of every arm has used its whole quota; short of that the "
    "report shows progress and withholds the decision",
    "replay": "one shared USDT account sizes every position itself from signals, never from "
    "quantities sized in a standalone run (ADR-0035). Per bar, in this order: exits decided at "
    "the previous close fill at this open; funding; liquidation and stops on the mark and the "
    "intrabar path, cut at each settlement; one equity snapshot; then admission in canonical "
    "order on that snapshot. Entries fill at the next bar's open (ADR-0003) with the stop "
    "anchored to the signal bar's close and fixed for the position's life. A wallet's loss stops "
    "at its own isolated margin; a fill through the bankruptcy price is a liquidation, not a "
    "fill; several funding events in one bar are never summed (ADR-0032)",
    "monitor": {
        "mode": "warn",
        "signal": "is_oos_diverging over DEGRADATION_CHECKPOINT points",
        "checkpoint_every": CHECKPOINT_EVERY,
        "note": "recorded as DEGRADATION_WARNING and reported; never stops an arm and never "
        "enters the decision (ADR-0028)",
    },
    "decision": {
        "gp_beats_random": "C-gp main engine; C-random stays as the permanent control",
        "tie": "C-random main engine; re-add GP components only after a dedicated ablation",
        "neither_meaningful": f"both arms < {MEANINGFUL_EFFICIENCY} per 100 trials: stop and "
        "find the cause in the DSL, the data or the gates",
        "incomplete": "progress only: the arms have not used their quotas, so the efficiencies "
        "are not comparable yet",
    },
    "ranking": {
        "version": 2,  # 1 = the absolute PSR value, 2 = its rank in the population (ADR-0029)
        "primary": "rank of PSR(IS returns vs SR0(N_eff, V[SR])) within the scored population, "
        "average ties, normalised to [0, 1]",
        "max_tunables": MAX_TUNABLES,
        "note": "what C-gp selects parents with; C-random reads no score at all (INV-65)",
    },
    "supporting": [
        "archive_cells", "search_cells", "proposals_per_trial", "ranking_margin",
        "gate4_backtests_per_passing_strategy", "is_oos_diverging",
        "portfolio_dsr_per_engine (built per §3.2.1 from that engine alone, same N)",
    ],
    "never": "the raw IS Sharpe of the best strategy (best-of-N)",
}  # fmt: skip


class ProtocolError(RuntimeError):
    """The comparison protocol is missing or was locked too late."""


def monitor_fingerprint(rule: DivergenceRule = DEFAULT_DIVERGENCE) -> str:
    """What the divergence signal decides on the fixed shapes of ``MONITOR_SCENARIOS``.

    A **secondary** check only: the thresholds themselves are hashed as data (``monitor.rule``),
    which is what binds a campaign. The fingerprint catches a rewrite that keeps the same
    thresholds but changes the arithmetic, on the shapes it covers — and only those, so it can
    never be the lock (ADR-0028 amendment).
    """
    verdicts = [is_oos_diverging(points, rule) for points in MONITOR_SCENARIOS]
    return hashlib.sha256(json.dumps(verdicts).encode()).hexdigest()


def ranking_fingerprint() -> str:
    """What the ranking *decides* on the fixed populations of ``RANKING_SCENARIOS``.

    A **secondary** check only: the λ themselves are hashed as data (``ranking.lambdas``), which
    is what binds a campaign. This catches a rewrite that keeps the λ but changes the arithmetic,
    on the shapes it covers — and only those, so it can never be the lock (ADR-0028 amendment).

    It hashes the **selection order**, which is all the engine acts on (per-cell elites, the
    migration draw, the parent pool), plus the scores rounded to six decimals. Raw floats would
    flip the hash on arithmetic reassociation and on last-ulp libm differences between platforms;
    the order survives any behaviour-preserving refactor, and the rounding still catches a
    dropped term or a λ drift. Ties break on ``candidate_id`` so no verdict depends on dict order.
    """
    out = []
    for population in RANKING_SCENARIOS:
        s = scores(population, RANKING_CTX)
        order = sorted(s, key=lambda cid: (-s[cid], cid))
        out.append([order, [round(s[cid], 6) for cid in order]])
    return hashlib.sha256(json.dumps(out).encode()).hexdigest()


def protocol() -> dict[str, Any]:
    """The protocol as it is locked and compared: the dict, the divergence rule the monitor will
    actually run with, and the measured behaviour of that rule and of the ranking."""
    monitor = {**PROTOCOL["monitor"], "rule": dataclasses.asdict(DEFAULT_DIVERGENCE)}
    # read at call time, like the divergence rule: the λ bind the campaign, so the lock must
    # carry the values the run will actually breed with, not a snapshot taken at import
    ranking = {
        **PROTOCOL["ranking"],
        "lambdas": {"sim": LAMBDA_SIM, "params": LAMBDA_PARAMS,
                    "spp": LAMBDA_SPP, "plateau": LAMBDA_PLATEAU},
    }  # fmt: skip
    return {
        **PROTOCOL,
        "monitor": monitor,
        "ranking": ranking,
        "monitor_fingerprint": monitor_fingerprint(DEFAULT_DIVERGENCE),
        "ranking_fingerprint": ranking_fingerprint(),
    }


def divergence_rule(ledger: Ledger, campaign_id: str) -> DivergenceRule:
    """The rule this campaign locked, so the monitor is read with the thresholds the campaign was
    run under. A campaign with no protocol keeps the code's default."""
    locked = (locked_protocol(ledger, campaign_id) or {}).get("protocol", {})
    raw = locked.get("monitor", {}).get("rule")
    return DivergenceRule.from_locked(raw) if raw else DEFAULT_DIVERGENCE


def protocol_hash(p: dict[str, Any] | None = None) -> str:
    return hashlib.sha256(json.dumps(p or protocol(), sort_keys=True).encode()).hexdigest()


def _locked_event_id(ledger: Ledger, campaign_id: str) -> int | None:
    return ledger.first_event_id(campaign_id, Event.PROTOCOL_LOCKED)


def locked_protocol(ledger: Ledger, campaign_id: str) -> dict[str, Any] | None:
    """What this campaign locked — not what the code says today."""
    for event, _island, detail in ledger.events_for(campaign_id):
        if event == Event.PROTOCOL_LOCKED and detail:
            return dict(detail)
    return None


def assert_protocol_is_the_locked_one(ledger: Ledger, campaign_id: str) -> str:
    """The campaign is measured by the protocol it locked. A campaign locked under an earlier
    protocol is never re-read under a newer one: it needs its own run (ADR-0027)."""
    locked = locked_protocol(ledger, campaign_id)
    if locked is None:
        raise ProtocolError("no comparison protocol locked for this campaign (cli compare --lock)")
    stored = str(locked.get("sha256", ""))
    if stored != protocol_hash():
        was = locked.get("protocol", {}).get("version", "?")
        raise ProtocolError(
            f"this campaign locked protocol v{was} (sha256 {stored[:12]}), the code now holds "
            f"v{PROTOCOL['version']} (sha256 {protocol_hash()[:12]}): report it with the code it "
            "was run under, or run the new protocol in a new campaign"
        )
    return stored


def lock_protocol(ledger: Ledger, campaign_id: str) -> str:
    """Record the protocol in the audit log — only while the campaign has no trial yet."""
    if ledger.trials(campaign_id):
        raise ProtocolError("the campaign already has trials: the protocol must come first")
    locked = locked_protocol(ledger, campaign_id)
    if locked is not None and str(locked.get("sha256", "")) != protocol_hash():
        raise ProtocolError(
            f"this campaign already locked another protocol (sha256 "
            f"{str(locked.get('sha256', ''))[:12]}): a changed protocol needs a new campaign"
        )
    if _locked_event_id(ledger, campaign_id) is None:
        ledger.log_event(
            GenerationEvent(
                run_id="protocol", campaign_id=campaign_id, engine="compare", seed=0,
                agent="human", model_used="none", event=Event.PROTOCOL_LOCKED,
                detail={"protocol": protocol(), "sha256": protocol_hash()},
            )
        )  # fmt: skip
    return protocol_hash()


def monitor_mode(ledger: Ledger, campaign_id: str) -> MonitorMode:
    """How this campaign's locked protocol lets the monitor act. A campaign with no protocol —
    any campaign that is not an engine comparison — keeps the ordinary early stop."""
    locked = locked_protocol(ledger, campaign_id)
    mode = (locked or {}).get("protocol", {}).get("monitor", {}).get("mode")
    return "warn" if mode == "warn" else "stop"


def assert_protocol_predates_trials(ledger: Ledger, campaign_id: str) -> None:
    """INV-70: the protocol event exists, matches the one in the code, and no submission of the
    campaign came before it."""
    assert_protocol_is_the_locked_one(ledger, campaign_id)
    locked = _locked_event_id(ledger, campaign_id)
    first = ledger.first_event_id(campaign_id, Event.CANDIDATE_SUBMITTED)
    if locked is not None and first is not None and first < locked:
        raise ProtocolError("a candidate was submitted before the protocol was locked")


def _sd(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def paired_differences(per_scope: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[float]:
    """gp minus random, matched on (instrument, direction, seed).

    Both arms of a pair saw the same instrument, the same window, the same data and the same
    quota, so their difference is attributable to the engine. A unit only one arm ran cannot be
    differenced and is left out rather than compared against a zero.
    """

    def by_unit(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[Any, Any, Any], float]:
        return {
            (r.get("instrument"), r.get("direction"), r.get("seed")): float(r["trial_efficiency"])
            for r in rows
        }

    gp = by_unit(per_scope.get("gp", []))
    rnd = by_unit(per_scope.get("random", []))
    return [gp[k] - rnd[k] for k in sorted(gp.keys() & rnd.keys(), key=str)]


def decide(
    per_scope: Mapping[str, Sequence[Mapping[str, Any]]],
    unfinished: Sequence[str] = (),
) -> dict[str, Any]:
    """The locked rule, protocol v5: a **paired within-scope** difference.

    ``unfinished`` are the units short of their quota: their efficiencies were measured at a
    different budget, so the engine decision is withheld.

    Pooling every unit's efficiency and taking its sd would admit between-instrument variance —
    BTC and XRP differ for reasons that have nothing to do with the engine — and the spread would
    swallow any real effect, turning the rule into a machine that always returns ``tie``. Pairing
    removes that variance; `tests/agent/test_paired_decision.py` keeps the negative control.

    No individual scope carries a verdict (``PROTOCOL["scope_verdicts"] == "none"``). A divergence
    warning never reaches this function: the monitor cannot cut an arm short, and the warning is
    reported beside the decision, not inside it (ADR-0028).
    """
    diffs = paired_differences(per_scope)
    mean_diff = statistics.fmean(diffs) if diffs else 0.0
    spread = _sd(diffs)
    levels = [float(r["trial_efficiency"]) for rows in per_scope.values() for r in rows]
    if unfinished:
        outcome = "incomplete"
    elif not diffs or max(levels, default=0.0) < MEANINGFUL_EFFICIENCY:
        outcome = "neither_meaningful"
    elif mean_diff > spread:
        outcome = "gp_beats_random"
    else:
        outcome = "tie"
    out = {
        "mean_difference": mean_diff, "spread": spread, "pairs": len(diffs),
        "outcome": outcome, "action": PROTOCOL["decision"][outcome],
    }  # fmt: skip
    if unfinished:
        out["unfinished"] = list(unfinished)
    return out


def campaign_keys(session: ResearchSession, seeds: int) -> list[Key]:
    """Every unit of search this campaign covers, in canonical order."""
    return [
        Key(scope.instrument, scope.direction, arm, seed)
        for scope in campaign_scopes(session.lock)
        for arm in PROTOCOL["arms"]
        for seed in range(seeds)
    ]


def _completeness(session: ResearchSession, seeds: int) -> dict[str, Any]:
    """Trials against quota for every unit of both arms, from the ledger and the campaign's
    locked budget — the protocol compares arms at the same quota."""
    ledger, cid = session.ledger, session.campaign_id
    _purpose, budget = ledger.campaign_purpose(cid)
    shares = {e: float(s) for e, s in session.lock["research"]["engines"].items()}
    scopes = campaign_scopes(session.lock)
    limits = quotas(budget, shares, seeds, scopes) if budget else {}
    rows: dict[str, Any] = {}
    unfinished: list[str] = []
    for key in campaign_keys(session, seeds):
        quota = limits.get(key)
        done = len(ledger.trials(cid, *scope_args(key)))
        rows[str(key)] = {"trials": done, "quota": quota}
        if quota is None or done < quota:
            unfinished.append(str(key))
    if seeds < MIN_SEEDS:
        unfinished.append(f"seeds={seeds} < {MIN_SEEDS}")
    return {"per_unit": rows, "unfinished": unfinished}


def _backtests_per_pass(ledger: Ledger, campaign_id: str, engine: str) -> float | None:
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    ids = {t.candidate_id for t in ledger.trials(campaign_id, engine=engine)}
    configs = sum(int(g4[c][1].get("n_configs", 0)) for c in ids if c in g4)
    passed = sum(1 for c in ids if c in g4 and g4[c][0])
    return (configs + len(ids)) / passed if passed else None


def _engine_portfolio(session: ResearchSession, engine: str, fmap: FeatureMap) -> Any:
    """§3.2.1 from this engine's eligible trials only (cells from the feature map), recorded as
    a variant like every built portfolio."""
    ledger, cid = session.ledger, session.campaign_id
    trials = [t for t in eligible_trials(ledger, cid) if t.engine == engine]
    if not trials:
        return None
    cells: dict[str, str] = {}
    for key in {Key(t.instrument, t.direction, engine, t.seed) for t in trials}:  # type: ignore[arg-type]
        cells.update({k: cell_id(v) for k, v in trial_cells(ledger, cid, key, fmap).items()})
    trials = [dataclasses.replace(t, cell_id=cells.get(t.candidate_id)) for t in trials]
    portfolio = build_portfolio(
        trials, {t.id: load_returns(t.returns_path) for t in trials},
        PortfolioRule.from_lock(session.lock), ledger.trial_stats(),
        periods_per_year(session.timeframe), cid,
    )  # fmt: skip
    record_variant(ledger, portfolio, session.results_dir)
    return portfolio


def _portfolio_dsr_per_engine(
    session: ResearchSession, arms: Sequence[str], fmap: FeatureMap
) -> dict[str, Any]:
    """Both portfolios first, then both DSRs against one snapshot of the ledger's statistics:
    recording a variant raises the variant count, so computing as we go would deflate the second
    arm harder than the first."""
    ledger = session.ledger
    built = {arm: _engine_portfolio(session, arm, fmap) for arm in arms}
    update_n_eff(ledger, seed=0)
    stats, variants = ledger.trial_stats(), ledger.total_portfolio_variants()
    ppy = periods_per_year(session.timeframe)
    out: dict[str, Any] = {"n_eff": stats.n_eff, "portfolio_variants": variants}
    for arm, portfolio in built.items():
        if portfolio is None:
            out[arm] = None
            continue
        report = portfolio_dsr(portfolio.returns.to_numpy(), stats, variants, ppy)
        out[arm] = {"members": len(portfolio.members), "dsr_n_eff": report.dsr_n_eff,
                    "dsr_n_raw": report.dsr_n_raw}  # fmt: skip
    return out


def compare(session: ResearchSession, seeds: int, with_portfolios: bool = True) -> dict[str, Any]:
    ledger, cid = session.ledger, session.campaign_id
    assert_protocol_predates_trials(ledger, cid)
    fmap = FeatureMap.from_lock(session.lock)
    # a report-only comparison needs no bars loaded; without them the ranking margin is omitted
    ppy = periods_per_year(session.timeframe) if session.is_data else None
    rule = divergence_rule(ledger, cid)  # the campaign is read with the rule it locked
    keys = campaign_keys(session, seeds)
    per_seed = {
        arm: [engine_report(ledger, cid, k, fmap, rule, ppy) for k in keys if k.engine == arm]
        for arm in PROTOCOL["arms"]
    }

    completeness = _completeness(session, seeds)
    # from the ledger, not from the current curve: a warning is an event that happened, and an
    # arm whose OOS later recovers must not drop out of the report (ADR-0028 amendment)
    warnings = sorted(str(k) for k in warned_keys(ledger, cid, keys))
    out: dict[str, Any] = {
        "campaign": cid,
        "protocol_sha256": protocol_hash(),
        "per_unit": per_seed,
        "completeness": completeness,
        "divergence_warnings": warnings,  # reported, never decisive (ADR-0028)
        "decision": decide(per_seed, completeness["unfinished"]),
        "gate4_backtests_per_passing_strategy": {
            arm: _backtests_per_pass(ledger, cid, arm) for arm in PROTOCOL["arms"]
        },
    }
    if with_portfolios:
        out["portfolio_dsr"] = _portfolio_dsr_per_engine(session, PROTOCOL["arms"], fmap)
    return out
