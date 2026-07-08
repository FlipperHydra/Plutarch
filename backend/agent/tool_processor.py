"""Streaming XML tool-call parser + async dispatcher.

Adapted from FlipperHydra/simple-agent. Tools are emitted by the model as:

    <tool_name>
      <arg1>value</arg1>
      <arg2>value</arg2>
    </tool_name>

Complete blocks are detected as tokens arrive, dispatched, and the result
is queued so the surrounding chat loop can re-inject it as a `tool`-role
message.
"""
from __future__ import annotations

import re
from typing import Any

from .tool_registry import ToolRegistry


_TAG_OPEN = re.compile(r"<([a-zA-Z_][a-zA-Z0-9_]*)>")


class ToolProcessor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._buffer: str = ""
        self._results: list[dict] = []
        self._calls: list[dict] = []

    def feed(self, text_chunk: str) -> None:
        if not text_chunk:
            return
        self._buffer += text_chunk

    async def finalize(self) -> None:
        """Drain the buffer, dispatch every complete tool block found."""
        while True:
            match = _TAG_OPEN.search(self._buffer)
            if not match:
                break
            tool_name = match.group(1)
            meta = self._registry.get(tool_name)
            if meta is None:
                # Not a registered tool - skip past this opening tag.
                self._buffer = self._buffer[match.end():]
                continue
            closing = f"</{tool_name}>"
            close_idx = self._buffer.find(closing, match.end())
            if close_idx == -1:
                # Incomplete block; wait for more data (should not happen post-stream).
                break
            block = self._buffer[match.end():close_idx]
            self._buffer = self._buffer[close_idx + len(closing):]
            await self._dispatch(tool_name, meta, block)

    async def _dispatch(self, name: str, meta, block: str) -> None:
        args: dict[str, str] = {}
        for arg in meta.arg_names:
            m = re.search(
                rf"<{re.escape(arg)}>(.*?)</{re.escape(arg)}>",
                block,
                flags=re.DOTALL,
            )
            args[arg] = (m.group(1).strip() if m else "")
        self._calls.append({"tool": name, "args": args})
        try:
            result: Any = await meta.fn(**args)
        except Exception as e:
            result = f"[tool error] {name}: {e}"
        self._results.append({"tool": name, "args": args, "result": result})

    def drain_results(self) -> list[dict]:
        out = self._results
        self._results = []
        return out

    def drain_calls(self) -> list[dict]:
        out = self._calls
        self._calls = []
        return out
