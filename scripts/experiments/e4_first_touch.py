"""E4 (P3-50, ADR-0038): first touch on a segment's running extremes — linear scan or binary
search? Both return the same index (tests/execution/kernels/test_perp_replay.py runs the kernel
against the Python replay); this times a 200-configuration grid on synthetic 4h bundles.

    uv run python scripts/experiments/e4_first_touch.py --bars 3000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.perp_fixtures import perp_market

from quantcrucible.core.perp_arrays import pack_bundle
from quantcrucible.execution.kernels import perp_replay


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bars", type=int, default=3000)
    parser.add_argument("--configs", type=int, default=200)
    args = parser.parse_args()
    bars, bundle = perp_market(args.bars, seed=1)
    packed = pack_bundle(bundle, bars)
    rng = np.random.default_rng(0)
    entry = (rng.random((args.bars, args.configs)) < 0.2).astype(np.int8)
    stop = np.where(entry == 1, bars.close[:, None] * rng.uniform(0.003, 0.03, entry.shape), 0.0)
    out: dict[str, float] = {}
    results = []
    for linear in (True, False):
        perp_replay.LINEAR_SEARCH = linear
        perp_replay._build.cache_clear()
        times = []
        for _ in range(4):  # the first run compiles
            t = time.perf_counter()
            res = perp_replay.run_perp_replay(
                bars.open, bars.close, packed, entry, stop, direction=1, tp_sl_ratio=1.1,
                fee=0.0015, max_risk_pct=0.01, initial_cash=100_000.0, leverage=5,
                max_holding_bars=12, trade_log=False,
            )  # fmt: skip
            times.append(time.perf_counter() - t)
        out["linear" if linear else "binary"] = float(np.median(times[1:]))
        results.append(res.equity)
    out["identical"] = float(results[0].tobytes() == results[1].tobytes())
    out["segments"] = float(packed.mark.seg_start.size)
    out["points_per_segment"] = float(packed.trade.low_ix.size / packed.trade.seg_start.size)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
