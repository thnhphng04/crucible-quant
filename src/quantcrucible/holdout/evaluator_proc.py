"""The holdout evaluator — its own process, its own entry point (Architecture §4.2 layer 3, P6,
ADR-0016)::

    uv run python -m quantcrucible.holdout.evaluator_proc --portfolio <portfolio_hash>

Takes only a frozen ``portfolio_hash``. After every check in :func:`campaign.preflight`, it re-runs
each member in the sandbox on a copy of the verified holdout slice (warmed up with the in-sample
bars just before it), combines them with the frozen weights and rebalance rule, compares the
annualized OOS Sharpe with ``research.holdout_pass`` (D4), records the one opening, burns the
campaign and prints exactly ``PASS`` or ``FAIL`` — never a number. ``sharpe_oos`` is stored in
``holdout_access`` for the human report only.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantcrucible.core.perp_inputs import PerpBundle, read_bundle
from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.holdout_split import verify_holdout
from quantcrucible.data.store import ResearchStore, file_name, parse_range, read_bars
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.holdout.campaign import (
    HoldoutRefused,
    burn,
    burn_after_error,
    claim,
    find_frozen,
    preflight,
)
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import utc_now
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.is_gates import backtest_options
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.portfolio import Member, combine, consolidate_on_account
from quantcrucible.validation.robustness import (
    account_options,
    annual_sharpe,
    rerun_member,
    weights_of,
)
from quantcrucible.validation.sandbox import JobRunner, SandboxJob, SandboxResult

Runner = JobRunner


class LazySandbox:
    """Builds the sandbox image only when the first member runs — i.e. after every check."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._runner: Runner | None = None

    def run(self, job: SandboxJob) -> SandboxResult:
        if self._runner is None:
            from quantcrucible.validation.sandbox import SandboxRunner, ensure_image

            self._runner = SandboxRunner(ensure_image(self.root))
        return self._runner.run(job)


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str  # PASS | FAIL — the only thing that leaves this process
    sharpe_oos: float  # stored in holdout_access, never printed


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path

    @property
    def ledger(self) -> Path:
        return self.root / "ledger" / "crucible.db"

    @property
    def lock(self) -> Path:
        return self.root / "config" / "evaluation.lock.yaml"

    @property
    def holdout_lock(self) -> Path:
        return self.root / "holdout.lock"

    @property
    def holdout_dir(self) -> Path:
        return self.root / "holdout"

    @property
    def in_sample_dir(self) -> Path:
        return self.root / "data" / "is"

    @property
    def perp_in_sample_dir(self) -> Path:
        return self.root / "data" / "perp"

    @property
    def perp_holdout_dir(self) -> Path:
        """Nested inside the spot holdout on purpose: the guard hook matches anything under that
        directory, so the second carve is protected with no change to the rail (P3-22)."""
        return self.holdout_dir / "perp"

    @property
    def perp_holdout_lock(self) -> Path:
        return self.holdout_dir / "perp.lock"

    @property
    def archive(self) -> Path:
        return self.root / "results" / "strategies"


def _concat(a: Bars, b: Bars) -> Bars:
    return Bars(
        b.symbol, b.timeframe, np.concatenate([a.ts, b.ts]),
        *(np.concatenate([getattr(a, f), getattr(b, f)])
          for f in ("open", "high", "low", "close", "volume")),
    )  # fmt: skip


def evaluation_bars(
    paths: Paths, symbols: Sequence[str], timeframe: str, holdout_range: str, warmup: int
) -> dict[str, Bars]:
    """Warm-up (the last ``warmup`` in-sample bars) + the verified holdout slice, per symbol."""
    store = ResearchStore(paths.in_sample_dir, [parse_range(holdout_range)])
    out: dict[str, Bars] = {}
    for s in symbols:
        held = read_bars(paths.holdout_dir / file_name(s, timeframe), s, timeframe)
        before = store.bars(s, timeframe)
        tail = before.slice(max(len(before) - warmup, 0), len(before))
        out[s] = _concat(tail, held)
    return out


