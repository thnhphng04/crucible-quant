"""PBO via CSCV (§3.2, 07 §4.2, INV-41, ADR-0011)."""

from __future__ import annotations

import ast
import importlib.metadata
import math
from pathlib import Path

import numpy as np
import purgedcv
import pytest

from quantcrucible.validation import pbo as pbo_module
from quantcrucible.validation.pbo import pbo

# The independent implementation PBO is checked against (arch §7 phase gate, ADR-0023). A new
# version is a new reference: re-review the cross-check before changing this pin.
PURGEDCV_VERSION = "0.1.6"


def test_pbo_reference_example() -> None:
    """Hand-computed from the definition in Bailey, Borwein, López de Prado & Zhu (2017), with
    S = 2 blocks: combination 1 = (IS: block 0, OOS: block 1), combination 2 = the reverse.

    Config A wins block 0 and is worst in block 1; config B is mediocre in both; config C is
    worst in block 0 and wins block 1 — the textbook overfit: the IS winner is always the OOS
    loser, rank 1 of 3 ⇒ ω = 1/4, logit = ln(1/3) < 0 in both combinations ⇒ PBO = 1.
    """
    up = [0.02, 0.01, 0.03, 0.01]
    flat = [0.001, 0.002, 0.0, 0.001]
    down = [-0.02, -0.01, -0.03, -0.01]
    a = up + down
    b = flat + flat
    c = down + up
    result = pbo(np.array([a, b, c]), n_splits=2)
    assert result.n_combos == 2 and result.pbo == 1.0
    np.testing.assert_allclose(result.logits, [math.log(1 / 3)] * 2)
    # the reverse ordering of fortunes: the IS winner stays the winner ⇒ ω = 3/4 ⇒ PBO = 0
    stable = pbo(np.array([up + up, flat + flat, down + down]), n_splits=2)
    assert stable.pbo == 0.0
    np.testing.assert_allclose(stable.logits, [math.log(3)] * 2)


def test_pbo_is_independent_of_purgedcv() -> None:
    """The cross-check below verifies something only if our PBO does not call the library it is
    compared with: `validation/pbo.py` imports numpy/scipy only, no purgedcv, no project code."""
    tree = ast.parse(Path(pbo_module.__file__ or "").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {m for m in imported if m.split(".")[0] in {"purgedcv", "quantcrucible"}}


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_matches_purgedcv(seed: int) -> None:
    """Same input, same CSCV result as an independent implementation (purgedcv, pinned)."""
    assert importlib.metadata.version("purgedcv") == PURGEDCV_VERSION
    m = np.random.default_rng(seed).normal(0.0002, 0.01, (25, 480))
    m[3] = 0.0  # a configuration that never trades
    ours = pbo(m, n_splits=8)
    ref = purgedcv.probability_of_backtest_overfitting(m, n_splits=8)
    assert ours.pbo == ref.pbo
    np.testing.assert_allclose(np.sort(ours.logits), np.sort(ref.logits), atol=1e-12)
    assert ours.slope == pytest.approx(ref.slope, rel=1e-9)


def test_pbo_noise() -> None:
    """No edge: the IS winner lands at a random OOS rank ⇒ PBO ≈ 0.5, not → 1 (07 §4.2)."""
    values = [
        pbo(np.random.default_rng(s).normal(0, 0.01, (50, 1600)), n_splits=16).pbo for s in range(8)
    ]
    assert float(np.mean(values)) == pytest.approx(0.5, abs=0.1)


def test_pbo_planted_edge() -> None:
    m = np.random.default_rng(10).normal(0, 0.01, (50, 1600))
    m[:5] += 0.002  # five configurations with a real, persistent edge
    assert pbo(m, n_splits=16).pbo < 0.05


def test_pbo_negative_is_oos_link_exceeds_half() -> None:
    """Configurations that fit one regime and invert in the other ⇒ PBO > 0.5."""
    rng = np.random.default_rng(11)
    n, t = 40, 1600
    tilt = np.linspace(-0.001, 0.001, n)[:, None]
    regime = np.where((np.arange(t) // 100) % 2 == 0, 1.0, -1.0)  # alternates every 100 bars
    m = rng.normal(0, 0.01, (n, t)) + tilt * regime
    assert pbo(m, n_splits=16).pbo > 0.5


def test_s16_is_fast() -> None:
    import time

    m = np.random.default_rng(12).normal(0, 0.01, (200, 2800))
    start = time.perf_counter()
    result = pbo(m, n_splits=16)
    assert result.n_combos == 12_870
    assert time.perf_counter() - start < 20


def test_invalid_inputs() -> None:
    m = np.zeros((3, 100))
    with pytest.raises(ValueError, match="2 configurations"):
        pbo(m[:1])
    with pytest.raises(ValueError, match="even"):
        pbo(m, n_splits=5)
    with pytest.raises(ValueError, match="too few"):
        pbo(m, n_splits=64)
    bad = np.ones((3, 100))
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        pbo(bad, n_splits=4)
