"""Gate ①a static guardrail (Architecture §3.3.1, §3.1.6) — INV-30, INV-31, INV-32."""

from importlib.resources import files
from pathlib import Path

import pytest

from quantcrucible.core.strategy.template import canonical_template, parse, render, template_hash
from quantcrucible.core.strategy.tunable import default_params
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import GateContext, GateResult, StrategyCandidate
from quantcrucible.validation.guardrail import StaticGuardrail

EMA = files("quantcrucible.core.zoo").joinpath("ema_crossover.py").read_text("utf-8")

GOOD_INDICATORS = """
    def indicators(self, bars: Bars) -> Features:
        return {"f": ind.ema(bars.close, self.p.n), "atr": ind.atr(bars, 14)}
"""
GOOD_SIGNAL = """
    def signal(self, x: FeatureView) -> Signal:
        if x["f"] > x["f"].ago(1):
            return Signal("long", 1.0, self.p.k * x["atr"])
        return Signal("flat", 0.0, 0.0)
"""
TUNABLES = "    # TUNABLE: n = 20, bounds=(5, 60)\n    # TUNABLE: k = 2.0, bounds=(1.0, 4.0)\n"
PARAMS = {"n": 20, "k": 2.0}


def body(indicators: str = GOOD_INDICATORS, signal: str = GOOD_SIGNAL) -> str:
    return TUNABLES + indicators + signal


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2025/2026", lock_hash="h")
    lock = {
        "research": {"evolve_scope": "joint"},
        "derived": {"template_hash": template_hash("joint")},
    }
    return GateContext(ledger=ledger, lock=lock)


def cand(source: str, params: dict[str, float | int] | None = None) -> StrategyCandidate:
    if params is None:
        params = default_params(list(parse(source).tunables))
    return StrategyCandidate(
        candidate_id="c", source=source, params=params, universe=("BTC/USDT",), timeframe="1d",
        timerange="x", run_id="r", campaign_id="c1",
    )  # fmt: skip


def run(ctx: GateContext, source: str, params: dict[str, float | int] | None = None) -> GateResult:
    return StaticGuardrail().check(cand(source, params), ctx)


def test_ema_crossover_passes(ctx: GateContext) -> None:
    result = run(ctx, EMA)
    assert result.passed, result.reason


@pytest.mark.parametrize("name", ["ema_crossover", "sma_trend", "rsi_momentum", "rsi_momentum_v2"])
def test_every_zoo_strategy_passes(ctx: GateContext, name: str) -> None:
    """The hand-written strategies of the phase-1 run (P1-12) are valid template instances."""
    source = files("quantcrucible.core.zoo").joinpath(f"{name}.py").read_text("utf-8")
    result = run(ctx, source)
    assert result.passed, result.reason


def test_good_block_passes(ctx: GateContext) -> None:
    assert run(ctx, render(canonical_template(), {"joint": body()}), PARAMS).passed


def test_template_tamper_rejected(ctx: GateContext) -> None:
    tampered = EMA.replace(
        "from quantcrucible.core.strategy.registry import ind",
        "from quantcrucible.core.strategy.registry import ind\nimport os",
    )
    result = run(ctx, tampered)
    assert not result.passed and result.event == Event.TEMPLATE_TAMPER


def test_whole_file_replacement_rejected(ctx: GateContext) -> None:
    result = run(ctx, "class GeneratedStrategy:\n    pass\n", {})
    assert not result.passed and result.event == Event.TEMPLATE_TAMPER


def test_missing_lock_fails_closed(ctx: GateContext) -> None:
    ctx.lock = {}
    assert not run(ctx, EMA).passed


def test_undeclared_constant_rejected(ctx: GateContext) -> None:
    src = render(
        canonical_template(), {"joint": body(signal=GOOD_SIGNAL.replace("self.p.k * x", "2.5 * x"))}
    )
    result = run(ctx, src, PARAMS)
    assert not result.passed and "undeclared constant 2.5" in result.reason


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"n": 20}, "TUNABLE names"),
        ({"n": 20, "k": 9.0}, "outside bounds"),
        ({"n": 20.5, "k": 2.0}, "must be an int"),
    ],
)
def test_params_must_match_tunables(
    ctx: GateContext, params: dict[str, float | int], message: str
) -> None:
    result = run(ctx, render(canonical_template(), {"joint": body()}), params)
    assert not result.passed and message in result.reason


