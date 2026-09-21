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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.store import ResearchStore, file_name, parse_range, read_bars
from quantcrucible.holdout.campaign import HoldoutRefused, burn, find_frozen, preflight
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import utc_now
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.is_gates import backtest_options
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.portfolio import Member, combine
from quantcrucible.validation.robustness import annual_sharpe, rerun_member, weights_of
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
        options: Mapping[str, Any] = backtest_options(lock, seed=0)
        symbols = sorted({s for m in members for s in m.universe})
        bars = evaluation_bars(
            paths, symbols, timeframe, campaign.holdout_range, int(options["lookback"])
        )
        start = pd.Timestamp(parse_range(campaign.holdout_range)[0])
        archive = StrategyArchive(paths.archive)
        series = {
            m.trial_id: rerun_member(m, archive.get(m.strategy_hash), bars, options, runner)
            for m in members
        }
        frame = pd.concat(series, axis=1, join="inner")
        frame = frame[frame.index >= start]
        oos = combine(frame[[m.trial_id for m in members]], weights_of(members), rebalance)
        sharpe = annual_sharpe(oos, periods_per_year(timeframe))
        threshold = float(lock["research"]["holdout_pass"])
        verdict = "PASS" if sharpe >= threshold else "FAIL"
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
