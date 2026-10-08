"""Shared fixtures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from quantcrucible.validation.sandbox import ensure_image

ROOT = Path(__file__).resolve().parents[1]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """``gpu`` tests need a device; ``cudasim`` tests need the simulator switched on before numba
    is imported (a separate run: ``NUMBA_ENABLE_CUDASIM=1 pytest -m cudasim``). The two exclude
    each other: under the simulator there is no real device to test."""
    simulated = os.environ.get("NUMBA_ENABLE_CUDASIM") == "1"
    wants_gpu = any("gpu" in item.keywords for item in items)
    device = False
    if wants_gpu and not simulated:
        try:
            from quantcrucible.execution.kernels._numba import cuda_available

            device = cuda_available()
        except ImportError:  # the gpu dependency group is not installed
            device = False
    for item in items:
        if "gpu" in item.keywords and not device:
            item.add_marker(pytest.mark.skip(reason="no CUDA device (or running the simulator)"))
        if "cudasim" in item.keywords and not simulated:
            item.add_marker(pytest.mark.skip(reason="set NUMBA_ENABLE_CUDASIM=1 to simulate CUDA"))


@pytest.fixture(autouse=True)
def _one_thread_per_simulated_block(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The simulator runs every CUDA thread of a block as a Python thread, and they contend for
    its locks at each device-function call: on Linux a 128-thread block took the cudasim run
    from about a minute to over half an hour. Every lane is independent (local arrays only, no
    shared memory or barriers), so one thread per block computes the same values."""
    if "cudasim" not in request.keywords:
        return
    from quantcrucible.execution.kernels import features, replay, signals

    for module in (features, replay, signals):
        monkeypatch.setattr(module, "THREADS", 1)


@pytest.fixture(scope="session")
def sandbox_image() -> str:
    """The sandbox image for the current sources, built on first use (docker-marked tests)."""
    probe = subprocess.run(["docker", "info"], capture_output=True, check=False)
    if probe.returncode != 0:
        if os.environ.get("CI"):
            pytest.fail("Docker is required for the docker-marked tests in CI")
        pytest.skip("Docker daemon not running")
    return ensure_image(ROOT)
