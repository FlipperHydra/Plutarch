"""Tag vocabulary + pending-tag governance."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db import db
from state import app_state, State
from tagging import run_tagging_pass, count_pending


router = APIRouter()


class TagBody(BaseModel):
    name: str
    prompt: str


class PromptBody(BaseModel):
    prompt: str


class MergeBody(BaseModel):
    into: str   # existing tag name to merge into (or "" to just accept the new one)


@router.get("")
async def list_tags():
    async with db.conn.execute(
        "SELECT name, prompt, is_seed FROM tags ORDER BY name"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("")
async def create_tag(body: TagBody):
    try:
        await db.conn.execute(
            "INSERT INTO tags(name, prompt, is_seed) VALUES(?, ?, 0)",
            (body.name.strip().lower(), body.prompt.strip()),
        )
        await db.conn.commit()
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.put("/{name}")
async def update_tag(name: str, body: PromptBody):
    async with db.conn.execute(
        "UPDATE tags SET prompt = ? WHERE lower(name) = lower(?)",
        (body.prompt.strip(), name),
    ) as cur:
        await db.conn.commit()
    return {"ok": True}


@router.delete("/{name}")
async def delete_tag(name: str, force: bool = False):
    async with db.conn.execute(
        "SELECT id, is_seed FROM tags WHERE lower(name) = lower(?)",
        (name,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "tag not found")
    if row["is_seed"] and not force:
        raise HTTPException(400, "seed tags require force=true")
    await db.conn.execute("DELETE FROM tags WHERE id = ?", (row["id"],))
    await db.conn.commit()
    return {"ok": True}


# ---- Pending (model-proposed) tags --------------------------------------

@router.get("/pending")
async def list_pending():
    async with db.conn.execute(
        "SELECT id, name, proposed_prompt, proposed_for_note, proposed_at "
        "FROM pending_tags ORDER BY proposed_at"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/pending/{pid}/accept")
async def accept_pending(pid: int, body: MergeBody):
    async with db.conn.execute(
        "SELECT name, proposed_prompt, proposed_for_note FROM pending_tags WHERE id = ?",
        (pid,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "pending tag not found")

    target_name = (body.into or row["name"]).strip().lower()

    # Ensure a target tag exists.
    async with db.conn.execute(
        "SELECT id FROM tags WHERE lower(name) = lower(?)", (target_name,)
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        target_id = existing["id"]
    else:
        result = await db.conn.execute(
            "INSERT INTO tags(name, prompt, is_seed) VALUES(?, ?, 0)",
            (target_name, row["proposed_prompt"]),
        )
        target_id = result.lastrowid

    # Attach to the proposing note, if it still exists.
    if row["proposed_for_note"]:
        await db.conn.execute(
            "INSERT OR IGNORE INTO note_tags(note_id, tag_id) VALUES(?, ?)",
            (row["proposed_for_note"], target_id),
        )

    await db.conn.execute("DELETE FROM pending_tags WHERE id = ?", (pid,))
    await db.conn.commit()
    return {"ok": True, "tag_id": target_id}


@router.post("/pending/{pid}/reject")
async def reject_pending(pid: int):
    await db.conn.execute("DELETE FROM pending_tags WHERE id = ?", (pid,))
    await db.conn.commit()
    return {"ok": True}


# ---- Manual tagging trigger ---------------------------------------------
#
# Runs the same tagging pass as sleep, but on-demand mid-session. Streams
# {processed, total} events via SSE so the UI can show live progress in the
# model-progress popup. A module-level lock prevents two overlapping runs
# (either from double-clicks or from Sleep firing while a manual run is in
# flight).

_manual_tag_lock = asyncio.Lock()


@router.post("/run")
async def run_tags():
    if app_state.state != State.ACTIVE:
        raise HTTPException(409, "tagging can only run while the app is active")
    if not app_state.loaded_model:
        raise HTTPException(
            409, "no model loaded — pick and load a model before tagging"
        )
    if _manual_tag_lock.locked():
        raise HTTPException(409, "a tagging pass is already running")

    model = app_state.loaded_model

    async def event_stream():
        # Acquire the lock inside the generator so the 409 above still fires
        # cleanly for the concurrent-call case (no partial stream body).
        async with _manual_tag_lock:
            total = await count_pending()
            yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"
            if total == 0:
                yield f"data: {json.dumps({'type': 'done', 'processed': 0, 'total': 0})}\n\n"
                return

            # Queue for pushing progress updates from the tagging pass into
            # the SSE stream without blocking the pass itself.
            queue: asyncio.Queue = asyncio.Queue()

            async def on_progress(done: int, tot: int) -> None:
                await queue.put({"type": "progress", "processed": done, "total": tot})
                app_state.tagging_queue_size = max(0, tot - done)

            async def runner():
                try:
                    result = await run_tagging_pass(
                        model, tagged_by="manual", on_progress=on_progress
                    )
                    await queue.put({
                        "type": "done",
                        "processed": result["processed"],
                        "failed": result["failed"],
                        "total": result["total"],
                    })
                except Exception as e:
                    await queue.put({"type": "error", "message": str(e)})
                finally:
                    await queue.put(None)  # sentinel
                    app_state.tagging_queue_size = 0

            task = asyncio.create_task(runner())
            try:
                while True:
                    ev = await queue.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                # If the client disconnected mid-stream, don't abandon the
                # tagging task — let it finish so notes still get tagged.
                if not task.done():
                    try:
                        await task
                    except Exception:
                        pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")
