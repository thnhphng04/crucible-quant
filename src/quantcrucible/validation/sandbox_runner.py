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

from quantcrucible.core.perp_inputs import PerpBundle, read_bundle
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
    perp = job.get("perp_inputs") or None
    res = run_backtest(
        cls(job.get("params") or {}),
        bars,
        exchange=job.get("exchange", "binance"),
        costs=costs,
        initial_cash=float(job.get("initial_cash", 100_000.0)),
        lookback=int(job["lookback"]),
        seed=int(job.get("seed", 0)),
        risk=RiskSettings(**job["risk"]),  # required: sizing is locked per campaign (ADR-0010)
        perp=perp,
        leverage=int(job.get("leverage", 5)),
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
        # Perpetual reporting (P3-21). Zero and empty on the spot path, which models neither.
        "funding_paid": res.funding_paid,
        "liquidated": list(res.liquidated),
        "terminated_at": str(res.terminated_at) if res.terminated_at is not None else None,
        "stops_placed": [[s.ts, s.symbol, s.direction, s.trigger, s.qty] for s in res.stops_placed],
        # The stream a portfolio replay needs later: it sizes from signals, not from these fills
        # (ADR-0035), so gate ③ is where it has to be captured.
        "signals_stream": {
            symbol: [
                [sig.direction, sig.strength, sig.stop_distance, sig.take_profit]
                for sig in generate_signals(strategy, series, int(job["lookback"]))
            ]
            for symbol, series in bars.items()
        },
    }


def _grid_backtest(
    cls: type[Strategy], bars: dict[str, Bars], job: dict[str, Any]
) -> dict[str, Any]:
    """Gate ④: one backtest per configuration of the pre-registered set, same data and costs.
    Returns the (n_configs × n_obs) return matrix on the shared bar-close axis."""
    from quantcrucible.execution.engine import run_backtest
    from quantcrucible.execution.nautilus_bridge import CostModel
    from quantcrucible.execution.risk import RiskSettings

    costs = CostModel(**job.get("costs", {}))
    perp = job.get("perp_inputs") or None
    rows: list[list[float]] = []
    trades: list[int] = []
    holding: list[float] = []
    funding: list[float] = []
    terminated: list[str | None] = []
    ts: list[str] = []
    for params in job["grid"]:
        res = run_backtest(
            cls(params),
            bars,
            exchange=job.get("exchange", "binance"),
            costs=costs,
            initial_cash=float(job.get("initial_cash", 100_000.0)),
            lookback=int(job["lookback"]),
            seed=int(job.get("seed", 0)),
            risk=RiskSettings(**job["risk"]),
            perp=perp,
            leverage=int(job.get("leverage", 5)),
        )
        rows.append(res.returns.tolist())
        trades.append(res.n_trades)
        holding.append(res.avg_holding_bars)
        funding.append(res.funding_paid)
        # A liquidation ends that configuration, it never raises: one grid cell must not take the
        # other 199 with it (ADR-0032 decision ⑤).
        terminated.append(str(res.terminated_at) if res.terminated_at is not None else None)
        ts = [str(t) for t in res.ts[1:]]
    return {
        "ts": ts, "returns": rows, "n_trades": trades, "avg_holding_bars": holding,
        "funding_paid": funding, "terminated_at": terminated,
    }  # fmt: skip


JOBS: dict[str, Job] = {
    "signals": _signals,
    "leak_check": _leak_check,
    "backtest": _backtest,
    "grid_backtest": _grid_backtest,
}


# Kinds that price a position, and therefore cannot run a perpetual without its bundle.
# `signals` and `leak_check` read direction and stop distance only; requiring it there would
# stage megabytes of mark and path data for jobs that never look at them.
PRICING_KINDS = frozenset({"backtest", "grid_backtest"})


def _is_perpetual(symbol: str) -> bool:
    """ccxt spells a perpetual ``BASE/QUOTE:SETTLE``. Asked of the symbol rather than read from
    a config flag, so the requirement cannot be switched off by a lock that forgot to say so —
    and so a spot campaign, which has no mark and no funding, is unaffected."""
    return ":" in symbol


def load_inputs(inp: Path) -> tuple[dict[str, Any], str, dict[str, Bars], dict[str, PerpBundle]]:
    job: dict[str, Any] = json.loads((inp / "job.json").read_text(encoding="utf-8"))
    source = (inp / "strategy.py").read_text(encoding="utf-8")
    timeframe = job["timeframe"]
    bars = {
        symbol: read_bars(inp / "data" / name, symbol, timeframe)
        for symbol, name in job["symbols"].items()
    }
    perp = {
        symbol: read_bundle(inp / "data", symbol, timeframe, names)
        for symbol, names in (job.get("perp") or {}).items()
    }
    unpriced = sorted(s for s in bars if _is_perpetual(s) and s not in perp)
    if job["kind"] in PRICING_KINDS and unpriced:
        # Fail closed (INV-94). Running anyway would silently treat the mark as the trade price
        # and funding as zero — a claim about the market, not an absence of data — and the result
        # would look like an ordinary backtest.
        raise ValueError(
            f"a {job['kind']} job carries no perpetual inputs for {', '.join(unpriced)}: mark, "
            "funding, intrabar paths and leverage brackets are required, and refusing is the "
            "only honest answer"
        )
    for symbol, bundle in perp.items():
        bundle.aligned_with(bars[symbol])
    return job, source, bars, perp


def execute(root: Path) -> dict[str, Any]:
    try:
        job, source, bars, perp = load_inputs(root / "in")
        kind = job["kind"]
        if kind not in JOBS:
            raise ValueError(f"unknown job kind {kind!r}")
        cls = load_strategy_class(source)  # generated code runs from here on
        job["perp_inputs"] = perp  # the job functions read it from here, not from the JSON
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
