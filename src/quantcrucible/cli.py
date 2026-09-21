"""Command line entry points.

    uv run python -m quantcrucible.cli data-fetch     download research data + carve the holdout

Never prints holdout prices — only row counts and hashes.
"""

from __future__ import annotations

import argparse
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


def holdout_reharden(root: Path) -> int:
    from quantcrucible.data.holdout_split import reharden

    summary = reharden(root / "holdout", root / "holdout.lock")
    sys.stdout.write(
        f"holdout range {summary.holdout_range}: {summary.holdout_files} files verified against "
        f"holdout.lock and re-locked; holdout.lock sha256 {summary.lock_sha256}\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantcrucible")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("data-fetch", help="download research data and carve the holdout")
    fetch.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    fetch.add_argument("--root", type=Path, default=Path("."))
    harden = sub.add_parser("holdout-reharden", help="verify holdout hashes and re-lock the files")
    harden.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if args.command == "data-fetch":
        return data_fetch(args.config, args.root)
    if args.command == "holdout-reharden":
        return holdout_reharden(args.root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
