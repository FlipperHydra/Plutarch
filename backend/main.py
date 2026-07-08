"""FastAPI entry point.

Only /status is served in every state. All other routes require the app to
be `active` (they will fail loudly via the db.conn guard if used before
/wake).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes import lifecycle, notes, chat, models, tags, settings


app = FastAPI(title="Plutarch", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lifecycle.router, tags=["lifecycle"])
app.include_router(notes.router,     prefix="/notes",    tags=["notes"])
app.include_router(chat.router,      prefix="/chat",     tags=["chat"])
app.include_router(models.router,    prefix="/models",   tags=["models"])
app.include_router(tags.router,      prefix="/tags",     tags=["tags"])
app.include_router(settings.router,  prefix="/settings", tags=["settings"])


# --- Static frontend -----------------------------------------------------
_HERE = Path(__file__).resolve().parent
_FRONTEND = _HERE.parent / "frontend"

if _FRONTEND.exists():
    app.mount("/css",    StaticFiles(directory=_FRONTEND / "css"),    name="css")
    app.mount("/js",     StaticFiles(directory=_FRONTEND / "js"),     name="js")
    app.mount("/vendor", StaticFiles(directory=_FRONTEND / "vendor"), name="vendor")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(_FRONTEND / "index.html")

    @app.get("/app", include_in_schema=False)
    async def workspace():
        return FileResponse(_FRONTEND / "app.html")