def _bundle_names(symbol: str, timeframe: str) -> dict[str, str]:
    stem = f"{symbol.replace('/', '-').replace(':', '-')}_{timeframe}"
    bare = symbol.replace("/", "-").replace(":", "-")
    return {
        "mark": f"{stem}.mark.parquet",
        "funding": f"{bare}.funding.parquet",
        "paths": f"{stem}.paths.parquet",
        "brackets": f"{bare}.brackets.json",
    }


def perp_evaluation_inputs(
    paths: Paths, symbols: Sequence[str], timeframe: str, warmup: int
) -> dict[str, PerpBundle]:
    """The warm-up window joined to the out-of-sample window, for every perpetual symbol.

    Fails closed (INV-94). A perpetual replay needs the mark price it is liquidated on and the
    funding it pays, and neither can be reconstructed from the trade price. Missing either would
    mean evaluating on a market with no funding cost and no liquidation — and the verdict would be
    a PASS built on an assumption nobody chose.
    """
    out: dict[str, PerpBundle] = {}
    if any(":" in symbol for symbol in symbols):
        verify_holdout(paths.perp_holdout_lock, paths.perp_holdout_dir)
    for symbol in symbols:
        if ":" not in symbol:
            continue  # spot: there is no mark and no funding to carry
        names = _bundle_names(symbol, timeframe)
        for directory in (paths.perp_in_sample_dir, paths.perp_holdout_dir):
            missing = [n for n in names.values() if not (directory / n).is_file()]
            if missing:
                raise HoldoutRefused(
                    f"{symbol}: {', '.join(sorted(missing))} missing from {directory.name} — "
                    "mark, funding, intrabar paths and brackets are all required, and refusing "
                    "is the only honest answer"
                )
        inside = read_bundle(paths.perp_in_sample_dir, symbol, timeframe, names)
        held = read_bundle(paths.perp_holdout_dir, symbol, timeframe, names)
        tail = inside.slice(max(len(inside) - warmup, 0), len(inside))
        out[symbol] = tail.followed_by(held)
    return out


def _is_perpetual_portfolio(members: Sequence[Member], lock: Mapping[str, Any]) -> bool:
    data: Mapping[str, Any] = lock.get("research", {}).get("data", {})
    market = str(data.get("market", "")).lower()
    return market in {"perp", "usdt_m_perpetual"} or any(
        ":" in symbol for member in members for symbol in member.universe
    )


def _write_signal_stream(path: Path, stream: Mapping[str, Sequence[Sequence[Any]]]) -> Path:
    rows = [
        (
            symbol,
            bar,
            str(sig[0]),
            float(sig[1]),
            float(sig[2]),
            float("nan") if sig[3] is None else float(sig[3]),
        )
        for symbol, signals in stream.items()
        for bar, sig in enumerate(signals)
    ]
    pd.DataFrame(
        rows, columns=["symbol", "bar", "direction", "strength", "stop_distance", "take_profit"]
    ).to_parquet(path, index=False)
    return path


def _member_signal_stream(
    member: Member,
    source: str,
    bars: Mapping[str, Bars],
    options: Mapping[str, Any],
    runner: Runner,
    out_dir: Path,
) -> Path:
    """Run only the member's signal function in the sandbox; host-side account replay prices it."""
    missing = [s for s in member.universe if s not in bars]
    if missing:
        raise ValueError(f"{member.candidate_id}: no data for {missing}")
    universe = {s: bars[s] for s in member.universe}
    res = runner.run(SandboxJob("signals", source, universe, member.params, options))
    if not res.ok or res.report is None:
        raise RuntimeError(f"{member.candidate_id}: sandbox {res.error}")
    stream: Mapping[str, Sequence[Sequence[Any]]] = res.report["result"]["signals"]
    return _write_signal_stream(out_dir / f"{member.trial_id}.parquet", stream)


def _evaluate_spot_portfolio(
    members: Sequence[Member],
    sources: Mapping[str, str],
    bars: Mapping[str, Bars],
    options: Mapping[str, Any],
    runner: Runner,
    start: pd.Timestamp,
    rebalance: str,
) -> pd.Series:
    series = {
        m.trial_id: rerun_member(m, sources[m.strategy_hash], bars, options, runner)
        for m in members
    }
    frame = pd.concat(series, axis=1, join="inner")
    frame = frame[frame.index >= start]
    return combine(frame[[m.trial_id for m in members]], weights_of(members), rebalance)


