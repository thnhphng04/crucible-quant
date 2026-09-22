"""Dimensional analysis of an evolvable block: the scale-invariance rule (arch §3.1.6, §3.1.11).

A rule must not change meaning when every price is multiplied by a constant (a split, a
continuous-contract roll, another quote currency). Each expression carries a unit — a pair of
exponents (price, volume): ``bars.close``, ``sma`` or ``atr`` are ``price``; ``rsi``, ``zscore``,
TUNABLE parameters and constants are dimensionless. Addition, subtraction, comparison,
``min``/``max`` and the two sides of ``cross_up``/``cross_down`` need equal units, so
``close > level`` is rejected while ``close / sma > level`` or ``close - sma > k * atr`` pass.
The literal ``0`` fits any unit: a sign test is scale-invariant. ``Signal`` needs a
dimensionless ``strength`` and ``stop_distance``/``take_profit`` in price units.

This pass assumes the block already passed the structural whitelist (gate ①a); it reports only
unit errors and stays silent on anything it cannot type.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from itertools import pairwise

from quantcrucible.core.strategy.registry import OPS

Unit = tuple[int, int]  # exponents of (price, volume)

DIMENSIONLESS: Unit = (0, 0)
PRICE: Unit = (1, 0)
VOLUME: Unit = (0, 1)


class _Any:
    """The literal 0: compatible with every unit."""


class _Bool:
    """A truth value (comparison, predicate, and/or/not)."""


ANY = _Any()
BOOL = _Bool()
Typed = Unit | _Any | _Bool | None  # None = could not be typed (reported elsewhere, if at all)

BAR_UNITS: dict[str, Unit] = {
    "open": PRICE,
    "high": PRICE,
    "low": PRICE,
    "close": PRICE,
    "volume": VOLUME,
}


def unit_name(u: Typed) -> str:
    if isinstance(u, _Any):
        return "0"
    if isinstance(u, _Bool):
        return "a truth value"
    if u is None:
        return "unknown"
    if u == DIMENSIONLESS:
        return "dimensionless"
    parts = []
    for name, exp in (("price", u[0]), ("volume", u[1])):
        if exp == 1:
            parts.append(name)
        elif exp:
            parts.append(f"{name}{_superscript(exp)}")
    return "·".join(parts)


def _superscript(n: int) -> str:
    table = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")
    return str(n).translate(table)


@dataclass(frozen=True, slots=True)
class UnitError:
    line: int
    message: str


def _is_unit(u: Typed) -> bool:
    return isinstance(u, tuple)


def _compatible(a: Typed, b: Typed) -> bool:
    if a is None or b is None or isinstance(a, _Any) or isinstance(b, _Any):
        return True
    return a == b


def _join(a: Typed, b: Typed) -> Typed:
    if isinstance(a, _Any):
        return b
    return a


class _Checker:
    def __init__(self) -> None:
        self.errors: list[UnitError] = []
        self.features: dict[str, Typed] = {}
        self.env: dict[str, Typed] = {}

    def error(self, node: ast.AST, message: str) -> None:
        self.errors.append(UnitError(getattr(node, "lineno", 0), f"scale: {message}"))

    # ── statements ───────────────────────────────────────────────────────────────────
    def method(self, fn: ast.FunctionDef, features: bool) -> None:
        self.env = {}
        for stmt in fn.body:
            self.stmt(stmt, features)

    def stmt(self, stmt: ast.stmt, features: bool) -> None:
        if isinstance(stmt, ast.Assign):
            u = self.expr(stmt.value)
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    self.env[t.id] = u
        elif isinstance(stmt, ast.Return) and stmt.value is not None:
            if features and isinstance(stmt.value, ast.Dict):
                for k, v in zip(stmt.value.keys, stmt.value.values, strict=True):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        self.features[k.value] = self.expr(v)
            else:
                self.expr(stmt.value)
        elif isinstance(stmt, ast.If):
            self.expr(stmt.test)
            for s in (*stmt.body, *stmt.orelse):
                self.stmt(s, features)

    # ── expressions ──────────────────────────────────────────────────────────────────
    def expr(self, node: ast.expr) -> Typed:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool | str):
                return None
            return ANY if node.value == 0 else DIMENSIONLESS
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            return self.attribute(node)
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                return self.features.get(node.slice.value)
            return None
        if isinstance(node, ast.UnaryOp):
            inner = self.expr(node.operand)
            return BOOL if isinstance(node.op, ast.Not) else inner
        if isinstance(node, ast.BinOp):
            return self.binop(node)
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                self.expr(v)
            return BOOL
        if isinstance(node, ast.Compare):
            self.compare(node)
            return BOOL
        if isinstance(node, ast.IfExp):
            self.expr(node.test)
            a, b = self.expr(node.body), self.expr(node.orelse)
            if not _compatible(a, b):
                self.error(node, f"the two branches are {unit_name(a)} and {unit_name(b)}")
            return _join(a, b)
        if isinstance(node, ast.Call):
            return self.call(node)
        return None

    def attribute(self, node: ast.Attribute) -> Typed:
        v = node.value
        if isinstance(v, ast.Name) and v.id == "bars":
            return BAR_UNITS.get(node.attr)
        if isinstance(v, ast.Attribute) and v.attr == "p":
            return DIMENSIONLESS  # a TUNABLE parameter is a plain number
        if node.attr == "now":
            return self.expr(v)
        return None

    def binop(self, node: ast.BinOp) -> Typed:
        a, b = self.expr(node.left), self.expr(node.right)
        if isinstance(a, _Bool) or isinstance(b, _Bool) or a is None or b is None:
            return None
        op = node.op
        if isinstance(op, ast.Add | ast.Sub | ast.Mod):
            if not _compatible(a, b):
                verb = "adds" if isinstance(op, ast.Add) else "subtracts"
                self.error(node, f"{verb} {unit_name(a)} and {unit_name(b)}")
                return None
            return _join(a, b)
        if isinstance(op, ast.FloorDiv):
            if not _compatible(a, b):
                self.error(node, f"divides {unit_name(a)} by {unit_name(b)} with //")
            return DIMENSIONLESS
        if isinstance(a, _Any) or isinstance(b, _Any):
            return ANY if isinstance(op, ast.Mult) else None
        assert isinstance(a, tuple) and isinstance(b, tuple)
        if isinstance(op, ast.Mult):
            return (a[0] + b[0], a[1] + b[1])
        if isinstance(op, ast.Div):
            return (a[0] - b[0], a[1] - b[1])
        if isinstance(op, ast.Pow):
            exp = node.right
            if isinstance(exp, ast.Constant) and isinstance(exp.value, int):
                return (a[0] * exp.value, a[1] * exp.value)
            if a != DIMENSIONLESS:
                self.error(node, f"raises {unit_name(a)} to a non-integer power")
                return None
            return DIMENSIONLESS
        return None

    def compare(self, node: ast.Compare) -> None:
        sides = [node.left, *node.comparators]
        units = [self.expr(s) for s in sides]
        for left, right, a, b in zip(sides, sides[1:], units, units[1:], strict=False):
            if isinstance(a, _Bool) or isinstance(b, _Bool):
                continue
            if not _compatible(a, b):
                self.error(node, _compare_message(left, right, a, b))

    def call(self, node: ast.Call) -> Typed:
        func = node.func
        args = [self.expr(a) for a in node.args]
        kwargs: dict[str, Typed] = {k.arg: self.expr(k.value) for k in node.keywords if k.arg}
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "ind" and func.attr in OPS:
                return self.indicator(node, func.attr, args)
            return None
        if isinstance(func, ast.Attribute) and func.attr == "ago":
            return self.expr(func.value)
        if not isinstance(func, ast.Name):
            return None
        if func.id in ("min", "max"):
            for a, b in pairwise(args):
                if not _compatible(a, b):
                    self.error(node, f"{func.id}() mixes {unit_name(a)} and {unit_name(b)}")
            result: Typed = args[0] if args else None
            for a in args[1:]:
                result = _join(result, a)
            return result
        if func.id == "abs":
            return args[0] if args else None
        if func.id == "Signal":
            self.signal(node, args, kwargs)
        return None

    def indicator(self, node: ast.Call, name: str, args: list[Typed]) -> Typed:
        spec = OPS[name]
        if spec.kind == "predicate":
            if len(args) == 2 and not _compatible(args[0], args[1]):
                text = f"{name}() crosses {unit_name(args[0])} with {unit_name(args[1])}"
                if _price_vs_number(args[0], args[1]):
                    text += " — not scale-invariant"
                self.error(node, text)
            return BOOL
        if spec.output == "price":
            return PRICE
        if spec.output == "dimensionless":
            return DIMENSIONLESS
        return args[0] if args else None  # "same": the unit of the input series

    def signal(self, node: ast.Call, args: list[Typed], kwargs: dict[str, Typed]) -> None:
        slots = ["direction", "strength", "stop_distance", "take_profit"]
        given = dict(zip(slots, args, strict=False))
        given.update({k: v for k, v in kwargs.items() if k in slots})
        strength = given.get("strength")
        if _is_unit(strength) and strength != DIMENSIONLESS:
            self.error(node, f"Signal strength must be dimensionless, got {unit_name(strength)}")
        for slot in ("stop_distance", "take_profit"):
            u = given.get(slot)
            if _is_unit(u) and u != PRICE:
                self.error(
                    node, f"Signal {slot} must be in price units (e.g. k × ATR), got "
                    f"{unit_name(u)}",
                )  # fmt: skip


def _price_vs_number(a: Typed, b: Typed) -> bool:
    """A unit-carrying value against a plain number: the rule changes meaning with the scale."""
    return _is_unit(a) and _is_unit(b) and a != b and DIMENSIONLESS in (a, b)


def _compare_message(left: ast.expr, right: ast.expr, a: Typed, b: Typed) -> str:
    text = (
        f"compares {unit_name(a)} ({ast.unparse(left)}) with {unit_name(b)} ({ast.unparse(right)})"
    )
    if _price_vs_number(a, b):
        text += " — not scale-invariant: use a ratio, a z-score or a series in the same units"
    return text


def check_units(tree: ast.Module) -> list[UnitError]:
    """Unit errors in a parsed evolvable block (``indicators`` then ``signal``)."""
    checker = _Checker()
    methods = {s.name: s for s in tree.body if isinstance(s, ast.FunctionDef)}
    if "indicators" in methods:
        checker.method(methods["indicators"], features=True)
    if "signal" in methods:
        checker.method(methods["signal"], features=False)
    return checker.errors
