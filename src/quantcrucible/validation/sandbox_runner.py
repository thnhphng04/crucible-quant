"""Runs INSIDE the sandbox container (Architecture §3.3.3, ADR-0004) — never on the host for
generated code.

Reads ``<root>/in/{strategy.py, job.json, data/*.parquet}``, executes one job and writes
``<root>/out/report.json``. The report is the only channel back to the host; stdout and stderr
are truncated there. Job kinds: ``signals``, ``leak_check`` (gate ①b) and ``backtest``.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from quantcrucible.core.strategy.base import Bars, Strategy, generate_signals
from quantcrucible.core.strategy.template import load_strategy_class
from quantcrucible.data.store import read_bars
from quantcrucible.validation.feature_stats import max_abs_change_corr
from quantcrucible.validation.leak_check import leak_check

ERROR_CHARS = 4_000
Job = Callable[[type[Strategy], dict[str, Bars], dict[str, Any]], dict[str, Any]]


def _signals(cls: type[Strategy], bars: dict[str, Bars], job: dict[str, Any]) -> dict[str, Any]:
    strategy = cls(job.get("params") or {})
    out: dict[str, list[list[Any]]] = {}
    for symbol, b in bars.items():
        sigs = generate_signals(strategy, b, int(job["lookback"]))
        out[symbol] = [[s.direction, s.strength, s.stop_distance, s.take_profit] for s in sigs]
    return {"signals": out}


def _leak_check(cls: type[Strategy], bars: dict[str, Bars], job: dict[str, Any]) -> dict[str, Any]:
    strategy = cls(job.get("params") or {})
    per_symbol = {
        symbol: leak_check(
            strategy, b, int(job.get("cut_points", 20)), int(job.get("seed", 0)),
            int(job.get("window", 20)),
        )
        for symbol, b in bars.items()
    }  # fmt: skip
    return {
        "checked": sum(r["checked"] for r in per_symbol.values()),
        "n_leaks": sum(r["n_leaks"] for r in per_symbol.values()),
        "leaks": [leak for r in per_symbol.values() for leak in r["leaks"]][:10],
    }


def _backtest(cls: type[Strategy], bars: dict[str, Bars], job: dict[str, Any]) -> dict[str, Any]:
    # imported here: NautilusTrader takes ~2 s to import and only this job needs it (O6)
    from quantcrucible.execution.engine import run_backtest
    from quantcrucible.execution.nautilus_bridge import CostModel
    from quantcrucible.execution.risk import RiskSettings

    costs = CostModel(**job.get("costs", {}))
    res = run_backtest(
        cls(job.get("params") or {}),
        bars,
        exchange=job.get("exchange", "binance"),
        costs=costs,
        initial_cash=float(job.get("initial_cash", 100_000.0)),
        lookback=int(job["lookback"]),
        seed=int(job.get("seed", 0)),
        risk=RiskSettings(**job["risk"]),  # required: sizing is locked per campaign (ADR-0010)
    )
    strategy = cls(job.get("params") or {})
    corr, pair = 0.0, None
    for symbol_bars in bars.values():
        rho, names = max_abs_change_corr(strategy.indicators(symbol_bars))
        if rho > corr:
            corr, pair = rho, names
    return {
        "public": res.public_metrics(),
        "indicator_corr": corr,
        "indicator_pair": list(pair) if pair else None,
        "ts": [str(t) for t in res.ts],
        "equity": res.equity.tolist(),
        "returns": res.returns.tolist(),
        "n_fills": len(res.fills),
        "denied_orders": res.denied_orders,
        "signal_counts": dict(res.signals),
        "periods_per_year": res.periods_per_year,
    }


JOBS: dict[str, Job] = {"signals": _signals, "leak_check": _leak_check, "backtest": _backtest}


def load_inputs(inp: Path) -> tuple[dict[str, Any], str, dict[str, Bars]]:
    job: dict[str, Any] = json.loads((inp / "job.json").read_text(encoding="utf-8"))
    source = (inp / "strategy.py").read_text(encoding="utf-8")
    bars = {
        symbol: read_bars(inp / "data" / name, symbol, job["timeframe"])
        for symbol, name in job["symbols"].items()
    }
    return job, source, bars


def execute(root: Path) -> dict[str, Any]:
    try:
        job, source, bars = load_inputs(root / "in")
        kind = job["kind"]
        if kind not in JOBS:
            raise ValueError(f"unknown job kind {kind!r}")
        cls = load_strategy_class(source)  # generated code runs from here on
        return {"ok": True, "kind": kind, "result": JOBS[kind](cls, bars, job)}
    except BaseException as e:  # every failure — SystemExit included — must reach the report
        if isinstance(e, KeyboardInterrupt):
            raise
        return {
            "ok": False,
            "error_type": type(e).__name__,
            "error": f"{type(e).__name__}: {e}"[:ERROR_CHARS],
            "traceback": traceback.format_exc(limit=8)[-ERROR_CHARS:],
            # the strategy touched the OS (network, files) — the host logs SANDBOX_VIOLATION
            "os_error": isinstance(e, OSError),
        }


def main(argv: list[str] | None = None) -> int:
    root = Path((argv or sys.argv[1:] or ["/job"])[0])
    report = execute(root)
    (root / "out" / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
