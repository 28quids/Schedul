"""The application entry point.

Serves the API and the single-page frontend from one process, so a local install
is one command and one URL.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..db.session import DATA_DIR, DatabaseUnreadable, init_db
from .routers import (
    catalogue, exports, impact, library, projects, schedules, settings,
)

log = logging.getLogger(__name__)

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"

app = FastAPI(
    title="Schedul",
    description="MEP equipment schedule manager",
    version="0.1.0",
)

# Wide open, because this runs on localhost as a single-user tool today. When it
# moves to a server this narrows to the deployed origin, alongside auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(schedules.router)
app.include_router(catalogue.router)
app.include_router(library.router)
app.include_router(exports.router)
app.include_router(settings.router)
app.include_router(impact.router)


@app.on_event("startup")
def _startup() -> None:
    try:
        init_db()
    except DatabaseUnreadable as problem:
        # Printed rather than raised through the server's own logging, because
        # what somebody needs here is the sentence, not the fifty frames of
        # traceback that would otherwise bury it.
        print(
            "\n"
            "Schedul cannot start: the database cannot be read.\n"
            "-------------------------------------------------\n"
            f"{problem}\n"
            "-------------------------------------------------\n"
            "Nothing has been changed or deleted. Move the damaged file aside "
            "and Schedul will start with an empty database, or use\n"
            "    python -m schedul.dbtool restore <path to a good .db>\n"
            "to put one in place safely.\n",
            file=sys.stderr,
        )
        raise SystemExit(1) from problem
    log.info("database ready at %s", DATA_DIR)


@app.get("/api/health")
def health() -> dict[str, object]:
    from ..export import pdf

    return {"ok": True, "pdf_available": pdf.available()}


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        """Serve the app for any non-API path so client routing works on reload."""
        candidate = FRONTEND / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND / "index.html")
