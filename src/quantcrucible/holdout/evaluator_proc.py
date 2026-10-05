"""The holdout evaluator — its own process, its own entry point (Architecture §4.2 layer 3, P6,
ADR-0016)::

    uv run python -m quantcrucible.holdout.evaluator_proc --portfolio <portfolio_hash>

Takes only a frozen ``portfolio_hash``. After every check in :func:`campaign.preflight` — and the
backtest engine's own, at the lock's precision (:func:`engine_runner`, P3-47) — it re-runs each
member on the engine (a genome on the kernels, anything else in the sandbox) on a copy of the
verified holdout slice (warmed up with the in-sample bars just before it), combines them with the
frozen weights and rebalance rule, compares the
annualized OOS Sharpe with ``research.holdout_pass`` (D4), records the one opening, burns the
campaign and prints exactly ``PASS`` or ``FAIL`` — never a number. ``sharpe_oos`` is stored in
``holdout_access`` for the human report only.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from quantcrucible.config.lock import read_lock
from quantcrucible.config.schema import AUDIT_RATE_FLOOR, Compute, ComputeEngine
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
from quantcrucible.validation.clock import annual_sharpe, evaluation_clock, on_clock
from quantcrucible.validation.is_gates import backtest_options
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.portfolio import (
    ACCOUNT_WEIGHTING,
    Member,
    combine,
    consolidate_on_account,
    consolidate_on_spot_account,
)
from quantcrucible.validation.robustness import (
    account_options,
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
    dataset_id: str | None = None

    def __post_init__(self) -> None:
        if self.dataset_id is not None and re.fullmatch(r"[0-9a-f]{64}", self.dataset_id) is None:
            raise HoldoutRefused("invalid dataset id in the campaign lock")

    @property
    def ledger(self) -> Path:
        return self.root / "ledger" / "crucible.db"

    @property
    def lock(self) -> Path:
        return self.root / "config" / "evaluation.lock.yaml"

    @property
    def holdout_lock(self) -> Path:
        if self.dataset_id is not None:
            return self.root / "holdout" / "datasets" / self.dataset_id / "holdout.lock"
        return self.root / "holdout.lock"

    @property
    def holdout_dir(self) -> Path:
        if self.dataset_id is not None:
            return self.root / "holdout" / "datasets" / self.dataset_id / "files"
        return self.root / "holdout"

    @property
    def in_sample_dir(self) -> Path:
        if self.dataset_id is not None:
            return self.root / "data" / "datasets" / self.dataset_id / "is"
        return self.root / "data" / "is"

    @property
    def perp_in_sample_dir(self) -> Path:
        if self.dataset_id is not None:
            return self.in_sample_dir
        return self.root / "data" / "perp"

    @property
    def perp_holdout_dir(self) -> Path:
        """Nested inside the spot holdout on purpose: the guard hook matches anything under that
        directory, so the second carve is protected with no change to the rail (P3-22)."""
        return self.holdout_dir if self.dataset_id is not None else self.holdout_dir / "perp"

    @property
    def perp_holdout_lock(self) -> Path:
        if self.dataset_id is not None:
            return self.holdout_lock
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
        "trade_paths": f"{stem}.trade-paths.parquet",
        "brackets": f"{bare}.brackets.json",
    }


def perp_evaluation_inputs(
    paths: Paths,
    symbols: Sequence[str],
    timeframe: str,
    warmup: int,
    require_trade_paths: bool = False,
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
        if not require_trade_paths:
            names.pop("trade_paths")
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
        from quantcrucible.execution.exit_policy import ExitPolicy

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
            exit_policy=ExitPolicy(**options.get("exit_policy", {})),
        )
    out = pd.Series(replay.returns, index=pd.to_datetime(replay.ts[1:]))
    return out[out.index >= start]


def _evaluate_spot_account(
    members: Sequence[Member],
    sources: Mapping[str, str],
    bars: Mapping[str, Bars],
    options: Mapping[str, Any],
    runner: Runner,
    start: pd.Timestamp,
) -> pd.Series:
    """A spot portfolio frozen as one cash account (ADR-0040): signals from the sandbox, the
    account replayed on the host — the same path its in-sample returns took."""
    with tempfile.TemporaryDirectory(prefix="qc-holdout-signals-") as tmp:
        from quantcrucible.execution.exit_policy import ExitPolicy

        root = Path(tmp)
        streams = {
            m.trial_id: _member_signal_stream(
                m, sources[m.strategy_hash], bars, options, runner, root
            )
            for m in members
        }
        replay = consolidate_on_spot_account(
            members,
            streams,
            bars,
            settings=RiskSettings(**options["risk"]),
            costs=CostModel(**options["costs"]),
            initial_cash=float(options.get("initial_cash", 100_000.0)),
            exit_policy=ExitPolicy(**options.get("exit_policy", {})),
            max_portfolio_risk_pct=float(options.get("max_portfolio_risk_pct", 0.10)),
        )
    out = pd.Series(replay.returns, index=pd.to_datetime(replay.ts[1:]))
    return out[out.index >= start]


def _on_one_account(rule_config: Mapping[str, Any]) -> bool:
    return str(rule_config.get("weighting", "")) == ACCOUNT_WEIGHTING


def engine_runner(
    ledger_path: Path,
    lock: Mapping[str, Any],
    members: Sequence[Member],
    sources: Mapping[str, str],
    options: Mapping[str, Any],
    compute: Compute,
    sandbox: Runner,
    on_account: bool = False,
) -> Runner:
    """The engine router at the lock's precision, checked before the claim (P3-47, INV-110):
    every member must have an engine that will answer it — in float32 the kernels, with a CPU
    build that reproduces its pinned result — and CUDA passes its self-test or is not used. A
    refusal here leaves the holdout unclaimed. Every CUDA job is cross-checked on the CPU build,
    and a device fault mid-run continues on the CPU build at the same precision."""
    from quantcrucible.validation.engine_router import EngineRefused, EngineRouter, MemberJob

    kind = "signals" if on_account or _is_perpetual_portfolio(members, lock) else "backtest"
    jobs = [MemberJob(kind, sources[m.strategy_hash], m.params, tuple(m.universe)) for m in members]
    router = EngineRouter(sandbox, lock, compute, ledger_path, "holdout", l1_rate=1.0)
    try:
        router.preflight(options, jobs)
    except EngineRefused as exc:
        raise HoldoutRefused(f"backtest engine: {exc}") from None
    return router


def evaluate(
    root: Path,
    portfolio_hash: str,
    runner: Runner | None = None,
    now: datetime | None = None,
    compute: Compute | None = None,
    sandbox: Runner | None = None,
) -> Verdict:
    """``runner`` answers every member job as given; without one, the engine router does
    (``compute``, default ``auto``), backed by ``sandbox`` (default: the Docker sandbox)."""
    paths = Paths(root)
    ledger = Ledger.open(paths.ledger)
    try:
        campaign, freeze = find_frozen(ledger, portfolio_hash)
        candidate_lock = read_lock(paths.lock)
        derived = candidate_lock.get("derived", {})
        dataset_id = derived.get("dataset_id") if isinstance(derived, dict) else None
        paths = Paths(root, str(dataset_id) if dataset_id is not None else None)
        now = now or utc_now()
        lock = preflight(
            ledger, campaign, freeze, paths.lock, paths.holdout_lock, paths.holdout_dir, now
        )
        if paths.dataset_id is not None:
            from quantcrucible.data.registry import DatasetRegistry, DatasetRegistryError

            try:
                DatasetRegistry(root).verify_dataset(paths.dataset_id)
            except DatasetRegistryError as exc:
                raise HoldoutRefused("dataset manifest failed verification") from exc
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
        clock = evaluation_clock(lock)  # the verified lock's; read before the holdout is claimed
        if runner is None:
            runner = engine_runner(
                paths.ledger, lock, members, sources, options,
                compute or Compute("auto", AUDIT_RATE_FLOOR), sandbox or LazySandbox(root),
                _on_one_account(variant.rule_config),
            )  # fmt: skip
        claim(ledger, campaign, freeze, now)  # from here on the holdout is consumed
        try:
            bars = evaluation_bars(
                paths, symbols, timeframe, campaign.holdout_range, int(options["lookback"])
            )
            start = pd.Timestamp(parse_range(campaign.holdout_range)[0])
            perp = perp_evaluation_inputs(
                paths,
                symbols,
                timeframe,
                int(options["lookback"]),
                require_trade_paths=(
                    options.get("exit_policy", {}).get("mode") == "bracket_timeout_v1"
                ),
            )
            if _is_perpetual_portfolio(members, lock):
                oos = _evaluate_perp_portfolio(members, sources, bars, perp, options, runner, start)
            elif _on_one_account(variant.rule_config):
                oos = _evaluate_spot_account(members, sources, bars, options, runner, start)
            else:
                oos = _evaluate_spot_portfolio(
                    members, sources, bars, options, runner, start, rebalance
                )
            sharpe = annual_sharpe(*on_clock(oos, periods_per_year(timeframe), clock))
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
    parser.add_argument(
        "--engine", choices=("auto", "gpu", "cpu_kernel", "sandbox"), default="auto",
        help="backtest engine (ADR-0038); the precision always comes from the campaign lock",
    )  # fmt: skip
    args = parser.parse_args(argv)
    try:
        compute = Compute(cast("ComputeEngine", args.engine), AUDIT_RATE_FLOOR)
        result = evaluate(args.root, args.portfolio, runner, compute=compute)
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
