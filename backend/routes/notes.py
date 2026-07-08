"""Notes CRUD + FTS5-backed user search endpoint.

The user-facing search is separate from the agent's `query_notes` tool.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import db


router = APIRouter()


_TAG_STRIP = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    text = _TAG_STRIP.sub(" ", html or "")
    return re.sub(r"\s+", " ", text).strip()


class NoteIn(BaseModel):
    title: str = "Untitled"
    body_html: str = ""


class NotePatch(BaseModel):
    title: Optional[str] = None
    body_html: Optional[str] = None


@router.get("")
async def list_notes():
    async with db.conn.execute(
        "SELECT id, title, modified_at FROM notes ORDER BY modified_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/{note_id}")
async def get_note(note_id: int):
    async with db.conn.execute(
        "SELECT id, title, body_html, body_text, description, "
        "created_at, modified_at, tagging_status "
        "FROM notes WHERE id = ?",
        (note_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "note not found")
    async with db.conn.execute(
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "WHERE nt.note_id = ? ORDER BY t.name",
        (note_id,),
    ) as tcur:
        tags = [r["name"] for r in await tcur.fetchall()]
    note = dict(row)
    note["tags"] = tags
    return note


@router.post("")
async def create_note(payload: NoteIn):
    text = _html_to_text(payload.body_html)
    cursor = await db.conn.execute(
        "INSERT INTO notes(title, body_html, body_text, tagging_status) "
        "VALUES(?, ?, ?, 'pending')",
        (payload.title, payload.body_html, text),
    )
    await db.conn.commit()
    return {"id": cursor.lastrowid}


@router.put("/{note_id}")
async def update_note(note_id: int, patch: NotePatch):
    sets: list[str] = []
    params: list = []
    if patch.title is not None:
        sets.append("title = ?")
        params.append(patch.title)
    if patch.body_html is not None:
        sets.append("body_html = ?")
        params.append(patch.body_html)
        sets.append("body_text = ?")
        params.append(_html_to_text(patch.body_html))
    if not sets:
        return {"updated": 0}
    sets.append("modified_at = datetime('now')")
    # Any content change re-queues tagging.
    if patch.body_html is not None or patch.title is not None:
        sets.append("tagging_status = 'pending'")
    params.append(note_id)
    await db.conn.execute(
        f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", params
    )
    await db.conn.commit()
    return {"updated": 1}


@router.delete("/{note_id}")
async def delete_note(note_id: int):
    await db.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    await db.conn.commit()
    return {"deleted": 1}


class SearchBody(BaseModel):
    query: str = ""
    limit: int = 25


@router.post("/search")
async def search_notes(body: SearchBody):
    if not body.query.strip():
        return []
    safe = body.query.replace('"', ' ')
    async with db.conn.execute(
        "SELECT n.id, n.title, n.description, n.modified_at "
        "FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid "
        "WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?",
        (safe, max(1, min(body.limit, 100))),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
