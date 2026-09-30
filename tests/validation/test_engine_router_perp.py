"""Perpetual jobs on the kernel engine (P3-51, ADR-0038; INV-94, INV-105).

Gate ③/④ perpetual jobs replay on K4 and ``signals`` jobs (the portfolio replays of ⑥′ and the
holdout) come from the signal kernel; both must report exactly what the in-container job
functions report. A perpetual job without its bundle is never priced by the kernels."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("numba")

from quantcrucible.core.strategy.genome import render_genome
from quantcrucible.core.strategy.genome_parse import parse_genome
from quantcrucible.validation.sandbox import SandboxJob
from tests.perp_fixtures import perp_market
from tests.validation.test_engine_router import (
    FP32,
    FP64,
    OPTIONS,
    FakeSandbox,
    _canon,
    _genome_job,
    _router,
)

PERP_OPTIONS: dict[str, Any] = {
    **OPTIONS, "lookback": 60, "leverage": 10,
    "exit_policy": {"mode": "bracket_timeout_v1", "tp_sl_ratio": 1.1, "max_holding_bars": 12},
}  # fmt: skip


def _job(kind: str, direction: str, seed: int = 4, bundle: bool = True) -> SandboxJob:
    genome = parse_genome(_genome_job().source).genome
    src, params = render_genome(genome, direction, 1.1)  # type: ignore[arg-type]
    bars, perp = perp_market(600, seed=seed, mark_noise=0.004)
    options = dict(PERP_OPTIONS)
    if kind == "grid_backtest":
        options["grid"] = [params, {**params, "n1": 12}, {**params, "k_stop": 0.8},
                           {**params, "n2": 24}]  # fmt: skip
    return SandboxJob(
        kind, src, {bars.symbol: bars}, params, options,
        perp={bars.symbol: perp} if bundle and kind != "signals" else None,
    )  # fmt: skip


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("kind", ["backtest", "grid_backtest", "signals"])
def test_a_perpetual_genome_job_reports_like_the_sandbox(kind: str, direction: str) -> None:
    job = _job(kind, direction)
    box = FakeSandbox()
    res = _router(box, FP64).run(job)
    assert res.ok and res.engine == "cpu_kernel" and not box.jobs
    canonical = FakeSandbox().run(job).report
    assert res.report is not None and canonical is not None
    assert _canon(res.report["result"]) == _canon(canonical["result"])
    if kind == "backtest":
        public = canonical["result"]["public"]
        assert public["n_trades"] > 3  # the fixture trades, so the comparison means something


def test_a_perpetual_audit_compares_funding_and_liquidations_too(tmp_path: Any) -> None:
    from quantcrucible.ledger.db import Ledger
    from quantcrucible.ledger.records import Event

    path = tmp_path / "ledger.db"
    Ledger.open(path).open_campaign("c1", "2025-09-21/2026-09-21", lock_hash="abc")
    box = FakeSandbox()
    res = _router(box, FP64, path, audit=1.0).run(_job("grid_backtest", "short"))
    assert res.engine == "cpu_kernel" and res.report is not None and res.report["audit"] == "L2"
    assert not Ledger.open(path).events_named(Event.ENGINE_AUDIT_MISMATCH)


def test_a_perpetual_job_without_its_bundle_is_never_priced_by_the_kernels() -> None:
    """INV-94: float64 hands it to the sandbox, which refuses it; float32 refuses it here."""
    job = _job("backtest", "long", bundle=False)
    box = FakeSandbox(canned=True)
    assert _router(box, FP64).run(job).engine == "sandbox" and len(box.jobs) == 1
    box32 = FakeSandbox(canned=True)
    res = _router(box32, FP32).run(job)
    assert not res.ok and res.engine == "refused" and not box32.jobs
