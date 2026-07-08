"""Runtime configuration and atomic-write helpers.

Environment overrides:
  PLUTARCH_DATA_DIR   Directory for the SQLite DB and chat session file.
  PLUTARCH_HOST       Uvicorn bind address (default 0.0.0.0).
  PLUTARCH_PORT       Uvicorn port (default 8000).
  OLLAMA_HOST         Read by the Ollama client to locate the daemon.
  PLUTARCH_NUM_CTX    Ollama context window (default 16384).
  PLUTARCH_COMPACT_THRESHOLD  Soft compaction threshold in tokens.
"""
from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path

# --- Paths ----------------------------------------------------------------
_DEFAULT_DATA = "/app/data" if Path("/app").exists() else str(
    Path(__file__).resolve().parent.parent / "data"
)
DATA_DIR: str = os.environ.get("PLUTARCH_DATA_DIR", _DEFAULT_DATA)
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

DB_FILE: str = os.path.join(DATA_DIR, "plutarch.db")
CHAT_FILE: str = os.path.join(DATA_DIR, "chat_session.json")

# --- Server ---------------------------------------------------------------
HOST: str = os.environ.get("PLUTARCH_HOST", "0.0.0.0")
PORT: int = int(os.environ.get("PLUTARCH_PORT", "8000"))

# --- Ollama ---------------------------------------------------------------
NUM_CTX: int = int(os.environ.get("PLUTARCH_NUM_CTX", "16384"))
COMPACT_THRESHOLD_TOKENS: int = int(
    os.environ.get("PLUTARCH_COMPACT_THRESHOLD", "2000")
)
COMPACT_REPROMPT_DELTA: int = int(
    os.environ.get("PLUTARCH_COMPACT_REPROMPT_DELTA", "250")
)

# --- Tagging thresholds ---------------------------------------------------
# Notes with plaintext under this token budget go straight to the tagger.
# Longer notes are routed through the chunked-summary pipeline first.
SMALL_NOTE_TOKEN_LIMIT: int = int(
    os.environ.get("PLUTARCH_SMALL_NOTE_TOKENS", "1500")
)

# --- Recommended models (all <= 4B for the standard install) --------------
# Deliberately excluded: gemma3:270m and qwen2.5:0.5b. Sub-1B models
# cannot reliably follow the Plutarch system prompt (identity as
# "Plutarch", XML tool-call syntax, ranking pipeline) and produce
# confused replies like "I am a Google AI model" instead of using the
# tools. 1B is the practical floor; 3–4B is the sweet spot for tool
# following. Ordered smallest → largest so the picker default lands on
# the smallest capable option.
RECOMMENDED_MODELS: list[str] = [
    "gemma3:1b",
    "llama3.2:1b",
    "phi3:mini",
    "qwen2.5:3b",
    "gemma3:4b",
]


def data_path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def atomic_write(path: str, content: str) -> None:
    """Write text atomically: temp file + os.replace."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".swap")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, obj) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2))
