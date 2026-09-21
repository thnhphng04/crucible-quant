"""Template: fixed region + EVOLVE-BLOCK (Architecture §3.3.1) — INV-30."""

from importlib.resources import files

import pytest

from quantcrucible.core.strategy.base import generate_signals
from quantcrucible.core.strategy.template import (
    TemplateError,
    canonical_template,
    fixed_region_hash,
    load_strategy_class,
    parse,
    render,
    template_hash,
)
from quantcrucible.core.strategy.tunable import default_params
from tests.factories import make_bars


def zoo_source(name: str) -> str:
    return files("quantcrucible.core.zoo").joinpath(f"{name}.py").read_text("utf-8")


MODULAR = """# fixed header
class GeneratedStrategy(Strategy):
    # ═══ EVOLVE-BLOCK-START: entry ═══
    # TUNABLE: a = 1, bounds=(0, 2)
    x = 1
    # ═══ EVOLVE-BLOCK-END: entry ═══
    # ═══ EVOLVE-BLOCK-START: exit ═══
    y = 2
    # ═══ EVOLVE-BLOCK-END ═══
# fixed footer
"""


def test_round_trip_canonical() -> None:
    src = canonical_template()
    parsed = parse(src)
    assert [b.name for b in parsed.blocks] == ["joint"]
    assert "".join((parsed.fixed[0], parsed.blocks[0].body, *parsed.fixed[1:])) == src
    assert render(src, {}) == src


def test_zoo_strategy_matches_template_fixed_region() -> None:
    parsed = parse(zoo_source("ema_crossover"))
    assert fixed_region_hash(parsed, "joint") == template_hash("joint")
    assert default_params(list(parsed.tunables)) == {"fast": 20, "slow": 100, "k_atr": 2.0}


def test_byte_flip_changes_hash() -> None:
    src = zoo_source("ema_crossover")
    tampered = src.replace("import Bars,", "import Bars ,", 1)
    assert fixed_region_hash(parse(tampered), "joint") != template_hash("joint")


def test_editing_the_block_keeps_the_hash() -> None:
    src = render(canonical_template(), {"joint": "    # TUNABLE: k = 3, bounds=(1, 9)\n"})
    assert fixed_region_hash(parse(src), "joint") == template_hash("joint")


def test_evolve_scope_locks_other_blocks() -> None:
    parsed = parse(MODULAR)
    assert parsed.editable("joint") == {"entry", "exit"}
    entry_only = fixed_region_hash(parsed, "entry")
    edited_exit = parse(render(MODULAR, {"exit": "    y = 3\n"}))
    edited_entry = parse(render(MODULAR, {"entry": "    x = 5\n"}))
    assert fixed_region_hash(edited_exit, "entry") != entry_only  # exit is locked
    assert fixed_region_hash(edited_entry, "entry") == entry_only
    with pytest.raises(TemplateError, match="evolve_scope"):
        parsed.editable("regime")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("class GeneratedStrategy(Strategy):\n    pass\n", "whole-file replacement"),
        ("# ═══ EVOLVE-BLOCK-START ═══\nx = 1\n", "never closed"),
        ("# ═══ EVOLVE-BLOCK-END ═══\n", "without START"),
        (
            "# ═══ EVOLVE-BLOCK-START ═══\n# ═══ EVOLVE-BLOCK-START ═══\n"
            "# ═══ EVOLVE-BLOCK-END ═══\n",
            "nested",
        ),
        (
            "# ═══ EVOLVE-BLOCK-START ═══\n# ═══ EVOLVE-BLOCK-END ═══\n"
            "# ═══ EVOLVE-BLOCK-START ═══\n# ═══ EVOLVE-BLOCK-END ═══\n",
            "duplicate",
        ),
        ("# ═══ EVOLVE-BLOCK-START: sizing ═══\n# ═══ EVOLVE-BLOCK-END ═══\n", "unknown block"),
        ("# === EVOLVE-BLOCK-START ===\n", "malformed"),
        (
            "# ═══ EVOLVE-BLOCK-START: entry ═══\n# ═══ EVOLVE-BLOCK-END: exit ═══\n",
            "closes",
        ),
    ],
)
def test_structure_errors(source: str, message: str) -> None:
    with pytest.raises(TemplateError, match=message):
        parse(source)


def test_zoo_strategy_runs() -> None:
    src = zoo_source("ema_crossover")
    cls = load_strategy_class(src)
    params = default_params(list(parse(src).tunables))
    signals = generate_signals(cls(params), make_bars(400, seed=3, drift=0.001), lookback=400)
    assert {s.direction for s in signals} == {"flat", "long"}


def test_load_requires_generated_strategy() -> None:
    with pytest.raises(TemplateError, match="GeneratedStrategy"):
        load_strategy_class("x = 1\n")
