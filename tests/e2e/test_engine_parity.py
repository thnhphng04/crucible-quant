"""The kernel engine end to end (P3-44, ADR-0038; INV-105): one synthetic campaign measured twice —
once by the Docker sandbox alone, once through the engine router — must leave the same trials,
the same gate verdicts and byte-identical result files (③ returns, signal streams, ④ matrices).
Synthetic data only, never the root holdout/.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.agent.compare import lock_protocol
from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.config.loader import parse_user_config
from quantcrucible.config.lock import open_campaign, read_lock, sha256_file
from quantcrucible.config.schema import Compute
from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.genome import (
    Combine,
    Cross,
    Genome,
    Indicator,
    Param,
    Slope,
    render_genome,
)
from quantcrucible.data.holdout_split import carve
from quantcrucible.data.store import ResearchStore, parse_range
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.engine_router import make_job_runner
from quantcrucible.validation.numerics import numerics_tag
from quantcrucible.validation.research_run import ResearchSession, submit
from quantcrucible.validation.run import Provenance, derived_settings
from quantcrucible.validation.sandbox import JobRunner, SandboxRunner
from tests.factories import make_trending_bars

pytestmark = [pytest.mark.docker, pytest.mark.slow]

SYMBOL = "A/USDT"
HOLDOUT = (date(2024, 2, 1), date(2025, 2, 1))
CAMPAIGN = "c-eng"
ENGINES = ("sandbox", "cpu_kernel", "auto")  # auto = CUDA when a device passes its self-test


def _sources() -> list[tuple[str, str, dict[str, float | int]]]:
    """A trend follower that reaches gate ④ on trending bars, and sampled genomes."""

    def n(v: int) -> Param:
        return Param("period", 2, 300, v)

    trend = Genome(
        Combine("and", (Cross(Indicator("ema", n(10)), True, Indicator("ema", n(40))),
                        Slope(Indicator("sma", n(30)), True))),
        Param("mult", 0.5, 5.0, 2.0),
    )  # fmt: skip
    genomes = [trend]
    rng = np.random.default_rng(44)
    genomes += [sample_genome(rng, GrammarConfig()) for _ in range(4)]
    out = []
    for i, g in enumerate(genomes):
        src, params = render_genome(g, "long", 1.1)
        out.append((f"cand-{i}", src, params))
    return out


def _campaign(root: Path, image: str, engine: str) -> Ledger:
    cfg = parse_user_config({
        "research": {
            "data": {"symbols": [SYMBOL]},
            "engines": {"gp": 1.0, "random": 0.0},
            "seeds": 1,
            "campaign": {"purpose": "harness_test", "trial_budget": 20},
            "pbo_grid": {"values_per_param": 3, "range": 0.3, "max_configs": 8},
            "holdout_pass": 0.5,
        }
    })  # fmt: skip
    full = {SYMBOL: make_trending_bars(2_600, seed=31, symbol=SYMBOL, regime=100)}
    carve(
        full, HOLDOUT[0], HOLDOUT[1], in_sample_dir=root / "data" / "is",
        holdout_dir=root / "holdout", lock_path=root / "holdout.lock", harden=False,
    )  # fmt: skip
    ledger_path = root / "crucible.db"
    ledger = Ledger.open(ledger_path)
    lock_path = root / "config" / "evaluation.lock.yaml"
    derived = derived_settings("joint")
    derived["exit_protocol"] = "bracket_timeout_v1"
    derived["backtest_numerics"] = numerics_tag("float64")
    open_campaign(
        cfg, ledger, CAMPAIGN, lock_path, holdout_range=f"{HOLDOUT[0]}/{HOLDOUT[1]}",
        holdout_lock_hash=sha256_file(root / "holdout.lock"), derived=derived,
    )  # fmt: skip
    lock = read_lock(lock_path)
    store = ResearchStore(root / "data" / "is", [parse_range(f"{HOLDOUT[0]}/{HOLDOUT[1]}")])
    is_data: dict[str, Bars] = {SYMBOL: store.bars(SYMBOL, "1d")}
    box = SandboxRunner(image, label=f"e2e-eng-{engine}")
    runner: JobRunner = box
    if engine != "sandbox":
        # audit_rate 0.02 (the floor): the comparison below is the audit that matters here
        runner = make_job_runner(box, lock, Compute(engine, 0.02), ledger_path, "e2e")  # type: ignore[arg-type]
    session = ResearchSession(
        ledger=ledger, lock=lock, campaign_id=CAMPAIGN, is_data=is_data, sandbox=runner,
        results_dir=root / "results",
    )  # fmt: skip
    lock_protocol(ledger, CAMPAIGN)
    for cid, src, params in _sources():
        prov = Provenance("gp", 0, "e2e", instrument=SYMBOL, direction="long")
        submit(session, src, cid, params=params, provenance=prov)
    return ledger


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory, sandbox_image: str) -> dict[str, Path]:
    roots = {}
    for engine in ENGINES:
        root = tmp_path_factory.mktemp(f"eng-{engine}")
        _campaign(root, sandbox_image, engine)
        roots[engine] = root
    return roots


def _trials(root: Path) -> list[tuple[object, ...]]:
    rows = Ledger.open(root / "crucible.db").trials(CAMPAIGN)
    return [
        (t.candidate_id, t.strategy_hash, t.params, t.universe, t.timeframe, t.timerange,
         np.float64(t.sharpe_is).tobytes(), t.verdict, t.instrument, t.direction,
         Path(t.returns_path).read_bytes())
        for t in sorted(rows, key=lambda r: r.candidate_id)
    ]  # fmt: skip


def _gates(root: Path) -> dict[str, list[tuple[str, bool, str]]]:
    ledger = Ledger.open(root / "crucible.db")
    return {cid: list(ledger.gate_results(cid)) for cid, _src, _p in _sources()}


def _files(root: Path) -> dict[str, list[bytes]]:
    """Result files by folder and extension: a measurement's file name is a random UUID."""
    base = root / "results"
    out: dict[str, list[bytes]] = {}
    for p in base.rglob("*"):
        if p.is_file():
            key = f"{p.parent.relative_to(base).as_posix()}/*{''.join(p.suffixes)}"
            out.setdefault(key, []).append(p.read_bytes())
    return {k: sorted(v) for k, v in out.items()}


