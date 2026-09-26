"""SQLite read model for the review UI. This module never migrates or writes the ledger."""

# ruff: noqa: E501 -- long SQL and Vietnamese explanations are clearer kept intact

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import yaml

from quantcrucible.ledger.records import LEGACY_DIRECTION, LEGACY_INSTRUMENT

SUPPORTED_SCHEMA = 7  # bumped by migration 007 (P3-11): trials and events carry a scope
# v6 is read too. This module never migrates, so refusing everything below the newest schema
# left it unable to open the only ledger that exists — every campaign run so far predates the
# scope columns. The difference is two absent columns whose meaning is known exactly: a row
# written before P3-11 belongs to the legacy scope, which is what a NULL already reads as on v7.
# Anything older is refused, because there the reader has no account of what else is missing.
READABLE_SCHEMAS = (6, SUPPORTED_SCHEMA)


def _has_scope_columns(db: sqlite3.Connection) -> bool:
    return any(r[1] == "instrument" for r in db.execute("PRAGMA table_info(trials)"))


def _scope(db: sqlite3.Connection, alias: str = "") -> str:
    """The `instrument, direction` select fragment for whichever schema is open.

    On v6 they become NULL literals, so every query downstream — grouping, ordering, filtering —
    sees the same shape it sees for a legacy row on v7 and needs no second code path.
    """
    if not _has_scope_columns(db):
        return "NULL AS instrument, NULL AS direction"
    prefix = f"{alias}." if alias else ""
    return f"{prefix}instrument, {prefix}direction"


def unit_label(instrument: str | None, direction: str | None, engine: str, seed: int | str) -> str:
    """One unit of search, spelled the way `compare` and the CLI spell it.

    A legacy row stores NULL scope columns; it is labelled by what those NULLs mean rather than
    left blank, so a v4 campaign still reads as a campaign and not as missing data.
    """
    return f"{instrument or LEGACY_INSTRUMENT}-{direction or LEGACY_DIRECTION}-{engine}-s{seed}"


class ReviewDataError(RuntimeError):
    pass


