"""Serve the review UI over a throwaway demo ledger.

This exists so the Playwright run has a deterministic campaign to look at: it never
touches the real `ledger/`, `holdout/` or `config/` of the repository. The fixture is
written into a temporary directory that is removed when the server stops.

    uv run python scripts/review_demo_server.py --port 8766
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import uvicorn
import yaml

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import (
    Event,
    GateResultRecord,
    GenerationEvent,
    PortfolioVariant,
    TrialRecord,
)
from quantcrucible.review.app import create_app

CAMPAIGN = "c-demo-20260101"
OTHER = "c-demo-20251220"
START = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
STRATEGY_SOURCE = '''"""Demo strategy artifact — never traded, only shown in the review UI."""


def signal(bar):
    """A two-line rule so the code viewer has something with shape to it."""
    return {"direction": 1 if bar.close > bar.sma_50 else 0, "strength": 0.5}
'''


def _returns(path: Path, seed: int, bars: int = 420) -> None:
    """A smooth, deterministic equity path — no randomness, so screenshots are stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    index = pd.date_range("2020-01-01", periods=bars, freq="D", tz="UTC")
    step = [
        0.0016 + 0.0042 * ((i * (seed + 3)) % 17 - 8) / 8 - (0.004 if i % 53 == 0 else 0.0)
        for i in range(bars)
    ]
    pd.DataFrame({"ts": index, "ret": step}).to_parquet(path, index=False)


