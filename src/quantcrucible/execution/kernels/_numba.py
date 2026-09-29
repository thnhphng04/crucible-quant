"""The one place numba is imported (INV-111, ADR-0038).

Every kernel is written once in Python and compiled for two targets: ``cpu`` (``numba.njit``,
parallel) and ``cuda`` (``numba.cuda.jit``). Floating-point arithmetic goes through the
:class:`Arith` helpers so the two builds round alike: on the CPU, LLVM does not contract without
fastmath, so plain operators are exact; on CUDA, NVVM would fuse ``a*b + c`` into an FMA, so every
op is libdevice's non-contracted ``*_rn`` (E7). numba-cuda has no ``dsub_rn``/``fsub_rn`` pair
for both widths, so ``a - b`` is ``a + (-b)`` — negation is exact.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import numba as _numba
import numpy as np
from numba import cuda as _cuda
from numba.cuda import libdevice as _libdevice

# numba ships partial type hints; the kernels are typed by numba, not mypy, so the thin wrapper
# hands them out untyped (02-CONVENTIONS: third-party APIs go through one wrapper module).
numba: Any = _numba
cuda: Any = _cuda
libdevice: Any = _libdevice

Target = Literal["cpu", "cuda"]
Dtype = Literal["float64", "float32"]
Fn = Callable[..., Any]


def simulator() -> bool:
    """True when numba runs CUDA kernels in its Python simulator (``NUMBA_ENABLE_CUDASIM=1``)."""
    return os.environ.get("NUMBA_ENABLE_CUDASIM") == "1"


def cuda_available() -> bool:
    try:
        return bool(cuda.is_available())
    except Exception:  # a broken driver is "no GPU", never a crash
        return False


def device(target: Target) -> Callable[[Fn], Fn]:
    """Decorator for a helper called from kernels of ``target``."""
    if target == "cpu":
        return lambda fn: numba.njit(fn)
    return lambda fn: cuda.jit(device=True)(fn)


def cpu_kernel(fn: Fn) -> Any:
    return numba.njit(parallel=True)(fn)


def cuda_kernel(fn: Fn) -> Any:
    return cuda.jit(fn)


prange: Any = numba.prange


@dataclass(frozen=True)
class Arith:
    """Scalar type, constants and exactly-rounded ops for one (target, dtype)."""

    T: type[np.floating[Any]]
    add: Fn
    sub: Fn
    mul: Fn
    div: Fn
    sqrt: Fn
    nan: Any
    inf: Any


def arith(target: Target, dtype: Dtype) -> Arith:
    t: type[np.floating[Any]] = np.float64 if dtype == "float64" else np.float32
    dev = device(target)
    nan, inf = t(math.nan), t(math.inf)
    # The CUDA simulator runs kernels as Python, which never contracts — and its libdevice is
    # stubs returning None — so it takes the plain operators like the CPU build.
    if target == "cpu" or simulator():

        @dev
        def add(a: Any, b: Any) -> Any:
            return a + b

        @dev
        def sub(a: Any, b: Any) -> Any:
            return a - b

        @dev
        def mul(a: Any, b: Any) -> Any:
            return a * b

        @dev
        def div(a: Any, b: Any) -> Any:
            return a / b

        @dev
        def sqrt(a: Any) -> Any:
            return np.sqrt(a)

        return Arith(t, add, sub, mul, div, sqrt, nan, inf)
    if dtype == "float64":

        @dev
        def add64(a: Any, b: Any) -> Any:
            return libdevice.dadd_rn(a, b)

        @dev
        def sub64(a: Any, b: Any) -> Any:
            return libdevice.dadd_rn(a, -b)

        @dev
        def mul64(a: Any, b: Any) -> Any:
            return libdevice.dmul_rn(a, b)

        @dev
        def div64(a: Any, b: Any) -> Any:
            return libdevice.ddiv_rn(a, b)

        @dev
        def sqrt64(a: Any) -> Any:
            return libdevice.dsqrt_rn(a)

        return Arith(t, add64, sub64, mul64, div64, sqrt64, nan, inf)

    @dev
    def add32(a: Any, b: Any) -> Any:
        return libdevice.fadd_rn(a, b)

    @dev
    def sub32(a: Any, b: Any) -> Any:
        return libdevice.fadd_rn(a, -b)

    @dev
    def mul32(a: Any, b: Any) -> Any:
        return libdevice.fmul_rn(a, b)

    @dev
    def div32(a: Any, b: Any) -> Any:
        return libdevice.fdiv_rn(a, b)

    @dev
    def sqrt32(a: Any) -> Any:
        return libdevice.fsqrt_rn(a)

    return Arith(t, add32, sub32, mul32, div32, sqrt32, nan, inf)
