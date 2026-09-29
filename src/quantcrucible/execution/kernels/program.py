"""Compile a genome and its configuration set into bounded numeric tables (P3-37, ADR-0038).

The kernels never see a genome object, only the :class:`Program` built here: indicator instances
deduplicated across configurations — the reference grid's trick — each configuration's feature
slots bound to those instances, the entry clauses as opcodes, and every TUNABLE as a column of
``pvals`` in the order its render names it. Compiling is total (INV-107): anything outside the
grammar, or a value the CPU oracle would refuse (a float period, say), raises
:class:`ProgramError` and the job takes another engine. Pure numpy — no numba here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from quantcrucible.core.strategy.genome import (
    Breakout,
    Close,
    Combine,
    Compare,
    Cross,
    CrossLevel,
    Distance,
    Genome,
    Indicator,
    Param,
    Series,
    Slope,
    Threshold,
    named_params,
)

IntArray = npt.NDArray[np.int64]
FloatArray = npt.NDArray[np.float64]

# ── limits: beyond them a job is refused rather than computed ───────────────────────────────
MAX_PERIOD = 480  # the kernels' pairwise sum is unrolled for windows up to this length
MAX_LOOKBACK = 10_000
MAX_CONFIGS = 4_096
MAX_CLAUSES = 8
ATR_PERIOD = 14  # the rendered signal() always uses ind.atr(bars, 14)
BAND_PERIOD = 8  # and the Bollinger stop ind.boll_*(bars.close, 8)

# ── opcodes ─────────────────────────────────────────────────────────────────────────────────
OP_CLOSE, OP_SMA, OP_EMA, OP_RSI, OP_ZSCORE = 0, 1, 2, 3, 4
OP_RMAX, OP_RMIN, OP_BOLL_UPPER, OP_BOLL_LOWER, OP_ATR = 5, 6, 7, 8, 9
SERIES_OPS = {
    "sma": OP_SMA, "ema": OP_EMA, "rsi": OP_RSI, "zscore": OP_ZSCORE,
    "rolling_max": OP_RMAX, "rolling_min": OP_RMIN,
}  # fmt: skip
CL_COMPARE, CL_CROSS, CL_THRESHOLD, CL_CROSSLEVEL = 0, 1, 2, 3
CL_DISTANCE, CL_BREAKOUT, CL_SLOPE = 4, 5, 6
CMP_GT, CMP_LT = 0, 1
COMBINE_SINGLE, COMBINE_AND, COMBINE_OR = 0, 1, 2
TP_NONE, TP_RATIO, TP_ATR = 0, 1, 2
NO_SLOT = -1


class ProgramError(ValueError):
    """The genome or a configuration is outside what the kernels compute exactly."""


@dataclass(frozen=True, slots=True)
class Program:
    """One job: one genome, ``n_configs`` parameter sets, one lookback.

    ``clauses`` rows are ``(kind, slot_a, slot_b, param_col, flag)``: ``flag`` is ``CMP_*`` for a
    comparison and 1/0 for up/down; ``param_col`` indexes ``pvals`` (``NO_SLOT`` if unused).
    """

    names: tuple[str, ...]
    pvals: FloatArray  # [configs, params]
    inst_op: IntArray  # [instances]
    inst_period: IntArray  # [instances]
    slot_inst: IntArray  # [configs, slots]
    clauses: IntArray  # [clauses, 5]
    combine: int
    close_slot: int
    atr_slot: int
    band_slot: int  # NO_SLOT unless the stop is Bollinger
    stop_col: int
    tp_mode: int
    tp_ratio: float
    tp_col: int
    direction: int  # +1 long, -1 short
    lookback: int

    @property
    def n_configs(self) -> int:
        return int(self.pvals.shape[0])


def compile_program(
    genome: Genome,
    direction: str,
    tp_sl_ratio: float | None,
    configs: Sequence[Mapping[str, Any]],
    lookback: int,
) -> Program:
    try:
        return _compile(genome, direction, tp_sl_ratio, configs, lookback)
    except ProgramError:
        raise
    except Exception as exc:  # untrusted shapes: any surprise is a refusal (INV-107)
        raise ProgramError(f"{type(exc).__name__}: {exc}") from exc


def _is_number(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _compile(
    genome: Genome,
    direction: str,
    tp_sl_ratio: float | None,
    configs: Sequence[Mapping[str, Any]],
    lookback: int,
) -> Program:
    if not isinstance(genome, Genome):
        raise ProgramError("not a genome")
    if direction not in ("long", "short"):
        raise ProgramError(f"direction must be long or short, got {direction!r}")
    if tp_sl_ratio is not None and not (
        _is_number(tp_sl_ratio) and math.isfinite(tp_sl_ratio) and tp_sl_ratio > 0
    ):
        raise ProgramError("tp_sl_ratio must be finite and > 0")
    if not (isinstance(lookback, int) and 2 <= lookback <= MAX_LOOKBACK):
        raise ProgramError(f"lookback must be an int in [2, {MAX_LOOKBACK}]")
    if not 1 <= len(configs) <= MAX_CONFIGS:
        raise ProgramError(f"need 1 to {MAX_CONFIGS} configurations, got {len(configs)}")

    named = named_params(genome)
    names = tuple(n for n, _ in named)
    column = {id(p): i for i, (_, p) in enumerate(named)}
    periods = {n for n, p in named if p.kind == "period"}
    pvals = np.empty((len(configs), len(names)), dtype=np.float64)
    for m, cfg in enumerate(configs):
        if set(cfg) != set(names):
            raise ProgramError(f"configuration {m} does not bind exactly {list(names)}")
        for j, name in enumerate(names):
            v = cfg[name]
            if name in periods:
                if not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_PERIOD):
                    raise ProgramError(f"{name} must be an int period in [1, {MAX_PERIOD}]")
            elif not (_is_number(v) and math.isfinite(v)):
                raise ProgramError(f"{name} must be a finite number")
            pvals[m, j] = float(v)

    b = _Builder(len(configs), pvals, column)
    close_slot = b.slot(("close",), OP_CLOSE, None)
    atr_slot = b.slot(("atr",), OP_ATR, ATR_PERIOD)
    clauses = genome.clauses()
    if not 1 <= len(clauses) <= MAX_CLAUSES:
        raise ProgramError(f"need 1 to {MAX_CLAUSES} clauses")
    rows = [b.clause(c) for c in clauses]
    if isinstance(genome.entry, Combine):
        if genome.entry.op not in ("and", "or"):
            raise ProgramError("combine must be and/or")
        combine = COMBINE_AND if genome.entry.op == "and" else COMBINE_OR
    else:
        combine = COMBINE_SINGLE
    if genome.stop_kind == "bollinger":
        band_op = OP_BOLL_LOWER if direction == "long" else OP_BOLL_UPPER
        band_slot = b.slot(("band", band_op), band_op, BAND_PERIOD)
    elif genome.stop_kind == "atr":
        band_slot = NO_SLOT
    else:
        raise ProgramError("stop kind must be atr or bollinger")
    stop_col = b.col(genome.stop)
    if tp_sl_ratio is not None:  # the campaign ratio wins over a k_tp gene (render order)
        tp_mode, tp_ratio, tp_col = TP_RATIO, float(tp_sl_ratio), NO_SLOT
    elif genome.take_profit is not None:
        tp_mode, tp_ratio, tp_col = TP_ATR, 0.0, b.col(genome.take_profit)
    else:
        tp_mode, tp_ratio, tp_col = TP_NONE, 0.0, NO_SLOT
    inst_op, inst_period, slot_inst = b.tables()
    return Program(
        names=names, pvals=pvals, inst_op=inst_op, inst_period=inst_period,
        slot_inst=slot_inst, clauses=np.asarray(rows, dtype=np.int64).reshape(-1, 5),
        combine=combine, close_slot=close_slot, atr_slot=atr_slot, band_slot=band_slot,
        stop_col=stop_col, tp_mode=tp_mode, tp_ratio=tp_ratio, tp_col=tp_col,
        direction=1 if direction == "long" else -1, lookback=lookback,
    )  # fmt: skip


class _Builder:
    def __init__(self, n_configs: int, pvals: FloatArray, column: Mapping[int, int]) -> None:
        self.n = n_configs
        self.pvals = pvals
        self.column = column
        self.slots: dict[tuple[Any, ...], int] = {}
        self.slot_cols: list[list[int]] = []  # per slot: the instance of each configuration
        self.instances: dict[tuple[int, int], int] = {}

    def col(self, p: object) -> int:
        if not isinstance(p, Param) or id(p) not in self.column:
            raise ProgramError("parameter not declared by the render")
        return self.column[id(p)]

    def _instance(self, op: int, period: int) -> int:
        return self.instances.setdefault((op, period), len(self.instances))

    def slot(self, key: tuple[Any, ...], op: int, period: int | Param | None) -> int:
        """A feature slot; ``period`` is a constant, a TUNABLE (varies per config) or none."""
        if key in self.slots:
            return self.slots[key]
        if isinstance(period, Param):
            j = self.col(period)
            per_config = [self._instance(op, int(self.pvals[m, j])) for m in range(self.n)]
        else:
            inst = self._instance(op, 0 if period is None else period)
            per_config = [inst] * self.n
        self.slots[key] = len(self.slot_cols)
        self.slot_cols.append(per_config)
        return self.slots[key]

    def series(self, s: Series) -> int:
        if isinstance(s, Close):
            return self.slot(("close",), OP_CLOSE, None)
        if isinstance(s, Indicator) and s.op in SERIES_OPS:
            return self.slot(("ind", s.op, id(s.period)), SERIES_OPS[s.op], s.period)
        raise ProgramError(f"unsupported series {s!r}")

    def clause(self, c: object) -> list[int]:
        if isinstance(c, Compare):
            return [CL_COMPARE, self.series(c.left), self.series(c.right), NO_SLOT, _cmp(c.op)]
        if isinstance(c, Cross):
            return [CL_CROSS, self.series(c.left), self.series(c.right), NO_SLOT, int(c.up)]
        if isinstance(c, Threshold):
            return [CL_THRESHOLD, self.series(c.osc), NO_SLOT, self.col(c.level), _cmp(c.op)]
        if isinstance(c, CrossLevel):
            return [CL_CROSSLEVEL, self.series(c.osc), NO_SLOT, self.col(c.level), int(c.up)]
        if isinstance(c, Distance):
            a, b = self.series(c.left), self.series(c.right)
            return [CL_DISTANCE, a, b, self.col(c.k), _cmp(c.op)]
        if isinstance(c, Breakout):
            op = OP_RMAX if c.up else OP_RMIN
            name = "rolling_max" if c.up else "rolling_min"
            level = self.slot(("ind", name, id(c.period)), op, c.period)
            return [CL_BREAKOUT, self.slot(("close",), OP_CLOSE, None), level, NO_SLOT, int(c.up)]
        if isinstance(c, Slope):
            return [CL_SLOPE, self.series(c.series), NO_SLOT, NO_SLOT, int(c.up)]
        raise ProgramError(f"unsupported clause {c!r}")

    def tables(self) -> tuple[IntArray, IntArray, IntArray]:
        by_index = sorted(self.instances.items(), key=lambda kv: kv[1])
        inst_op = np.asarray([op for (op, _), _ in by_index], dtype=np.int64)
        inst_period = np.asarray([per for (_, per), _ in by_index], dtype=np.int64)
        slot_inst = np.asarray(self.slot_cols, dtype=np.int64).T.reshape(self.n, -1)
        return inst_op, inst_period, np.ascontiguousarray(slot_inst)


def _cmp(op: str) -> int:
    if op == ">":
        return CMP_GT
    if op == "<":
        return CMP_LT
    raise ProgramError(f"comparison must be > or <, got {op!r}")
