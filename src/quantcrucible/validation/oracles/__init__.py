"""Leaky-oracle suite, 4 levels — the harness must reject all of them (07-VALIDATION-LAYER §9).

Each oracle is a strategy in template form that cheats in a different way. They are hand-written
and trusted: tests may load them on the host.
"""

from __future__ import annotations

from importlib.resources import files

ORACLES: dict[int, str] = {
    1: "level1_shift.py",
    2: "level2_forming_candle.py",
    3: "level3_repaint.py",
    4: "level4_late_info.py",
}


def oracle_source(level: int) -> str:
    return files(__package__).joinpath(ORACLES[level]).read_text(encoding="utf-8")
