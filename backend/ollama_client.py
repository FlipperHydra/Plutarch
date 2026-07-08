"""Thin async wrapper around ollama.AsyncClient.

Provides list/pull/load/unload semantics with the eviction pattern Plutarch
needs: switching models sends `keep_alive: 0` to the outgoing model to force
eviction before the new model is warmed. This matches the "one model at a
time" constraint (Issue 14 option a).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Optional

import ollama


log = logging.getLogger(__name__)


def _extract_name(m) -> Optional[str]:
    """Best-effort tag extraction from an ollama.list() record.

    The ollama-python client's response schema has drifted across versions:
      * newer builds expose ``.model`` on the Pydantic record,
      * older builds exposed ``.name``,
      * some proxies / mock servers return plain dicts.

    We try each shape in turn so a schema change doesn't silently drop
    every model (which manifests as "Ollama reports no models on disk"
    in the UI even though ``ollama list`` shows them).
    """
    # Attribute-style access first (Pydantic model).
    for attr in ("model", "name"):
        v = getattr(m, attr, None)
        if v:
            return v
    # Dict fallback.
    if isinstance(m, dict):
        for key in ("model", "name"):
            v = m.get(key)
            if v:
                return v
    return None


def _extract_size(m) -> Optional[int]:
    """Best-effort byte-size extraction, symmetric with _extract_name."""
    v = getattr(m, "size", None)
    if isinstance(v, int):
        return v
    if isinstance(m, dict):
        v = m.get("size")
        if isinstance(v, int):
            return v
    return None


def normalize_model_name(name: str) -> str:
    """Canonicalise an Ollama model tag for reliable string comparison.

    Ollama's on-disk tag names are of the form ``family:tag`` (e.g.
    ``gemma3:270m``). When a tag is omitted, Ollama treats the reference
    as ``family:latest`` — but the on-disk name is still stored with the
    explicit ``:latest`` suffix, while user-facing forms often drop it.

    We normalise on both sides of every comparison so that these three
    references are treated as the same model:

        ``Gemma3``  ==  ``gemma3``  ==  ``gemma3:latest``

    Rules:
      * Strip surrounding whitespace.
      * Lowercase the entire string (Ollama tags are case-insensitive).
      * If there is no ``:`` after the (optional) registry prefix, append
        ``:latest``. A registry prefix is detected by the presence of a
        ``/`` (e.g. ``registry.ollama.ai/library/gemma3``); we only look
        after the last ``/`` when deciding whether a tag was supplied.

    Empty strings are returned unchanged so callers can pass through
    "no model" sentinels without special-casing.
    """
    if not name:
        return name
    s = name.strip().lower()
    # Consider only the portion after the last '/' when checking for a tag,
    # so a registry-qualified name like foo/bar/gemma3 still gets :latest.
    tail = s.rsplit("/", 1)[-1]
    if ":" not in tail:
        s = s + ":latest"
    return s


class OllamaClient:
    def __init__(self) -> None:
        # Resolve the effective host explicitly so we can surface it to
        # the UI. The ollama client reads OLLAMA_HOST from env, defaulting
        # to http://127.0.0.1:11434. When Plutarch runs in Docker on
        # Linux this default points at the *container* loopback — not the
        # host machine — which is a common source of "I pulled it but
        # the app says no" reports.
        self.ollama_host: str = (
            os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        )
        self._client = ollama.AsyncClient()

    # --- Discovery --------------------------------------------------------
    # Cache of the last successful list() error so callers (routes/state)
    # can surface it to the UI. Cleared on any successful call.
    last_list_error: str = ""

    async def list_local(self) -> list[str]:
        """Return the set of models present on the Ollama daemon's disk.

        Names are returned in normalised form (see ``normalize_model_name``)
        so callers can compare against user-supplied references without
        worrying about ``:latest`` or case differences.

        On failure to reach Ollama we log the exception, cache it on
        ``last_list_error``, and return an empty list. The empty return is
        indistinguishable from "Ollama is running with no models" — callers
        that need to disambiguate should read ``last_list_error``.
        """
        try:
            resp = await self._client.list()
        except Exception as e:
            self.last_list_error = f"{type(e).__name__}: {e}"
            log.warning(
                "ollama.list() failed against host=%s: %s",
                self.ollama_host, self.last_list_error,
            )
            return []
        self.last_list_error = ""
        raw = getattr(resp, "models", None) or []
        log.debug(
            "ollama.list() host=%s returned %d record(s)",
            self.ollama_host, len(raw),
        )
        names: list[str] = []
        for m in raw:
            name = _extract_name(m)
            if not name:
                # A record with neither .model nor .name is a schema mismatch
                # — log it loudly so the next diagnostic pass sees which
                # attributes the record actually has instead of silently
                # returning [].
                log.warning(
                    "ollama.list() record has no model/name field; "
                    "type=%s repr=%r", type(m).__name__, m,
                )
                continue
            names.append(normalize_model_name(name))
        if not names and raw:
            log.warning(
                "ollama.list() returned %d record(s) but none had a usable "
                "name field; the client schema may have changed.", len(raw),
            )
        return names

    async def local_sizes(self) -> dict[str, int]:
        """Best-effort disk size (bytes) per local model tag.

        Keys are normalised, matching ``list_local()``. Uses the same
        defensive attribute extraction as ``list_local()`` so a schema
        drift can't silently zero out size lookups.
        """
        out: dict[str, int] = {}
        try:
            resp = await self._client.list()
        except Exception as e:
            self.last_list_error = f"{type(e).__name__}: {e}"
            log.warning(
                "ollama.list() failed (sizes) against host=%s: %s",
                self.ollama_host, self.last_list_error,
            )
            return out
        for m in getattr(resp, "models", None) or []:
            name = _extract_name(m)
            size = _extract_size(m)
            if name and size is not None:
                out[normalize_model_name(name)] = size
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
        """Prime the model into VRAM without running inference.

        Ollama documents two ways to warm a model without generating any
        tokens (https://github.com/ollama/ollama/blob/main/docs/api.md
        — "Load a model" section):

            POST /api/generate  { model, keep_alive }        # no prompt
            POST /api/chat      { model, messages: [], keep_alive }

        The ollama-python client's ``generate()`` builds its JSON body
        with ``exclude_none=True``, so passing ``prompt=None`` (the
        default) makes the field disappear from the wire payload —
        which is exactly the load-only shape.

        Previously this method passed ``prompt=""``, which the client
        serialises to ``{"prompt": ""}`` (empty string is not None).
        Some Ollama server builds accept that as a warm; others reject
        it with a 400 / runner error. The ``prompt=None`` form is the
        documented, version-independent way.

        We wrap in a try/except and re-raise with a clearer prefix so
        the /models/select 500 response tells the user which step
        failed (previously the exception string leaked raw httpx
        internals with no context).
        """
        try:
            await self._client.generate(
                model=name, prompt=None, keep_alive="30m"
            )
        except Exception as e:
            log.warning(
                "ollama.generate(load) failed for model=%r host=%s: %s: %s",
                name, self.ollama_host, type(e).__name__, e,
            )
            raise RuntimeError(
                f"Ollama refused to load model {name!r}: "
                f"{type(e).__name__}: {e}"
            ) from e

    async def unload(self, name: str) -> None:
        """Force eviction by sending keep_alive=0 to the model.

        Same rationale as ``load()`` — pass ``prompt=None`` so the
        client omits the field entirely rather than sending an empty
        string. Unload errors are still swallowed because "model wasn't
        loaded" is a valid pre-state we don't want to fail on.
        """
        if not name:
            return
        try:
            await self._client.generate(
                model=name, prompt=None, keep_alive=0
            )
        except Exception as e:
            # Log at debug — an already-evicted model is not an error.
            log.debug(
                "ollama.generate(unload) for model=%r ignored: %s: %s",
                name, type(e).__name__, e,
            )

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
