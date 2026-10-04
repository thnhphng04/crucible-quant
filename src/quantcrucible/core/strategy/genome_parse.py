"""Recover an engine-C genome from its rendered source — without running it (P3-33, ADR-0038).

Re-runs (gate ⑥′, calibration, the holdout) carry a strategy's source and parameters, not its
genome. The kernel engine may interpret a genome only if it *is* that source, so this module reads
the source's syntax tree (``ast`` — never ``exec``), rebuilds the genome, and accepts it only when
``render_genome`` reproduces the source **byte for byte** (INV-106). Anything else — hand-written
strategies, LLM code, a single edited byte — raises :class:`GenomeParseError` and takes the
sandbox path. The reader only has to be right for renders: the final comparison proves it.
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from quantcrucible.core.strategy.base import ScopeDirection
from quantcrucible.core.strategy.genome import (
    Bandwidth,
    Breakout,
    Clause,
    Close,
    Cmp,
    Combine,
    Compare,
    Cross,
    CrossLevel,
    Distance,
    DistanceScale,
    Entry,
    Genome,
    Indicator,
    Param,
    ParamKind,
    Series,
    Slope,
    Threshold,
    VolRatio,
    render_genome,
)
from quantcrucible.core.strategy.registry import OPS
from quantcrucible.core.strategy.template import JOINT, parse
from quantcrucible.core.strategy.tunable import Tunable

MAX_SOURCE_BYTES = 64 * 1024  # a render is ~2 KB; refuse anything far larger before parsing it
_KIND = (
    (re.compile(r"k_stop|k_tp"), "mult"),
    (re.compile(r"n_stop"), "period"),
    (re.compile(r"n\d+"), "period"),
    (re.compile(r"lv\d+"), "level"),
    (re.compile(r"k\d+"), "mult"),
)
_SERIES_OPS = frozenset(op for op, spec in OPS.items() if spec.kind == "series")
_RATIO_INPUT = {"atr_ratio": "bars", "band_ratio": "bars.close"}  # ADR-0047


class GenomeParseError(ValueError):
    """The source is not the exact render of an engine-C genome."""


@dataclass(frozen=True, slots=True)
class ParsedGenome:
    genome: Genome
    direction: ScopeDirection
    tp_sl_ratio: float | None


def parse_genome(source: str) -> ParsedGenome:
    """The genome ``source`` renders from, or :class:`GenomeParseError`."""
    size = len(source.encode("utf-8"))
    if size > MAX_SOURCE_BYTES:
        raise GenomeParseError(f"source is {size} bytes, more than {MAX_SOURCE_BYTES}")
    try:
        parsed = parse(source)
        if [b.name for b in parsed.blocks] != [JOINT]:
            raise GenomeParseError("not a single joint EVOLVE-BLOCK")
        tree = ast.parse(textwrap.dedent(parsed.block(JOINT).body))
        result = _Reader({t.name: t for t in parsed.tunables}).read(tree)
        rendered, _ = render_genome(result.genome, result.direction, result.tp_sl_ratio)
    except GenomeParseError:
        raise
    except Exception as exc:  # untrusted input: any failure is a refusal, never a crash
        raise GenomeParseError(f"{type(exc).__name__}: {exc}") from exc
    if rendered != source:
        raise GenomeParseError("re-rendering the genome does not reproduce the source")
    return result


def _fail(what: str) -> GenomeParseError:
    return GenomeParseError(f"unexpected {what}")


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _attr(node: ast.AST, owner: str, attr: str | None = None) -> str | None:
    """``owner.attr`` → attr (``None`` when the shape differs)."""
    if isinstance(node, ast.Attribute) and _is_name(node.value, owner):
        return node.attr if attr is None or node.attr == attr else None
    return None


def _param_name(node: ast.AST) -> str | None:
    """``self.p.NAME`` → NAME."""
    if isinstance(node, ast.Attribute) and _attr(node.value, "self", "p") is not None:
        return node.attr
    return None


def _feature_key(node: ast.AST) -> str | None:
    """``x["name"]`` → name."""
    if (
        isinstance(node, ast.Subscript)
        and _is_name(node.value, "x")
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value
    return None


def _is_ago1(node: ast.AST) -> str | None:
    """``x["name"].ago(1)`` → name."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ago"
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == 1
    ):
        return _feature_key(node.func.value)
    return None


def _cmp(op: ast.cmpop) -> Cmp:
    if isinstance(op, ast.Gt):
        return ">"
    if isinstance(op, ast.Lt):
        return "<"
    raise _fail("comparison operator")


