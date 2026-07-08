"""Wake, sleep, and status endpoints.

The health check (/status) is always available - it's what a "sleeping" app
listens on to know when the user pressed wake.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from db import db
from ollama_client import ollama_client, normalize_model_name
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


async def _finalize_wake() -> None:
    """Background wake work: open DB, wipe stale session, optionally warm the
    default model, then transition to ACTIVE. Runs OUTSIDE the HTTP request
    so the /wake POST returns immediately — previously the endpoint blocked
    for the full model load (routinely 15-60s on cold Ollama), which caused
    the frontend polling loop to time out even though the wake would have
    eventually succeeded server-side.
    """
    try:
        await db.open()

        # Session rule: any lingering chat_session.json belongs to a crashed
        # or ungracefully-closed session. Wipe, do not restore.
        chat_store.wipe()

        default_model = (await db.get_setting("default_model")) or ""
        app_state.default_model = default_model

        if default_model:
            # Skip the warm-up if the default was never pulled to disk. This
            # is the very common bootstrap case: user set a default before
            # pulling, or `ollama rm`'d the model since last session. Trying
            # to `generate` on a missing model would either implicit-pull
            # (slow, no progress feedback) or error — either way, the wake
            # would appear to hang. Better to enter ACTIVE with no loaded
            # model and let the user pull explicitly from the model panel.
            try:
                local = set(await ollama_client.list_local())
            except Exception:
                local = set()
            # Compare in normalized form — ``list_local()`` returns
            # ``family:latest`` for tagless entries and the user's saved
            # default may drop the tag. Without this, wake would incorrectly
            # report "default not pulled" for a model that is on disk.
            if normalize_model_name(default_model) not in local:
                app_state.last_error = (
                    f"default model '{default_model}' is not pulled yet — "
                    f"open the model panel to pull it."
                )
                app_state.loaded_model = ""
            else:
                try:
                    await ollama_client.load(default_model)
                    # Store normalized so the frontend picker (which now
                    # emits normalized <option value> entries for system
                    # rows) can match `data.loaded` against an option and
                    # highlight it correctly.
                    app_state.loaded_model = normalize_model_name(default_model)
                except Exception as e:
                    app_state.last_error = f"default model load failed: {e}"
                    app_state.loaded_model = ""

        app_state.tagging_queue_size = await count_pending()
        app_state.state = State.ACTIVE
    except Exception as e:
        app_state.last_error = str(e)
        app_state.state = State.COLD


@router.post("/wake")
async def wake():
    """Kick off the wake sequence and return immediately.

    The client polls /status until state == 'active'. This decouples the
    HTTP request from the (potentially long) model warm-up.
    """
    async with app_state.lock:
        if app_state.state in (State.WAKING, State.ACTIVE):
            return app_state.snapshot()
        app_state.state = State.WAKING
        app_state.last_error = ""

    # Fire and forget; _finalize_wake owns all subsequent state transitions.
    asyncio.create_task(_finalize_wake())
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
