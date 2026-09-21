# ADR-0004 — Docker sandbox for generated code

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P0-08 · **Arch:** §3.3.3, A6 · **Open item:** O6

## Context

§3.3.3 requires every piece of generated code to run with an empty environment, no network, the IS data read-only, writes only to a temp folder, no view of `holdout/`, `config/`, `ledger/`, a timeout plus RAM and CPU limits, and output returned only as a JSON report with stdout/stderr truncated. On Windows (A6) the architecture says to use a container, not a hand-rolled Python sandbox. How the pieces fit — which code runs inside, how data gets in, what counts as a violation, and what one run costs (O6) — was left open.

## Decision

1. **Image:** `docker/sandbox.Dockerfile` installs the locked non-dev dependencies and the package into `/opt/venv` on `python:3.12-slim`, and runs as a non-root user (uid 10001). The build context is an allow-list (`.dockerignore`): `pyproject.toml`, `uv.lock`, `README.md`, `src/` — no data, results, ledger, config or lock files. The tag is `quantcrucible-sandbox:<hash>` over the Dockerfile, project metadata, lockfile and `src/` (CRLF normalized), so a code change builds a new image (`ensure_image`).
2. **Runner inside:** `validation/sandbox_runner.py` reads `/job/in/{strategy.py, job.json, data/*.parquet}`, runs one job (`signals`, `backtest`; `leak_check` with ①b in P0-11) and writes `/job/out/report.json`. Every failure, `SystemExit` included, becomes an error report. An import-linter contract keeps the ledger, config, gates and host-side sandbox code out of it.
3. **Empty environment:** the entrypoint is `env -i /opt/venv/bin/python -I -B -m …`. This is stricter than §3.3.3's "minimal `PYTHONPATH`": the package is installed, so no path is needed. Python itself sets `LC_CTYPE` at startup (PEP 538); nothing is inherited from the host.
4. **Container flags:** `--rm --network none --read-only --tmpfs /tmp (256 MB) --memory/--memory-swap 2g --cpus 1 --pids-limit 256 --cap-drop ALL --security-opt no-new-privileges --ipc none`; on a Linux host also `--user <uid>:<gid>`. Exactly two mounts: the job's `in/` (read-only) and `out/` (read-write), both in a fresh temp folder that is deleted after the run.
5. **Data in:** the host writes only the job's IS slice into `in/data/` — the data folder itself is never mounted.
6. **Output:** stdout and stderr are drained by threads that keep the first 4 KB each (endless output cannot fill host memory); the report must be JSON and at most 64 MB.
7. **Violations:** timeout (the container is `docker kill`ed), exit code 137 (memory or PID limit), a missing report, or a strategy exception that is an `OSError` (it touched the network or the file system) → `SandboxResult.violation`. Gates turn it into a rejection with `sandbox_failure()`, logged as `SANDBOX_VIOLATION`; an ordinary exception in the strategy keeps the gate's own reject event.
8. **Cost (O6), measured on Windows 11 + Docker Desktop (WSL2):** container start ≈ 0.6 s; a typical job 2.5–4 s. The NautilusTrader import (≈ 2 s) happens only in `backtest` jobs. Enough for phase 0.

## Consequences

- Generated code is executed only by the runner inside a container; `load_strategy_class` on the host stays limited to trusted source (zoo, tests).
- CI gets a second job, `sandbox`, which builds the image and runs `pytest -m docker` (INV-36).
- A cold image build takes about 2.5 minutes; source-only changes reuse the dependency layer.

## Alternatives considered

- **Long-lived worker container** with a fresh temp folder per job: deferred to phase 2, if per-job start-up becomes the bottleneck; the limits would stay identical.
- **gVisor / `runsc`:** stronger isolation, but not available on Docker Desktop for Windows.
- **Python-level sandbox (restricted builtins, audit hooks):** rejected by §3.3.3 and easy to escape.
