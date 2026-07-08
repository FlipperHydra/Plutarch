"""Wake, sleep, and status endpoints.

The health check (/status) is always available - it's what a "sleeping" app
listens on to know when the user pressed wake.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import db
from ollama_client import ollama_client
from state import app_state, State
from agent import conversation as chat_store
from tagging import run_tagging_pass, count_pending, count_tagged


router = APIRouter()


class SleepBody(BaseModel):
    # One of: use_current | set_default | skip | (empty = default flow)
    no_model_choice: Literal["", "use_current", "set_default", "skip"] = ""
    new_default: str = ""


@router.get("/status")
async def status():
    return app_state.snapshot()


@router.post("/wake")
async def wake():
    async with app_state.lock:
        if app_state.state in (State.WAKING, State.ACTIVE):
            return app_state.snapshot()
        app_state.state = State.WAKING
        app_state.last_error = ""

    try:
        await db.open()

        # Session rule: any lingering chat_session.json belongs to a crashed
        # or ungracefully-closed session. Wipe, do not restore.
        chat_store.wipe()

        default_model = (await db.get_setting("default_model")) or ""
        app_state.default_model = default_model

        if default_model:
            try:
                await ollama_client.load(default_model)
                app_state.loaded_model = default_model
            except Exception as e:
                app_state.last_error = f"default model load failed: {e}"
                app_state.loaded_model = ""

        app_state.tagging_queue_size = await count_pending()
        app_state.state = State.ACTIVE
    except Exception as e:
        app_state.last_error = str(e)
        app_state.state = State.COLD
        raise HTTPException(500, detail=str(e))
    return app_state.snapshot()


async def _finalize_sleep(model_for_tagging: str) -> None:
    """Run tagging with the chosen model, then unload and go cold.

    Notes with tagging_status='done' (already tagged, either by a prior
    Sleep or by the manual Tag button) are skipped by run_tagging_pass'
    SQL filter. We log the counts on app_state.last_error so the UI can
    surface an audit line like 'Tagged 2 new notes; 5 already tagged'.
    """
    if model_for_tagging:
        try:
            already_tagged = await count_tagged()
            result = await run_tagging_pass(
                model_for_tagging, tagged_by="sleep"
            )
            summary = (
                f"Sleep tagged {result['processed']} note(s); "
                f"skipped {already_tagged} already-tagged; "
                f"{result['failed']} failed."
            )
            print(f"[sleep] {summary}")
            # Record on state so /status callers can surface it.
            app_state.last_error = "" if result["failed"] == 0 else (
                f"tagging finished with {result['failed']} failure(s)"
            )
        except Exception as e:
            app_state.last_error = f"tagging failed: {e}"

    # Unload whatever is currently loaded.
    if app_state.loaded_model:
        await ollama_client.unload(app_state.loaded_model)
        app_state.loaded_model = ""

    chat_store.wipe()
    await db.close()

    app_state.state = State.COLD
    app_state.tagging_queue_size = 0


@router.post("/sleep")
async def sleep(body: SleepBody):
    async with app_state.lock:
        if app_state.state == State.COLD:
            return app_state.snapshot()
        # Determine which model does tagging.
        model = ""
        default = app_state.default_model or ""
        if default:
            model = default
        else:
            # No default; consult the choice.
            if body.no_model_choice == "use_current":
                model = app_state.loaded_model
            elif body.no_model_choice == "set_default" and body.new_default:
                model = body.new_default
                await db.set_setting("default_model", body.new_default)
                app_state.default_model = body.new_default
            elif body.no_model_choice == "skip":
                model = ""
            else:
                # Ask the frontend to prompt the user.
                app_state.state = State.SLEEPING_NO_MODEL
                return app_state.snapshot()

        app_state.state = State.SLEEPING_TAGGING
        app_state.tagging_queue_size = await count_pending()

    # Run outside the lock so /status stays responsive.
    asyncio.create_task(_finalize_sleep(model))
    return app_state.snapshot()
