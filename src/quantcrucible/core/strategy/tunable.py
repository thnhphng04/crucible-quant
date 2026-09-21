"""``# TUNABLE: name = value, bounds=(low, high)`` declarations (Architecture §3.3.1 rule 2).

Every free parameter of a strategy is declared this way: at most :data:`MAX_TUNABLES`, finite
bounds, default inside the bounds. The declarations feed the PBO grid (§3.2), the ``n_params``
complexity count and the calibration space (§3.2.1 step 5b).
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass

import numpy as np

MAX_TUNABLES = 6

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TUNABLE = re.compile(
    rf"^\s*#\s*TUNABLE:\s*(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<default>{_NUM})\s*,"
    rf"\s*bounds\s*=\s*\(\s*(?P<low>{_NUM})\s*,\s*(?P<high>{_NUM})\s*\)\s*$"
)
_TUNABLE_PREFIX = re.compile(r"^\s*#\s*TUNABLE\b")


class TunableError(ValueError):
    """A TUNABLE declaration is malformed or breaks a rule."""


def _is_int_literal(text: str) -> bool:
    return re.fullmatch(r"[-+]?\d+", text) is not None


@dataclass(frozen=True, slots=True)
class Tunable:
    name: str
    default: float
    low: float
    high: float
    is_int: bool

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.default, self.low, self.high)):
            raise TunableError(f"{self.name}: bounds and default must be finite")
        if not self.low < self.high:
            raise TunableError(f"{self.name}: bounds must satisfy low < high")
        if not self.low <= self.default <= self.high:
            raise TunableError(f"{self.name}: default {self.default} outside bounds")

    @property
    def value(self) -> float | int:
        return int(self.default) if self.is_int else self.default


def parse_tunables(source: str) -> list[Tunable]:
    """All TUNABLE declarations in ``source``, in order. Raises on any malformed line."""
    found: list[Tunable] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if not _TUNABLE_PREFIX.match(line):
            continue
        m = _TUNABLE.match(line)
        if m is None:
            raise TunableError(f"line {lineno}: malformed TUNABLE declaration: {line.strip()!r}")
        texts = (m["default"], m["low"], m["high"])
        found.append(
            Tunable(
                name=m["name"],
                default=float(m["default"]),
                low=float(m["low"]),
                high=float(m["high"]),
                is_int=all(_is_int_literal(t) for t in texts),
            )
        )
    names = [t.name for t in found]
    if len(set(names)) != len(names):
        raise TunableError(f"duplicate TUNABLE names: {sorted(names)}")
    if len(found) > MAX_TUNABLES:
        raise TunableError(f"{len(found)} TUNABLE declarations; at most {MAX_TUNABLES} allowed")
    return found


def default_params(tunables: list[Tunable]) -> dict[str, float | int]:
    return {t.name: t.value for t in tunables}


def _grid_values(t: Tunable, values_per_param: int, range_frac: float) -> list[float | int]:
    a, b = sorted((t.default * (1 - range_frac), t.default * (1 + range_frac)))
    lo, hi = max(t.low, a), min(t.high, b)
    raw = [*np.linspace(lo, hi, values_per_param).tolist(), t.default]
    vals: list[float | int] = [round(v) if t.is_int else float(v) for v in raw]
    return sorted(set(vals))


def pbo_grid(
    tunables: list[Tunable],
    values_per_param: int,
    range_frac: float,
    max_configs: int,
    seed: int,
) -> list[dict[str, float | int]]:
    """Pre-registered configuration set for gate ④ (§3.2, D13).

    Each parameter takes ``values_per_param`` values within ±``range_frac`` of its default
    (clipped to its bounds, default always included). If the cartesian product exceeds
    ``max_configs`` it is sampled with ``seed``; the default configuration is always kept.
    """
    if not tunables:
        return [{}]
    axes = [_grid_values(t, values_per_param, range_frac) for t in tunables]
    names = [t.name for t in tunables]
    configs = [dict(zip(names, combo, strict=True)) for combo in itertools.product(*axes)]
    if len(configs) <= max_configs:
        return configs
    default = default_params(tunables)
    others = [c for c in configs if c != default]
    rng = np.random.default_rng(seed)
    picked = rng.choice(len(others), size=max_configs - 1, replace=False)
    return [default, *(others[i] for i in sorted(picked))]
