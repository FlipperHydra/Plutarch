"""App state machine.

    cold                initial / after sleep completes
    waking              DB opening, default model loading (if any)
    active              user is using the app
    sleeping_no_model   sleep pressed but no default model set; awaiting user choice
    sleeping_tagging    tagging + description pass in progress

Only one transition at a time. Guarded by an asyncio lock.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class State(str, Enum):
    COLD = "cold"
    WAKING = "waking"
    ACTIVE = "active"
    SLEEPING_NO_MODEL = "sleeping_no_model"
    SLEEPING_TAGGING = "sleeping_tagging"


@dataclass
class AppState:
    state: State = State.COLD
    default_model: str = ""
    loaded_model: str = ""            # currently loaded in Ollama (may differ from default)
    tagging_queue_size: int = 0
    last_error: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "default_model": self.default_model,
            "loaded_model": self.loaded_model,
            "tagging_queue_size": self.tagging_queue_size,
            "last_error": self.last_error,
        }


app_state = AppState()
