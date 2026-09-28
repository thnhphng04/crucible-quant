"""Fixed Studio job commands. Only the local Studio server constructs these arguments."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from quantcrucible.config.loader import parse_user_config
from quantcrucible.data.registry import DatasetRegistry
from quantcrucible.studio.store import DraftStore


def prepare_data(root: Path, draft_id: str, revision: int) -> int:
    drafts = DraftStore(root)
    draft = drafts.get(draft_id)
    if draft["revision"] != revision or draft["state"] == "created":
        raise ValueError("draft changed before data preparation")
    cfg = parse_user_config(draft["config"])
    descriptor = DatasetRegistry(root).fetch_and_publish(cfg)
    drafts.set_dataset(draft_id, revision, descriptor.dataset_id)
    sys.stdout.write(json.dumps({"dataset_id": descriptor.dataset_id}) + "\n")
    return 0


def evolve(root: Path, campaign_id: str, engines: list[str], workers: int) -> int:
    from quantcrucible.agent.compare import lock_protocol
    from quantcrucible.agent.run import evolve as run_evolve
    from quantcrucible.cli import _session

    label = "studio-" + os.environ.get("QUANTCRUCIBLE_STUDIO_JOB_ID", campaign_id)
    session = _session(root / "config" / "user.yaml", root, label=label, campaign_id=campaign_id)
    purpose, _ = session.ledger.campaign_purpose(campaign_id)
    if purpose == "harness_test":
        lock_protocol(session.ledger, campaign_id)
    stats = run_evolve(session, engines, workers=workers)
    sys.stdout.write(
        json.dumps(
            {
                "campaign_id": campaign_id,
                "proposed": {str(k): v for k, v in stats.proposed.items()},
                "trials": {str(k): v for k, v in stats.trials.items()},
                "passed": {str(k): v for k, v in stats.passed.items()},
            }
        )
        + "\n"
    )
    return 0


def portfolio(root: Path, campaign_id: str) -> int:
    from quantcrucible.cli import _session
    from quantcrucible.validation.research_run import evaluate_portfolio

    label = "studio-" + os.environ.get("QUANTCRUCIBLE_STUDIO_JOB_ID", campaign_id)
    session = _session(root / "config" / "user.yaml", root, label=label, campaign_id=campaign_id)
    purpose, _ = session.ledger.campaign_purpose(campaign_id)
    if purpose != "research":
        raise ValueError("portfolio evaluation is only for research campaigns")
    if not session.second_is_data and not session.perp_data:
        raise ValueError("second-source data is required before portfolio evaluation")
    built, outcome = evaluate_portfolio(session)
    sys.stdout.write(
        json.dumps(
            {
                "campaign_id": campaign_id,
                "portfolio_hash": built.portfolio_hash,
                "verdict": "PASS" if outcome.passed else "REJECTED",
            }
        )
        + "\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantcrucible.studio.worker")
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare-data")
    prep.add_argument("draft_id")
    prep.add_argument("revision", type=int)
    run = sub.add_parser("evolve")
    run.add_argument("campaign_id")
    run.add_argument("--engine", action="append", choices=("gp", "random"), required=True)
    run.add_argument("--workers", type=int, required=True)
    port = sub.add_parser("portfolio")
    port.add_argument("campaign_id")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "prepare-data":
        return prepare_data(root, args.draft_id, args.revision)
    if args.command == "evolve":
        return evolve(root, args.campaign_id, args.engine, args.workers)
    return portfolio(root, args.campaign_id)


if __name__ == "__main__":
    raise SystemExit(main())
