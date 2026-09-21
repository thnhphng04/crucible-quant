"""Phase-0 gate, end to end (Architecture §7): the EMA crossover and the 4 leaky oracles go
through ①a → ①b → ② → ③ with real containers and a real ledger. INV-01, INV-34.

Synthetic in-sample data only — never real market data, never the holdout.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest

from quantcrucible.config.lock import open_campaign, read_lock
from quantcrucible.config.schema import UserConfig
from quantcrucible.core.strategy.base import Bars
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import (
    G1A_STATIC,
    G1B_DYNAMIC,
    G2_MINBTL,
    G3_IS,
    GateContext,
    GatePipeline,
    PipelineOutcome,
)
from quantcrucible.validation.guardrail import DynamicGuardrail
from quantcrucible.validation.is_gates import InSampleGate, MinBtlGate
from quantcrucible.validation.oracles import ORACLES, oracle_source
from quantcrucible.validation.run import derived_settings, make_candidate, run_candidate
from quantcrucible.validation.sandbox import SandboxRunner
from tests.factories import make_bars

pytestmark = [pytest.mark.docker, pytest.mark.slow]

EMA = files("quantcrucible.core.zoo").joinpath("ema_crossover.py").read_text("utf-8")


class Phase0:
    def __init__(self, root: Path, image: str) -> None:
        self.root = root
        self.ledger = Ledger.open(root / "ledger.db")
        lock_path = root / "evaluation.lock.yaml"
        open_campaign(
            UserConfig(), self.ledger, "c-e2e", lock_path,
            holdout_range="2030-01-01/2031-01-01", holdout_lock_hash="0" * 64,
            derived=derived_settings("joint"),
        )  # fmt: skip
        self.lock = read_lock(lock_path)
        self.is_data: dict[str, Bars] = {
            f"S{i}/USDT": make_bars(1_800, seed=100 + i, symbol=f"S{i}/USDT", drift=0.0003)
            for i in range(4)
        }
        self.sandbox = SandboxRunner(image)
        self.outcomes: dict[str, PipelineOutcome] = {}

    def run(self, name: str, source: str) -> PipelineOutcome:
        candidate = make_candidate(source, "c-e2e", self.is_data, candidate_id=name)
        outcome = run_candidate(
            candidate, self.ledger, self.lock, self.is_data, self.sandbox, self.root / "results"
        )
        self.outcomes[name] = outcome
        return outcome


@pytest.fixture(scope="module")
def phase0(tmp_path_factory: pytest.TempPathFactory, sandbox_image: str) -> Phase0:
    p = Phase0(tmp_path_factory.mktemp("phase0"), sandbox_image)
    for level in sorted(ORACLES):
        p.run(f"oracle-{level}", oracle_source(level))
    p.run("ema", EMA)
    return p


def test_ema_crossover_passes_every_phase0_gate(phase0: Phase0) -> None:
    outcome = phase0.outcomes["ema"]
    assert outcome.passed, [(r.gate, r.reason) for r in outcome.results]
    gates = [(g, ok) for g, ok, _ in phase0.ledger.gate_results("ema")]
    assert gates == [(G1A_STATIC, True), (G1B_DYNAMIC, True), (G2_MINBTL, True), (G3_IS, True)]
    assert outcome.trial_id is not None
    assert phase0.ledger.trial_verdicts("c-e2e") == [(outcome.trial_id, "ema", "PASS")]
    assert (phase0.root / "results" / "returns" / "c-e2e" / "ema.parquet").is_file()


@pytest.mark.parametrize("level", sorted(ORACLES))
def test_oracle_rejected_before_it_becomes_a_trial(phase0: Phase0, level: int) -> None:
    outcome = phase0.outcomes[f"oracle-{level}"]
    assert not outcome.passed and outcome.failed_gate == G1A_STATIC
    assert outcome.trial_id is None


def test_every_candidate_has_ledger_rows(phase0: Phase0) -> None:
    events = [e for e, _ in phase0.ledger.events("c-e2e")]
    assert events.count(Event.CANDIDATE_SUBMITTED) == len(phase0.outcomes)
    assert events.count(Event.AST_REJECT) == len(ORACLES)
    for name in phase0.outcomes:
        assert phase0.ledger.gate_results(name), name
    assert phase0.ledger.trial_stats().n_raw == 1  # rejected oracles are not trials


def test_oracles_stopped_by_1b_when_1a_is_bypassed(phase0: Phase0) -> None:
    """Defence in depth: with ①a gone, ①b stops every oracle before ② and ③."""
    pipeline = GatePipeline([DynamicGuardrail(), MinBtlGate(), InSampleGate()])
    services = {
        "sandbox": phase0.sandbox, "is_data": phase0.is_data,
        "results_dir": phase0.root / "results",
    }  # fmt: skip
    ctx = GateContext(phase0.ledger, phase0.lock, services)
    for level in sorted(ORACLES):
        name = f"bypass-{level}"
        candidate = make_candidate(oracle_source(level), "c-e2e", phase0.is_data, candidate_id=name)
        outcome = pipeline.run(candidate, ctx)
        assert outcome.failed_gate == G1B_DYNAMIC and outcome.trial_id is None, name
    assert phase0.ledger.trial_stats().n_raw == 1
