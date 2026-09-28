"""Loopback-only campaign control API, separate from the read-only review app."""

from __future__ import annotations

import dataclasses
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from quantcrucible.config.loader import ConfigError, load_user_config, parse_user_config
from quantcrucible.config.lock import LockMismatchError
from quantcrucible.config.schema import UserConfig
from quantcrucible.data.registry import DatasetRegistryError
from quantcrucible.ledger.db import LedgerError
from quantcrucible.review.app import create_app as create_review_app
from quantcrucible.studio.jobs import JobConflict, JobNotFound, JobRecord, JobSupervisor
from quantcrucible.studio.service import StudioService
from quantcrucible.studio.store import DraftStore, StudioConflict
from quantcrucible.validation.campaign_service import WriterLockBusy


def _config_json(cfg: UserConfig) -> dict[str, Any]:
    from quantcrucible.config.loader import research_to_dict

    return {
        "operational": dataclasses.asdict(cfg.operational),
        "research": research_to_dict(cfg.research),
    }


def _validate_studio_config(cfg: UserConfig) -> None:
    if cfg.research.data.timeframe not in {"15m", "1h"}:
        raise ConfigError("research.data.timeframe: choose 15m or 1h for a new Studio campaign")
    end = cfg.research.data.end
    if end is not None and end > datetime.now(UTC).date():
        raise ConfigError("research.data.end: cannot be in the future")


def _config_error(exc: ConfigError) -> HTTPException:
    message = str(exc)
    fields = []
    for item in message.split("; "):
        path = item.split(":", 1)[0].split(" must ", 1)[0]
        fields.append({"path": path, "code": "INVALID", "message": item})
    return HTTPException(
        status_code=400,
        detail={"code": "CONFIG_INVALID", "message": message, "fields": fields},
    )


async def _body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(status_code=413, detail="request body is too large")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return value


