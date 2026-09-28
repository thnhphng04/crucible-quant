"""Mutable Studio drafts and previews; research results remain in the append-only ledger."""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class StudioConflict(ValueError):
    """An optimistic revision or preview no longer describes current state."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class DraftStore:
    def __init__(self, root: Path) -> None:
        folder = root / "studio"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "drafts.sqlite"
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                  draft_id TEXT PRIMARY KEY,
                  revision INTEGER NOT NULL,
                  config_json TEXT NOT NULL,
                  dataset_id TEXT,
                  campaign_id TEXT,
                  state TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS previews (
                  token TEXT PRIMARY KEY,
                  draft_id TEXT NOT NULL,
                  revision INTEGER NOT NULL,
                  binding_json TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  FOREIGN KEY(draft_id) REFERENCES drafts(draft_id)
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(drafts)")}
            if "campaign_id" not in columns:
                db.execute("ALTER TABLE drafts ADD COLUMN campaign_id TEXT")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _draft(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "draft_id": row["draft_id"],
            "revision": row["revision"],
            "config": json.loads(row["config_json"]),
            "dataset_id": row["dataset_id"],
            "campaign_id": row["campaign_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create(self, config: dict[str, Any]) -> dict[str, Any]:
        draft_id = str(uuid.uuid4())
        stamp = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO drafts
                   (draft_id,revision,config_json,dataset_id,campaign_id,state,created_at,updated_at)
                   VALUES (?, 1, ?, NULL, NULL, 'editing', ?, ?)""",
                (draft_id, _json(config), stamp, stamp),
            )
        return self.get(draft_id)

    def get(self, draft_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE draft_id=?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return self._draft(row)

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM drafts ORDER BY updated_at DESC").fetchall()
        return [self._draft(row) for row in rows]

    def update(self, draft_id: str, revision: int, config: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT revision,state,config_json,dataset_id FROM drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["state"] == "created":
                raise StudioConflict("a created campaign cannot be edited")
            if row["revision"] != revision:
                raise StudioConflict(f"draft revision is {row['revision']}, not {revision}")
            old_data = json.loads(row["config_json"])["research"]["data"]
            new_data = config["research"]["data"]
            dataset_id = None if old_data != new_data else row["dataset_id"]
            count = db.execute(
                """UPDATE drafts SET revision=revision+1,config_json=?,dataset_id=?,
                   state='editing',updated_at=? WHERE draft_id=? AND revision=?""",
                (_json(config), dataset_id, _now(), draft_id, revision),
            ).rowcount
            if count != 1:
                raise StudioConflict("draft changed while editing")
            db.execute("DELETE FROM previews WHERE draft_id=?", (draft_id,))
        return self.get(draft_id)

    def set_dataset(self, draft_id: str, revision: int, dataset_id: str) -> dict[str, Any]:
        with self._connect() as db:
            count = db.execute(
                """UPDATE drafts SET dataset_id=?,state='ready',updated_at=?
                   WHERE draft_id=? AND revision=? AND state!='created'""",
                (dataset_id, _now(), draft_id, revision),
            ).rowcount
            if count != 1:
                raise StudioConflict("draft changed during data preparation")
            db.execute("DELETE FROM previews WHERE draft_id=?", (draft_id,))
        return self.get(draft_id)

    def issue_preview(
        self, draft_id: str, revision: int, binding: dict[str, Any]
    ) -> tuple[str, str]:
        draft = self.get(draft_id)
        if draft["revision"] != revision:
            raise StudioConflict("draft changed before preview")
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO previews VALUES (?, ?, ?, ?, ?)",
                (token, draft_id, revision, _json(binding), expires_at),
            )
        return token, expires_at

    def assert_preview(
        self, token: str, draft_id: str, revision: int, binding: dict[str, Any]
    ) -> None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM previews WHERE token=?", (token,)).fetchone()
        if (
            row is None
            or row["draft_id"] != draft_id
            or row["revision"] != revision
            or row["binding_json"] != _json(binding)
            or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
        ):
            raise StudioConflict("preview expired or its inputs changed; preview again")

    def mark_created(self, draft_id: str, campaign_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE drafts SET state='created',campaign_id=?,updated_at=? WHERE draft_id=?",
                (campaign_id, _now(), draft_id),
            )
