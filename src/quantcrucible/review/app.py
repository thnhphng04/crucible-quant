from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from quantcrucible.review.repository import ReviewDataError, ReviewRepository


def _read[**P, T](fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Turn a missing campaign or an unreadable ledger into a 404 the UI can explain."""
    try:
        return fn(*args, **kwargs)
    except ReviewDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def create_app(root: Path, static_dir: Path | None = None) -> FastAPI:
    """`root` is the project the ledger lives in; `static_dir` overrides where the built
    front end is read from, which lets an end-to-end run serve a throwaway ledger.

    Every handler closes over one repository. It deliberately does not use `Depends`: with
    `from __future__ import annotations` a dependency declared on a nested function cannot
    be resolved, and FastAPI silently downgrades it to a required query parameter.
    """
    repo = ReviewRepository(root)
    app = FastAPI(title="QuantCrucible Review", docs_url="/api/docs", redoc_url=None)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "read-only"}

    @app.get("/api/campaigns")
    def campaigns() -> object:
        return _read(repo.campaigns)

    @app.get("/api/campaigns/{cid}/overview")
    def overview(cid: str) -> object:
        return _read(repo.overview, cid)

    @app.get("/api/campaigns/{cid}/comparison")
    def comparison(cid: str) -> object:
        return _read(repo.comparison, cid)

    @app.get("/api/campaigns/{cid}/candidates")
    def candidates(
        cid: str,
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=200),
        q: str = "",
        engine: str = "",
        seed: str = "",
        island: str = "",
    ) -> object:
        query = {"q": q, "engine": engine, "seed": seed, "island": island}
        return _read(repo.candidates, cid, query, page, size)

    @app.get("/api/campaigns/{cid}/candidates/{candidate_id}")
    def candidate(cid: str, candidate_id: str) -> object:
        return _read(repo.candidate, cid, candidate_id)

    @app.get("/api/campaigns/{cid}/portfolios")
    def portfolios(cid: str) -> object:
        return _read(repo.portfolios, cid)

    @app.get("/api/campaigns/{cid}/archive")
    def archive(cid: str) -> object:
        return _read(repo.archive, cid)

    @app.get("/api/campaigns/{cid}/events")
    def events(
        cid: str,
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=200),
        event: str | None = None,
    ) -> object:
        return _read(repo.events, cid, page, size, event)

    @app.get("/api/campaigns/{cid}/lock")
    def lock(cid: str) -> object:
        return _read(repo.lock, cid)

    static = static_dir if static_dir is not None else root / "ui" / "dist"
    if static.is_dir():
        assets = static / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            # A wrong /api/ path must stay a 404 instead of quietly returning the app shell.
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail=f"Không có endpoint: /{path}")
            return FileResponse(static / "index.html")

    return app