def create_app(root: Path, port: int = 8765, static_dir: Path | None = None) -> FastAPI:
    """Serve Studio mutations first, then mount the unchanged read-only review app."""
    root = root.resolve()
    drafts = DraftStore(root)
    service = StudioService(root, drafts)

    def prepared(job: JobRecord) -> bool:
        from quantcrucible.data.registry import DatasetRegistry

        try:
            draft = drafts.get(str(job.draft_id))
            dataset_id = draft["dataset_id"]
            return dataset_id is not None and DatasetRegistry(root).get(dataset_id) is not None
        except Exception:
            return False

    def evolved(job: JobRecord) -> bool:
        try:
            campaign_id = str(job.campaign_id)
            lock = service.assert_runnable(campaign_id)
            argv = list(job.argv)
            engines = {argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg == "--engine"}
            return service.search_complete(campaign_id, lock, engines)
        except Exception:
            return False

    jobs = JobSupervisor(root, postconditions={"prepare_data": prepared, "evolve": evolved})
    jobs.reconcile_startup()
    ledger_path = root / "ledger" / "crucible.db"
    if ledger_path.exists():
        from quantcrucible.ledger.db import Ledger
        from quantcrucible.validation.campaign_service import CampaignService

        with Ledger.open(ledger_path) as ledger:
            CampaignService(root, ledger).recover_latest_active_copy()
    session_token = secrets.token_urlsafe(32)
    app = FastAPI(title="QuantCrucible Studio", docs_url=None, redoc_url=None)

    @app.exception_handler(DatasetRegistryError)
    async def dataset_error(_request: Request, exc: DatasetRegistryError) -> JSONResponse:
        return JSONResponse(
            {"code": "DATASET_INVALID", "message": str(exc), "fields": []}, status_code=422
        )

    @app.exception_handler(LockMismatchError)
    async def lock_error(_request: Request, exc: LockMismatchError) -> JSONResponse:
        return JSONResponse({"code": "LOCK_CONFLICT", "message": str(exc)}, status_code=409)

    @app.exception_handler(LedgerError)
    async def ledger_error(_request: Request, exc: LedgerError) -> JSONResponse:
        return JSONResponse({"code": "LEDGER_CONFLICT", "message": str(exc)}, status_code=409)

    @app.exception_handler(WriterLockBusy)
    async def writer_error(_request: Request, exc: WriterLockBusy) -> JSONResponse:
        return JSONResponse({"code": "WRITER_BUSY", "message": str(exc)}, status_code=409)

    @app.middleware("http")
    async def local_guard(request: Request, call_next: Any) -> Any:
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Studio accepts loopback hosts only"}, status_code=403)
        if request.url.path.startswith("/api/studio/") and request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin is not None:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost"}
                    or parsed.port != port
                ):
                    return JSONResponse({"detail": "wrong Origin"}, status_code=403)
            if request.headers.get("x-studio-token") != session_token:
                return JSONResponse({"detail": "missing Studio session token"}, status_code=403)
        return await call_next(request)

    @app.get("/api/studio/capabilities")
    def capabilities() -> dict[str, Any]:
        try:
            cfg = load_user_config(root / "config" / "user.yaml")
        except (FileNotFoundError, ConfigError):
            cfg = UserConfig()
        return {
            "contract_version": 1,
            "session_token": session_token,
            "defaults": _config_json(cfg),
            "markets_available": {
                "spot": {"enabled": True},
                "usdt_m_perpetual": {
                    "enabled": True,
                    "reason": "Cần đủ trade, mark, funding, bracket và trade path thực",
                },
            },
            "timeframes": ["15m", "1h"],
            "purposes": ["research", "harness_test"],
            "engines": ["gp", "random"],
            "enums": {
                "markets": ["spot", "usdt_m_perpetual"],
                "timeframes": ["15m", "1h"],
                "purposes": ["research", "harness_test"],
                "engines": ["gp", "random"],
            },
            "max_workers": 12,
        }

    @app.get("/api/studio/drafts")
    def list_drafts() -> list[dict[str, Any]]:
        return drafts.list()

    @app.post("/api/studio/drafts", status_code=201)
    async def create_draft(request: Request) -> dict[str, Any]:
        payload = await _body(request)
        if set(payload) != {"config"}:
            raise HTTPException(status_code=400, detail="expected config only")
        try:
            cfg = parse_user_config(payload["config"])
            _validate_studio_config(cfg)
        except ConfigError as exc:
            raise _config_error(exc) from exc
        return drafts.create(_config_json(cfg))

    @app.get("/api/studio/drafts/{draft_id}")
    def get_draft(draft_id: str) -> dict[str, Any]:
        try:
            return drafts.get(draft_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="draft not found") from exc

    @app.patch("/api/studio/drafts/{draft_id}")
    async def update_draft(draft_id: str, request: Request) -> dict[str, Any]:
        payload = await _body(request)
        if set(payload) != {"config"}:
            raise HTTPException(status_code=400, detail="expected config only")
        try:
            cfg = parse_user_config(payload["config"])
            _validate_studio_config(cfg)
        except ConfigError as exc:
            raise _config_error(exc) from exc
        revision = request.headers.get("if-match", "").strip('"')
        if not revision.isdecimal():
            raise HTTPException(status_code=428, detail="If-Match draft revision required")
        try:
            return drafts.update(draft_id, int(revision), _config_json(cfg))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="draft not found") from exc
        except StudioConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def job_json(job: JobRecord) -> dict[str, Any]:
        progress: dict[str, Any] = {}
        if job.campaign_id is not None and (root / "ledger" / "crucible.db").exists():
            import sqlite3

            path = root / "ledger" / "crucible.db"
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute(
                    "SELECT COUNT(*) FROM trials WHERE campaign_id=?", (job.campaign_id,)
                ).fetchone()
                budget = db.execute(
                    "SELECT trial_budget FROM campaign_purposes WHERE campaign_id=?",
                    (job.campaign_id,),
                ).fetchone()
            progress = {"trials": int(row[0]), "quota": budget[0] if budget else None}
        return {
            "job_id": job.job_id,
            "kind": job.kind,
            "state": job.state,
            "phase": job.kind,
            "campaign_id": job.campaign_id,
            "draft_id": job.draft_id,
            "started_at": (
                job.started_at.isoformat() if isinstance(job.started_at, datetime) else None
            ),
            "updated_at": job.updated_at.isoformat(),
            "exit_code": job.exit_code,
            "error_code": job.error_code,
            "progress": progress,
            "log_tail": jobs.log_tail(job.job_id),
        }

    @app.get("/api/studio/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [job_json(job) for job in jobs.list_jobs()]

    @app.get("/api/studio/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return job_json(jobs.status(job_id))
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.post("/api/studio/jobs/{job_id}/stop")
    async def stop_job(job_id: str, request: Request) -> dict[str, Any]:
        payload = await _body(request)
        if not isinstance(payload.get("request_key"), str):
            raise HTTPException(status_code=400, detail="request_key required")
        try:
            stopped = jobs.stop(job_id, request_key=payload["request_key"])
            result = job_json(stopped)
            if stopped.kind in {"evolve", "portfolio"}:
                from quantcrucible.validation.sandbox import SandboxRunner

                try:
                    SandboxRunner("unused", label=f"studio-{job_id}").kill_all()
                except OSError:
                    result["cleanup_warning"] = "Docker cleanup could not be verified"
            return result
        except JobNotFound as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.post("/api/studio/drafts/{draft_id}/prepare-data")
    async def prepare_data(draft_id: str, request: Request) -> dict[str, Any]:
        payload = await _body(request)
        revision = payload.get("draft_revision")
        request_key = payload.get("request_key")
        if not isinstance(revision, int) or not isinstance(request_key, str):
            raise HTTPException(status_code=400, detail="draft_revision and request_key required")
        try:
            draft = drafts.get(draft_id)
            if draft["revision"] != revision or draft["state"] == "created":
                raise StudioConflict("draft changed before data preparation")
            job = jobs.enqueue(
                kind="prepare_data",
                argv=(
                    sys.executable,
                    "-m",
                    "quantcrucible.studio.worker",
                    "--root",
                    str(root),
                    "prepare-data",
                    draft_id,
                    str(revision),
                ),
                request_key=request_key,
                draft_id=draft_id,
            )
            return job_json(jobs.start(job.job_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="draft not found") from exc
        except (StudioConflict, JobConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/studio/drafts/{draft_id}/preview")
    async def preview(draft_id: str, request: Request) -> dict[str, Any]:
        payload = await _body(request)
        revision = payload.get("draft_revision")
        if not isinstance(revision, int):
            raise HTTPException(status_code=400, detail="draft_revision required")
        try:
            return service.preview(draft_id, revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="draft not found") from exc
        except StudioConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/studio/campaigns", status_code=201)
    async def create_campaign(request: Request) -> dict[str, str]:
        payload = await _body(request)
        required = {"draft_id", "draft_revision", "preview_token", "request_key"}
        if set(payload) != required or not isinstance(payload["draft_revision"], int):
            raise HTTPException(status_code=400, detail="invalid create request")
        try:
            return service.create(
                payload["draft_id"],
                payload["draft_revision"],
                payload["preview_token"],
                payload["request_key"],
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="draft not found") from exc
        except StudioConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/studio/campaigns/{campaign_id}/runs")
    async def run_campaign(campaign_id: str, request: Request) -> dict[str, Any]:
        payload = await _body(request)
        engines = payload.get("engines")
        workers = payload.get("workers")
        key = payload.get("request_key")
        if (
            not isinstance(engines, list)
            or not engines
            or any(engine not in {"gp", "random"} for engine in engines)
            or len(set(engines)) != len(engines)
            or not isinstance(workers, int)
            or isinstance(workers, bool)
            or not 1 <= workers <= 12
            or not isinstance(key, str)
        ):
            raise HTTPException(status_code=400, detail="invalid run options")
        try:
            lock = service.assert_runnable(campaign_id)
            if any(float(lock["research"]["engines"][engine]) <= 0 for engine in engines):
                raise StudioConflict("selected engine has no trial share")
            job = jobs.enqueue(
                kind="evolve",
                argv=(
                    sys.executable,
                    "-m",
                    "quantcrucible.studio.worker",
                    "--root",
                    str(root),
                    "evolve",
                    campaign_id,
                    *(part for engine in engines for part in ("--engine", engine)),
                    "--workers",
                    str(workers),
                ),
                request_key=key,
                campaign_id=campaign_id,
            )
            return job_json(jobs.start(job.job_id))
        except (JobConflict, StudioConflict, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/studio/campaigns/{campaign_id}/portfolio")
    async def run_portfolio(campaign_id: str, request: Request) -> dict[str, Any]:
        payload = await _body(request)
        key = payload.get("request_key")
        if not isinstance(key, str):
            raise HTTPException(status_code=400, detail="request_key required")
        try:
            lock = service.assert_runnable(campaign_id, purpose="research")
            if not service.search_complete(campaign_id, lock):
                raise StudioConflict("search quotas are not complete")
            job = jobs.enqueue(
                kind="portfolio",
                argv=(
                    sys.executable,
                    "-m",
                    "quantcrucible.studio.worker",
                    "--root",
                    str(root),
                    "portfolio",
                    campaign_id,
                ),
                request_key=key,
                campaign_id=campaign_id,
            )
            return job_json(jobs.start(job.job_id))
        except (JobConflict, StudioConflict, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/studio/campaigns/{campaign_id}/abandon")
    async def abandon_campaign(campaign_id: str, request: Request) -> dict[str, str]:
        from quantcrucible.config.lock import read_lock
        from quantcrucible.ledger.db import Ledger
        from quantcrucible.studio.writer_lock import ProjectBusy, project_writer_lock
        from quantcrucible.validation.freeze import FreezeError, abandon

        payload = await _body(request)
        reason = payload.get("reason")
        if (
            payload.get("campaign_id") != campaign_id
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(payload.get("request_key"), str)
        ):
            raise HTTPException(status_code=400, detail="exact campaign_id and reason required")
        try:
            with project_writer_lock(root):
                active = read_lock(root / "config" / "evaluation.lock.yaml")
                if active["campaign_id"] != campaign_id:
                    raise StudioConflict("campaign is not the active campaign")
                with Ledger.open(root / "ledger" / "crucible.db") as ledger:
                    current = ledger.campaign(campaign_id)
                    if current is not None and current.status == "ABANDONED":
                        return {"campaign_id": campaign_id, "status": "ABANDONED"}
                    abandon(ledger, campaign_id, reason.strip())
        except (ProjectBusy, StudioConflict, FreezeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"campaign_id": campaign_id, "status": "ABANDONED"}

    # The review ASGI app remains GET-only and owns its own read-only SQLite connection.
    # Mount last: its SPA catchall must not shadow Studio routes.
    app.mount("/", create_review_app(root, static_dir=static_dir))
    return app
