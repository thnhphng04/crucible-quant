"""Content-addressed archive of every strategy source the pipeline has seen (ADR-0013).

The ledger records ``strategy_hash`` only. Later stages must re-run portfolio members — gate ⑥′
(costs × 2, a second data source) and the holdout evaluator — so the source is kept at
``<root>/<strategy_hash>.py`` and verified against its hash on every read.
"""

from __future__ import annotations

from pathlib import Path

from quantcrucible.validation.gates import strategy_hash


class ArchiveError(RuntimeError):
    """A strategy is missing from the archive or no longer matches its hash."""


class StrategyArchive:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, s_hash: str) -> Path:
        return self.root / f"{s_hash}.py"

    def put(self, source: str) -> str:
        s_hash = strategy_hash(source)
        target = self.path(s_hash)
        if not target.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8", newline="\n")
        return s_hash

    def get(self, s_hash: str) -> str:
        target = self.path(s_hash)
        if not target.is_file():
            raise ArchiveError(f"strategy {s_hash[:12]}… is not in the archive {self.root}")
        source = target.read_text(encoding="utf-8")
        if strategy_hash(source) != s_hash:
            raise ArchiveError(f"archived strategy {s_hash[:12]}… no longer matches its hash")
        return source
