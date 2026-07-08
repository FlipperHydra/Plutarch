"""Model discovery, pull-with-progress, load/unload, default, manual add."""
from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import NUM_CTX, RECOMMENDED_MODELS
from db import db
from ollama_client import ollama_client
from state import app_state
from vram import estimate


router = APIRouter()

# Ollama tag names: letters, digits, dot, colon, slash, underscore, dash.
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:/\-]{1,100}$")


class NameBody(BaseModel):
    name: str


class DefaultBody(BaseModel):
    name: str  # empty string clears the default


@router.get("/available")
async def available():
    local = set(await ollama_client.list_local())
    async with db.conn.execute("SELECT name FROM custom_models ORDER BY added_at") as cur:
        custom = [r["name"] for r in await cur.fetchall()]

    default = app_state.default_model
    known = list(RECOMMENDED_MODELS)
    for c in custom:
        if c not in known:
            known.append(c)

    entries = []
    for name in known:
        entries.append({
            "name": name,
            "recommended": name in RECOMMENDED_MODELS,
            "pulled": name in local,
            "custom": name in custom,
            "is_default": name == default,
        })
    return {"models": entries, "loaded": app_state.loaded_model}


@router.post("/pull")
async def pull(body: NameBody):
    if not _MODEL_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid model name")

    async def stream():
        async for ev in ollama_client.pull(body.name):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/select")
async def select(body: NameBody):
    if not _MODEL_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid model name")
    # Guard: require the model to be pulled locally before load. Loading a
    # missing model surfaces as an obscure httpx "All connection attempts
    # failed" error from the Ollama client. Pull first via /models/pull.
    local = set(await ollama_client.list_local())
    if body.name not in local:
        raise HTTPException(
            409,
            f"model '{body.name}' is not pulled. Pull it first via /models/pull.",
        )
    # Enforce one-model-at-a-time by evicting the outgoing model first.
    if app_state.loaded_model and app_state.loaded_model != body.name:
        await ollama_client.unload(app_state.loaded_model)
    try:
        await ollama_client.load(body.name)
    except Exception as e:
        app_state.last_error = f"load failed: {e}"
        raise HTTPException(500, str(e))
    app_state.loaded_model = body.name
    return app_state.snapshot()


@router.post("/default")
async def set_default(body: DefaultBody):
    if body.name and not _MODEL_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid model name")
    await db.set_setting("default_model", body.name)
    app_state.default_model = body.name
    return app_state.snapshot()


@router.post("/manual-add")
async def manual_add(body: NameBody):
    if not _MODEL_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid model name")
    await db.conn.execute(
        "INSERT OR IGNORE INTO custom_models(name) VALUES(?)", (body.name,)
    )
    await db.conn.commit()
    return {"ok": True}


@router.get("/vram")
async def vram_estimate(model: str, ctx: Optional[int] = None):
    if not _MODEL_NAME_RE.match(model):
        raise HTTPException(400, "invalid model name")
    ctx_val = ctx or NUM_CTX
    sizes = await ollama_client.local_sizes()
    est = await estimate(model, ctx_val, sizes.get(model))
    return {
        "model": model,
        "ctx": ctx_val,
        "estimate_gb": est.estimate_gb,
        "available_gb": est.available_gb,
        "level": est.level,
        "is_heuristic": est.is_heuristic,
        "note": est.note,
    }
