"""P3-28 toolchain spike: does numba-cuda work on this Windows laptop GPU?"""

import math
import sys
import time

import numba
import numba_cuda
import numpy as np
from numba import cuda, njit
from numba.cuda import libdevice

print("python", sys.version.split()[0])


print("numba", numba.__version__, "numba_cuda", numba_cuda.__version__)
print("available:", cuda.is_available())
cuda.detect()
dev = cuda.get_current_device()
print("device", dev.name, "cc", dev.compute_capability)
free, total = cuda.current_context().get_memory_info()
print(f"vram free {free / 2**20:.0f} MiB / {total / 2**20:.0f} MiB")
print("driver version", cuda.cudadrv.driver.driver.get_version())
print("runtime version", cuda.runtime.get_version())


@cuda.jit(cache=True)
def ema_kernel(x, alpha, out):
    i = cuda.grid(1)
    if i >= out.size:
        return
    y = x[0]
    for k in range(1, i + 1):
        y = libdevice.dadd_rn(libdevice.dmul_rn(1.0 - alpha, y), libdevice.dmul_rn(alpha, x[k]))
    out[i] = y


@cuda.jit
def plain_kernel(x, alpha, out):
    i = cuda.grid(1)
    if i >= out.size:
        return
    y = x[0]
    for k in range(1, i + 1):
        y = (1.0 - alpha) * y + alpha * x[k]
    out[i] = y


@cuda.jit
def f32_kernel(x, alpha, out):
    i = cuda.grid(1)
    if i >= out.size:
        return
    a = np.float32(alpha)
    y = x[0]
    for k in range(1, i + 1):
        y = (np.float32(1.0) - a) * y + a * x[k]
    out[i] = y


@cuda.jit
def nextafter_kernel(x, out):
    i = cuda.grid(1)
    if i < x.size:
        out[i] = libdevice.nextafter(x[i], math.inf)


@njit(cache=True)
def ema_cpu(x, alpha, out):
    for i in range(out.size):
        y = x[0]
        for k in range(1, i + 1):
            y = (1.0 - alpha) * y + alpha * x[k]
        out[i] = y


n = 4096
rng = np.random.default_rng(0)
x = 100 + np.cumsum(rng.normal(0, 1, n))
alpha = 2.0 / 21.0
threads = 128
blocks = (n + threads - 1) // threads

ref = np.empty(n)
ema_cpu(x, alpha, ref)
# python reference (same op order)
py = np.empty(n)
for i in range(n):
    y = x[0]
    for k in range(1, i + 1):
        y = (1.0 - alpha) * y + alpha * x[k]
    py[i] = y
print("njit == python bitwise:", bool(np.array_equal(ref, py)))

d_x = cuda.to_device(x)
for name, kern in (("strict _rn", ema_kernel), ("plain", plain_kernel)):
    out = cuda.device_array(n)
    t0 = time.perf_counter()
    kern[blocks, threads](d_x, alpha, out)
    cuda.synchronize()
    t1 = time.perf_counter()
    kern[blocks, threads](d_x, alpha, out)
    cuda.synchronize()
    t2 = time.perf_counter()
    got = out.copy_to_host()
    mism = int((got != py).sum())
    print(
        f"{name}: first {t1 - t0:.3f}s warm {1e3 * (t2 - t1):.2f}ms bit-mismatches {mism}/{n}"
        f" max rel {np.max(np.abs(got - py) / np.abs(py)):.2e}"
    )

out32 = cuda.device_array(n, dtype=np.float32)
f32_kernel[blocks, threads](cuda.to_device(x.astype(np.float32)), alpha, out32)
got32 = out32.copy_to_host()
print(f"fp32 max rel vs fp64: {np.max(np.abs(got32 - py) / np.abs(py)):.2e}")

vals = np.array([1.1, 100.0, 12345.678])
na = cuda.device_array(3)
nextafter_kernel[1, 32](cuda.to_device(vals), na)
print(
    "nextafter matches numpy:", bool(np.array_equal(na.copy_to_host(), np.nextafter(vals, np.inf)))
)


# empty-kernel launch latency
@cuda.jit
def noop(a):
    pass


d = cuda.device_array(1)
noop[1, 1](d)
cuda.synchronize()
t0 = time.perf_counter()
for _ in range(200):
    noop[1, 1](d)
cuda.synchronize()
print(f"launch latency {1e6 * (time.perf_counter() - t0) / 200:.1f} us")
