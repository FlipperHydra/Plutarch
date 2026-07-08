"""Session-end tagging + description pass.

Runs after the user presses Sleep. Behaviour:
  * Load every note with tagging_status IN ('pending', 'in_progress').
  * Mark it in_progress.
  * Small notes (<= SMALL_NOTE_TOKEN_LIMIT tokens): tag + describe directly.
  * Large notes: chunk-summarize first, then tag + describe on the summary.
  * Any brand-new tag the model proposes goes to pending_tags, NOT tags.
  * On success, tagging_status = 'done'.

The job runner is deliberately serial: one note at a time, one model.
"""
from __future__ import annotations

import asyncio
import re
from typing import Iterable

from config import NUM_CTX, SMALL_NOTE_TOKEN_LIMIT
from db import db
from ollama_client import ollama_client
from agent.compaction import count_tokens
from agent.prompts import (
    tagging_prompt,
    description_prompt,
    CHUNK_SUMMARY_PROMPT,
    FINAL_SUMMARY_PROMPT,
)


TAG_LINE_RE = re.compile(r"^\s*TAGS:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
NEW_TAG_RE = re.compile(
    r'NEW_TAG\s+name\s*=\s*"?([\w-]+)"?\s+prompt\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)


async def _load_vocab() -> list[tuple[str, str]]:
    async with db.conn.execute("SELECT name, prompt FROM tags ORDER BY name") as cur:
        rows = await cur.fetchall()
    return [(r["name"], r["prompt"]) for r in rows]


async def _existing_tag_ids(names: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in names:
        async with db.conn.execute(
            "SELECT id FROM tags WHERE lower(name) = lower(?)", (name,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            out[name.lower()] = row["id"]
    return out


async def _chunk_summarize(model: str, text: str) -> str:
    """Split text into ~1000-token chunks, summarize each, then finalize."""
    # Rough char-based chunking to avoid a hard tiktoken dependency at runtime.
    chunk_chars = 3500
    chunks = [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    per_chunk: list[str] = []
    for i, chunk in enumerate(chunks):
        summary = await ollama_client.chat_once(
            model=model,
            messages=[
                {"role": "system", "content": CHUNK_SUMMARY_PROMPT},
                {"role": "user",
                 "content": f"Chunk {i + 1} of {len(chunks)}:\n\n{chunk}"},
            ],
            num_ctx=NUM_CTX,
        )
        per_chunk.append(summary)
    if len(per_chunk) == 1:
        return per_chunk[0]
    final = await ollama_client.chat_once(
        model=model,
        messages=[
            {"role": "system", "content": FINAL_SUMMARY_PROMPT},
            {"role": "user",
             "content": "\n\n".join(f"- {s}" for s in per_chunk)},
        ],
        num_ctx=NUM_CTX,
    )
    return final


def _parse_tag_response(raw: str) -> tuple[list[str], tuple[str, str] | None]:
    tags: list[str] = []
    m = TAG_LINE_RE.search(raw)
    if m:
        tags = [t.strip().lower() for t in m.group(1).split(",") if t.strip()]
        tags = tags[:4]
    new_tag: tuple[str, str] | None = None
    m2 = NEW_TAG_RE.search(raw)
    if m2:
        new_tag = (m2.group(1).strip().lower(), m2.group(2).strip())
    return tags, new_tag


async def _apply_tags(note_id: int, tag_names: list[str]) -> None:
    if not tag_names:
        return
    existing = await _existing_tag_ids(tag_names)
    # Only apply names that already exist in the vocabulary.
    tag_ids = list(existing.values())
    for tid in tag_ids:
        await db.conn.execute(
            "INSERT OR IGNORE INTO note_tags(note_id, tag_id) VALUES(?, ?)",
            (note_id, tid),
        )


async def _queue_pending_tag(note_id: int, name: str, prompt: str) -> None:
    # Skip if this name is already in tags OR already pending.
    async with db.conn.execute(
        "SELECT 1 FROM tags WHERE lower(name) = ?", (name.lower(),)
    ) as cur:
        if await cur.fetchone():
            return
    async with db.conn.execute(
        "SELECT 1 FROM pending_tags WHERE lower(name) = ?", (name.lower(),)
    ) as cur:
        if await cur.fetchone():
            return
    await db.conn.execute(
        "INSERT INTO pending_tags(name, proposed_prompt, proposed_for_note) "
        "VALUES (?, ?, ?)",
        (name, prompt, note_id),
    )


async def _pending_note_ids() -> list[int]:
    async with db.conn.execute(
        "SELECT id FROM notes "
        "WHERE tagging_status IN ('pending','in_progress') "
        "ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()
    return [r["id"] for r in rows]


async def _process_note(model: str, note_id: int) -> None:
    async with db.conn.execute(
        "SELECT title, body_text FROM notes WHERE id = ?", (note_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return
    title = row["title"] or "Untitled"
    body = row["body_text"] or ""

    await db.conn.execute(
        "UPDATE notes SET tagging_status = 'in_progress' WHERE id = ?",
        (note_id,),
    )
    await db.conn.commit()

    if count_tokens(body) > SMALL_NOTE_TOKEN_LIMIT:
        body_for_model = await _chunk_summarize(model, body)
    else:
        body_for_model = body

    vocab = await _load_vocab()

    tag_raw = await ollama_client.chat_once(
        model=model,
        messages=[{"role": "user",
                   "content": tagging_prompt(vocab, title, body_for_model)}],
        num_ctx=NUM_CTX,
    )
    tag_names, new_tag = _parse_tag_response(tag_raw)
    await _apply_tags(note_id, tag_names)
    if new_tag:
        await _queue_pending_tag(note_id, new_tag[0], new_tag[1])

    desc = await ollama_client.chat_once(
        model=model,
        messages=[{"role": "user",
                   "content": description_prompt(title, body_for_model)}],
        num_ctx=NUM_CTX,
    )
    # Enforce <=3 sentences at write time.
    sentences = re.split(r"(?<=[.!?])\s+", desc.strip())
    desc_out = " ".join(sentences[:3]).strip()

    await db.conn.execute(
        "UPDATE notes SET description = ?, tagging_status = 'done', "
        "modified_at = modified_at WHERE id = ?",
        (desc_out, note_id),
    )
    await db.conn.commit()


async def run_tagging_pass(model: str, on_progress=None) -> int:
    """Tag every pending note. Returns count processed."""
    ids = await _pending_note_ids()
    processed = 0
    for nid in ids:
        try:
            await _process_note(model, nid)
        except Exception as e:
            print(f"[tagging] note {nid} failed: {e}")
            # Leave it as in_progress so wake-time resume picks it up.
            continue
        processed += 1
        if on_progress:
            try:
                await on_progress(processed, len(ids))
            except Exception:
                pass
    return processed


async def count_pending() -> int:
    async with db.conn.execute(
        "SELECT COUNT(*) AS c FROM notes "
        "WHERE tagging_status IN ('pending','in_progress')"
    ) as cur:
        row = await cur.fetchone()
    return int(row["c"]) if row else 0
