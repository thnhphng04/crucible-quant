# ADR-0038 — GPU backtest engine: genome as data, bit-exact with the CPU oracle

- **Status:** Accepted
- **Date:** 2026-09-30
- **Task:** P3-27 … P3-30 (engine: P3-32 … P3-51) · **Arch:** §3.3.3, §3.5, §6 · **Open item:** narrows O14

## Context

On 1h data (≈ 67k bars, L = 400), `step()` recomputes every indicator on each lookback window. That is 87–98% of a backtest (P3-27, `scripts/bench_backtest.py`), so one gate-④ grid costs 0.4–3.3 h of one core per candidate. ADR-0025 rejected a vectorized grid backtester because a second engine breaks P5. The user asked to move backtesting to the GPU (RTX 3050 Ti Laptop, 4 GB, WDDM with watchdog, fp32:fp64 = 32:1).

## Decision

1. **The genome is data.** Host kernels written in this repo interpret an engine-C genome as numeric tables. They never import, `exec` or compile candidate source. The source still runs in the Docker sandbox for ①a/①b. A strategy without a grammar genome keeps the sandbox path.
2. **One source, two targets.** Each kernel is one Python function compiled by `numba.njit` (CPU, parallel) and by `numba.cuda.jit` (GPU). CUDA is primary; the CPU build is the fallback, the CI backend and the fp32 reference. The layout follows `reference_architecture/train_full_grid_gpu.py`: indicator instances deduplicated, one lane per (instance, bar), one thread per configuration for the replay.
3. **Bit-exact parity with the CPU oracle** (`registry.py` on `bars.window(t, L)`, the rendered source, `_run_spot_bracket`):
   - sums reproduce numpy's pairwise order (8 accumulators, block 128, halves rounded to 8). They are written without recursion, because a cached recursive `njit` segfaulted.
   - `_smooth` reproduces scipy's `lfilter` step `y = (1−α)·y + α·x`, seeded with that pairwise mean.
   - on CUDA, every fp64 op goes through non-contracted `libdevice.*_rn`. There is no `dsub_rn`, so `a − b` is `dadd_rn(a, −b)`, which is exact. Plain operators contracted to FMA and mismatched 2676/4096 EMA values.
4. **Toolchain.** `numba` 0.67 + `numba-cuda[cu12]` 0.30.4 are in the non-default `gpu` group; the NVIDIA wheels are proprietary but redistributable. The CUDA 12.9 runtime loads on driver 12.7 by minor-version compatibility. numpy is capped `<2.5`, because numba-cuda still calls `np.row_stack`.
5. **Parity is enforced at runtime too** (audit, fail closed). Precision is locked per campaign ([ADR-0039](0039-campaign-locked-backtest-precision.md)).

## Consequences

- **E7 (CPU):** zero bit mismatches against `registry.py` for sma, ema, rsi, atr, zscore, boll, rolling_max, over 3 seeds and 4 series.
- **G1 prototype, 6 configurations × 4 variants:** equity bit-identical to `_run_spot_bracket`, as are trades, denials, ambiguous bars, signals and stop bits.
- **E0, G1 at 67k bars, M = 200:** CUDA fused 0.18 s, njit fused 0.38 s, Python ≈ 1,440 s. At M = 1: hybrid 0.018 s. CUDA beats njit by only ≈ 2.1×, below the 3× rule, and the user kept CUDA primary. Expect more on genomes with heavier indicators.
- **E2/E5 (P3-43), 8 pipeline workers, sampled genomes, 200-config grids, 67k bars:** CUDA runs features 5× and signals 2–3× faster than the CPU but the sequential replay 6× slower, so a CUDA job computes features and signals on the device and replays on the CPU; one job's GPU stages hold the device at a time. After removing two host costs (a JSON round trip of each report, a JSON comparison in the audits), the engine does ≈ 8.4 grids/s on CUDA against ≈ 3.9 on CPU njit alone — about 0.12 s per grid, against 0.4–3.3 h before.
- **E4 (P3-50, `scripts/experiments/e4_first_touch.py`):** the perpetual replay (K4) runs on the CPU only, like every replay here. First touch on a segment's running extremes: a linear scan and bisection return the same index; bisection, the oracle's own `bisect_left`, was ≈ 8% faster on 16-point segments. 200 configurations × 3k 4h bars take 5 ms, against ≈ 14 s for the Python replay.
- ADR-0025's rejection no longer holds for engine-C genomes: the second engine is proved against the oracle rather than trusted. Parity tests and the oracle fingerprint become mandatory whenever `registry.py`, `genome` rendering or `engine.py` change.
- **Arch edits needed (P3-31, VI first, then EN, then `/sync-docs`):**
  - §3.3.3: host interpreter, never the source.
  - §3.5: kernel engine, CPU oracle, parity and audit.
  - §6: numba and NVIDIA wheels.
  - §10: decision D23.
  - §10.1: the new keys.
- After the speed-up, the ①b Docker leak check is the next bottleneck (to open as O22).

## Alternatives considered

- **CUDA code generated per genome:** faster per run, but a JIT per genome, and the host would compile generated code.
- **CuPy/PyTorch tensors:** heavy dependency, the windowed EMA is awkward, and the replay stays sequential.
- **njit as the primary engine:** simpler (no TDR or VRAM limits) and within 2.1× of CUDA; kept as the fallback because the user chose GPU.
- **A tolerance instead of bit parity:** unnecessary, since E7 reached exact equality.
