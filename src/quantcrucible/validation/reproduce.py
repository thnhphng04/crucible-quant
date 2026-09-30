"""Audit L3: re-produce the kernel-measured members before a freeze (P3-48, ADR-0038; INV-109).

The kernel engine is audited while it runs (L0-L2), but on a sample. Before a campaign freezes on
a portfolio, every member whose gate-③ trial the kernel measured is measured again by the
canonical path of the campaign's precision — the Docker sandbox in float64, the CPU build in
float32 (ADR-0039) — and its returns must equal the recorded ones bit for bit. The outcome is an
audit event (``MEMBERS_REPRODUCED``) that :func:`~quantcrucible.validation.freeze.freeze_campaign`
reads; a member measured in the sandbox is canonical already.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from quantcrucible.config.schema import AUDIT_RATE_FLOOR, Compute
from quantcrucible.core.strategy.base import Bars
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent, TrialRow
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.is_gates import backtest_options
from quantcrucible.validation.numerics import Numerics
from quantcrucible.validation.portfolio import Member, load_returns
from quantcrucible.validation.sandbox import JobRunner, SandboxJob

SANDBOX = "sandbox"


@dataclass(frozen=True, slots=True)
class Reproduction:
    trial_id: int
    candidate_id: str
    measured_by: str  # the engine that recorded the trial
    canonical: str  # the engine that measured it again
    matched: bool


def members_of(ledger: Ledger, campaign_id: str, portfolio_hash: str) -> list[Member]:
    for v in ledger.portfolio_variants(campaign_id):
        if v.portfolio_hash == portfolio_hash:
            return [Member.from_dict(d) for d in v.members]
    raise ValueError(f"{portfolio_hash[:12]}… is not a portfolio variant of {campaign_id}")


def kernel_trials(ledger: Ledger, campaign_id: str, portfolio_hash: str) -> list[TrialRow]:
    """The members' trials that a kernel build measured."""
    by_id = {t.id: t for t in ledger.trials(campaign_id)}
    members = members_of(ledger, campaign_id, portfolio_hash)
    return [by_id[m.trial_id] for m in members if by_id[m.trial_id].backtest_engine != SANDBOX]


def reproduce_members(
    ledger: Ledger,
    campaign_id: str,
    portfolio_hash: str,
    lock: Mapping[str, Any],
    is_data: Mapping[str, Bars],
    archive: StrategyArchive,
    sandbox: JobRunner,
) -> list[Reproduction]:
    """Measure every kernel-measured member again on the canonical path; record the outcome."""
    from quantcrucible.validation.engine_router import EngineRouter

    numerics = Numerics.from_lock(lock)
    canonical = SANDBOX if numerics.precision == "float64" else "cpu_kernel"
    runner: JobRunner = sandbox
    if canonical != SANDBOX:
        runner = EngineRouter(sandbox, lock, Compute("cpu_kernel", AUDIT_RATE_FLOOR))
    out: list[Reproduction] = []
    for t in kernel_trials(ledger, campaign_id, portfolio_hash):
        universe = [s for s in t.universe.split(",") if s]
        job = SandboxJob(
            "backtest", archive.get(t.strategy_hash), {s: is_data[s] for s in universe},
            dict(t.params), backtest_options(lock, t.seed),
        )  # fmt: skip
        res = runner.run(job)
        recorded = load_returns(t.returns_path).to_numpy(dtype=np.float64)
        matched = bool(
            res.ok
            and res.report is not None
            and res.engine == canonical
            and np.asarray(res.report["result"]["returns"], dtype=np.float64).tobytes()
            == recorded.tobytes()
        )
        out.append(Reproduction(t.id, t.candidate_id, t.backtest_engine, canonical, matched))
    ledger.log_event(
        GenerationEvent(
            run_id=f"reproduce-{campaign_id}", campaign_id=campaign_id, engine="none", seed=0,
            agent="engine_router", model_used="none", event=Event.MEMBERS_REPRODUCED,
            detail={
                "portfolio_hash": portfolio_hash, "numerics": numerics.tag,
                "members": [asdict(r) for r in out],
            },
        )
    )  # fmt: skip
    return out


def reproduction_problem(ledger: Ledger, campaign_id: str, portfolio_hash: str) -> str | None:
    """Why the portfolio may not freeze yet on L3 grounds, or ``None``."""
    needed = {t.id for t in kernel_trials(ledger, campaign_id, portfolio_hash)}
    if not needed:
        return None
    for event, detail in reversed(ledger.event_details(campaign_id)):
        if event != Event.MEMBERS_REPRODUCED or not detail:
            continue
        if detail.get("portfolio_hash") != portfolio_hash:
            continue
        rows = {int(r["trial_id"]): bool(r["matched"]) for r in detail.get("members", [])}
        missing = sorted(needed - rows.keys())
        failed = sorted(i for i in needed & rows.keys() if not rows[i])
        if failed:
            return f"trials {failed} did not re-produce on the canonical path (audit L3)"
        if missing:
            return f"trials {missing} have not been re-produced on the canonical path"
        return None
    return (
        f"{len(needed)} member(s) were measured by the kernel engine and have not been "
        "re-produced on the canonical path (audit L3): run `cli freeze`, which does it first"
    )