def build(root: Path) -> None:
    (root / "ledger").mkdir(parents=True)
    (root / "results" / "strategies").mkdir(parents=True)
    (root / "config").mkdir(parents=True)

    lock_body = {
        "campaign_id": CAMPAIGN,
        "protocol": 3,
        "gates": {"dsr_min": 0.95, "pbo_max": 0.5, "minbtl_target_sharpe": 1.5},
        "universe": ["BTC/USDT", "ETH/USDT"],
        "timeframe": "1d",
    }
    lock_path = root / "config" / "evaluation.lock.yaml"
    lock_path.write_text(yaml.safe_dump(lock_body, sort_keys=True), encoding="utf-8")
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest()

    with Ledger.open(root / "ledger" / "crucible.db") as ledger:
        ledger.open_campaign(
            CAMPAIGN,
            "2025-01-01/2026-01-01",
            lock_hash,
            started_at=START,
            purpose="harness_test",
            trial_budget=24,
        )
        ledger.open_campaign(
            OTHER,
            "2025-01-01/2026-01-01",
            "0" * 64,
            started_at=START - timedelta(days=12),
            purpose="research",
        )
        ledger.log_event(
            GenerationEvent(
                run_id="demo",
                campaign_id=CAMPAIGN,
                engine="compare",
                seed=0,
                agent="human",
                model_used="none",
                event=Event.PROTOCOL_LOCKED,
                detail={"protocol": {"version": 3, "budget": 24}, "sha256": lock_hash},
                ts=START,
            )
        )

        for index, (engine, seed) in enumerate(
            [("gp", 0), ("gp", 1), ("random", 0), ("random", 1)]
        ):
            for step in range(3):
                nth = index * 3 + step
                candidate = f"{engine}-s{seed}-{step:03d}"
                strategy_hash = hashlib.sha256(candidate.encode()).hexdigest()
                (root / "results" / "strategies" / f"{strategy_hash}.py").write_text(
                    STRATEGY_SOURCE, encoding="utf-8"
                )
                returns = Path("results") / "returns" / f"{candidate}.parquet"
                _returns(root / returns, seed=nth)
                at = START + timedelta(minutes=10 * nth)
                sharpe = 1.45 - 0.09 * nth + (0.25 if engine == "gp" else 0.0)
                passed_four = nth % 3 == 0
                ledger.log_event(
                    GenerationEvent(
                        run_id="demo",
                        campaign_id=CAMPAIGN,
                        engine=engine,
                        seed=seed,
                        agent="engine",
                        model_used="none",
                        event=Event.CANDIDATE_SUBMITTED,
                        strategy_hash=strategy_hash,
                        cell_id=f"mom{step}-vol{index % 2}",
                        detail={"candidate_id": candidate, "parents": []},
                        ts=at,
                    )
                )
                trial_id = ledger.record_trial(
                    TrialRecord(
                        run_id="demo",
                        campaign_id=CAMPAIGN,
                        candidate_id=candidate,
                        engine=engine,
                        seed=seed,
                        strategy_hash=strategy_hash,
                        params={"lookback": 20 + step * 10, "vol_target": 0.15},
                        universe="BTC/USDT",
                        timeframe="1d",
                        timerange="2020-01-01/2024-12-31",
                        cell_id=f"mom{step}-vol{index % 2}",
                        source="evolution",
                        sharpe_is=sharpe,
                        returns_path=str(returns),
                        gate_failed=None if passed_four else "g4_pbo",
                        verdict="PASS" if passed_four else "REJECT",
                        ts=at,
                    )
                )
                for gate, ok, reason in [
                    ("g1a_static", True, "static checks passed"),
                    ("g1b_sandbox", True, "sandbox run clean"),
                    ("g2_screen", True, "coarse screen above floor"),
                    ("g3_cpcv", True, "CPCV folds complete"),
                    ("g4_pbo", passed_four, "PBO under cap" if passed_four else "PBO over cap"),
                ]:
                    ledger.record_gate_result(
                        GateResultRecord(
                            campaign_id=CAMPAIGN,
                            candidate_id=candidate,
                            gate=gate,
                            passed=ok,
                            reason=reason,
                            value=0.31 if gate == "g4_pbo" else 0.0,
                            strategy_hash=strategy_hash,
                            trial_id=trial_id,
                            ts=at,
                        )
                    )
                if step == 2:
                    ledger.log_event(
                        GenerationEvent(
                            run_id="demo",
                            campaign_id=CAMPAIGN,
                            engine=engine,
                            seed=seed,
                            agent="engine",
                            model_used="none",
                            event=Event.DEGRADATION_CHECKPOINT,
                            detail={
                                "candidate_id": candidate,
                                "is_sharpe": sharpe,
                                "oos_median": sharpe - 0.35,
                                "trials": (index + 1) * 3,
                            },
                            ts=at,
                        )
                    )
        ledger.log_event(
            GenerationEvent(
                run_id="demo",
                campaign_id=CAMPAIGN,
                engine="random",
                seed=1,
                agent="engine",
                model_used="none",
                event=Event.DEGRADATION_WARNING,
                detail={"reason": "IS→OOS gap above the monitor threshold"},
                ts=START + timedelta(hours=3),
            )
        )
        ledger.record_portfolio_variant(
            PortfolioVariant(
                portfolio_hash="d" * 64,
                campaign_id=CAMPAIGN,
                rule_config={"rule": "top_k", "k": 3, "snapshot": {"trials": 12}},
                members=[{"candidate_id": "gp-s0-000"}, {"candidate_id": "random-s0-000"}],
                sharpe_is=1.31,
                ts=START + timedelta(hours=4),
            )
        )
        ledger.start_calibration(CAMPAIGN, "gp-s0-000", "a" * 64, 40, at=START)
        ledger.finish_calibration(
            CAMPAIGN, "gp-s0-000", "finished", 31, 31, 0, at=START + timedelta(hours=1)
        )
        ledger.start_calibration(CAMPAIGN, "gp-s1-000", "b" * 64, 40, at=START)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--static",
        type=Path,
        default=Path("ui") / "dist",
        help="the built front end to serve",
    )
    args = parser.parse_args()
    static = args.static.resolve()
    if not static.is_dir():
        raise SystemExit(
            f"chưa build front-end: không có {static} (chạy `npm run build` trong ui/)"
        )

    demo = Path(tempfile.mkdtemp(prefix="qc-review-demo-"))
    try:
        build(demo)
        uvicorn.run(
            create_app(demo, static_dir=static),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
    finally:
        shutil.rmtree(demo, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
