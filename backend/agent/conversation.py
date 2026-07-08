"""Session-scoped chat persistence.

- Autosave after every user + assistant turn to chat_session.json.
- Autoload on wake to survive browser refresh mid-session.
- Deleted on sleep completion (before Ollama unload).
- If a stale file exists at wake time (crash recovery), it is deleted, not
  restored, because a crashed session still counts as ended.
"""
from __future__ import annotations

import json
import os
from typing import Any

from config import CHAT_FILE, atomic_write_json


def load() -> list[dict]:
    if not os.path.exists(CHAT_FILE):
        return []
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save(messages: list[dict]) -> None:
    try:
        atomic_write_json(CHAT_FILE, messages)
    except OSError as e:
        # Never crash the request on a save failure.
        print(f"[chat] save failed: {e}")


def wipe() -> None:
    if os.path.exists(CHAT_FILE):
        # rename-then-unlink so a crash mid-delete leaves no half-state.
        tomb = CHAT_FILE + ".tombstone"
        try:
            os.replace(CHAT_FILE, tomb)
            os.unlink(tomb)
        except OSError:
            try:
                os.unlink(CHAT_FILE)
            except OSError:
                pass
