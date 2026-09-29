"""P3-30 driver: bit parity of the G1 prototype against the CPU oracle, then E0/E2 timings."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from numba import cuda

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

from bench_backtest import fixture_genomes, hourly_bars  # noqa: E402
from gpu_proto import build  # noqa: E402

from quantcrucible.agent.grammar import render_genome  # noqa: E402
from quantcrucible.core.strategy.base import generate_signals  # noqa: E402
from quantcrucible.core.strategy.template import load_strategy_class, parse  # noqa: E402
from quantcrucible.core.strategy.tunable import pbo_grid  # noqa: E402
from quantcrucible.execution.engine import _run_spot_bracket  # noqa: E402
from quantcrucible.execution.exit_policy import ExitPolicy  # noqa: E402
from quantcrucible.execution.nautilus_bridge import CostModel, lot_step  # noqa: E402
from quantcrucible.execution.risk import RiskSettings  # noqa: E402

RATIO, MAXH, PCT, CASH0, L = 1.1, 100, 0.01, 100_000.0, 400
FEE = float(CostModel().taker_rate)
LOT = lot_step("BTC/USDT")


def tables(configs):
    periods = sorted({int(c[k]) for c in configs for k in ("n1", "n2", "n3")})
    idx = {p: i for i, p in enumerate(periods)}
    ema_n = np.array(periods, dtype=np.int64)
    ema_alpha = np.array([2.0 / (p + 1.0) for p in periods])
    i1 = np.array([idx[int(c["n1"])] for c in configs], dtype=np.int64)
    i2 = np.array([idx[int(c["n2"])] for c in configs], dtype=np.int64)
    i3 = np.array([idx[int(c["n3"])] for c in configs], dtype=np.int64)
    kstop = np.array([float(c["k_stop"]) for c in configs])
    return ema_n, ema_alpha, i1, i2, i3, kstop


def run_cpu(k, bars, configs, fused=False, only=None):
    kf, ks, kr = k
    ema_n, ema_alpha, i1, i2, i3, kstop = tables(configs)
    n = len(bars)
    now = np.empty((ema_n.size, n))
    prev = np.empty((ema_n.size, n))
    atr = np.empty(n)
    t0 = time.perf_counter()
    kf(bars.close, bars.high, bars.low, L, ema_n, ema_alpha, now, prev, atr, 14)
    t1 = time.perf_counter()
    entry = np.zeros((n, kstop.size), np.int8)
    stopd = np.zeros((n, kstop.size))
    if not fused:
        ks(now, prev, atr, i1, i2, i3, kstop, RATIO, entry, stopd)
    t2 = time.perf_counter()
    eq = np.empty((kstop.size, n))
    stats = np.zeros((kstop.size, 3), np.int64)
    kr(
        bars.open,
        bars.high,
        bars.low,
        bars.close,
        entry,
        stopd,
        fused,
        now,
        prev,
        atr,
        i1,
        i2,
        i3,
        kstop,
        RATIO,
        FEE,
        LOT,
        PCT,
        CASH0,
        MAXH,
        eq,
        stats,
    )
    t3 = time.perf_counter()
    return eq, stats, entry, stopd, {"features": t1 - t0, "signals": t2 - t1, "replay": t3 - t2}


def run_cuda(k, bars, configs, fused=False, cpu_replay=None):
    kf, ks, kr = k
    ema_n, ema_alpha, i1, i2, i3, kstop = tables(configs)
    n, m = len(bars), kstop.size
    TPB = 128
    t0 = time.perf_counter()
    d_c, d_h, d_l, d_o = (
        cuda.to_device(np.ascontiguousarray(a))
        for a in (bars.close, bars.high, bars.low, bars.open)
    )
    d_n, d_a = cuda.to_device(ema_n), cuda.to_device(ema_alpha)
    d_i1, d_i2, d_i3, d_k = (cuda.to_device(a) for a in (i1, i2, i3, kstop))
    now = cuda.device_array((ema_n.size, n))
    prev = cuda.device_array((ema_n.size, n))
    atr = cuda.device_array(n)
    lanes = (ema_n.size + 1) * n
    kf[(lanes + TPB - 1) // TPB, TPB](d_c, d_h, d_l, L, d_n, d_a, now, prev, atr, 14)
    cuda.synchronize()
    t1 = time.perf_counter()
    entry = cuda.device_array((n, m), np.int8)
    stopd = cuda.device_array((n, m))
    if not fused:
        ks[(m * n + TPB - 1) // TPB, TPB](
            now, prev, atr, d_i1, d_i2, d_i3, d_k, RATIO, entry, stopd
        )
    cuda.synchronize()
    t2 = time.perf_counter()
    if cpu_replay is not None:  # hybrid: GPU features+signals, CPU replay
        e_h, s_h = entry.copy_to_host(), stopd.copy_to_host()
        eq = np.empty((m, n))
        stats = np.zeros((m, 3), np.int64)
        cpu_replay(
            bars.open,
            bars.high,
            bars.low,
            bars.close,
            e_h,
            s_h,
            False,
            np.empty((1, 1)),
            np.empty((1, 1)),
            np.empty(1),
            i1,
            i2,
            i3,
            kstop,
            RATIO,
            FEE,
            LOT,
            PCT,
            CASH0,
            MAXH,
            eq,
            stats,
        )
        t3 = time.perf_counter()
        return (
            eq,
            stats,
            None,
            None,
            {"features": t1 - t0, "signals": t2 - t1, "replay+copy": t3 - t2},
        )
    eq_d = cuda.device_array((m, n))
    st_d = cuda.to_device(np.zeros((m, 3), np.int64))
    kr[(m + 31) // 32, 32](
        d_o,
        d_h,
        d_l,
        d_c,
        entry,
        stopd,
        fused,
        now,
        prev,
        atr,
        d_i1,
        d_i2,
        d_i3,
        d_k,
        RATIO,
        FEE,
        LOT,
        PCT,
        CASH0,
        MAXH,
        eq_d,
        st_d,
    )
    cuda.synchronize()
    t3 = time.perf_counter()
    eq, stats = eq_d.copy_to_host(), st_d.copy_to_host()
    t4 = time.perf_counter()
    e_h = s_h = None
    if not fused:
        e_h, s_h = entry.copy_to_host(), stopd.copy_to_host()
    return (
        eq,
        stats,
        e_h,
        s_h,
        {"features": t1 - t0, "signals": t2 - t1, "replay": t3 - t2, "copy": t4 - t3},
    )


def oracle(source, cfg, bars):
    strat = load_strategy_class(source, "oracle_g1")(cfg)
    sigs = tuple(generate_signals(strat, bars, L))
    counts = {d: sum(s.direction == d for s in sigs) for d in ("long", "short", "flat")}
    res = _run_spot_bracket(
        "BTC/USDT",
        bars,
        sigs,
        counts,
        CostModel(),
        CASH0,
        L,
        RiskSettings(PCT),
        None,
        ExitPolicy("bracket_timeout_v1", RATIO, MAXH),
    )
    return sigs, res


def parity(kc, kg):
    bars = hourly_bars(int(sys.argv[2]) if len(sys.argv) > 2 else 3000, seed=1)
    source, params = render_genome(fixture_genomes()["G1"], "long", tp_sl_ratio=RATIO)
    configs = pbo_grid(list(parse(source).tunables), 5, 0.30, 200, 0, center=params)
    pick = [0, 1, 17, 63, 120, 199]
    sub = [configs[i] for i in pick]
    results = {
        "cpu": run_cpu(kc, bars, sub),
        "cuda": run_cuda(kg, bars, sub),
        "cuda_fused": run_cuda(kg, bars, sub, fused=True),
        "cpu_fused": run_cpu(kc, bars, sub, fused=True),
    }
    report = {}
    for j, cfg in enumerate(sub):
        sigs, res = oracle(source, cfg, bars)
        o_entry = np.array([s.direction == "long" for s in sigs])
        o_stop = np.array([s.stop_distance if s.direction == "long" else 0.0 for s in sigs])
        row = {"oracle_trades": res.n_trades, "oracle_entries": int(o_entry.sum())}
        for name, (eq, stats, e, s, _t) in results.items():
            ok_eq = bool(np.array_equal(eq[j], res.equity))
            row[name] = {
                "equity_bits": ok_eq,
                "trades": int(stats[j, 0]),
                "denied": int(stats[j, 1]) == res.denied_orders,
                "ambiguous": int(stats[j, 2]) == res.ambiguous_bars,
            }
            if e is not None:
                row[name]["entry_eq"] = bool(np.array_equal(e[:, j] == 1, o_entry))
                row[name]["stop_bits"] = bool(
                    np.array_equal(np.where(e[:, j] == 1, s[:, j], 0.0), o_stop)
                )
            if not ok_eq:
                row[name]["max_rel"] = float(np.max(np.abs(eq[j] - res.equity) / res.equity))
        report[f"cfg{pick[j]}"] = row
    print(json.dumps(report, indent=1))


def timings(kc, kg, n_bars):
    bars = hourly_bars(n_bars)
    source, params = render_genome(fixture_genomes()["G1"], "long", tp_sl_ratio=RATIO)
    configs = pbo_grid(list(parse(source).tunables), 5, 0.30, 200, 0, center=params)
    out = {}
    for m in (1, 200):
        cfgs = configs[:m]
        for name, fn in (
            ("cpu_njit", lambda c=cfgs: run_cpu(kc, bars, c)),
            ("cpu_njit_fused", lambda c=cfgs: run_cpu(kc, bars, c, fused=True)),
            ("cuda", lambda c=cfgs: run_cuda(kg, bars, c)),
            ("cuda_fused", lambda c=cfgs: run_cuda(kg, bars, c, fused=True)),
            ("hybrid", lambda c=cfgs: run_cuda(kg, bars, c, cpu_replay=kc[2])),
        ):
            fn()  # warm (JIT)
            walls, stages = [], None
            for _ in range(3):
                t = time.perf_counter()
                r = fn()
                walls.append(time.perf_counter() - t)
                stages = r[4]
            out[f"M={m} {name}"] = {
                "median_s": float(np.median(walls)),
                "stages": {k: round(v, 4) for k, v in stages.items()},
            }
            print(
                f"M={m:3d} {name:15} {np.median(walls):8.3f}s  {out[f'M={m} {name}']['stages']}",
                flush=True,
            )
    return out


if __name__ == "__main__":
    t = time.perf_counter()
    kc = build("cpu")
    kg = build("cuda")
    mode = sys.argv[1] if len(sys.argv) > 1 else "parity"
    if mode == "parity":
        parity(kc, kg)
    else:
        timings(kc, kg, int(sys.argv[2]) if len(sys.argv) > 2 else 67_000)
    print(f"total {time.perf_counter() - t:.1f}s", file=sys.stderr)
