"""Tag vocabulary + pending-tag governance."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import db


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