IND = "    def indicators(self, bars: Bars) -> Features:\n"
SIG = "    def signal(self, x: FeatureView) -> Signal:\n"

MALICIOUS: list[tuple[str, str, str]] = [
    # (indicators body, signal body, expected fragment of the reason)
    (IND + "        import os\n        return {}\n", GOOD_SIGNAL, "imports"),
    (IND + '        return {"f": bars.close[1:]}\n', GOOD_SIGNAL, "indexing"),
    (IND + '        return {"f": bars.close.shift(-1)}\n', GOOD_SIGNAL, "shift"),
    (IND + '        return {"f": bars.close[-1]}\n', GOOD_SIGNAL, "indexing"),
    (IND + "        for i in []:\n            pass\n        return {}\n", GOOD_SIGNAL, "loops"),
    (IND + "        while True:\n            pass\n", GOOD_SIGNAL, "loops"),
    (IND + '        return {"f": eval("1")}\n', GOOD_SIGNAL, "eval"),
    (IND + '        return {"f": open("x")}\n', GOOD_SIGNAL, "open"),
    (IND + '        return {"f": getattr(bars, "close")}\n', GOOD_SIGNAL, "getattr"),
    (IND + '        return {"f": bars.__class__}\n', GOOD_SIGNAL, "dunder"),
    (IND + '        return {"f": ind._smooth(bars.close, 3, 0.5)}\n', GOOD_SIGNAL, "_smooth"),
    (IND + '        return {"f": ind.vwap(bars.close, self.p.n)}\n', GOOD_SIGNAL, "whitelisted"),
    (IND + '        return {"f": np.roll(bars.close, -1)}\n', GOOD_SIGNAL, "not allowed"),
    (IND + '        return {"f": (lambda: 1)()}\n', GOOD_SIGNAL, "may be called"),
    (IND + '        return {"f": [c for c in bars.close]}\n', GOOD_SIGNAL, "ListComp"),
    (IND + "        global g\n        return {}\n", GOOD_SIGNAL, "global"),
    (IND + '        return {"f": bars.ts}\n', GOOD_SIGNAL, "bars.ts"),
    (IND + '        return {"f": ind.ema(bars.close, self.p.hidden)}\n', GOOD_SIGNAL, "TUNABLE"),
    (
        IND + '        return {"f": ind.ema(bars.close, 37)}\n',
        GOOD_SIGNAL,
        "undeclared constant 37",
    ),
    (GOOD_INDICATORS, SIG + '        return Signal("long", 1.0, x["f"].ago(-1))\n', "future"),
    (
        GOOD_INDICATORS,
        SIG + "        k = self.p.n\n        return Signal('flat', 0.0, x[k])\n",
        "indexing",
    ),
    (GOOD_INDICATORS, SIG + "        with open('x'):\n            pass\n", "With"),
    (GOOD_INDICATORS, SIG + "        ind = 3\n        return Signal('flat', 0.0, 0.0)\n", "rebind"),
    (GOOD_INDICATORS, SIG + "        return Signal('flat', 0.0, x.__dict__)\n", "dunder"),
    (
        GOOD_INDICATORS,
        SIG + "        try:\n            pass\n        except Exception:\n            pass\n",
        "Try",
    ),
    ("    p = 1\n" + GOOD_INDICATORS, GOOD_SIGNAL, "only `def indicators"),
    (GOOD_INDICATORS, "", "`signal` exactly once"),
    (
        GOOD_INDICATORS.replace("(self, bars: Bars)", "(self, bars, extra)"),
        GOOD_SIGNAL,
        "must take exactly",
    ),
]


@pytest.mark.parametrize(("indicators", "signal", "fragment"), MALICIOUS)
def test_malicious_snippets(ctx: GateContext, indicators: str, signal: str, fragment: str) -> None:
    src = render(canonical_template(), {"joint": body(indicators, signal)})
    result = run(ctx, src, PARAMS)
    assert not result.passed
    assert result.event == Event.AST_REJECT
    assert fragment in result.reason, result.reason