def _json(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class ReviewRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.ledger_path = self.root / "ledger" / "crucible.db"

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if not self.ledger_path.is_file():
            raise ReviewDataError(f"Không tìm thấy ledger: {self.ledger_path}")
        uri = f"file:{self.ledger_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version not in READABLE_SCHEMAS:
            conn.close()
            readable = ", ".join(f"v{v}" for v in READABLE_SCHEMAS)
            raise ReviewDataError(f"Ledger schema v{version}; giao diện đọc được {readable}.")
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("ROLLBACK")
        finally:
            conn.close()

    @staticmethod
    def snapshot_at() -> str:
        return datetime.now(UTC).isoformat()

    def campaigns(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT c.*, COALESCE(p.purpose, 'research') purpose, p.trial_budget,
                   a.abandoned_at, a.reason abandonment_reason,
                   (SELECT COUNT(*) FROM trials t WHERE t.campaign_id=c.campaign_id) trials,
                   (SELECT COUNT(*) FROM generation_log g WHERE g.campaign_id=c.campaign_id) events,
                   MAX((SELECT MAX(ts) FROM trials t WHERE t.campaign_id=c.campaign_id),
                       (SELECT MAX(ts) FROM generation_log g WHERE g.campaign_id=c.campaign_id)) last_activity
                   FROM campaigns c
                   LEFT JOIN campaign_purposes p USING(campaign_id)
                   LEFT JOIN campaign_abandonments a USING(campaign_id)
                   ORDER BY c.started_at DESC"""
            ).fetchall()
            return [
                {
                    **dict(r),
                    "status": "ABANDONED" if r["abandoned_at"] else r["status"],
                }
                for r in rows
            ]

    def _campaign(self, db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
        row = db.execute(
            """SELECT c.*, COALESCE(p.purpose, 'research') purpose, p.trial_budget,
               a.abandoned_at, a.reason abandonment_reason
               FROM campaigns c LEFT JOIN campaign_purposes p USING(campaign_id)
               LEFT JOIN campaign_abandonments a USING(campaign_id)
               WHERE c.campaign_id=?""",
            (campaign_id,),
        ).fetchone()
        if row is None:
            raise ReviewDataError(f"Campaign không tồn tại: {campaign_id}")
        return cast(sqlite3.Row, row)

    def overview(self, campaign_id: str) -> dict[str, Any]:
        with self.connection() as db:
            campaign = self._campaign(db, campaign_id)
            counts = dict(
                db.execute(
                    """SELECT COUNT(*) trials, COUNT(DISTINCT candidate_id) candidates,
                       SUM(CASE WHEN verdict='PASS' THEN 1 ELSE 0 END) passed
                       FROM trials WHERE campaign_id=?""",
                    (campaign_id,),
                ).fetchone()
            )
            events = int(
                db.execute(
                    "SELECT COUNT(*) FROM generation_log WHERE campaign_id=?", (campaign_id,)
                ).fetchone()[0]
            )
            last = db.execute(
                """SELECT MAX(ts) FROM (SELECT ts FROM trials WHERE campaign_id=?
                   UNION ALL SELECT ts FROM generation_log WHERE campaign_id=?)""",
                (campaign_id, campaign_id),
            ).fetchone()[0]
            protocol = self._protocol(db, campaign_id)
            warning_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM generation_log WHERE campaign_id=? AND event='DEGRADATION_WARNING'",
                    (campaign_id,),
                ).fetchone()[0]
            )
            status = "ABANDONED" if campaign["abandoned_at"] else campaign["status"]
            reason = self._verdict_reason(db, campaign, protocol)
            return {
                "campaign": {**dict(campaign), "status": status},
                "counts": {**counts, "events": events, "warnings": warning_count},
                "last_activity": last,
                "protocol": protocol,
                "verdict": reason,
                "snapshot_at": self.snapshot_at(),
            }

    def _protocol(self, db: sqlite3.Connection, campaign_id: str) -> dict[str, Any] | None:
        row = db.execute(
            """SELECT detail FROM generation_log WHERE campaign_id=? AND event='PROTOCOL_LOCKED'
               ORDER BY id LIMIT 1""",
            (campaign_id,),
        ).fetchone()
        return _json(row[0], {}) if row else None

    def _verdict_reason(
        self, db: sqlite3.Connection, campaign: sqlite3.Row, protocol: dict[str, Any] | None
    ) -> dict[str, str]:
        purpose = campaign["purpose"]
        if purpose != "harness_test":
            return {
                "outcome": "research",
                "label": "Campaign nghiên cứu",
                "reason": "Không phải campaign so sánh engine.",
            }
        if protocol is None:
            return {
                "outcome": "incomplete",
                "label": "Chưa kết luận",
                "reason": "Chưa khóa protocol so sánh.",
            }
        version = protocol.get("protocol", {}).get("version")
        budget = campaign["trial_budget"]
        used = int(
            db.execute(
                "SELECT COUNT(*) FROM trials WHERE campaign_id=?", (campaign["campaign_id"],)
            ).fetchone()[0]
        )
        warnings = int(
            db.execute(
                "SELECT COUNT(*) FROM generation_log WHERE campaign_id=? AND event='DEGRADATION_WARNING'",
                (campaign["campaign_id"],),
            ).fetchone()[0]
        )
        if version == 2 and used < int(budget or 0):
            return {
                "outcome": "stopped_early",
                "label": "Chưa kết luận",
                "reason": "Protocol v2 dừng arm trước khi đủ quota; chênh lệch chỉ là bằng chứng gợi ý.",
            }
        if budget and used < budget:
            return {
                "outcome": "incomplete",
                "label": "Chưa kết luận",
                "reason": f"Đã dùng {used}/{budget} trial; các arm chưa cùng ngân sách.",
            }
        if warnings:
            return {
                "outcome": "complete_with_warnings",
                "label": "Đủ ngân sách, có cảnh báo",
                "reason": "Cảnh báo IS→OOS được giữ trong audit và không tự quyết định thắng thua.",
            }
        return {
            "outcome": "complete",
            "label": "Đủ ngân sách",
            "reason": "Xem tab GP ↔ Random để đọc kết quả theo protocol đã khóa.",
        }

    def comparison(self, campaign_id: str) -> dict[str, Any]:
        with self.connection() as db:
            campaign = self._campaign(db, campaign_id)
            protocol = self._protocol(db, campaign_id)
            scope = _scope(db)
            rows = db.execute(
                f"""WITH units AS (
                   SELECT {scope}, engine, seed, COUNT(*) trials,
                     SUM(CASE WHEN EXISTS(SELECT 1 FROM gate_results g WHERE g.campaign_id=t.campaign_id
                       AND g.candidate_id=t.candidate_id AND g.gate='g4_pbo' AND g.passed=1) THEN 1 ELSE 0 END) passed
                   FROM trials t WHERE campaign_id=?
                   GROUP BY instrument, direction, engine, seed)
                   SELECT * FROM units ORDER BY instrument, direction, engine, seed""",
                (campaign_id,),
            ).fetchall()
            checkpoints = db.execute(
                f"""SELECT {scope}, engine, seed, ts, detail FROM generation_log
                   WHERE campaign_id=? AND event='DEGRADATION_CHECKPOINT' ORDER BY id""",
                (campaign_id,),
            ).fetchall()
            warnings = db.execute(
                f"""SELECT DISTINCT {scope}, engine, seed FROM generation_log
                   WHERE campaign_id=? AND event='DEGRADATION_WARNING'
                   ORDER BY instrument, direction, engine, seed""",
                (campaign_id,),
            ).fetchall()
            engines = {}
            # The quota is the budget divided by the number of units, not by seeds x 2: the
            # literal 2 was the arm count, which stopped being the whole story when the unit
            # widened to (instrument, direction, engine, seed) in P3-12.
            quota = int(campaign["trial_budget"] or 0) // max(len(rows), 1)
            for r in rows:
                key = unit_label(r["instrument"], r["direction"], r["engine"], r["seed"])
                engines[key] = {
                    **dict(r),
                    "unit": key,
                    "instrument": r["instrument"] or LEGACY_INSTRUMENT,
                    "direction": r["direction"] or LEGACY_DIRECTION,
                    "quota": quota,
                    "pass_rate": 100 * r["passed"] / r["trials"] if r["trials"] else None,
                }
            return {
                "protocol": protocol,
                "units": list(engines.values()),
                "checkpoints": [{**dict(r), "detail": _json(r["detail"], {})} for r in checkpoints],
                "warnings": [dict(r) for r in warnings],
                "snapshot_at": self.snapshot_at(),
            }

    def candidates(
        self, campaign_id: str, query: dict[str, str], page: int, size: int
    ) -> dict[str, Any]:
        where = ["c.campaign_id = ?"]
        args: list[Any] = [campaign_id]
        for column in ("engine", "seed", "island", "instrument", "direction"):
            if value := query.get(column):
                where.append(f"c.{column} = ?")
                args.append(int(value) if column == "seed" else value)
        if value := query.get("q"):
            where.append("(c.candidate_id LIKE ? OR c.strategy_hash LIKE ?)")
            args.extend([f"%{value}%", f"%{value}%"])
        clause = " AND ".join(where)
        with self.connection() as db:
            base = f"""WITH c AS (
              SELECT t.campaign_id,t.candidate_id,t.engine,t.seed,t.island,t.strategy_hash,t.source,
                     t.sharpe_is,t.verdict,t.gate_failed,t.ts,t.id trial_id,t.cell_id,
                     {_scope(db, "t")}
              FROM trials t WHERE t.campaign_id=?
              UNION ALL
              SELECT g.campaign_id,json_extract(g.detail,'$.candidate_id'),g.engine,g.seed,g.island,
                     g.strategy_hash,'evolution',NULL,'PRE_TRIAL',NULL,g.ts,NULL,g.cell_id,
                     {_scope(db, "g")}
              FROM generation_log g WHERE g.campaign_id=? AND g.event='CANDIDATE_SUBMITTED'
                AND json_extract(g.detail,'$.candidate_id') IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM trials t WHERE t.campaign_id=g.campaign_id
                  AND t.candidate_id=json_extract(g.detail,'$.candidate_id')))
            """
            count_args = [campaign_id, campaign_id, *args]
            total = int(
                db.execute(base + f"SELECT COUNT(*) FROM c WHERE {clause}", count_args).fetchone()[
                    0
                ]
            )
            rows = db.execute(
                base + f"SELECT * FROM c WHERE {clause} ORDER BY ts DESC LIMIT ? OFFSET ?",
                [*count_args, size, (page - 1) * size],
            ).fetchall()
            return {
                "items": [dict(r) for r in rows],
                "page": page,
                "size": size,
                "total": total,
                "snapshot_at": self.snapshot_at(),
            }

    def candidate(self, campaign_id: str, candidate_id: str) -> dict[str, Any]:
        with self.connection() as db:
            self._campaign(db, campaign_id)
            trials = db.execute(
                "SELECT * FROM trials WHERE campaign_id=? AND candidate_id=? ORDER BY id",
                (campaign_id, candidate_id),
            ).fetchall()
            gates = db.execute(
                "SELECT * FROM gate_results WHERE campaign_id=? AND candidate_id=? ORDER BY id",
                (campaign_id, candidate_id),
            ).fetchall()
            event = db.execute(
                "SELECT * FROM generation_log WHERE campaign_id=? AND json_extract(detail,'$.candidate_id')=? ORDER BY id LIMIT 1",
                (campaign_id, candidate_id),
            ).fetchone()
            strategy_hash = (
                trials[0]["strategy_hash"]
                if trials
                else (event["strategy_hash"] if event else None)
            )
            source = (
                self._strategy_source(strategy_hash)
                if strategy_hash
                else {"status": "missing", "text": None}
            )
            measurements = []
            for row in trials:
                measurements.append(
                    {
                        **dict(row),
                        "params": _json(row["params"], {}),
                        "curve": self._returns_curve(row["returns_path"]),
                    }
                )
            return {
                "candidate_id": candidate_id,
                "strategy_hash": strategy_hash,
                "source": source,
                "submission": (
                    {**dict(event), "detail": _json(event["detail"], {})} if event else None
                ),
                "measurements": measurements,
                "gates": [{**dict(r), "detail": _json(r["detail"], {})} for r in gates],
                "snapshot_at": self.snapshot_at(),
            }

    def account(self, campaign_id: str, portfolio_hash: str) -> dict[str, Any]:
        """The shared account's equity curve and its per-slot decomposition (P3-23, ADR-0035).

        The screen's promise is arithmetic — ``initial_cash + Σ contributions == equity`` at every
        bar, including while positions are open — so the residual is **measured from what was
        stored** and served alongside it. A reader should be able to see a drift, not be told
        there is none.

        A portfolio built before the account replay existed has no curve. That reads as absent
        rather than as a flat line at zero, which would be a claim about the account.
        """
        with self.connection() as db:
            self._campaign(db, campaign_id)  # 404 for an unknown campaign, as every route does

        path = (
            self.root / "results" / "account" / campaign_id / f"{portfolio_hash}.parquet"
        ).resolve()
        allowed = (self.root / "results" / "account").resolve()
        if allowed not in path.parents or not path.is_file():
            # INV-80: the hash comes out of the database and must not address a file outside
            # `results/`. An escape and an honestly absent curve are the same answer here.
            return {
                "status": "missing", "portfolio_hash": portfolio_hash, "initial_cash": None,
                "points": [], "slots": [], "max_residual": None, "reconciles": None,
                "snapshot_at": self.snapshot_at(),
            }  # fmt: skip

        frame = pd.read_parquet(path)
        equity = frame["equity"].to_numpy(dtype=float)
        initial = float(frame["initial_cash"].iloc[0]) if len(frame) else 0.0
        slots = [c for c in frame.columns if c not in ("ts", "equity", "initial_cash")]
        summed = frame[slots].to_numpy(dtype=float).sum(axis=1) if slots else np.zeros(len(frame))
        residual = np.abs(initial + summed - equity)
        worst = float(residual.max()) if len(residual) else 0.0
        return {
            "status": "ok",
            "portfolio_hash": portfolio_hash,
            "initial_cash": initial,
            "points": [
                {
                    "ts": str(pd.Timestamp(t).date()),
                    "equity": float(e),
                    "residual": float(r),
                }
                for t, e, r in zip(frame["ts"], equity, residual, strict=True)
            ],
            "slots": [
                {
                    "slot": name,
                    "final": float(frame[name].to_numpy(dtype=float)[-1]) if len(frame) else 0.0,
                    "values": [float(v) for v in frame[name].to_numpy(dtype=float)],
                }
                for name in slots
            ],
            "max_residual": worst,
            # A tolerance, not equality: the arithmetic is exact by construction but the curve
            # has been through parquet, so float64 round-tripping is the only slack allowed.
            "reconciles": bool(worst <= max(1e-6, abs(initial) * 1e-12)),
            "snapshot_at": self.snapshot_at(),
        }

    def _strategy_source(self, strategy_hash: str) -> dict[str, Any]:
        path = (self.root / "results" / "strategies" / f"{strategy_hash}.py").resolve()
        allowed = (self.root / "results" / "strategies").resolve()
        if allowed not in path.parents or not path.is_file():
            return {"status": "missing", "text": None}
        return {"status": "ok", "text": path.read_text("utf-8")}

    def _returns_curve(self, value: str) -> dict[str, Any]:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        allowed = (self.root / "results").resolve()
        if allowed not in path.parents or not path.is_file() or path.suffix != ".parquet":
            return {"status": "missing", "points": []}
        try:
            frame = pd.read_parquet(path, columns=["ts", "ret"])
            equity = (1.0 + frame["ret"].fillna(0.0)).cumprod()
            peak = equity.cummax()
            step = max(len(frame) // 300, 1)
            points = [
                {
                    "ts": str(frame.iloc[i]["ts"]),
                    "equity": float(equity.iloc[i]),
                    "drawdown": float(equity.iloc[i] / peak.iloc[i] - 1),
                }
                for i in range(0, len(frame), step)
            ]
            return {"status": "ok", "points": points}
        except Exception as exc:
            return {"status": "error", "message": type(exc).__name__, "points": []}

    def portfolios(self, campaign_id: str) -> dict[str, Any]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM portfolio_variants WHERE campaign_id=? ORDER BY ts DESC",
                (campaign_id,),
            ).fetchall()
            calibrations = db.execute(
                """SELECT r.*, f.finished_at,f.outcome,f.attempts,f.trials,f.errors
              FROM calibration_runs r LEFT JOIN calibration_finishes f USING(campaign_id,candidate_id)
              WHERE r.campaign_id=? ORDER BY r.started_at DESC""",
                (campaign_id,),
            ).fetchall()
            current = int(
                db.execute(
                    "SELECT COUNT(*) FROM trials WHERE campaign_id=?", (campaign_id,)
                ).fetchone()[0]
            )
            items = []
            for r in rows:
                rule = _json(r["rule_config"], {})
                snapshot = rule.get("snapshot", {}) if isinstance(rule, dict) else {}
                items.append(
                    {
                        **dict(r),
                        "rule_config": rule,
                        "members": _json(r["members"], []),
                        "stale": bool(snapshot and snapshot.get("trials") != current),
                    }
                )
            return {
                "items": items,
                "calibrations": [dict(r) for r in calibrations],
                "ledger_trials": current,
                "snapshot_at": self.snapshot_at(),
            }

    def archive(self, campaign_id: str) -> dict[str, Any]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT candidate_id,engine,seed,island,cell_id,sharpe_is,verdict FROM trials WHERE campaign_id=? AND cell_id IS NOT NULL ORDER BY id",
                (campaign_id,),
            ).fetchall()
            eligible = {
                r[0]
                for r in db.execute(
                    "SELECT candidate_id FROM gate_results WHERE campaign_id=? AND gate='g4_pbo' AND passed=1",
                    (campaign_id,),
                )
            }
            return {
                "items": [{**dict(r), "eligible": r["candidate_id"] in eligible} for r in rows],
                "search_cells": len({r["cell_id"] for r in rows}),
                "eligible_cells": len(
                    {r["cell_id"] for r in rows if r["candidate_id"] in eligible}
                ),
                "snapshot_at": self.snapshot_at(),
            }

    def events(self, campaign_id: str, page: int, size: int, event: str | None) -> dict[str, Any]:
        where = "campaign_id=?"
        args: list[Any] = [campaign_id]
        if event:
            where += " AND event=?"
            args.append(event)
        with self.connection() as db:
            total = int(
                db.execute(f"SELECT COUNT(*) FROM generation_log WHERE {where}", args).fetchone()[0]
            )
            rows = db.execute(
                f"SELECT * FROM generation_log WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*args, size, (page - 1) * size],
            ).fetchall()
            # The full name list is independent of the filter, so the filter can never
            # narrow itself into a dead end.
            names = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT event FROM generation_log WHERE campaign_id=? ORDER BY event",
                    (campaign_id,),
                )
            ]
            return {
                "items": [{**dict(r), "detail": _json(r["detail"], {})} for r in rows],
                "names": names,
                "total": total,
                "page": page,
                "size": size,
                "snapshot_at": self.snapshot_at(),
            }

    def lock(self, campaign_id: str) -> dict[str, Any]:
        with self.connection() as db:
            campaign = self._campaign(db, campaign_id)
        candidates = [
            self.root / "config" / "evaluation.lock.yaml",
            self.root / "config" / "locks" / f"{campaign_id}.lock.yaml",
        ]
        path = next(
            (
                p
                for p in candidates
                if p.is_file()
                and yaml.safe_load(p.read_text("utf-8")).get("campaign_id") == campaign_id
            ),
            None,
        )
        if path is None:
            return {"status": "missing", "lock": None, "expected_hash": campaign["lock_hash"]}
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        return {
            "status": "verified" if actual == campaign["lock_hash"] else "mismatch",
            "lock": yaml.safe_load(raw),
            "expected_hash": campaign["lock_hash"],
            "actual_hash": actual,
        }
