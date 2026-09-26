"""Command line entry points.

    uv run python -m quantcrucible.cli data-fetch     download research data + carve the holdout
    uv run python -m quantcrucible.cli data-fetch-second   in-sample bars of the second source (⑥′)
    uv run python -m quantcrucible.cli validate FILE  run one strategy through gates ①a → ④
    uv run python -m quantcrucible.cli portfolio [--calibrate]   build + gates ⑤ → ⑥′ (5b)
    uv run python -m quantcrucible.cli freeze HASH    freeze the campaign on that portfolio
    uv run python -m quantcrucible.cli campaign-abandon --reason TEXT   OPEN → ABANDONED
    uv run python -m quantcrucible.cli evolve --engine random [--workers 8]   engine C (P2-09)
    uv run python -m quantcrucible.cli compare --lock | compare   phase-2 engine comparison (P2-15)

The holdout is opened only by its own process (quantcrucible.holdout.evaluator_proc).

Never prints holdout prices — only row counts and hashes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
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


def data_fetch(config: Path, root: Path, market: str | None = None) -> int:
    from quantcrucible.data.ccxt_source import CcxtSource
    from quantcrucible.data.holdout_split import carve

    cfg = load_user_config(config).research.data
    requested = market or ("perp" if cfg.market == "usdt_m_perpetual" else "spot")
    if requested != ("perp" if cfg.market == "usdt_m_perpetual" else "spot"):
        sys.stderr.write("--market must match research.data.market in the config\n")
        return 2
    if requested == "perp":
        return data_fetch_perp(config, root)
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


def data_fetch_perp(config: Path, root: Path) -> int:
    """Prepare all four perpetual inputs before the write-once carve is attempted."""
    from quantcrucible.data.manifest import write_manifest
    from quantcrucible.data.perp_carve import carve_perp
    from quantcrucible.data.perp_pipeline import prepare_perpetual
    from quantcrucible.data.perp_source import PerpSource, common_window

    cfg = load_user_config(config).research.data
    if cfg.market != "usdt_m_perpetual":
        raise ValueError("research.data.market must be usdt_m_perpetual")
    today = datetime.now(UTC).date()
    start = datetime.combine(cfg.start, time(), tzinfo=UTC)
    end = datetime.combine(today, time(), tzinfo=UTC)  # only completed bars
    prepared = prepare_perpetual(
        PerpSource(),
        root / "data" / "perp-minutes",
        cfg.symbols,
        cfg.timeframe,
        start,
        end,
        funding_interval_hours=cfg.funding_interval_hours,
    )
    common_start, common_end = common_window(
        {s: (c.start, c.end) for s, c in prepared.coverage.items()}
    )
    if common_start != start or common_end != end:
        raise ValueError("perpetual series do not cover the configured common window")
    directory = root / "data" / "perp"
    summary = carve_perp(
        prepared.data,
        add_months(today, -cfg.holdout_months),
        today + timedelta(days=1),
        in_sample_dir=directory,
        holdout_dir=root / "holdout" / "perp",
        lock_path=root / "holdout" / "perp.lock",
    )
    coverage = {
        symbol: f"{item.start.date().isoformat()}/{item.end.date().isoformat()}"
        for symbol, item in prepared.coverage.items()
    }
    files = list(directory.glob("*.parquet")) + [
        path for path in directory.glob("*.json") if path.name != "manifest.json"
    ]
    manifest = write_manifest(directory / "manifest.json", files, "binanceusdm", coverage)
    sys.stdout.write(
        f"perp common window {common_start.date()}/{common_end.date()}; "
        f"{len(manifest.files)} IS files checksummed; holdout {summary.holdout_range} locked\n"
    )
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
    if cfg.market == "usdt_m_perpetual":
        return data_fetch_second_perp(config, root)
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


def data_fetch_second_perp(config: Path, root: Path) -> int:
    """Download an independent venue's perpetual IS bundle, never its holdout window."""
    from quantcrucible.core.perp_inputs import write_bundle
    from quantcrucible.data.holdout_split import read_holdout_lock
    from quantcrucible.data.manifest import write_manifest
    from quantcrucible.data.perp_pipeline import prepare_perpetual
    from quantcrucible.data.perp_source import PerpSource
    from quantcrucible.data.source import timeframe_delta
    from quantcrucible.data.store import file_name, write_bars

    cfg = load_user_config(config).research.data
    if cfg.second_exchange is None:
        sys.stderr.write("research.data.second_exchange is not set\n")
        return 2
    manifest = read_holdout_lock(root / "holdout" / "perp.lock")
    cut = date.fromisoformat(str(manifest["range"]).split("/")[0])
    start = datetime.combine(cfg.start, time(), tzinfo=UTC)
    end = datetime.combine(cut, time(), tzinfo=UTC) - timeframe_delta(cfg.timeframe)
    prepared = prepare_perpetual(
        PerpSource(exchange_id=cfg.second_exchange),
        root / "data" / f"perp-second-{cfg.second_exchange}-minutes",
        cfg.symbols,
        cfg.timeframe,
        start,
        end,
        funding_interval_hours=cfg.funding_interval_hours,
    )
    directory = root / "data" / f"perp-second-{cfg.second_exchange}"
    for symbol, (trade, bundle) in prepared.data.items():
        write_bars(directory / file_name(symbol, cfg.timeframe), trade)
        write_bundle(directory, bundle)
    files = list(directory.glob("*.parquet")) + [
        path for path in directory.glob("*.json") if path.name != "manifest.json"
    ]
    coverage = {
        symbol: f"{item.start.date()}/{item.end.date()}"
        for symbol, item in prepared.coverage.items()
    }
    saved = write_manifest(directory / "manifest.json", files, cfg.second_exchange, coverage)
    sys.stdout.write(
        f"{cfg.second_exchange} perpetual IS: {len(saved.files)} files checksummed; "
        f"window {start.date()}/{end.date()}\n"
    )
    return 0


