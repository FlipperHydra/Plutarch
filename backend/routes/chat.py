"""Chat SSE endpoint.

The chat lives inside the active session only. On any state other than
active, this endpoint returns 409 so the frontend can render an empty
chat panel.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import conversation as chat_store
from agent.loop import run_turn
from agent.tool_registry import ToolRegistry
from agent.tools import (
    get_datetime,
    list_tags,
    query_notes,
    score_candidate,
    propose_top3,
)
from state import app_state, State


router = APIRouter()


def _build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_tool(
        "get_datetime", get_datetime, [],
        "Return the current datetime plus common relative offsets.",
    )
    reg.register_tool(
        "list_tags", list_tags, [],
        "Return the current tag vocabulary and each tag's descriptive prompt.",
    )
    reg.register_tool(
        "query_notes", query_notes,
        ["keywords", "tags_csv", "after", "before", "title_contains", "limit"],
        "Search notes via FTS5 with optional tag/time/title filters. "
        "Args may be blank. Returns up to `limit` candidates.",
    )
    reg.register_tool(
        "score_candidate", score_candidate, ["note_id", "query"],
        "Compute a deterministic 0-1 confidence score for a note against the user query.",
    )
    reg.register_tool(
        "propose_top3", propose_top3, ["results_json"],
        'Register the top 3 matches for UI. Pass a JSON list: '
        '[{"note_id":1,"reason":"...","score":0.72}, ...]',
    )
    return reg


class ChatBody(BaseModel):
    message: str


@router.post("/stream")
async def stream(body: ChatBody):
    if app_state.state != State.ACTIVE:
        raise HTTPException(409, detail=f"session not active (state={app_state.state.value})")
    if not app_state.loaded_model:
        raise HTTPException(409, detail="no model loaded")

    conversation = chat_store.load()
    registry = _build_registry()

    async def event_stream():
        try:
            async for ev in run_turn(
                app_state.loaded_model, registry, conversation, body.message
            ):
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("type") in ("done", "error"):
                    chat_store.save(conversation)
        finally:
            chat_store.save(conversation)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/history")
async def history():
    if app_state.state != State.ACTIVE:
        return []
    return chat_store.load()
