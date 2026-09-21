"""TUNABLE declarations and the PBO grid (Architecture §3.3.1 rule 2, §3.2, D13) — INV-31."""

import pytest

from quantcrucible.core.strategy.tunable import (
    Tunable,
    TunableError,
    default_params,
    parse_tunables,
    pbo_grid,
)


def test_parse_types() -> None:
    ts = parse_tunables(
        "# TUNABLE: fast = 20, bounds=(5, 60)\n"
        "    #TUNABLE: k=2.0,bounds=( 1.0 , 4.0 )\n"
        "x = 1  # not a declaration\n"
    )
    assert ts == [Tunable("fast", 20, 5, 60, True), Tunable("k", 2.0, 1.0, 4.0, False)]
    assert default_params(ts) == {"fast": 20, "k": 2.0}
    assert isinstance(default_params(ts)["fast"], int)


def test_seven_tunables_rejected() -> None:
    src = "".join(f"# TUNABLE: p{i} = 1, bounds=(0, 2)\n" for i in range(7))
    with pytest.raises(TunableError, match="at most 6"):
        parse_tunables(src)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("# TUNABLE: a = 5, bounds=(6, 9)", "outside bounds"),
        ("# TUNABLE: a = 5, bounds=(9, 1)", "low < high"),
        ("# TUNABLE: a = 5, bounds=(0, inf)", "malformed"),
        ("# TUNABLE: a = 5", "malformed"),
        ("# TUNABLE: 1a = 5, bounds=(0, 9)", "malformed"),
        ("# TUNABLE: a = 5, bounds=(0, 1e400)", "finite"),
    ],
)
def test_bad_declarations(line: str, message: str) -> None:
    with pytest.raises(TunableError, match=message):
        parse_tunables(line)


def test_duplicate_names_rejected() -> None:
    with pytest.raises(TunableError, match="duplicate"):
        parse_tunables("# TUNABLE: a = 1, bounds=(0, 2)\n# TUNABLE: a = 1, bounds=(0, 2)\n")


def test_pbo_grid_values_around_default() -> None:
    ts = [Tunable("fast", 20, 5, 60, True), Tunable("k", 2.0, 1.0, 4.0, False)]
    grid = pbo_grid(ts, values_per_param=5, range_frac=0.3, max_configs=200, seed=0)
    fast = sorted({c["fast"] for c in grid})
    k = sorted({c["k"] for c in grid})
    assert fast == [14, 17, 20, 23, 26]
    assert k == pytest.approx([1.4, 1.7, 2.0, 2.3, 2.6])
    assert len(grid) == 25
    assert {"fast": 20, "k": 2.0} in grid


def test_pbo_grid_clips_to_bounds() -> None:
    grid = pbo_grid([Tunable("a", 10, 9, 60, True)], 5, 0.3, 200, seed=0)
    assert min(c["a"] for c in grid) == 9


def test_pbo_grid_sampling_is_seeded_and_keeps_default() -> None:
    ts = [Tunable(f"p{i}", 10, 1, 100, True) for i in range(4)]  # 5^4 = 625 configs
    g1 = pbo_grid(ts, 5, 0.3, 200, seed=7)
    g2 = pbo_grid(ts, 5, 0.3, 200, seed=7)
    assert g1 == g2 and len(g1) == 200
    assert g1[0] == {f"p{i}": 10 for i in range(4)}
    assert pbo_grid(ts, 5, 0.3, 200, seed=8) != g1


def test_no_tunables_single_config() -> None:
    assert pbo_grid([], 5, 0.3, 200, seed=0) == [{}]
