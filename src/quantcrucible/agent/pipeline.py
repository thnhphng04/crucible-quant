"""Asynchronous evaluation pipeline for engine C (arch §3.1.8, ADR-0024, P2-08).

The orchestrating thread asks each (engine, seed) for proposals — engine C's are cheap to make —
reserves a trial slot with the scheduler, and hands the proposal to one of ``workers``
evaluation slots. A slot takes it through ``submit`` → gates ①a → ④ → ledger with its own
ledger connection (SQLite serializes the writes; WAL + busy_timeout). When a slot finishes, the
reservation is settled as a trial or not and the engine hears the outcome. An interrupt (or any
error) kills the run's sandbox containers before it propagates (INV-69).

The ledger is the only state: stopping and starting again continues from what it holds.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Protocol

from quantcrucible.agent.engines.random_search import Proposal
from quantcrucible.agent.grammar import categories, genome_to_dict, signature
from quantcrucible.agent.scheduler import Key, TrialScheduler
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.research_run import ResearchSession, submit
from quantcrucible.validation.run import Provenance

DEFAULT_WORKERS = 8  # the machine saturates near 1 backtest/s at 8-12 slots (ADR-0025)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What an evaluation slot reports back: did the candidate become a trial, did it pass."""

    candidate_id: str
    measured: bool
    passed: bool
    failed_gate: str | None = None


class Engine(Protocol):
    def next(self) -> Proposal: ...


class Observer(Protocol):
    def observe(self, proposal: Proposal, outcome: Outcome) -> None: ...


Evaluate = Callable[[Key, Proposal, str], Outcome]


@dataclass
class RunStats:
    proposed: dict[Key, int] = field(default_factory=dict)
    trials: dict[Key, int] = field(default_factory=dict)
    passed: dict[Key, int] = field(default_factory=dict)
    starved: set[Key] = field(default_factory=set)  # stopped: too many proposals, too few trials
    stopped: set[Key] = field(default_factory=set)  # stopped by the monitor (IS→OOS divergence)


class Pipeline:
    def __init__(
        self,
        engines: Mapping[Key, Engine],
        scheduler: TrialScheduler,
        evaluate: Evaluate,
        workers: int,
        run_label: str,
        max_attempts_per_trial: int = 50,
        on_abort: Callable[[], object] | None = None,
        monitor: Callable[[Key, int], bool] | None = None,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be >= 1")
        self.engines = dict(engines)
        self.scheduler = scheduler
        self.evaluate = evaluate
        self.workers = workers
        self.run_label = run_label
        self.max_attempts_per_trial = max_attempts_per_trial
        self.on_abort = on_abort
        self.monitor = monitor  # (key, trials in the ledger) → stop this (engine, seed)?
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def _candidate_id(self, key: Key) -> str:
        with self._lock:
            n = next(self._counter)
        engine, seed = key
        return f"{self.run_label}-{engine}-s{seed}-{n:06d}"

    def _starved(self, key: Key, stats: RunStats) -> bool:
        """Too many proposals per trial: the engine's candidates keep failing before gate ③."""
        trials = stats.trials.get(key, 0)
        return stats.proposed.get(key, 0) >= self.max_attempts_per_trial * (trials + 1)

    def run(self) -> RunStats:
        stats = RunStats()
        keys = [k for k in self.scheduler.quota_keys() if k in self.engines]
        in_flight: dict[Future[Outcome], tuple[Key, Proposal]] = {}
        pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="qc-eval")
        try:
            while True:
                progressed = False
                for key in keys:
                    if len(in_flight) >= self.workers:
                        break
                    if key in stats.starved or key in stats.stopped:
                        continue
                    if not self.scheduler.reserve(*key):
                        continue
                    proposal = self.engines[key].next()
                    stats.proposed[key] = stats.proposed.get(key, 0) + 1
                    cid = self._candidate_id(key)
                    in_flight[pool.submit(self.evaluate, key, proposal, cid)] = (key, proposal)
                    progressed = True
                if not in_flight:
                    if not progressed:
                        break  # every quota used up (or starved)
                    continue
                done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                for fut in done:
                    key, proposal = in_flight.pop(fut)
                    outcome = fut.result()  # an evaluation error stops the run (fail closed)
                    self.scheduler.settle(*key, measured=outcome.measured)
                    stats.trials[key] = stats.trials.get(key, 0) + outcome.measured
                    stats.passed[key] = stats.passed.get(key, 0) + outcome.passed
                    engine = self.engines[key]
                    if hasattr(engine, "observe"):
                        engine.observe(proposal, outcome)
                    if self._starved(key, stats):
                        stats.starved.add(key)
                    if self.monitor is not None and self.monitor(
                        key, self.scheduler.measured(*key)
                    ):
                        stats.stopped.add(key)
        except BaseException:
            for fut in in_flight:
                fut.cancel()
            if self.on_abort is not None:
                self.on_abort()  # kill this run's sandbox containers before anything else
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return stats


def session_evaluator(session: ResearchSession, run_label: str) -> Evaluate:
    """Evaluate through the real gates: each slot thread gets its own session and ledger
    connection (a SQLite connection is bound to its thread); the sandbox runner is shared."""
    local = threading.local()

    def evaluate(key: Key, proposal: Proposal, candidate_id: str) -> Outcome:
        s: ResearchSession | None = getattr(local, "session", None)
        if s is None:
            s = replace(session, ledger=Ledger.open(session.ledger.path))
            local.session = s
        engine, seed = key
        provenance = Provenance(
            engine=engine, seed=seed, run_id=f"{run_label}-{engine}-s{seed}",
            island=proposal.island, parents=proposal.parents, mutation=proposal.mutation,
            descriptors={
                "categories": list(categories(proposal.genome)),
                "signature": list(signature(proposal.genome)),
                "genome": genome_to_dict(proposal.genome),
            },
        )  # fmt: skip
        out = submit(s, proposal.source, candidate_id, params=proposal.params,
                     provenance=provenance)  # fmt: skip
        return Outcome(candidate_id, out.trial_id is not None, out.passed, out.failed_gate)

    return evaluate
