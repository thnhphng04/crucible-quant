"""Command line entry points.

    uv run python -m quantcrucible.cli data-fetch     download research data + carve the holdout
    uv run python -m quantcrucible.cli data-fetch-second   in-sample bars of the second source (⑥′)
    uv run python -m quantcrucible.cli validate FILE  run one strategy through gates ①a → ③

Never prints holdout prices — only row counts and hashes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from quantcrucible.config.loader import load_user_config


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


def validate(strategy: Path, params: str | None, config: Path, root: Path) -> int:
    from quantcrucible.config.lock import read_lock
    from quantcrucible.data.store import ResearchStore, parse_range
    from quantcrucible.ledger.db import Ledger
    from quantcrucible.validation.run import current_campaign, make_candidate, run_candidate
    from quantcrucible.validation.sandbox import SandboxRunner, ensure_image

    cfg = load_user_config(config)
    (root / "ledger").mkdir(exist_ok=True)
    ledger = Ledger.open(root / "ledger" / "crucible.db")
    lock_path = root / "config" / "evaluation.lock.yaml"
    campaign_id = current_campaign(cfg, ledger, lock_path, root)
    lock = read_lock(lock_path)
    data = cfg.research.data
    store = ResearchStore(root / "data" / "is", [parse_range(lock["holdout_range"])])
    is_data = {s: store.bars(s, data.timeframe) for s in data.symbols}
    candidate = make_candidate(
        strategy.read_text(encoding="utf-8"), campaign_id, is_data,
        json.loads(params) if params else None, evolve_scope=cfg.research.evolve_scope,
    )  # fmt: skip
    sandbox = SandboxRunner(ensure_image(root))
    outcome = run_candidate(candidate, ledger, lock, is_data, sandbox, root / "results")
    out = sys.stdout
    out.write(f"campaign {campaign_id} · candidate {candidate.candidate_id}\n")
    for r in outcome.results:
        out.write(f"  {r.gate:<12} {'PASS' if r.passed else 'REJECT':<6} {r.reason}\n")
        if r.report is not None:
            for line in r.report.feedback.splitlines():
                out.write(f"  {'':<12} {'':<6} {line}\n")
    verdict = "PASS" if outcome.passed else f"REJECTED at {outcome.failed_gate}"
    trial = f" · trial #{outcome.trial_id}" if outcome.trial_id is not None else ""
    out.write(f"verdict: {verdict}{trial}\n")
    return 0 if outcome.passed else 1


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
    val = sub.add_parser("validate", help="run one strategy file through gates ①a → ③")
    val.add_argument("strategy", type=Path)
    val.add_argument("--params", help="JSON object; default: the TUNABLE defaults")
    val.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    val.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if args.command == "data-fetch":
        return data_fetch(args.config, args.root)
    if args.command == "data-fetch-second":
        return data_fetch_second(args.config, args.root)
    if args.command == "holdout-reharden":
        return holdout_reharden(args.root)
    if args.command == "validate":
        return validate(args.strategy, args.params, args.config, args.root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