@pytest.mark.parametrize("engine", ENGINES[1:])
def test_the_router_leaves_the_same_trials_as_the_sandbox(
    runs: dict[str, Path], engine: str
) -> None:
    want = _trials(runs["sandbox"])
    assert want, "no candidate reached gate ③"
    assert _trials(runs[engine]) == want


@pytest.mark.parametrize("engine", ENGINES[1:])
def test_every_gate_verdict_is_the_same(runs: dict[str, Path], engine: str) -> None:
    assert _gates(runs[engine]) == _gates(runs["sandbox"])


@pytest.mark.parametrize("engine", ENGINES[1:])
def test_result_files_are_byte_identical(runs: dict[str, Path], engine: str) -> None:
    want = _files(runs["sandbox"])
    assert any(k.startswith("pbo/") and k.endswith(".parquet") for k in want), "no ④ matrix"
    assert _files(runs[engine]) == want


@pytest.mark.parametrize("engine", ENGINES[1:])
def test_the_kernel_measured_every_trial_without_falling_back(
    runs: dict[str, Path], engine: str
) -> None:
    ledger = Ledger.open(runs[engine] / "crucible.db")
    assert {t.backtest_engine for t in ledger.trials(CAMPAIGN)} <= {"cuda", "cpu_kernel"}
    assert not ledger.events_named(Event.ENGINE_AUDIT_MISMATCH)
    assert not ledger.events_named(Event.ENGINE_FALLBACK)
    sandboxed = Ledger.open(runs["sandbox"] / "crucible.db").trials(CAMPAIGN)
    assert {t.backtest_engine for t in sandboxed} == {"sandbox"}
