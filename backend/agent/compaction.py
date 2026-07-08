"""Token-budgeted conversation compaction (adapted from simple-agent).

Automatic hard-cap compaction when tokens exceed NUM_CTX. Soft-threshold
compaction is silent in Plutarch (no interactive REPL prompt); above the
threshold we compact eagerly, oldest half at a time.
"""
from __future__ import annotations

from typing import Any

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _ENC = None

from config import NUM_CTX, COMPACT_THRESHOLD_TOKENS
from ollama_client import ollama_client
from .prompts import COMPACTION_PROMPT


def count_tokens(messages) -> int:
    if isinstance(messages, str):
        text = messages
    else:
        text = "\n".join(str(m.get("content", "")) for m in messages)
    if _ENC:
        return len(_ENC.encode(text))
    return max(1, len(text) // 4)


def _split(conversation: list[dict]) -> int | None:
    boundaries = [i for i, m in enumerate(conversation) if m.get("role") == "user"]
    if len(boundaries) < 2:
        return None
    total = count_tokens(conversation)
    half = total / 2
    split = boundaries[-1]
    for b in boundaries[1:]:
        if count_tokens(conversation[:b]) >= half:
            split = b
            break
    return split


def _render(segment: list[dict]) -> str:
    return "\n\n".join(
        f"{m.get('role','?').upper()}: {m.get('content','')}" for m in segment
    )


async def _summarize(model: str, segment: list[dict]) -> str | None:
    try:
        text = await ollama_client.chat_once(
            model=model,
            messages=[
                {"role": "system", "content": COMPACTION_PROMPT},
                {"role": "user",   "content": _render(segment)},
            ],
            num_ctx=NUM_CTX,
        )
        return text or None
    except Exception:
        return None


async def compact_once(model: str, conversation: list[dict]) -> list[dict]:
    split = _split(conversation)
    if not split:
        return conversation
    older, recent = conversation[:split], conversation[split:]
    summary = await _summarize(model, older)
    if not summary:
        return conversation
    return [{
        "role": "system",
        "content": f"[Compacted summary of earlier conversation]: {summary}",
    }] + recent


async def maybe_compact(model: str, conversation: list[dict]) -> list[dict]:
    if not model:
        return conversation
    total = count_tokens(conversation)
    if total > NUM_CTX:
        while count_tokens(conversation) > NUM_CTX:
            new = await compact_once(model, conversation)
            if new is conversation:
                break
            conversation = new
        return conversation
    if total > COMPACT_THRESHOLD_TOKENS:
        conversation = await compact_once(model, conversation)
    return conversation
