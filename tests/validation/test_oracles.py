"""Leaky-oracle suite vs gates ①a and ①b (07-VALIDATION-LAYER §9, ADR-0005) — INV-34.

The phase-0 gate: the harness rejects all 4 oracle levels, and the EMA crossover passes. Every
oracle needs code outside the ①a whitelist to cheat, so the full pipeline stops it at ①a; ①b
is proven separately, with ①a removed, as the second line of defence.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import numpy as np
import pytest

from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.template import load_strategy_class, template_hash
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import (
    G1A_STATIC,
    G1B_DYNAMIC,
    GateContext,
    GatePipeline,
    StrategyCandidate,
)
from quantcrucible.validation.guardrail import DynamicGuardrail, StaticGuardrail
from quantcrucible.validation.leak_check import leak_check, perturb_after
from quantcrucible.validation.oracles import ORACLES, oracle_source
from quantcrucible.validation.sandbox import SandboxRunner
from tests.factories import make_bars

EMA = files("quantcrucible.core.zoo").joinpath("ema_crossover.py").read_text("utf-8")
EMA_PARAMS = {"fast": 20, "slow": 100, "k_atr": 2.0}
LEVELS = sorted(ORACLES)


def is_data(n: int = 600) -> dict[str, Bars]:
    return {
        "BTC/USDT": make_bars(n, seed=11, symbol="BTC/USDT"),
        "ETH/USDT": make_bars(n, seed=12, symbol="ETH/USDT", drift=0.0005),
    }


def candidate(
    source: str, cid: str, params: dict[str, float | int] | None = None
) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=cid, source=source, params=params or {}, universe=("BTC/USDT", "ETH/USDT"),
        timeframe="1d", timerange="2020-01-01/2021-08-23", run_id="r1", campaign_id="c1",
    )  # fmt: skip


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2025/2026", lock_hash="h")
    lock = {
        "research": {"evolve_scope": "joint"},
        "derived": {"template_hash": template_hash("joint")},
    }
    return GateContext(ledger=ledger, lock=lock, services={"is_data": is_data()})


# ── ①a: static ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("level", LEVELS)
def test_oracle_rejected_at_static_gate(ctx: GateContext, level: int) -> None:
    result = StaticGuardrail().check(candidate(oracle_source(level), f"o{level}"), ctx)
    assert not result.passed
    assert result.event == Event.AST_REJECT  # the template itself is intact: not a tamper


def test_level_1_rejected_for_its_shift(ctx: GateContext) -> None:
    result = StaticGuardrail().check(candidate(oracle_source(1), "o1"), ctx)
    assert "`.shift()` is not allowed (look-ahead risk)" in result.reason


def test_ema_passes_static_gate(ctx: GateContext) -> None:
    assert StaticGuardrail().check(candidate(EMA, "ema", EMA_PARAMS), ctx).passed


# ── ①b's measurement, on the host (oracles are trusted, hand-written code) ──


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("seed", range(5))
def test_leak_check_catches_every_oracle(level: int, seed: int) -> None:
    strategy = load_strategy_class(oracle_source(level))()
    for bars in is_data().values():
        assert leak_check(strategy, bars, seed=seed)["n_leaks"] > 0


@pytest.mark.parametrize("seed", range(5))
def test_leak_check_clears_the_ema_crossover(seed: int) -> None:
    strategy = load_strategy_class(EMA)(EMA_PARAMS)
    for bars in is_data().values():
        report = leak_check(strategy, bars, seed=seed)
        assert report["n_leaks"] == 0 and report["checked"] == 20 * 20 * 2


def test_perturbation_keeps_the_past() -> None:
    bars = make_bars(100)
    out = perturb_after(bars, 59, np.random.default_rng(0))
    np.testing.assert_array_equal(out.ts, bars.ts)
    np.testing.assert_array_equal(out.close[:60], bars.close[:60])
    assert not np.allclose(out.close[60:], bars.close[60:])
    assert np.all(out.high >= np.maximum(out.open, out.close))
    assert np.all(out.low <= np.minimum(out.open, out.close))


# ── ①b through the sandbox, and the pipeline with ledger rows ────────────


@pytest.fixture
def sandboxed(ctx: GateContext, sandbox_image: str) -> GateContext:
    ctx.services["sandbox"] = SandboxRunner(sandbox_image)
    return ctx


@pytest.mark.docker
@pytest.mark.parametrize("level", LEVELS)
def test_oracle_level_rejected_by_dynamic_gate_alone(sandboxed: GateContext, level: int) -> None:
    """①a removed (as if it had a hole): ①b still rejects every oracle, with ledger rows."""
    cid = f"oracle-{level}"
    oracle = candidate(oracle_source(level), cid)
    outcome = GatePipeline([DynamicGuardrail()]).run(oracle, sandboxed)
    assert not outcome.passed and outcome.failed_gate == G1B_DYNAMIC
    [(gate, passed, reason)] = sandboxed.ledger.gate_results(cid)
    assert (gate, passed) == (G1B_DYNAMIC, False) and reason.startswith("look-ahead")
    assert sandboxed.ledger.events("c1")[-1][0] == Event.LEAK_REJECT


@pytest.mark.docker
def test_full_guardrail_rejects_oracles_and_passes_ema(sandboxed: GateContext) -> None:
    pipeline = GatePipeline([StaticGuardrail(), DynamicGuardrail()])
    for level in LEVELS:
        outcome = pipeline.run(candidate(oracle_source(level), f"oracle-{level}"), sandboxed)
        assert outcome.failed_gate == G1A_STATIC
        assert sandboxed.ledger.gate_results(f"oracle-{level}")[0][:2] == (G1A_STATIC, False)
    outcome = pipeline.run(candidate(EMA, "ema", EMA_PARAMS), sandboxed)
    assert outcome.passed, outcome.results
    assert [g for g, _, _ in sandboxed.ledger.gate_results("ema")] == [G1A_STATIC, G1B_DYNAMIC]
    rejects = [e for e, _ in sandboxed.ledger.events("c1") if e == Event.AST_REJECT]
    assert len(rejects) == len(LEVELS)
