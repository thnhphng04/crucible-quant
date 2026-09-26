"""Integrity record for a downloaded data set (Architecture §6.1, P3-09).

The spot in-sample parquet has no manifest and no checksum: the only integrity record in the
project is ``holdout.lock``. Re-deriving which exchange produced ``data/is/BTC-USDT_1d.parquet``
means reading ``config/user.yaml``, which can drift from what was actually downloaded.

Perpetual data cannot afford that, because it carries two series the backtest cannot reconstruct
from price — mark prices and funding — and a silently missing window in either would change every
result without changing any error message.

So a manifest records what was fetched (coverage per instrument), where from (the exchange id),
and the SHA256 of each file. Same JSON shape and newline handling as ``holdout.lock``, so the
bytes are identical on every OS.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Manifest:
    source: str  # exchange id, e.g. "binanceusdm"
    created_at: str
    coverage: dict[str, str]  # instrument -> "start/end", end exclusive
    files: dict[str, str]  # file name -> sha256 hex

    @classmethod
    def read(cls, path: Path) -> Manifest:
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            source=str(d["source"]),
            created_at=str(d["created_at"]),
            coverage=dict(d["coverage"]),
            files=dict(d["files"]),
        )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(
    path: Path, files: Sequence[Path], source: str, coverage: Mapping[str, str]
) -> Manifest:
    manifest = Manifest(
        source=source,
        created_at=datetime.now(UTC).isoformat(),
        coverage=dict(sorted(coverage.items())),
        files={f.name: sha256_file(f) for f in sorted(files, key=lambda p: p.name)},
    )
    payload = {
        "source": manifest.source,
        "created_at": manifest.created_at,
        "coverage": manifest.coverage,
        "files": manifest.files,
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")  # same bytes on every OS
    return manifest


def verify_manifest(path: Path, directory: Path) -> Manifest:
    """Every listed file is present and unchanged.

    Like ``verify_holdout``, this only iterates what the manifest lists: a file added to the
    directory afterwards is invisible here. That is a known limit, not an oversight — the
    manifest answers "is what I recorded still what I recorded", not "is this directory clean".
    """
    from quantcrucible.data.perp_source import CoverageError

    manifest = Manifest.read(path)
    for name, digest in manifest.files.items():
        f = directory / name
        if not f.is_file():
            raise CoverageError(f"{name} is missing from {directory}")
        if sha256_file(f) != digest:
            raise CoverageError(f"{name} does not match the manifest checksum")
    return manifest
