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
    """Enumerate models the app knows about, with Ollama as the enumeration
    seed (not just the pulled-flag oracle).

    Design shift (2026-07): previously the app enumerated from
    ``RECOMMENDED_MODELS`` + ``custom_models`` and only stamped a
    ``pulled`` flag from Ollama. That meant any tag pulled outside the app
    (e.g. ``ollama pull mistral:7b`` from a terminal) was invisible to the
    picker unless it happened to match a curated name.

    Now the returned ``models`` list is the union of three sources:

      * ``system``       — everything Ollama reports via ``list_local()``.
                            These are always ``pulled: True`` by construction.
      * ``recommended``  — curated ≤4B tags from ``RECOMMENDED_MODELS`` that
                            aren't already covered by system entries. Shown
                            even if not pulled, so users can pull them.
      * ``custom``       — user-added tags from the ``custom_models`` table
                            that aren't already covered.

    The DB narrows to a preferences/history store; Ollama is the truth of
    both existence *and* enumeration.

    Each entry carries a ``source`` tag (``"system"``, ``"recommended"``,
    ``"custom"``) plus the legacy flags for backward compatibility with the
    frontend picker.
    """
    # System truth. Normalised names (e.g. ``gemma3:latest``).
    local_norm_list = await ollama_client.list_local()
    local_norm = set(local_norm_list)

    async with db.conn.execute("SELECT name FROM custom_models ORDER BY added_at") as cur:
        custom = [r["name"] for r in await cur.fetchall()]

    default = app_state.default_model
    default_norm = normalize_model_name(default) if default else ""

    # Precompute normalized forms of recommended + custom so we can dedupe
    # against Ollama-reported names without duplicate rows for e.g.
    # ``gemma3`` (recommended) vs ``gemma3:latest`` (system-reported).
    recommended_norm = {normalize_model_name(n): n for n in RECOMMENDED_MODELS}
    custom_norm = {normalize_model_name(n): n for n in custom}

    entries: list[dict] = []
    seen_norm: set[str] = set()

    # 1. System-reported models first. Display name = the exact tag Ollama
    #    returned, so nothing is lossy or renamed. We also stamp whether it
    #    happens to match a recommended / custom entry, so UI badges stay
    #    accurate.
    for name in local_norm_list:
        if name in seen_norm:
            continue
        seen_norm.add(name)
        entries.append({
            "name": name,
            "source": "system",
            "recommended": name in recommended_norm,
            "pulled": True,
            "custom": name in custom_norm,
            "is_default": name == default_norm,
        })

    # 2. Recommended entries not already covered by a system row. These are
    #    the curated ≤4B tags we want to offer for one-click pulling.
    for name in RECOMMENDED_MODELS:
        norm = normalize_model_name(name)
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        entries.append({
            "name": name,
            "source": "recommended",
            "recommended": True,
            "pulled": False,  # would have been in local_norm_list otherwise
            "custom": name in custom,
            "is_default": norm == default_norm,
        })

    # 3. Custom (user manual-add) entries not already covered.
    for name in custom:
        norm = normalize_model_name(name)
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        entries.append({
            "name": name,
            "source": "custom",
            "recommended": False,
            "pulled": norm in local_norm,
            "custom": True,
            "is_default": norm == default_norm,
        })

    return {
        "models": entries,
        "loaded": app_state.loaded_model,
        # Surface Ollama connectivity so the UI can distinguish
        # "no models pulled" from "can't reach the daemon".
        "list_error": ollama_client.last_list_error,
        # Which daemon are we talking to? Included so a Docker/host
        # mismatch ("I pulled on the host but the container asks the
        # container's own loopback") is diagnosable from the UI without
        # reading logs.
        "ollama_host": ollama_client.ollama_host,
    }


@router.get("/detected")
async def detected():
    """Read-only passthrough of what Ollama reports on disk.

    Distinct from ``/available`` (which unions system + recommended +
    custom for the picker). This endpoint is a plain "what does the
    daemon see?" diagnostic — useful for a dedicated "Detected on
    system" section, for tooltips, and for troubleshooting when the
    picker and reality seem to disagree.

    Response shape:
      * ``models``     — list of ``{name, size_bytes}`` for every tag
                          Ollama reports. Names are normalised.
      * ``list_error`` — non-empty if the daemon could not be reached.
    """
    names = await ollama_client.list_local()
    sizes = await ollama_client.local_sizes()
    models = [{"name": n, "size_bytes": sizes.get(n)} for n in names]
    return {
        "models": models,
        "list_error": ollama_client.last_list_error,
        "ollama_host": ollama_client.ollama_host,
    }


@router.get("/diagnostic")
async def diagnostic():
    """Return the raw ollama.list() payload the backend sees.

    This is a debugging shortcut for the "terminal shows models but the
    app says none" case: comparing this output against
    ``curl http://127.0.0.1:11434/api/tags`` tells you unambiguously
    whether Plutarch is querying a different daemon than your shell.

    Fields:
      * ``ollama_host``  — the URL the Ollama client was configured with.
      * ``list_error``   — exception string if the call failed, else "".
      * ``record_count`` — number of records returned by ollama.list().
      * ``raw``          — each record dumped as-is (attributes + dict).
                            Names and sizes are shown pre-normalization so
                            the parser output can be reasoned about.
    """
    import ollama as _ollama_pkg
    raw: list[dict] = []
    error = ""
    try:
        resp = await ollama_client._client.list()
        for m in getattr(resp, "models", None) or []:
            entry = {
                "type": type(m).__name__,
                "attr_model": getattr(m, "model", None),
                "attr_name": getattr(m, "name", None),
                "attr_size": getattr(m, "size", None),
            }
            if hasattr(m, "model_dump"):
                try:
                    entry["dump"] = m.model_dump()
                except Exception as e:
                    entry["dump_error"] = f"{type(e).__name__}: {e}"
            elif isinstance(m, dict):
                entry["dump"] = m
            raw.append(entry)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return {
        "ollama_host": ollama_client.ollama_host,
        "ollama_client_version": getattr(_ollama_pkg, "__version__", "unknown"),
        "list_error": error,
        "record_count": len(raw),
        "raw": raw,
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
                "ollama_host": ollama_client.ollama_host,
                "hint": (
                    "Ollama did not report this model. Pull it via "
                    "/models/pull, or check `ollama list` from a terminal. "
                    "If the terminal shows the model but this app does not, "
                    "Plutarch may be pointed at a different Ollama daemon "
                    "than your terminal — check the OLLAMA_HOST env var."
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
    # Store the normalized form so the pill, picker (which now emits
    # normalized names in <option value>), and any subsequent comparison
    # all agree. Without this, a legacy default like ``gemma3`` set
    # before the Ollama-first enumeration shift would fail to select
    # itself in the picker on refresh.
    app_state.loaded_model = requested_norm
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
