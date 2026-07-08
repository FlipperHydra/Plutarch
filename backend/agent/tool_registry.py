"""Dynamic tool registration - adapted from FlipperHydra/simple-agent.

Each registered tool has a name, an async callable, a positional-argument
list (used by the streaming XML parser), and a human description that the
model reads at startup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolFn = Callable[..., Awaitable[Any]]


@dataclass
class ToolMeta:
    fn: ToolFn
    arg_names: list[str]
    description: str
    dangerous: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolMeta] = {}

    def register_tool(
        self,
        name: str,
        fn: ToolFn,
        arg_names: list[str],
        description: str,
        dangerous: bool = False,
    ) -> None:
        self._tools[name] = ToolMeta(fn, arg_names, description, dangerous)

    def get(self, name: str) -> ToolMeta | None:
        return self._tools.get(name)

    def all(self) -> dict[str, ToolMeta]:
        return dict(self._tools)
