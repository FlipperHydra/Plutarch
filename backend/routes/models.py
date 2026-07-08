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
from ollama_client import ollama_client, normalize_model_name
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
    # ``list_local()`` returns normalized tags (e.g. ``gemma3:latest``). We
    # normalise every comparison key so a recommended entry like
    # ``gemma3:270m`` still matches its on-disk form, and a manually-added
    # ``Gemma3`` still resolves to ``gemma3:latest``.
    local_norm = set(await ollama_client.list_local())
    async with db.conn.execute("SELECT name FROM custom_models ORDER BY added_at") as cur:
        custom = [r["name"] for r in await cur.fetchall()]

    default = app_state.default_model
    known = list(RECOMMENDED_MODELS)
    for c in custom:
        if c not in known:
            known.append(c)

    entries = []
    for name in known:
        norm = normalize_model_name(name)
        entries.append({
            "name": name,
            "recommended": name in RECOMMENDED_MODELS,
            "pulled": norm in local_norm,
            "custom": name in custom,
            "is_default": name == default,
        })
    return {
        "models": entries,
        "loaded": app_state.loaded_model,
        # Surface Ollama connectivity so the UI can distinguish
        # "no models pulled" from "can't reach the daemon".
        "list_error": ollama_client.last_list_error,
    }


@router.post("/pull")
async def pull(body: NameBody):
    if not _MODEL_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid model name")

    async def stream():
        saw_success = False
        async for ev in ollama_client.pull(body.name):
            # Ollama's terminal event is ``status: 'success'``. Record the
            # pull to our audit table so the UI has a persistent trail of
            # "we successfully pulled X at T" even after the app restarts.
            # This is diagnostic only — the source of truth for
            # "is X on disk" remains ``ollama.list()``.
            status = ev.get("status") if isinstance(ev, dict) else None
            if isinstance(status, str) and status.lower() == "success":
                saw_success = True
            yield f"data: {json.dumps(ev)}\n\n"
        if saw_success:
            try:
                await db.record_pull(body.name)
            except Exception:
                # Non-fatal — audit is a nice-to-have.
                pass

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/select")
async def select(body: NameBody):
    if not _MODEL_NAME_RE.match(body.name):
        raise HTTPException(400, "invalid model name")
    # Guard: require the model to be pulled locally before load. Loading a
    # missing model surfaces as an obscure httpx "All connection attempts
    # failed" error from the Ollama client. Pull first via /models/pull.
    #
    # BUG FIX (2026-07): compare on normalized names. Ollama's on-disk name
    # is often ``family:latest`` while the UI may pass ``family``; without
    # normalisation the ``in`` check falsely rejects a pulled model with a
    # 409, which is what triggered the "I pulled it but it says no" bug.
    requested_norm = normalize_model_name(body.name)
    local_norm = set(await ollama_client.list_local())
    if requested_norm not in local_norm:
        # Include diagnostic detail so the UI (and logs) can immediately
        # show which names Ollama actually reports. If ``last_list_error``
        # is set the daemon is unreachable, not the model missing.
        raise HTTPException(
            409,
            {
                "error": f"model '{body.name}' is not pulled.",
                "requested": body.name,
                "requested_normalized": requested_norm,
                "available": sorted(local_norm),
                "list_error": ollama_client.last_list_error,
                "hint": (
                    "Ollama did not report this model. Pull it via "
                    "/models/pull, or check `ollama list` from a terminal."
                ),
            },
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
    # ``local_sizes()`` keys are normalized; normalize the query too so
    # ``gemma3`` matches the on-disk ``gemma3:latest`` entry.
    est = await estimate(model, ctx_val, sizes.get(normalize_model_name(model)))
    return {
        "model": model,
        "ctx": ctx_val,
        "estimate_gb": est.estimate_gb,
        "available_gb": est.available_gb,
        "level": est.level,
        "is_heuristic": est.is_heuristic,
        "note": est.note,
    }
