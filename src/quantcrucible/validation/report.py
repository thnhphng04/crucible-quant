"""EvaluationReport — public / private metrics (Architecture §3.3.2).

``public`` (IS metrics) may reach prompts; ``private`` (CPCV paths, PBO, robustness, anything
beyond IS) goes to the ledger ONLY. ``feedback`` is generated from ``public`` (and the error)
by :func:`make_feedback` — the constructor refuses any other feedback, so private information
cannot leak into it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_ERROR_CHARS = 500


def make_feedback(public: Mapping[str, float], error: str | None) -> str:
    """Deterministic text built only from ``public`` and ``error``."""
    lines: list[str] = []
    if error:
        first = error.strip().splitlines()[0] if error.strip() else "error"
        lines.append(f"error: {first[:MAX_ERROR_CHARS]}")
    for key in sorted(public):
        value = public[key]
        lines.append(f"{key}: {value:.4g}" if math.isfinite(value) else f"{key}: {value}")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    public: Mapping[str, float] = field(default_factory=dict)
    private: Mapping[str, float] = field(default_factory=dict)
    feedback: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        overlap = set(self.public) & set(self.private)
        if overlap:
            raise ValueError(f"metric(s) {sorted(overlap)} are both public and private")
        if self.feedback != make_feedback(self.public, self.error):
            raise ValueError("feedback must be generated from public metrics by make_feedback()")

    @classmethod
    def build(
        cls,
        public: Mapping[str, float] | None = None,
        private: Mapping[str, float] | None = None,
        error: str | None = None,
    ) -> EvaluationReport:
        pub = {k: float(v) for k, v in (public or {}).items()}
        priv = {k: float(v) for k, v in (private or {}).items()}
        return cls(pub, priv, make_feedback(pub, error), error)

    def to_json(self) -> str:
        return json.dumps(
            {"public": dict(self.public), "private": dict(self.private), "error": self.error},
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> EvaluationReport:
        data: dict[str, Any] = json.loads(text)
        return cls.build(data.get("public"), data.get("private"), data.get("error"))
