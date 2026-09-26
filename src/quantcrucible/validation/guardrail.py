"""Gates ①a (static) and ①b (dynamic) — the guardrail (Architecture §3.2, §3.3.1, §3.1.6).

①a runs before any candidate code executes. It checks, in order:

1. template structure and the fixed-region hash against the campaign lock (TEMPLATE_TAMPER);
2. TUNABLE declarations, and that the candidate's params are exactly those, within bounds;
3. an AST whitelist over every editable block (AST_REJECT): only ``indicators`` / ``signal``
   methods built from whitelisted indicators, arithmetic, comparisons and ``Signal``; no imports,
   loops, dunders, dynamic execution, indexing into bars (look-ahead) or undeclared constants;
4. units (``core/strategy/dims.py``): no rule compares values in different units, e.g. a price
   with a constant — rules must be scale-invariant (§3.1.6, §3.1.11).

①b executes the candidate in the sandbox and compares its signals on full, truncated and
perturbed data (``validation/leak_check.py``, ADR-0005).
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass

from quantcrucible.core.strategy.dims import check_units
from quantcrucible.core.strategy.registry import INDICATORS
from quantcrucible.core.strategy.template import (
    ParsedStrategy,
    TemplateError,
    fixed_region_hash,
    parse,
)
from quantcrucible.core.strategy.tunable import Tunable, TunableError
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import (
    G1A_STATIC,
    G1B_DYNAMIC,
    GateContext,
    GateResult,
    StrategyCandidate,
)
from quantcrucible.validation.sandbox import SandboxJob, SandboxRunner, sandbox_failure

BAR_FIELDS = frozenset({"open", "high", "low", "close", "volume"})
BUILTIN_CALLS = frozenset({"Signal", "min", "max", "abs"})
ALLOWED_NUMBERS = frozenset({0, 1, 14})  # rule 2 whitelist: 0, 1, the standard ATR period
METHOD_ARGS = {"indicators": ("self", "bars"), "signal": ("self", "x")}
_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.FloorDiv, ast.Mod)
_UNARY_OPS = (ast.USub, ast.UAdd, ast.Not)
_CMP_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


@dataclass(frozen=True, slots=True)
class Violation:
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


class _BlockChecker(ast.NodeVisitor):
    """Collects violations in one editable block (already dedented)."""

    def __init__(self, tunables: frozenset[str]) -> None:
        self.tunables = tunables
        self.violations: list[Violation] = []
        self.locals: set[str] = set()
        self.params: tuple[str, ...] = ()

    def fail(self, node: ast.AST, message: str) -> None:
        self.violations.append(Violation(getattr(node, "lineno", 0), message))

    # ── top level ────────────────────────────────────────────────────────────────────
    def check_module(self, tree: ast.Module) -> None:
        seen: list[str] = []
        for i, stmt in enumerate(tree.body):
            if i == 0 and _is_docstring(stmt):
                continue
            if not isinstance(stmt, ast.FunctionDef) or stmt.name not in METHOD_ARGS:
                self.fail(stmt, "only `def indicators(self, bars)` / `def signal(self, x)` allowed")
                continue
            seen.append(stmt.name)
            self.check_method(stmt)
        for name in METHOD_ARGS:
            if seen.count(name) != 1:
                self.fail(tree, f"the block must define `{name}` exactly once")

    def check_method(self, fn: ast.FunctionDef) -> None:
        a = fn.args
        names = tuple(arg.arg for arg in a.args)
        if (
            names != METHOD_ARGS[fn.name]
            or a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs or a.defaults
        ):  # fmt: skip
            self.fail(fn, f"`{fn.name}` must take exactly {METHOD_ARGS[fn.name]}")
        if fn.decorator_list:
            self.fail(fn, "decorators are not allowed")
        self.params = names
        self.locals = {
            t.id for node in ast.walk(fn) if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)
        }  # fmt: skip
        for i, stmt in enumerate(fn.body):
            if i == 0 and _is_docstring(stmt):
                continue
            self.check_stmt(stmt)

    # ── statements ───────────────────────────────────────────────────────────────────
    def check_stmt(self, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.Return):
            if stmt.value is not None:
                self.check_expr(stmt.value)
        elif isinstance(stmt, ast.If):
            self.check_expr(stmt.test)
            for s in [*stmt.body, *stmt.orelse]:
                self.check_stmt(s)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if not isinstance(target, ast.Name) or target.id.startswith("_"):
                    self.fail(stmt, "assign only to plain local names")
                elif target.id in {"self", "bars", "x", "ind", *BUILTIN_CALLS}:
                    self.fail(stmt, f"cannot rebind `{target.id}`")
            self.check_expr(stmt.value)
        elif isinstance(stmt, ast.Pass):
            return
        elif isinstance(stmt, ast.For | ast.While | ast.AsyncFor):
            self.fail(stmt, "loops are not allowed — use whitelisted indicators")
        elif isinstance(stmt, ast.Import | ast.ImportFrom):
            self.fail(stmt, "imports are not allowed")
        elif isinstance(stmt, ast.Global | ast.Nonlocal):
            self.fail(stmt, "global/nonlocal are not allowed")
        else:
            self.fail(stmt, f"statement `{type(stmt).__name__}` is not allowed")

    # ── expressions ──────────────────────────────────────────────────────────────────
    def check_expr(self, node: ast.expr) -> None:
        if isinstance(node, ast.Constant):
            self.check_constant(node)
        elif isinstance(node, ast.Name):
            allowed = {*self.params, *self.locals, "ind", *BUILTIN_CALLS}
            if node.id not in allowed:
                self.fail(node, f"name `{node.id}` is not allowed")
            elif node.id in {"ind", *BUILTIN_CALLS}:
                self.fail(node, f"`{node.id}` may only be called")
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _BIN_OPS):
                self.fail(node, f"operator `{type(node.op).__name__}` is not allowed")
            self.check_expr(node.left)
            self.check_expr(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY_OPS):
                self.fail(node, f"operator `{type(node.op).__name__}` is not allowed")
            if isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
                self.check_constant(node.operand, negative=True)
            else:
                self.check_expr(node.operand)
        elif isinstance(node, ast.BoolOp):
            for v in node.values:
                self.check_expr(v)
        elif isinstance(node, ast.Compare):
            if not all(isinstance(op, _CMP_OPS) for op in node.ops):
                self.fail(node, "only <, <=, >, >=, ==, != comparisons are allowed")
            for v in [node.left, *node.comparators]:
                self.check_expr(v)
        elif isinstance(node, ast.IfExp):
            for v in (node.test, node.body, node.orelse):
                self.check_expr(v)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=True):
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    self.fail(node, "feature names must be string literals")
                self.check_expr(v)
        elif isinstance(node, ast.Call):
            self.check_call(node)
        elif isinstance(node, ast.Attribute):
            self.check_attribute(node)
        elif isinstance(node, ast.Subscript):
            self.check_subscript(node)
        else:
            self.fail(node, f"expression `{type(node).__name__}` is not allowed")

    def check_constant(self, node: ast.Constant, negative: bool = False) -> None:
        v = node.value
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return
        if isinstance(v, int | float):
            if v not in ALLOWED_NUMBERS:
                shown = -v if negative else v
                self.fail(node, f"undeclared constant {shown!r} — declare it as a TUNABLE")
            return
        self.fail(node, f"constant of type {type(v).__name__} is not allowed")

    def check_call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id != "ind":
                self.fail(node, f"call `{func.value.id}.{func.attr}()` is not allowed")
            elif func.attr not in INDICATORS:
                self.fail(node, f"`ind.{func.attr}` is not a whitelisted indicator")
        elif isinstance(func, ast.Attribute) and func.attr == "ago":
            self.check_feature(func.value)
            ok = (
                len(node.args) == 1 and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, int) and not isinstance(node.args[0].value, bool)
                and node.args[0].value >= 0
            )  # fmt: skip
            if not ok:
                self.fail(node, "`.ago(k)` needs a literal k >= 0 (a negative k reads the future)")
            return
        elif isinstance(func, ast.Name):
            if func.id not in BUILTIN_CALLS:
                self.fail(node, f"call `{func.id}()` is not allowed")
        elif isinstance(func, ast.Attribute) and func.attr == "shift":
            self.fail(node, "`.shift()` is not allowed (look-ahead risk)")
            return
        else:
            self.fail(node, "only ind.<indicator>(), Signal(), min(), max(), abs() may be called")
            if isinstance(func, ast.Attribute):  # also report what the chain is built on,
                self.check_expr(func.value)  # e.g. the `.shift(-1)` in `s.shift(-1).to_numpy()`
            return
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                self.fail(arg, "star-arguments are not allowed")
            else:
                self.check_expr(arg)
        for kw in node.keywords:
            if kw.arg is None:
                self.fail(node, "**kwargs are not allowed")
            elif not (isinstance(func, ast.Name) and func.id == "Signal"):
                self.fail(node, "keyword arguments are only allowed for Signal")
            self.check_expr(kw.value)

    def check_attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            self.fail(node, f"private/dunder attribute `{node.attr}` is not allowed")
            return
        v = node.value
        if isinstance(v, ast.Name) and v.id == "bars" and "bars" in self.params:
            if node.attr not in BAR_FIELDS:
                self.fail(node, f"`bars.{node.attr}` is not allowed")
            return
        if (
            isinstance(v, ast.Attribute) and v.attr == "p"
            and isinstance(v.value, ast.Name) and v.value.id == "self"
        ):  # fmt: skip
            if node.attr not in self.tunables:
                self.fail(node, f"`self.p.{node.attr}` is not a declared TUNABLE")
            return
        if node.attr == "now":
            self.check_feature(v)
            return
        if node.attr == "shift":
            self.fail(node, "`.shift` is not allowed (look-ahead risk)")
            return
        self.fail(node, f"attribute `{ast.unparse(node)}` is not allowed")

    def check_subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.value, ast.Name) and node.value.id == "x" and "x" in self.params
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
        ):  # fmt: skip
            return
        self.fail(node, "indexing is not allowed (look-ahead risk) — only x['feature']")

    def check_feature(self, node: ast.expr) -> None:
        if not (
            isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and node.value.id == "x" and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):  # fmt: skip
            self.fail(node, "`.ago()` / `.now` apply only to x['feature']")


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def check_block(body: str, tunables: frozenset[str]) -> list[Violation]:
    """AST-whitelist violations in one editable block."""
    try:
        tree = ast.parse(textwrap.dedent(body))
    except SyntaxError as e:
        return [Violation(e.lineno or 0, f"syntax error: {e.msg}")]
    checker = _BlockChecker(tunables)
    checker.check_module(tree)
    if checker.violations:
        return checker.violations
    # units only on a structurally valid block: scale invariance (arch §3.1.6, §3.1.11 rule 2)
    return [Violation(e.line, e.message) for e in check_units(tree)]


def check_direction(body: str, direction: str) -> list[Violation]:
    """Every ``Signal(...)`` in the block emits this scope's side or ``flat`` (INV-91), at
    strength 1.0 (ADR-0031: strength must not become a second risk lever).

    The arguments are checked as literals. A computed direction or strength is refused outright
    — a scope whose side depends on the data is not a scope, and the whole point of the check is
    that it can be decided before the strategy ever runs.
    """
    try:
        tree = ast.parse(textwrap.dedent(body))
    except SyntaxError:
        return []  # check_block reports the syntax error; do not report it twice
    out: list[Violation] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "Signal" or not node.args:
            continue
        emitted = node.args[0]
        if not (isinstance(emitted, ast.Constant) and isinstance(emitted.value, str)):
            out.append(Violation(node.lineno, "Signal direction must be a literal string"))
            continue
        if emitted.value not in (direction, "flat"):
            out.append(
                Violation(
                    node.lineno,
                    f"direction {emitted.value!r} is not this scope's side "
                    f"({direction!r} or 'flat')",
                )
            )
        if len(node.args) < 2:
            continue
        strength = node.args[1]
        if not (isinstance(strength, ast.Constant) and isinstance(strength.value, int | float)):
            out.append(Violation(node.lineno, "Signal strength must be a literal number"))
        else:
            expected = 0.0 if emitted.value == "flat" else 1.0
            if float(strength.value) != expected:
                out.append(
                    Violation(node.lineno, f"strength must be {expected} for {emitted.value!r}")
                )
    return out


def check_params(tunables: tuple[Tunable, ...], params: Mapping[str, float | int]) -> list[str]:
    problems: list[str] = []
    declared = {t.name: t for t in tunables}
    if set(params) != set(declared):
        problems.append(f"params {sorted(params)} != TUNABLE names {sorted(declared)}")
    for name, value in params.items():
        t = declared.get(name)
        if t is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            problems.append(f"param {name} must be a number")
        elif t.is_int and not isinstance(value, int):
            problems.append(f"param {name} must be an int")
        elif not t.low <= value <= t.high:
            problems.append(f"param {name}={value} outside bounds ({t.low}, {t.high})")
    return problems


class StaticGuardrail:
    """Gate ①a. Needs ``lock['derived']['template_hash']`` and ``lock['research']``."""

    id = G1A_STATIC
    cost = 1

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        def tamper(reason: str) -> GateResult:
            return GateResult(False, self.id, None, reason, event=Event.TEMPLATE_TAMPER)

        def reject(reason: str) -> GateResult:
            return GateResult(False, self.id, None, reason, event=Event.AST_REJECT)

        expected = ctx.lock.get("derived", {}).get("template_hash")
        locked_scope = ctx.lock.get("research", {}).get("evolve_scope")
        if not expected or not locked_scope:
            return tamper("campaign lock has no template_hash / evolve_scope")
        if candidate.evolve_scope != locked_scope:
            return tamper(f"evolve_scope {candidate.evolve_scope!r} != locked {locked_scope!r}")
        try:
            parsed: ParsedStrategy = parse(candidate.source)
            actual = fixed_region_hash(parsed, locked_scope)
        except (TemplateError, TunableError) as e:
            return tamper(str(e))
        if actual != expected:
            return tamper("fixed region differs from the locked template")
        problems = check_params(parsed.tunables, candidate.params)
        if problems:
            return reject("; ".join(problems))
        names = frozenset(t.name for t in parsed.tunables)
        editable = [b for b in parsed.blocks if b.name in parsed.editable(locked_scope)]
        violations = [v for b in editable for v in check_block(b.body, names)]
        violations += [v for b in editable for v in check_direction(b.body, candidate.direction)]
        if violations:
            return GateResult(
                False, self.id, float(len(violations)),
                "; ".join(str(v) for v in violations[:5]),
                detail={"violations": [str(v) for v in violations]},
                event=Event.AST_REJECT,
            )  # fmt: skip
        return GateResult(True, self.id, 0.0, "static checks passed")


class DynamicGuardrail:
    """Gate ①b. Needs ``services['sandbox']`` (a SandboxRunner) and ``services['is_data']``
    (IS bars by symbol); the candidate's universe selects from them."""

    id = G1B_DYNAMIC
    cost = 2

    def __init__(self, cut_points: int = 20, window: int = 20) -> None:
        self.cut_points = cut_points
        self.window = window

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        runner: SandboxRunner = ctx.services["sandbox"]
        data = ctx.services["is_data"]
        missing = [s for s in candidate.universe if s not in data]
        if missing or not candidate.universe:
            return GateResult(False, self.id, None, f"no IS data for {missing or 'empty universe'}")
        options = {"cut_points": self.cut_points, "window": self.window, "seed": candidate.seed}
        job = SandboxJob(
            "leak_check", candidate.source, {s: data[s] for s in candidate.universe},
            candidate.params, options,
        )  # fmt: skip
        res = runner.run(job)
        if not res.ok or res.report is None:
            return sandbox_failure(self.id, res)
        result = res.report["result"]
        n_leaks, checked = int(result["n_leaks"]), int(result["checked"])
        if n_leaks:
            return GateResult(
                False, self.id, float(n_leaks),
                f"look-ahead: {n_leaks} of {checked} signals changed when bars after the cut"
                " were removed or replaced",
                detail={"leaks": result["leaks"]},
                event=Event.LEAK_REJECT,
            )  # fmt: skip
        return GateResult(True, self.id, 0.0, f"{checked} signals unchanged without the future")
