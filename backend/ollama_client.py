"""Thin async wrapper around ollama.AsyncClient.

Provides list/pull/load/unload semantics with the eviction pattern Plutarch
needs: switching models sends `keep_alive: 0` to the outgoing model to force
eviction before the new model is warmed. This matches the "one model at a
time" constraint (Issue 14 option a).
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

import ollama


class OllamaClient:
    def __init__(self) -> None:
        self._client = ollama.AsyncClient()

    # --- Discovery --------------------------------------------------------
    async def list_local(self) -> list[str]:
        try:
            resp = await self._client.list()
        except Exception:
            return []
        names: list[str] = []
        for m in getattr(resp, "models", None) or []:
            name = getattr(m, "model", None)
            if name is None and isinstance(m, dict):
                name = m.get("model") or m.get("name")
            if name:
                names.append(name)
        return names

    async def local_sizes(self) -> dict[str, int]:
        """Best-effort disk size (bytes) per local model tag."""
        out: dict[str, int] = {}
        try:
            resp = await self._client.list()
        except Exception:
            return out
        for m in getattr(resp, "models", None) or []:
            name = getattr(m, "model", None)
            size = getattr(m, "size", None)
            if isinstance(m, dict):
                name = name or m.get("model") or m.get("name")
                size = size or m.get("size")
            if name and isinstance(size, int):
                out[name] = size
        return out

    # --- Pull -------------------------------------------------------------
    async def pull(self, name: str) -> AsyncIterator[dict]:
        """Stream Ollama pull events. Yields dicts with progress or an error."""
        try:
            async for chunk in await self._client.pull(name, stream=True):
                if hasattr(chunk, "model_dump"):
                    yield chunk.model_dump()
                elif isinstance(chunk, dict):
                    yield chunk
                else:
                    yield {"status": str(chunk)}
        except Exception as e:
            yield {"error": str(e)}

    # --- Load / unload ----------------------------------------------------
    async def load(self, name: str) -> None:
        """Prime the model into VRAM by issuing an empty generate call."""
        await self._client.generate(model=name, prompt="", keep_alive="30m")

    async def unload(self, name: str) -> None:
        """Force eviction by sending keep_alive=0 to the model."""
        if not name:
            return
        try:
            await self._client.generate(model=name, prompt="", keep_alive=0)
        except Exception:
            # If Ollama complains, the model was likely already evicted.
            pass

    # --- Chat streaming ---------------------------------------------------
    async def chat_stream(
        self, model: str, messages: list, num_ctx: int, think: bool = False
    ):
        """Start a streaming chat. `think=True` enables the Ollama thinking
        API and is only supported by a small subset of models (e.g. Qwen3,
        DeepSeek-R1). Passing it to unsupported models raises an httpx error.
        Callers should catch and retry with think=False on failure."""
        kwargs = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": num_ctx},
        }
        if think:
            kwargs["think"] = True
        return await self._client.chat(**kwargs)

    async def chat_once(self, model: str, messages: list, num_ctx: int) -> str:
        """One-shot chat used for tagging, compaction, and other background
        jobs. Thinking is deliberately NEVER enabled here — small models used
        for these jobs don't support it, and thinking output would pollute
        structured tagging responses even when supported."""
        resp = await self._client.chat(
            model=model,
            messages=messages,
            stream=False,
            options={"num_ctx": num_ctx},
        )
        return (resp.message.content or "").strip()


ollama_client = OllamaClient()
