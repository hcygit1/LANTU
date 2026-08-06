from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from lantu.tools.lens.reader import LensReader


def create_lens_app(work_dir: str | Path) -> FastAPI:
    reader = LensReader(work_dir)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    viewer_dir = Path(__file__).parent / "viewer"
    app.mount("/static", StaticFiles(directory=viewer_dir), name="lens-static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(viewer_dir / "index.html")

    @app.get("/api/sessions")
    async def sessions() -> list[dict[str, Any]]:
        return [{"id": session_id} for session_id in reader.session_ids()]

    @app.get("/api/session/{session_id}")
    async def session(session_id: str) -> dict[str, Any]:
        try:
            events = reader.read(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {
            "session_id": session_id,
            "events": [asdict(event) for event in events],
            "tasks": [asdict(task) for task in reader.tasks(session_id)],
            "diagnosis": reader.report(session_id).to_dict(),
            "evidence": [asdict(link) for link in reader.evidence_links(session_id)],
        }

    @app.get("/api/search")
    async def search(query: str) -> list[dict[str, Any]]:
        return [
            {"session_id": session_id, "event": asdict(event)}
            for session_id, event in reader.search(query)
        ]

    return app


def run_lens_web(work_dir: str | Path, port: int = 18889) -> None:
    import uvicorn

    uvicorn.run(create_lens_app(work_dir), host="127.0.0.1", port=port)
