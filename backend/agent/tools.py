"""Concrete tool implementations bound to the Plutarch database.

All tools are async and return strings that will be re-injected into the
chat as `tool`-role messages. `propose_top3` also pushes a structured
payload to the SSE stream so the frontend can render buttons.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from typing import Any

from db import db


# --- Text helpers ---------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


# --- Tools ----------------------------------------------------------------

async def get_datetime() -> str:
    now = datetime.now()
    return json.dumps({
        "now": now.isoformat(timespec="seconds"),
        "today": now.date().isoformat(),
        "week_ago": (now - timedelta(days=7)).date().isoformat(),
        "month_ago": (now - timedelta(days=30)).date().isoformat(),
    })


async def list_tags() -> str:
    async with db.conn.execute("SELECT name, prompt FROM tags ORDER BY name") as cur:
        rows = await cur.fetchall()
    return json.dumps([{"name": r["name"], "prompt": r["prompt"]} for r in rows])


async def query_notes(
    keywords: str = "",
    tags_csv: str = "",
    after: str = "",
    before: str = "",
    title_contains: str = "",
    limit: str = "25",
) -> str:
    """FTS5 + tag/time/title filter. Returns candidate list as JSON."""
    try:
        lim = max(1, min(int(limit), 100))
    except ValueError:
        lim = 25

    where: list[str] = []
    params: list[Any] = []

    if keywords.strip():
        # Basic sanitation: escape double quotes for FTS5 phrase safety.
        safe = keywords.replace('"', ' ').strip()
        where.append(
            "n.id IN (SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?)"
        )
        params.append(safe)

    if title_contains.strip():
        where.append("n.title LIKE ?")
        params.append(f"%{title_contains.strip()}%")

    after_dt = _parse_dt(after)
    before_dt = _parse_dt(before)
    if after_dt:
        where.append("n.modified_at >= ?")
        params.append(after_dt.isoformat(sep=" ", timespec="seconds"))
    if before_dt:
        where.append("n.modified_at <= ?")
        params.append(before_dt.isoformat(sep=" ", timespec="seconds"))

    tag_names = [t.strip().lower() for t in tags_csv.split(",") if t.strip()]
    if tag_names:
        placeholders = ",".join("?" for _ in tag_names)
        where.append(
            f"n.id IN (SELECT nt.note_id FROM note_tags nt "
            f"JOIN tags t ON nt.tag_id = t.id "
            f"WHERE lower(t.name) IN ({placeholders}) "
            f"GROUP BY nt.note_id HAVING COUNT(DISTINCT t.name) = {len(tag_names)})"
        )
        params.extend(tag_names)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT n.id, n.title, n.description, n.modified_at
        FROM notes n
        {where_sql}
        ORDER BY n.modified_at DESC
        LIMIT ?
    """
    params.append(lim)

    async with db.conn.execute(sql, params) as cur:
        rows = await cur.fetchall()

    # Attach tags per row.
    results = []
    for r in rows:
        async with db.conn.execute(
            "SELECT t.name FROM tags t "
            "JOIN note_tags nt ON nt.tag_id = t.id "
            "WHERE nt.note_id = ? ORDER BY t.name",
            (r["id"],),
        ) as tcur:
            tag_rows = await tcur.fetchall()
        results.append({
            "id": r["id"],
            "title": r["title"],
            "description": r["description"],
            "modified_at": r["modified_at"],
            "tags": [t["name"] for t in tag_rows],
        })
    return json.dumps(results)


async def score_candidate(note_id: str, query: str) -> str:
    """Deterministic 0-1 confidence score. See docstring for formula."""
    try:
        nid = int(note_id)
    except ValueError:
        return json.dumps({"error": "invalid note_id"})

    async with db.conn.execute(
        "SELECT title, body_text, description, modified_at FROM notes WHERE id = ?",
        (nid,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return json.dumps({"error": "note not found", "note_id": nid})

    async with db.conn.execute(
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "WHERE nt.note_id = ?",
        (nid,),
    ) as tcur:
        tag_rows = await tcur.fetchall()
    tag_names = {t["name"].lower() for t in tag_rows}

    q_tokens = _tokens(query)

    # bm25-ish signal via FTS5 rank. Higher (less negative) = better.
    bm25 = 0.0
    if query.strip():
        try:
            async with db.conn.execute(
                "SELECT bm25(notes_fts) AS rank FROM notes_fts "
                "WHERE notes_fts MATCH ? AND rowid = ?",
                (query.replace('"', ' '), nid),
            ) as bcur:
                b = await bcur.fetchone()
            if b and b["rank"] is not None:
                # FTS5 bm25 returns negative values; invert and clamp.
                bm25 = min(1.0, max(0.0, -float(b["rank"]) / 20.0))
        except Exception:
            bm25 = 0.0

    tag_hits = len(q_tokens & tag_names)
    tag_score = min(1.0, tag_hits / max(1, len(q_tokens))) if q_tokens else 0.0

    title_score = _jaccard(q_tokens, _tokens(row["title"]))
    desc_score = _jaccard(q_tokens, _tokens(row["description"]))

    mod_dt = _parse_dt(row["modified_at"]) or datetime.now()
    delta_days = max(0.0, (datetime.now() - mod_dt).total_seconds() / 86400.0)
    recency = math.exp(-delta_days / 30.0)

    score = (
        0.35 * bm25
        + 0.25 * tag_score
        + 0.20 * title_score
        + 0.10 * desc_score
        + 0.10 * recency
    )
    score = round(max(0.0, min(1.0, score)), 3)

    return json.dumps({
        "note_id": nid,
        "score": score,
        "components": {
            "bm25": round(bm25, 3),
            "tag": round(tag_score, 3),
            "title": round(title_score, 3),
            "description": round(desc_score, 3),
            "recency": round(recency, 3),
        },
    })


# --- Top-3 output sink ---------------------------------------------------
# Populated by propose_top3 for the current chat turn; the SSE endpoint
# drains it once the model finishes responding.
_top3_sink: list[dict] = []


def drain_top3() -> list[dict]:
    global _top3_sink
    out = _top3_sink
    _top3_sink = []
    return out


async def propose_top3(results_json: str) -> str:
    """Parse a JSON list of {note_id, reason, score} and record for the UI."""
    global _top3_sink
    try:
        data = json.loads(results_json)
    except json.JSONDecodeError:
        return "[error] propose_top3 requires JSON: [{\"note_id\":..,\"reason\":..,\"score\":..}, ...]"
    if not isinstance(data, list):
        return "[error] propose_top3 expects a list"
    accepted = []
    for item in data[:3]:
        try:
            nid = int(item.get("note_id"))
            score = float(item.get("score", 0.0))
            reason = str(item.get("reason", ""))[:280]
        except (TypeError, ValueError):
            continue
        async with db.conn.execute(
            "SELECT title FROM notes WHERE id = ?", (nid,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            continue
        accepted.append({
            "note_id": nid,
            "title": row["title"],
            "score": round(score, 3),
            "reason": reason,
        })
    _top3_sink.extend(accepted)
    return json.dumps({"accepted": len(accepted)})
