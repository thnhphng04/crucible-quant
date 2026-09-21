"""Shared fixtures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from quantcrucible.validation.sandbox import ensure_image

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def sandbox_image() -> str:
    """The sandbox image for the current sources, built on first use (docker-marked tests)."""
    probe = subprocess.run(["docker", "info"], capture_output=True, check=False)
    if probe.returncode != 0:
        if os.environ.get("CI"):
            pytest.fail("Docker is required for the docker-marked tests in CI")
        pytest.skip("Docker daemon not running")
    return ensure_image(ROOT)
