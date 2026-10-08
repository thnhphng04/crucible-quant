"""The indicator-correlation constraint gives the same bits on every platform (ADR-0038, INV-105).

`np.corrcoef` goes through a BLAS matrix product, and the Windows and Linux builds of the same
numpy disagree in its last bit. The kernel engine computes gate ③'s report on the host and the
sandbox inside Linux, so the L2 audit saw a 1-ulp `indicator_corr` difference on the first real
bracket campaign, refused both kernel builds and sent the rest of the campaign to the sandbox.
The correlation is therefore written out with sums, a square root and a division: IEEE-exact
operations in a fixed order.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from scipy import stats

from quantcrucible.core.strategy import registry
from quantcrucible.validation.feature_stats import max_abs_change_corr


def _features(seed: int) -> dict[str, npt.NDArray[np.float64]]:
    rng = np.random.default_rng(seed)
    close = np.asarray(100 * np.cumprod(1 + rng.normal(0.0003, 0.02, 3_000)), dtype=np.float64)
    return {
        "ema20": registry.ema(close, 20),
        "sma50": registry.sma(close, 50),
        "rsi14": registry.rsi(close, 14),
        "z20": registry.zscore(close, 20),
    }


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_the_correlation_is_pearsons(seed: int) -> None:
    """Checked against an independent implementation, not against itself."""
    feats = _features(seed)
    rho, (a, b) = max_abs_change_corr(feats)  # type: ignore[misc]
    x, y = np.diff(feats[a]), np.diff(feats[b])
    ok = np.isfinite(x) & np.isfinite(y)
    assert rho == pytest.approx(abs(stats.pearsonr(x[ok], y[ok]).statistic), rel=1e-12)


def test_no_blas_product_is_involved(monkeypatch: pytest.MonkeyPatch) -> None:
    """The functions whose last bit depends on the platform's BLAS are not called."""

    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("platform-dependent BLAS call")

    for name in ("corrcoef", "cov", "dot", "matmul", "inner", "vdot"):
        monkeypatch.setattr(np, name, refuse)
    rho, pair = max_abs_change_corr(_features(0))
    assert 0.0 < rho <= 1.0 and pair is not None


def test_a_perfect_linear_relation_is_one_and_a_constant_is_skipped() -> None:
    feats = _features(3)
    x = feats["ema20"]
    assert max_abs_change_corr({"a": x, "b": -3 * x + 7})[0] == pytest.approx(1.0, abs=1e-15)
    assert max_abs_change_corr({"a": x, "flat": np.ones_like(x)}) == (0.0, None)


PROBE = """
import json, sys
import numpy as np
from quantcrucible.validation.feature_stats import max_abs_change_corr
d = np.load("/w/features.npz")
seeds = sorted({k.split(":")[0] for k in d.files})
print(json.dumps([
    max_abs_change_corr({k.split(":")[1]: d[k] for k in d.files if k.startswith(s + ":")})[0].hex()
    for s in seeds
]))
"""


@pytest.mark.docker
def test_the_sandbox_and_the_host_agree_bit_for_bit(tmp_path: Path, sandbox_image: str) -> None:
    """The L2 audit compares the two with `==` on the float's bytes."""
    arrays = {f"{s:02d}:{k}": v for s in range(12) for k, v in _features(s).items()}
    np.savez(tmp_path / "features.npz", **arrays)  # type: ignore[arg-type]
    # pytest's tmp_path is 0700: on a Linux host the image's own user cannot read it, so run as
    # the folder's owner, as the sandbox itself does (validation/sandbox.py)
    user: list[str] = []
    if sys.platform != "win32":
        user = ["--user", f"{os.getuid()}:{os.getgid()}"]
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", *user,
         "--entrypoint", "/opt/venv/bin/python",
         "-v", f"{tmp_path}:/w:ro", sandbox_image, "-c", PROBE],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    host = [max_abs_change_corr(_features(s))[0].hex() for s in range(12)]
    assert json.loads(out.stdout) == host