def _evaluate_perp_portfolio(
    members: Sequence[Member],
    sources: Mapping[str, str],
    bars: Mapping[str, Bars],
    perp: Mapping[str, PerpBundle],
    options: Mapping[str, Any],
    runner: Runner,
    start: pd.Timestamp,
) -> pd.Series:
    needed = sorted({s for member in members for s in member.universe if ":" in s})
    missing = [s for s in needed if s not in perp]
    if missing:
        raise ValueError(f"no perpetual inputs for {missing}")
    with tempfile.TemporaryDirectory(prefix="qc-holdout-signals-") as tmp:
        root = Path(tmp)
        streams = {
            m.trial_id: _member_signal_stream(
                m, sources[m.strategy_hash], bars, options, runner, root
            )
            for m in members
        }
        replay = consolidate_on_account(
            members,
            streams,
            bars,
            perp,
            settings=RiskSettings(**options["risk"]),
            costs=CostModel(**options["costs"]),
            initial_cash=float(options.get("initial_cash", 100_000.0)),
            leverage=int(options.get("leverage", 5)),
            max_portfolio_risk_pct=float(options.get("max_portfolio_risk_pct", 0.10)),
        )
    out = pd.Series(replay.returns, index=pd.to_datetime(replay.ts[1:]))
    return out[out.index >= start]


def evaluate(
    root: Path, portfolio_hash: str, runner: Runner, now: datetime | None = None
) -> Verdict:
    paths = Paths(root)
    ledger = Ledger.open(paths.ledger)
    try:
        campaign, freeze = find_frozen(ledger, portfolio_hash)
        now = now or utc_now()
        lock = preflight(
            ledger, campaign, freeze, paths.lock, paths.holdout_lock, paths.holdout_dir, now
        )
        variant = next(
            v for v in ledger.portfolio_variants(campaign.campaign_id)
            if v.portfolio_hash == portfolio_hash
        )  # fmt: skip
        members = [Member.from_dict(d) for d in variant.members]
        rebalance = str(variant.rule_config["rebalance"])
        timeframe = members[0].timeframe
        options: Mapping[str, Any] = account_options(lock, backtest_options(lock, seed=0))
        symbols = sorted({s for m in members for s in m.universe})
        archive = StrategyArchive(paths.archive)
        sources = {m.strategy_hash: archive.get(m.strategy_hash) for m in members}
        threshold = float(lock["research"]["holdout_pass"])
        claim(ledger, campaign, freeze, now)  # from here on the holdout is consumed
        try:
            bars = evaluation_bars(
                paths, symbols, timeframe, campaign.holdout_range, int(options["lookback"])
            )
            start = pd.Timestamp(parse_range(campaign.holdout_range)[0])
            perp = perp_evaluation_inputs(paths, symbols, timeframe, int(options["lookback"]))
            if _is_perpetual_portfolio(members, lock):
                oos = _evaluate_perp_portfolio(members, sources, bars, perp, options, runner, start)
            else:
                oos = _evaluate_spot_portfolio(
                    members, sources, bars, options, runner, start, rebalance
                )
            sharpe = annual_sharpe(oos, periods_per_year(timeframe))
            verdict = "PASS" if sharpe >= threshold else "FAIL"
        except BaseException as e:
            burn_after_error(ledger, campaign, freeze, now, e)
            raise
        burn(ledger, campaign, freeze, now, verdict, sharpe)
        return Verdict(verdict, sharpe)
    finally:
        ledger.close()


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantcrucible.holdout.evaluator_proc")
    parser.add_argument("--portfolio", required=True, help="the frozen portfolio_hash")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.root, args.portfolio, runner or LazySandbox(args.root))
    except HoldoutRefused as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2
    except Exception as e:  # no traceback: it could carry holdout values back to the operator
        sys.stderr.write(f"evaluator error: {type(e).__name__}\n")
        return 3
    sys.stdout.write(result.verdict + "\n")  # exactly one token — no gradient flows back
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