def holdout_reharden(root: Path) -> int:
    from quantcrucible.data.holdout_split import reharden

    summary = reharden(root / "holdout", root / "holdout.lock")
    sys.stdout.write(
        f"holdout range {summary.holdout_range}: {summary.holdout_files} files verified against "
        f"holdout.lock and re-locked; holdout.lock sha256 {summary.lock_sha256}\n"
    )
    return 0


def _session(config: Path, root: Path, label: str = "default") -> ResearchSession:
    """The open campaign (verified against config/user.yaml, or a new one) and its IS data."""
    from quantcrucible.config.lock import LockTamperedError, read_lock, sha256_file
    from quantcrucible.core.perp_inputs import read_bundle
    from quantcrucible.data.manifest import verify_manifest
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
    is_dir = root / "data" / ("perp" if data.market == "usdt_m_perpetual" else "is")
    store = ResearchStore(is_dir, holdout)
    bars = {s: store.bars(s, data.timeframe) for s in data.symbols}
    perps = {}
    if data.market == "usdt_m_perpetual":
        manifest_path = is_dir / "manifest.json"
        expected = lock.get("derived", {}).get("perp_manifest_sha256")
        if expected != sha256_file(manifest_path):
            raise LockTamperedError("perpetual IS manifest differs from the campaign lock")
        manifest = verify_manifest(manifest_path, is_dir)
        if manifest.source != "binanceusdm" or set(manifest.coverage) != set(data.symbols):
            raise LockTamperedError("perpetual IS manifest has the wrong source or symbols")
        for symbol, trade in bars.items():
            stem = symbol.replace("/", "-").replace(":", "-")
            names = {
                "mark": f"{stem}_{data.timeframe}.mark.parquet",
                "funding": f"{stem}.funding.parquet",
                "paths": f"{stem}_{data.timeframe}.paths.parquet",
                "brackets": f"{stem}.brackets.json",
            }
            bundle = read_bundle(is_dir, symbol, data.timeframe, names)
            bundle.aligned_with(trade)
            perps[symbol] = bundle
    second: dict[str, Bars] = {}
    second_perps = {}
    if data.second_exchange is not None:
        second_dir = (
            root / "data" / f"perp-second-{data.second_exchange}"
            if data.market == "usdt_m_perpetual"
            else second_source_dir(root, data.second_exchange)
        )
        if data.market == "usdt_m_perpetual" and not second_dir.is_dir():
            raise LockTamperedError("second perpetual IS bundle is missing")
        if second_dir.exists():
            second_store = ResearchStore(second_dir, holdout)
            second = {s: second_store.bars(s, data.timeframe) for s in data.symbols}
            if data.market == "usdt_m_perpetual":
                expected_second = lock.get("derived", {}).get("second_perp_manifest_sha256")
                if expected_second != sha256_file(second_dir / "manifest.json"):
                    raise LockTamperedError(
                        "second perpetual IS manifest differs from campaign lock"
                    )
                second_manifest = verify_manifest(second_dir / "manifest.json", second_dir)
                if second_manifest.source != data.second_exchange or set(
                    second_manifest.coverage
                ) != set(data.symbols):
                    raise LockTamperedError("second perpetual manifest has wrong source or symbols")
                for symbol, trade in second.items():
                    stem = symbol.replace("/", "-").replace(":", "-")
                    names = {
                        "mark": f"{stem}_{data.timeframe}.mark.parquet",
                        "funding": f"{stem}.funding.parquet",
                        "paths": f"{stem}_{data.timeframe}.paths.parquet",
                        "brackets": f"{stem}.brackets.json",
                    }
                    bundle = read_bundle(second_dir, symbol, data.timeframe, names)
                    bundle.aligned_with(trade)
                    second_perps[symbol] = bundle
    return ResearchSession(
        ledger=ledger, lock=lock, campaign_id=campaign_id,
        is_data=bars,
        sandbox=SandboxRunner(ensure_image(root), label=label), results_dir=root / "results",
        second_is_data=second, perp_data=perps, second_perp_data=second_perps,
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
    from quantcrucible.validation.calibration import CalibrationError
    from quantcrucible.validation.research_run import calibrate_members, evaluate_portfolio

    session = _session(config, root)
    perpetual = session.lock["research"].get("data", {}).get("market") == "usdt_m_perpetual"
    if not session.second_is_data and not perpetual:
        sys.stderr.write("no second-source data: run data-fetch-second first (gate ⑥′)\n")
        return 2
    built, outcome = evaluate_portfolio(session)
    if calibrate:
        try:
            results = calibrate_members(session, built)
        except CalibrationError as e:
            sys.stderr.write(f"calibration refused: {e}\n")
            return 2
        for c in results:
            if c.confirmation is None:
                verdict = "no attempt passed ③, nothing to confirm — parameters unchanged"
            elif c.confirmation.passed:
                verdict = f"confirmation PASSED (trial #{c.confirmation.trial_id})"
            else:
                verdict = "confirmation REJECTED — the candidate leaves the portfolio"
            sys.stdout.write(
                f"calibration of {c.candidate_id}: search finished, budget used —"
                f" {c.evaluations} attempts = {c.n_trials} trials + {c.n_errors} errors"
                f" (nothing measured, no trial); {verdict}\n"
            )
        if results:
            built, outcome = evaluate_portfolio(session)
    sys.stdout.write(f"campaign {session.campaign_id} · portfolio {built.portfolio_hash}\n")
    for m in built.members:
        allocation = "risk 1%" if perpetual else f"weight {m.weight:.3f}"
        sys.stdout.write(f"  member {m.candidate_id} (trial #{m.trial_id}) {allocation}\n")
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


def campaign_dryrun(config: Path, root: Path) -> int:
    """Preview a campaign lock and admission checks without modifying the ledger or lock."""
    from quantcrucible.config.lock import dry_run_lock, sha256_file
    from quantcrucible.data.holdout_split import read_holdout_lock
    from quantcrucible.data.manifest import verify_manifest
    from quantcrucible.ledger.db import Ledger
    from quantcrucible.validation.run import _check_trial_budget, derived_settings

    cfg = load_user_config(config)
    perpetual = cfg.research.data.market == "usdt_m_perpetual"
    holdout_lock = root / "holdout" / "perp.lock" if perpetual else root / "holdout.lock"
    manifest = read_holdout_lock(holdout_lock)  # manifest only; never holdout prices
    derived = derived_settings(cfg.research.evolve_scope)
    if perpetual:
        is_dir = root / "data" / "perp"
        data_manifest_path = is_dir / "manifest.json"
        data_manifest = verify_manifest(data_manifest_path, is_dir)
        if data_manifest.source != "binanceusdm" or set(data_manifest.coverage) != set(
            cfg.research.data.symbols
        ):
            raise CampaignNotOpened("perpetual IS manifest has the wrong source or symbols")
        derived["perp_manifest_sha256"] = sha256_file(data_manifest_path)
        if cfg.research.data.second_exchange is not None:
            second_dir = root / "data" / f"perp-second-{cfg.research.data.second_exchange}"
            second_path = second_dir / "manifest.json"
            second_manifest = verify_manifest(second_path, second_dir)
            if second_manifest.source != cfg.research.data.second_exchange or set(
                second_manifest.coverage
            ) != set(cfg.research.data.symbols):
                raise CampaignNotOpened("second perpetual IS manifest has wrong source or symbols")
            derived["second_perp_manifest_sha256"] = sha256_file(second_path)

    ledger_path = root / "ledger" / "crucible.db"
    if ledger_path.exists():
        connection = sqlite3.connect(ledger_path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        ledger = Ledger(connection, str(ledger_path))
    else:
        ledger = Ledger.open(":memory:")
    try:
        base = "c-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        campaign_id, n = base, 1
        while ledger.campaign(campaign_id) is not None:
            n += 1
            campaign_id = f"{base}-{n}"
        preview = dry_run_lock(
            cfg,
            ledger,
            campaign_id,
            root / "config" / "evaluation.lock.yaml",
            str(manifest["range"]),
            sha256_file(holdout_lock),
            derived,
        )
        problems = list(preview.problems)
        if cfg.research.holdout_pass is None:
            problems.append("research.holdout_pass must be set before a campaign opens")
        try:
            _check_trial_budget(cfg, ledger, str(manifest["range"]))
        except CampaignNotOpened as exc:
            problems.append(str(exc))
    finally:
        ledger.close()
    sys.stdout.write(preview.text)
    for problem in problems:
        sys.stderr.write(f"refused: {problem}\n")
    return 2 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantcrucible")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("data-fetch", help="download research data and carve the holdout")
    fetch.add_argument("--market", choices=["spot", "perp"], help="must match research.data.market")
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
    dry = sub.add_parser("campaign-dryrun", help="preview the lock without opening a campaign")
    dry.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    dry.add_argument("--root", type=Path, default=Path("."))
    evo = sub.add_parser("evolve", help="run engine C on the campaign until its quotas are used")
    evo.add_argument("--engine", action="append", choices=["random", "gp"], required=True)
    evo.add_argument("--workers", type=int, default=8)
    evo.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    evo.add_argument("--root", type=Path, default=Path("."))
    cmp_ = sub.add_parser("compare", help="lock the comparison protocol, or report the comparison")
    cmp_.add_argument("--lock", action="store_true", help="record the protocol (before any trial)")
    cmp_.add_argument("--config", type=Path, default=Path("config/user.yaml"))
    cmp_.add_argument("--root", type=Path, default=Path("."))
    review = sub.add_parser("review", help="open the local read-only research review UI")
    review.add_argument("--root", type=Path, default=Path("."))
    review.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except CampaignNotOpened as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "data-fetch":
        return data_fetch(args.config, args.root, args.market)
    if args.command == "campaign-dryrun":
        return campaign_dryrun(args.config, args.root)
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
    if args.command == "evolve":
        return evolve(args.engine, args.workers, args.config, args.root)
    if args.command == "compare":
        return compare(args.lock, args.config, args.root)
    if args.command == "review":
        import uvicorn

        from quantcrucible.review.app import create_app

        uvicorn.run(create_app(args.root.resolve()), host="127.0.0.1", port=args.port)
        return 0
    return 2


def compare(lock: bool, config: Path, root: Path) -> int:
    from quantcrucible.agent.compare import ProtocolError, lock_protocol
    from quantcrucible.agent.compare import compare as run_compare

    session = _session(config, root)
    try:
        if lock:
            digest = lock_protocol(session.ledger, session.campaign_id)
            sys.stdout.write(f"protocol locked for {session.campaign_id}: sha256 {digest}\n")
            return 0
        seeds = int(session.lock["research"]["seeds"])
        report = run_compare(session, seeds)
    except ProtocolError as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2
    sys.stdout.write(json.dumps(report, indent=1, default=str) + "\n")
    return 0


def evolve(engines: Sequence[str], workers: int, config: Path, root: Path) -> int:
    """Engine C on the open campaign; resumes from the ledger if called again."""
    from quantcrucible.agent.run import EvolveError
    from quantcrucible.agent.run import evolve as run_evolve

    label = "run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    session = _session(config, root, label)
    try:
        stats = run_evolve(session, engines, workers=workers, run_label=label)
    except EvolveError as e:
        sys.stderr.write(f"refused: {e}\n")
        return 2
    summary = {
        str(k): {
            "proposed": stats.proposed.get(k, 0),
            "trials": stats.trials.get(k, 0),
            "passed": stats.passed.get(k, 0),
            "starved": k in stats.starved,
        }
        for k in sorted(set(stats.proposed) | set(stats.trials))
    }
    report = {"campaign": session.campaign_id, "run": label, **summary}
    sys.stdout.write(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
