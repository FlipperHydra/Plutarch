"""SQLite schema, connection pool, FTS5 wiring, and seed data.

Uses aiosqlite so requests never block the event loop. A single shared
connection is opened when the app enters `active` state and closed on
sleep.
"""
from __future__ import annotations

import json
import aiosqlite
from typing import Optional

from config import DB_FILE

# --- Seed tag vocabulary --------------------------------------------------
SEED_TAGS: list[tuple[str, str]] = [
    ("creative",   "Use for original fiction, poetry, brainstorms, worldbuilding, or freeform expressive writing."),
    ("academic",   "Use for coursework, essays, structured research, citations, or scholarly analysis."),
    ("personal",   "Use for journal entries, personal reflections, or private thoughts not tied to a project."),
    ("draft",      "Use when the note is clearly incomplete, a work-in-progress, or explicitly marked as rough."),
    ("idea",       "Use for a self-contained concept, spark, or thought the user may want to develop later."),
    ("technology", "Use for code, software, hardware, tools, systems, or engineering concepts."),
    ("philosophy", "Use for ethics, epistemology, metaphysics, or abstract non-theological argument."),
    ("fantasy",    "Use for fantasy worldbuilding, magic systems, mythologies, or speculative lore distinct from realism."),
    ("politics",   "Use for political theory, current-events analysis, policy, governance, or civic topics."),
    ("education",  "Use for learning plans, study materials, lesson notes, or teaching content."),
    ("management", "Use for project planning, delegation, workflow, team, or organizational coordination."),
    ("theology",   "Use for biblical studies, doctrine, church history, or Christian scholarly analysis."),
    ("todo",       "Use for actionable task lists or a single actionable item the user must complete."),
    ("schedule",   "Use for time-bound plans, calendars, appointments, or dated commitments."),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  title          TEXT    NOT NULL DEFAULT 'Untitled',
  body_html      TEXT    NOT NULL DEFAULT '',
  body_text      TEXT    NOT NULL DEFAULT '',
  description    TEXT    NOT NULL DEFAULT '',
  created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
  modified_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  tagging_status TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (tagging_status IN ('pending','in_progress','done'))
);
CREATE INDEX IF NOT EXISTS idx_notes_modified ON notes(modified_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_tagging  ON notes(tagging_status);

CREATE TABLE IF NOT EXISTS tags (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  name     TEXT    NOT NULL UNIQUE,
  prompt   TEXT    NOT NULL,
  is_seed  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS note_tags (
  note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
  PRIMARY KEY (note_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_note_tags_tag ON note_tags(tag_id);

-- Model-proposed tags awaiting user review.
CREATE TABLE IF NOT EXISTS pending_tags (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT    NOT NULL,
  proposed_prompt TEXT NOT NULL,
  proposed_for_note INTEGER REFERENCES notes(id) ON DELETE SET NULL,
  proposed_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
  title, body_text, description,
  content='notes', content_rowid='id',
  tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts(rowid, title, body_text, description)
  VALUES (new.id, new.title, new.body_text, new.description);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, body_text, description)
  VALUES ('delete', old.id, old.title, old.body_text, old.description);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, body_text, description)
  VALUES ('delete', old.id, old.title, old.body_text, old.description);
  INSERT INTO notes_fts(rowid, title, body_text, description)
  VALUES (new.id, new.title, new.body_text, new.description);
END;

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_models (
  name     TEXT PRIMARY KEY,
  added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DEFAULT_SETTINGS: dict[str, str] = {
    "default_model": "",
    "tool_disclosure_enabled": "false",
    "editor_font": "Times New Roman",
    "editor_size": "12",
}


class Database:
    def __init__(self) -> None:
        self._conn: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(DB_FILE)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._seed()
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is closed. Call /wake first.")
        return self._conn

    async def _seed(self) -> None:
        assert self._conn is not None
        # Seed tag vocabulary if empty.
        async with self._conn.execute("SELECT COUNT(*) FROM tags") as cur:
            row = await cur.fetchone()
        if row and row[0] == 0:
            await self._conn.executemany(
                "INSERT INTO tags (name, prompt, is_seed) VALUES (?, ?, 1)",
                SEED_TAGS,
            )
        # Seed settings.
        for k, v in DEFAULT_SETTINGS.items():
            await self._conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (k, v),
            )

    # --- Settings helpers -------------------------------------------------
    async def get_setting(self, key: str) -> Optional[str]:
        async with self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()


# Module-level singleton used by routes.
db = Database()
