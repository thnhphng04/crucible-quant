"""Command line entry points.

    uv run python -m quantcrucible.cli data-fetch     download research data + carve the holdout
    uv run python -m quantcrucible.cli data-fetch-second   in-sample bars of the second source (⑥′)
    uv run python -m quantcrucible.cli validate FILE  run one strategy through gates ①a → ④
    uv run python -m quantcrucible.cli portfolio [--calibrate]   build + gates ⑤ → ⑥′ (5b)
    uv run python -m quantcrucible.cli freeze HASH    freeze the campaign on that portfolio
    uv run python -m quantcrucible.cli campaign-abandon --reason TEXT   OPEN → ABANDONED

The holdout is opened only by its own process (quantcrucible.holdout.evaluator_proc).

Never prints holdout prices — only row counts and hashes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from quantcrucible.config.loader import load_user_config
from quantcrucible.config.lock import CampaignNotOpened

if TYPE_CHECKING:
    from quantcrucible.core.strategy.base import Bars
    from quantcrucible.validation.gates import GateResult
    from quantcrucible.validation.research_run import ResearchSession


def add_months(d: date, months: int) -> date:
    month_index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def data_fetch(config: Path, root: Path) -> int:
    from quantcrucible.data.ccxt_source import CcxtSource
    from quantcrucible.data.holdout_split import carve

    cfg = load_user_config(config).research.data
    now = datetime.now(UTC)
    today = now.date()
    holdout_start = add_months(today, -cfg.holdout_months)
    holdout_end = today + timedelta(days=1)
    source = CcxtSource(cfg.exchange)
    start = datetime.combine(cfg.start, time(), tzinfo=UTC)
    bars = {s: source.bars(s, cfg.timeframe, start, now) for s in cfg.symbols}
    summary = carve(
        bars,
        holdout_start,
        holdout_end,
        in_sample_dir=root / "data" / "is",
        holdout_dir=root / "holdout",
        lock_path=root / "holdout.lock",
    )
    out = sys.stdout
    out.write(f"holdout range {summary.holdout_range}; {summary.holdout_files} files locked\n")
    out.write(f"holdout.lock sha256 {summary.lock_sha256}\n")
    for symbol, rows in summary.in_sample_rows.items():
        out.write(f"in-sample {symbol}: {rows} bars\n")
    return 0


def second_source_dir(root: Path, exchange: str) -> Path:
    return root / "data" / f"is-{exchange}"


def data_fetch_second(config: Path, root: Path) -> int:
    """In-sample bars from ``research.data.second_exchange`` — never the holdout period."""
    from quantcrucible.data.ccxt_source import CcxtSource
    from quantcrucible.data.holdout_split import read_holdout_lock
    from quantcrucible.data.second_source import download_in_sample
    from quantcrucible.data.store import parse_range

    cfg = load_user_config(config).research.data
    if cfg.second_exchange is None:
        sys.stderr.write("research.data.second_exchange is not set\n")
        return 2
    manifest = read_holdout_lock(root / "holdout.lock")  # the range only: no prices
    written = download_in_sample(
        CcxtSource(cfg.second_exchange), cfg.symbols, cfg.timeframe, cfg.start,
        [parse_range(str(manifest["range"]))], second_source_dir(root, cfg.second_exchange),
    )  # fmt: skip
    for symbol, rows in written.items():
        sys.stdout.write(f"{cfg.second_exchange} in-sample {symbol}: {rows} bars\n")
    return 0


def holdout_reharden(root: Path) -> int:
    from quantcrucible.data.holdout_split import reharden

    summary = reharden(root / "holdout", root / "holdout.lock")
    sys.stdout.write(
        f"holdout range {summary.holdout_range}: {summary.holdout_files} files verified against "
        f"holdout.lock and re-locked; holdout.lock sha256 {summary.lock_sha256}\n"
    )
    return 0


def _session(config: Path, root: Path) -> ResearchSession:
    """The open campaign (verified against config/user.yaml, or a new one) and its IS data."""
    from quantcrucible.config.lock import read_lock
    from quantcrucible.data.store import ResearchStore, parse_range
    from quantcrucible.ledger.db import Ledger
    from quantcrucible.validation.research_run import ResearchSession
    from quantcrucible.validation.run import current_campaign
    from quantcrucible.validation.sandbox import SandboxRunner, ensure_image

    cfg = load_user_config(config)
    (root / "ledger").mkdir(exist_ok=True)
    ledger = Ledger.open(root / "ledger" / "crucible.db")
    lock_path = root / "config" / "evaluation.lock.yaml"
    campaign_id = current_campaign(cfg, ledger, lock_path, root)
    lock = read_lock(lock_path)
    data = cfg.research.data
    holdout = [parse_range(lock["holdout_range"])]
    store = ResearchStore(root / "data" / "is", holdout)
    second: dict[str, Bars] = {}
    if data.second_exchange is not None:
        second_dir = second_source_dir(root, data.second_exchange)
        if second_dir.exists():
            second_store = ResearchStore(second_dir, holdout)
            second = {s: second_store.bars(s, data.timeframe) for s in data.symbols}
    return ResearchSession(
        ledger=ledger, lock=lock, campaign_id=campaign_id,
        is_data={s: store.bars(s, data.timeframe) for s in data.symbols},
        sandbox=SandboxRunner(ensure_image(root)), results_dir=root / "results",
        second_is_data=second,
    )  # fmt: skip


def _write_results(results: Sequence[GateResult]) -> None:
    out = sys.stdout
    for r in results:
        out.write(f"  {r.gate:<12} {'PASS' if r.passed else 'REJECT':<6} {r.reason}\n")
        if r.report is not None:
            for line in r.report.feedback.splitlines():
                out.write(f"  {'':<12} {'':<6} {line}\n")


def validate(strategy: Path, params: str | None, config: Path, root: Path) -> int:
    """One strategy file through every per-candidate gate, ①a → ④."""
    from quantcrucible.validation.run import candidate_pipeline, make_candidate

    session = _session(config, root)
    candidate = make_candidate(
        strategy.read_text(encoding="utf-8"), session.campaign_id, session.is_data,
        json.loads(params) if params else None,
        evolve_scope=str(session.lock["research"].get("evolve_scope", "joint")),
    )  # fmt: skip
    outcome = candidate_pipeline().run(candidate, session.context())
    sys.stdout.write(f"campaign {session.campaign_id} · candidate {candidate.candidate_id}\n")
    _write_results(outcome.results)
    verdict = "PASS" if outcome.passed else f"REJECTED at {outcome.failed_gate}"
    trial = f" · trial #{outcome.trial_id}" if outcome.trial_id is not None else ""
    sys.stdout.write(f"verdict: {verdict}{trial}\n")
    return 0 if outcome.passed else 1


def portfolio(config: Path, root: Path, calibrate: bool) -> int:
    """Build the portfolio by the locked rule, then ⑤ → ⑥′; with ``--calibrate``, step 5b for
    each member first and a rebuild after (a new variant)."""
    from quantcrucible.validation.research_run import calibrate_members, evaluate_portfolio

    session = _session(config, root)
    if not session.second_is_data:
        sys.stderr.write("no second-source data: run data-fetch-second first (gate ⑥′)\n")
        return 2
    built, outcome = evaluate_portfolio(session)
    if calibrate:
        results = calibrate_members(session, built)
        for c in results:
            sys.stdout.write(
                f"calibrated {c.candidate_id}: {c.evaluations} attempts = {c.n_trials} trials"
                f" + {c.n_errors} errors (nothing measured, no trial)\n"
            )
        if results:
            built, outcome = evaluate_portfolio(session)
    sys.stdout.write(f"campaign {session.campaign_id} · portfolio {built.portfolio_hash}\n")
    for m in built.members:
        sys.stdout.write(f"  member {m.candidate_id} (trial #{m.trial_id}) weight {m.weight:.3f}\n")
    _write_results(outcome.results)
    sys.stdout.write(f"verdict: {'PASS' if outcome.passed else 'REJECTED'}\n")
    return 0 if outcome.passed else 1


def freeze(portfolio_hash: str, root: Path) -> int:
    """OPEN → FROZEN on one portfolio that passed ⑤ and ⑥′. The holdout is opened by the
    separate evaluator process, never from here."""
    from quantcrucible.config.lock import read_lock
    from quantcrucible.ledger.db import Ledger
    from quantcrucible.validation.freeze import FreezeError, freeze_campaign

    lock = read_lock(root / "config" / "evaluation.lock.yaml")
    ledger = Ledger.open(root / "ledger" / "crucible.db")
    try:
        done = freeze_campaign(ledger, str(lock["campaign_id"]), portfolio_hash)
    except FreezeError as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2
    sys.stdout.write(
        f"campaign {done.campaign_id} FROZEN on {done.portfolio_hash} at {done.frozen_at}\n"
        f"holdout: uv run python -m quantcrucible.holdout.evaluator_proc "
        f"--portfolio {done.portfolio_hash}\n"
    )
    return 0


def campaign_abandon(reason: str, root: Path) -> int:
    """OPEN → ABANDONED without opening the holdout (ADR-0019). The lock stays untouched until
    the next campaign opens and archives it; the holdout, never claimed, stays unused."""
    from quantcrucible.config.lock import read_lock
    from quantcrucible.ledger.db import Ledger
    from quantcrucible.validation.freeze import FreezeError, abandon

    lock = read_lock(root / "config" / "evaluation.lock.yaml")
    ledger = Ledger.open(root / "ledger" / "crucible.db")
    campaign_id = str(lock["campaign_id"])
    try:
        abandon(ledger, campaign_id, reason)
    except FreezeError as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2
    sys.stdout.write(
        f"campaign {campaign_id} ABANDONED ({reason}); its trials stay in N. Set "
        "research.holdout_pass (D4) before the next command opens a new campaign.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantcrucible")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("data-fetch", help="download research data and carve the holdout")
    fetch.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    fetch.add_argument("--root", type=Path, default=Path("."))
    second = sub.add_parser(
        "data-fetch-second", help="in-sample bars from the second source for gate ⑥′"
    )
    second.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    second.add_argument("--root", type=Path, default=Path("."))
    harden = sub.add_parser("holdout-reharden", help="verify holdout hashes and re-lock the files")
    harden.add_argument("--root", type=Path, default=Path("."))
    val = sub.add_parser("validate", help="run one strategy file through gates ①a → ④")
    val.add_argument("strategy", type=Path)
    val.add_argument("--params", help="JSON object; default: the TUNABLE defaults")
    val.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    val.add_argument("--root", type=Path, default=Path("."))
    port = sub.add_parser("portfolio", help="build the portfolio, then gates ⑤ → ⑥′")
    port.add_argument("--calibrate", action="store_true", help="step 5b, then rebuild")
    port.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    port.add_argument("--root", type=Path, default=Path("."))
    frz = sub.add_parser("freeze", help="freeze the campaign on one validated portfolio")
    frz.add_argument("portfolio_hash")
    frz.add_argument("--root", type=Path, default=Path("."))
    aband = sub.add_parser(
        "campaign-abandon", help="close the OPEN campaign without opening its holdout"
    )
    aband.add_argument("--reason", required=True)
    aband.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except CampaignNotOpened as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "data-fetch":
        return data_fetch(args.config, args.root)
    if args.command == "data-fetch-second":
        return data_fetch_second(args.config, args.root)
    if args.command == "holdout-reharden":
        return holdout_reharden(args.root)
    if args.command == "validate":
        return validate(args.strategy, args.params, args.config, args.root)
    if args.command == "portfolio":
        return portfolio(args.config, args.root, args.calibrate)
    if args.command == "freeze":
        return freeze(args.portfolio_hash, args.root)
    if args.command == "campaign-abandon":
        return campaign_abandon(args.reason, args.root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