class _Reader:
    def __init__(self, tunables: Mapping[str, Tunable]) -> None:
        self.tunables = tunables
        self.params: dict[str, Param] = {}  # one object per TUNABLE name, as the renderer shares
        # feature name → ("close",) | ("series", op, param name) | ("band", op) | ("spread",)
        # | ("atr_ratio" or "band_ratio", fast param name, slow param name)
        self.features: dict[str, tuple[str, ...]] = {}

    # ── leaves ───────────────────────────────────────────────────────────────────────────
    def param(self, name: str | None) -> Param:
        if name is None or name not in self.tunables:
            raise _fail(f"parameter {name!r}")
        if name not in self.params:
            t = self.tunables[name]
            kind: ParamKind | None = next((k for rx, k in _KIND if rx.fullmatch(name)), None)  # type: ignore[misc]
            if kind is None or (kind == "period") != t.is_int:
                raise _fail(f"parameter kind for {name!r}")
            value: float | int = int(t.default) if t.is_int else float(t.default)
            self.params[name] = Param(kind, t.low, t.high, value)
        return self.params[name]

    def series(self, node: ast.AST) -> Series:
        key = _feature_key(node)
        feature = self.features.get(key) if key is not None else None
        if feature == ("close",):
            return Close()
        if feature is not None and feature[0] == "series":
            return Indicator(feature[1], self.param(feature[2]))
        raise _fail("series")

    def indicator(self, node: ast.AST) -> Indicator:
        s = self.series(node)
        if not isinstance(s, Indicator):
            raise _fail("oscillator")
        return s

    # ── indicators() ─────────────────────────────────────────────────────────────────────
    def read_features(self, fn: ast.FunctionDef) -> None:
        if len(fn.body) != 1 or not isinstance(fn.body[0], ast.Return):
            raise _fail("indicators() body")
        d = fn.body[0].value
        if not isinstance(d, ast.Dict):
            raise _fail("indicators() return")
        for k, v in zip(d.keys, d.values, strict=True):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                raise _fail("feature key")
            if k.value in ("atr", "atr_stop"):  # fixed by the render: the final comparison checks
                continue
            self.features[k.value] = self._feature(v)

    def _feature(self, v: ast.AST) -> tuple[str, ...]:
        if _attr(v, "bars", "close") is not None:
            return ("close",)
        if (  # a grammar-v4 Distance yardstick (ADR-0045); the final comparison checks its args
            isinstance(v, ast.Call)
            and _attr(v.func, "ind") == "spread_stdev"
            and len(v.args) == 3
            and not v.keywords
        ):
            return ("spread",)
        if (  # a grammar-v5 volatility ratio (ADR-0047): ind.<ratio>(<input>, fast, slow)
            isinstance(v, ast.Call)
            and _attr(v.func, "ind") in _RATIO_INPUT
            and len(v.args) == 3
            and not v.keywords
        ):
            ratio = str(_attr(v.func, "ind"))
            if ast.unparse(v.args[0]) != _RATIO_INPUT[ratio]:
                raise _fail("ratio input")
            return (ratio, self._period_name(v.args[1]), self._period_name(v.args[2]))
        if not (isinstance(v, ast.Call) and len(v.args) == 2 and not v.keywords):
            raise _fail("feature")
        op = _attr(v.func, "ind")
        if op is None or op not in _SERIES_OPS or _attr(v.args[0], "bars", "close") is None:
            raise _fail("feature call")
        if op in ("boll_lower", "boll_upper"):
            return ("band", op)
        return ("series", op, self._period_name(v.args[1]))

    @staticmethod
    def _period_name(node: ast.AST) -> str:
        name = _param_name(node)
        if name is None:
            raise _fail("feature period")
        return name

    # ── signal() ─────────────────────────────────────────────────────────────────────────
    def read(self, tree: ast.Module) -> ParsedGenome:
        fns = [s for s in tree.body if isinstance(s, ast.FunctionDef)]
        if len(fns) != 2 or len(tree.body) != 2:
            raise _fail("block layout")
        indicators, signal = fns
        if indicators.name != "indicators" or signal.name != "signal":
            raise _fail("method names")
        self.read_features(indicators)
        body = signal.body
        if len(body) != 5 or not isinstance(body[1], ast.Assign) or not isinstance(body[3], ast.If):
            raise _fail("signal() body")
        stop_kind = self._stop_kind(body[1].value)
        stop = self.param("k_stop")
        test = body[3].test
        if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)):
            raise _fail("entry guard")
        if len(test.values) != 2 or not _is_name(test.values[0], "ready"):
            raise _fail("entry guard")
        entry = self._entry(test.values[1])
        if len(body[3].body) != 1 or not isinstance(body[3].body[0], ast.Return):
            raise _fail("entry return")
        direction, take_profit, ratio = self._signal(body[3].body[0].value)
        if take_profit is None and "k_tp" in self.tunables:
            # A campaign TP ratio overrides the genome's k_tp in signal(), but the render still
            # declares it: the genome keeps its take-profit gene.
            take_profit = self.param("k_tp")
        stop_period = self.param("n_stop") if "n_stop" in self.tunables else None
        genome = Genome(entry, stop, take_profit, stop_kind, stop_period)
        return ParsedGenome(genome, direction, ratio)

    def _stop_kind(self, node: ast.AST) -> Literal["atr", "bollinger"]:
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)):
            raise _fail("stop")
        if _param_name(node.left) != "k_stop":
            raise _fail("stop multiplier")
        atr = _is_name(node.right, "atr") or _feature_key(node.right) == "atr_stop"
        return "atr" if atr else "bollinger"

    def _entry(self, node: ast.AST) -> Entry:
        if isinstance(node, ast.BoolOp):
            op: Literal["and", "or"] = "and" if isinstance(node.op, ast.And) else "or"
            return Combine(op, tuple(self._clause(v) for v in node.values))
        return self._clause(node)

    def _clause(self, node: ast.AST) -> Clause:
        if isinstance(node, ast.Call):
            fn = _attr(node.func, "ind")
            if fn not in ("cross_up", "cross_down") or len(node.args) != 2:
                raise _fail("call clause")
            up = fn == "cross_up"
            level = _param_name(node.args[1])
            if level is not None:
                return CrossLevel(self.indicator(node.args[0]), up, self.param(level))
            return Cross(self.series(node.args[0]), up, self.series(node.args[1]))
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
            raise _fail("clause")
        op, left, right = _cmp(node.ops[0]), node.left, node.comparators[0]
        if isinstance(left, ast.BinOp):  # (a - b) / x["atr"] op k, or / x["fK"] (spread_stdev)
            if not (isinstance(left.op, ast.Div) and isinstance(left.left, ast.BinOp)):
                raise _fail("distance")
            diff, den = left.left, _feature_key(left.right)
            if den == "atr":
                scale: DistanceScale = "atr"
            elif den is not None and self.features.get(den) == ("spread",):
                scale = "spread"
            else:
                raise _fail("distance scale")
            return Distance(
                self.series(diff.left),
                self.series(diff.right),
                op,
                self.param(_param_name(right)),
                scale,
            )
        prev = _is_ago1(right)
        if prev is not None:
            if prev == _feature_key(left):
                return Slope(self.series(left), op == ">")
            feature = self.features.get(prev)
            if (
                feature is None
                or feature[0] != "series"
                or feature[1] not in ("rolling_max", "rolling_min")
            ):
                raise _fail("breakout level")
            return Breakout(op == ">", self.param(feature[2]))
        level_name = _param_name(right)
        if level_name is not None:
            ratio = self.features.get(_feature_key(left) or "")
            if ratio is not None and ratio[0] in _RATIO_INPUT:  # a v5 volatility ratio (ADR-0047)
                kind = VolRatio if ratio[0] == "atr_ratio" else Bandwidth
                return kind(self.param(ratio[1]), self.param(ratio[2]), op, self.param(level_name))
            return Threshold(self.indicator(left), op, self.param(level_name))
        return Compare(self.series(left), op, self.series(right))

    def _signal(self, node: ast.AST | None) -> tuple[ScopeDirection, Param | None, float | None]:
        if not (isinstance(node, ast.Call) and _is_name(node.func, "Signal")):
            raise _fail("entry signal")
        args = node.args
        if len(args) not in (3, 4) or not isinstance(args[0], ast.Constant):
            raise _fail("entry signal arguments")
        direction: ScopeDirection
        if args[0].value == "long":
            direction = "long"
        elif args[0].value == "short":
            direction = "short"
        else:
            raise _fail("direction")
        if len(args) == 3:
            return direction, None, None
        tp = args[3]
        if not (isinstance(tp, ast.BinOp) and isinstance(tp.op, ast.Mult)):
            raise _fail("take profit")
        if _is_name(tp.right, "stop") and isinstance(tp.left, ast.Constant):
            ratio = tp.left.value
            if not isinstance(ratio, float):
                raise _fail("take-profit ratio")
            return direction, None, ratio
        return direction, self.param(_param_name(tp.left)), None
