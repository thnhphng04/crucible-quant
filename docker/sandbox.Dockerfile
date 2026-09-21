# Sandbox image for generated strategy code (Architecture §3.3.3, ADR-0004).
# Build context = repository root; .dockerignore lets in only the package sources and lockfile.
# Tag = quantcrucible-sandbox:<hash of this file + lockfile + src> (validation/sandbox.py).

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv venv /opt/venv --python /usr/local/bin/python3.12 \
 && uv export --locked --no-dev --no-emit-project --no-hashes -o requirements.txt \
 && uv pip install --python /opt/venv/bin/python --no-cache -r requirements.txt \
 && uv pip install --python /opt/venv/bin/python --no-cache --no-deps . \
 && /opt/venv/bin/python -m compileall -q /opt/venv

FROM python:3.12-slim
COPY --from=build /opt/venv /opt/venv
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
USER 10001
WORKDIR /tmp
# `env -i`: the runner starts with an EMPTY environment; `-I -B`: isolated mode, no bytecode writes
ENTRYPOINT ["/usr/bin/env", "-i", "/opt/venv/bin/python", "-I", "-B", "-m", "quantcrucible.validation.sandbox_runner"]
CMD ["/job"]
