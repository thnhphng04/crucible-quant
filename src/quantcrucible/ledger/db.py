"""The trial ledger — single source of truth, append-only (Architecture §4.1, §4.2, P2).

Append-only is enforced by triggers in ``schema.sql``, so it holds even for code that bypasses
this class. This API only adds rows; it has no update, delete or generic ``execute``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from quantcrucible.ledger.records import (
    Campaign,
    CampaignStatus,
    GateResultRecord,
    GenerationEvent,
    HoldoutAccess,
    PortfolioVariant,
    StarvedCell,
    TrialRecord,
    TrialRow,
    TrialStats,
    utc_now,
)

# Scripts taking a ledger from version i to i + 1, applied in order; never edit a shipped one.
MIGRATIONS = ("schema.sql", "migration_002_no_replace.sql")
SCHEMA_VERSION = len(MIGRATIONS)


class LedgerError(Exception):
    """A write the ledger refused (append-only rule, campaign state, integrity)."""


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("ledger timestamps must be timezone-aware (UTC)")
    return value.isoformat()


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"))


class Ledger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: Path | str) -> Self:
        """Open (and on first use create) the ledger at ``path``; ``":memory:"`` for tests."""
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA recursive_triggers = ON")  # defence in depth; v2 triggers suffice
        if str(path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for step in range(version, SCHEMA_VERSION):
            script = files("quantcrucible.ledger").joinpath(MIGRATIONS[step]).read_text("utf-8")
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {step + 1};\nCOMMIT;")
        if version > SCHEMA_VERSION:
            conn.close()
            raise LedgerError(f"ledger schema v{version}, code expects v{SCHEMA_VERSION}")
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _insert(self, sql: str, params: tuple[Any, ...]) -> int:
        try:
            cur = self._conn.execute(sql, params)
        except sqlite3.IntegrityError as e:
            raise LedgerError(str(e)) from e
        return int(cur.lastrowid or 0)

    # ── campaigns (§4.2) ────────────────────────────────────────────────────────────────
    def open_campaign(
        self,
        campaign_id: str,
        holdout_range: str,
        lock_hash: str,
        holdout_lock_hash: str | None = None,
        started_at: datetime | None = None,
    ) -> Campaign:
        started = started_at or utc_now()
        self._insert(
            "INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, 'OPEN')",
            (campaign_id, _ts(started), holdout_range, lock_hash, holdout_lock_hash),
        )
        return Campaign(campaign_id, started, holdout_range, lock_hash, holdout_lock_hash, "OPEN")

    def campaign(self, campaign_id: str) -> Campaign | None:
        row = self._conn.execute(
            "SELECT campaign_id, started_at, holdout_range, lock_hash, holdout_lock_hash, status "
            "FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None
        return Campaign(row[0], datetime.fromisoformat(row[1]), row[2], row[3], row[4], row[5])

    def campaigns(self) -> list[Campaign]:
        ids = [r[0] for r in self._conn.execute("SELECT campaign_id FROM campaigns ORDER BY rowid")]
        return [c for i in ids if (c := self.campaign(i)) is not None]

    def transition(self, campaign_id: str, status: CampaignStatus) -> None:
        if self.campaign(campaign_id) is None:
            raise LedgerError(f"unknown campaign {campaign_id!r}")
        try:
            self._conn.execute(
                "UPDATE campaigns SET status = ? WHERE campaign_id = ?", (status, campaign_id)
            )
        except sqlite3.IntegrityError as e:
            raise LedgerError(str(e)) from e

    # ── audit log + trials (§4.1) ───────────────────────────────────────────────────────
    def log_event(self, e: GenerationEvent) -> int:
        return self._insert(
            "INSERT INTO generation_log (ts, run_id, campaign_id, engine, seed, evolve_scope,"
            " agent, model_used, cell_id, event, strategy_hash, drift_delta, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _ts(e.ts), e.run_id, e.campaign_id, e.engine, e.seed, e.evolve_scope, e.agent,
                e.model_used, e.cell_id, str(e.event), e.strategy_hash, e.drift_delta,
                _json(e.detail),
            ),
        )  # fmt: skip

    def record_trial(self, t: TrialRecord) -> int:
        return self._insert(
            "INSERT INTO trials (ts, run_id, campaign_id, candidate_id, engine, seed, evolve_scope,"
            " strategy_hash, hypothesis, params, universe, timeframe, timerange, cell_id, source,"
            " sharpe_is, returns_path, gate_failed, verdict)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _ts(t.ts), t.run_id, t.campaign_id, t.candidate_id, t.engine, t.seed,
                t.evolve_scope, t.strategy_hash, t.hypothesis, _json(t.params), t.universe,
                t.timeframe, t.timerange, t.cell_id, t.source, t.sharpe_is, t.returns_path,
                t.gate_failed, t.verdict,
            ),
        )  # fmt: skip

    def record_gate_result(self, g: GateResultRecord) -> int:
        return self._insert(
            "INSERT INTO gate_results (ts, campaign_id, candidate_id, strategy_hash, trial_id,"
            " gate, passed, value, reason, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _ts(g.ts), g.campaign_id, g.candidate_id, g.strategy_hash, g.trial_id, g.gate,
                int(g.passed), g.value, g.reason, _json(g.detail),
            ),
        )  # fmt: skip

    def record_clustering(self, method: str, assignment: Mapping[int, int]) -> int:
        """Append one N_eff clustering run: {trial_id: cluster_id} (ADR-0002)."""
        try:
            self._conn.execute("BEGIN")
            run = self._conn.execute(
                "INSERT INTO clustering_runs (ts, method, n_trials) VALUES (?, ?, ?)",
                (_ts(utc_now()), method, len(assignment)),
            ).lastrowid
            self._conn.executemany(
                "INSERT INTO trial_clusters VALUES (?, ?, ?)",
                [(run, trial_id, cluster) for trial_id, cluster in assignment.items()],
            )
            self._conn.execute("COMMIT")
        except sqlite3.IntegrityError as e:
            self._conn.execute("ROLLBACK")
            raise LedgerError(str(e)) from e
        return int(run or 0)

    # ── portfolio + holdout (§3.2.1, §4.2) ──────────────────────────────────────────────
    def record_portfolio_variant(self, v: PortfolioVariant) -> None:
        self._insert(
            "INSERT INTO portfolio_variants VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                v.portfolio_hash, v.campaign_id, _json(v.rule_config), _json(v.members),
                v.sharpe_is, v.returns_path, _ts(v.ts),
            ),
        )  # fmt: skip

    def record_holdout_access(self, a: HoldoutAccess) -> None:
        self._insert(
            "INSERT INTO holdout_access VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                a.campaign_id, a.portfolio_hash, _ts(a.frozen_at), _ts(a.accessed_at),
                a.timerange, a.verdict, a.sharpe_oos,
            ),
        )  # fmt: skip

    # ── reads ───────────────────────────────────────────────────────────────────────────
    def trial_stats(self) -> TrialStats:
        n_raw, n_eff, var_sr = self._conn.execute(
            "SELECT n_raw, n_eff, var_sr FROM trial_stats"
        ).fetchone()
        return TrialStats(int(n_raw), int(n_eff), None if var_sr is None else float(var_sr))

    def total_portfolio_variants(self) -> int:
        return int(self._conn.execute("SELECT n FROM total_portfolio_variants").fetchone()[0])

    def starved_cells(self) -> list[StarvedCell]:
        rows = self._conn.execute("SELECT cell_id, fail_rate, attempts FROM starved_cells")
        return [StarvedCell(r[0], float(r[1]), int(r[2])) for r in rows]

    def gate_results(self, candidate_id: str) -> list[tuple[str, bool, str]]:
        """(gate, passed, reason) for one candidate, in the order they were written."""
        rows = self._conn.execute(
            "SELECT gate, passed, reason FROM gate_results WHERE candidate_id = ? ORDER BY id",
            (candidate_id,),
        )
        return [(r[0], bool(r[1]), r[2]) for r in rows]

    def events(self, campaign_id: str) -> list[tuple[str, str | None]]:
        """(event, strategy_hash) for one campaign, in order."""
        rows = self._conn.execute(
            "SELECT event, strategy_hash FROM generation_log WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        )
        return [(r[0], r[1]) for r in rows]

    def trials(self, campaign_id: str | None = None) -> list[TrialRow]:
        """Every trial (all campaigns unless ``campaign_id`` is given), in insertion order."""
        sql = (
            "SELECT id, campaign_id, candidate_id, engine, strategy_hash, params, universe,"
            " timeframe, source, sharpe_is, returns_path, verdict, hypothesis, cell_id FROM trials"
        )
        args: tuple[Any, ...] = ()
        if campaign_id is not None:
            sql += " WHERE campaign_id = ?"
            args = (campaign_id,)
        rows = self._conn.execute(sql + " ORDER BY id", args)
        return [
            TrialRow(
                int(r[0]), r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7], r[8],
                float(r[9]), r[10], r[11], r[12], r[13],
            )
            for r in rows
        ]  # fmt: skip

    def passed_gate(self, campaign_id: str, gate: str) -> set[str]:
        """Candidate ids whose latest result at ``gate`` in ``campaign_id`` is a pass."""
        rows = self._conn.execute(
            "SELECT candidate_id, passed FROM gate_results WHERE campaign_id = ? AND gate = ?"
            " ORDER BY id",
            (campaign_id, gate),
        )
        latest = {r[0]: bool(r[1]) for r in rows}
        return {c for c, ok in latest.items() if ok}

    def trial_verdicts(self, campaign_id: str) -> list[tuple[int, str, str]]:
        """(trial_id, candidate_id, verdict) for one campaign, in order."""
        rows = self._conn.execute(
            "SELECT id, candidate_id, verdict FROM trials WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        )
        return [(int(r[0]), r[1], r[2]) for r in rows]
