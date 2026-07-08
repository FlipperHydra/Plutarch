"""Chat orchestration: streams model tokens, dispatches tool calls, and
re-injects tool outputs so the model can react to them within the same turn.

Design (mirrors simple-agent):
  1. Send the current conversation (+ system messages) to Ollama.
  2. Stream tokens; feed each token to ToolProcessor.
  3. When the stream ends, finalize the parser - any complete <tool>
     blocks in the buffered content are dispatched.
  4. If tools ran, append the assistant message + tool-role messages to
     the conversation and loop again (up to MAX_TOOL_ROUNDS times) so the
     model can consume tool output.
  5. Yield SSE-style event dicts throughout for the HTTP layer to stream.
"""
from __future__ import annotations

from typing import AsyncIterator

from config import NUM_CTX
from db import db
from ollama_client import ollama_client
from .compaction import maybe_compact
from .prompts import SYSTEM_PROMPT, REDUCE_PROMPT, TOP3_PROMPT, tool_prompt
from .tool_processor import ToolProcessor
from .tool_registry import ToolRegistry
from .tools import drain_top3


MAX_TOOL_ROUNDS = 4


def build_system_messages(registry: ToolRegistry) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": tool_prompt(registry)},
        {"role": "system", "content": REDUCE_PROMPT},
        {"role": "system", "content": TOP3_PROMPT},
    ]


async def run_turn(
    model: str,
    registry: ToolRegistry,
    conversation: list[dict],
    user_message: str,
) -> AsyncIterator[dict]:
    """Run one user turn to completion, yielding SSE-style events."""
    conversation.append({"role": "user", "content": user_message})
    conversation[:] = await maybe_compact(model, conversation)

    system_messages = build_system_messages(registry)

    # User-controlled toggle. Off by default because most small models
    # (gemma3, llama3.2, phi3, qwen2.5) do not implement the thinking API
    # and error out when it is requested.
    think_setting = (await db.get_setting("thinking_enabled")) or "off"
    want_think = think_setting == "on"

    for _round in range(MAX_TOOL_ROUNDS):
        tp = ToolProcessor(registry)
        assistant_text = ""
        thinking_text = ""

        messages = list(system_messages) + conversation
        try:
            response = await ollama_client.chat_stream(
                model, messages, NUM_CTX, think=want_think
            )
        except Exception as e:
            # If the failure looks like a thinking-unsupported error, retry
            # once without it and warn the user so they can turn the toggle
            # off permanently.
            msg = str(e).lower()
            if want_think and ("think" in msg or "thinking" in msg):
                yield {
                    "type": "warning",
                    "message": (
                        f"Model '{model}' does not support the thinking API. "
                        f"Retrying without it — turn off 'Show thinking' to skip this next time."
                    ),
                }
                want_think = False
                try:
                    response = await ollama_client.chat_stream(
                        model, messages, NUM_CTX, think=False
                    )
                except Exception as e2:
                    yield {"type": "error", "message": f"chat failed: {e2}"}
                    return
            else:
                yield {"type": "error", "message": f"chat failed: {e}"}
                return

        async for chunk in response:
            msg = getattr(chunk, "message", None)
            if msg is None:
                continue
            if getattr(msg, "thinking", None):
                thinking_text += msg.thinking
                yield {"type": "think", "text": msg.thinking}
            if getattr(msg, "content", None):
                assistant_text += msg.content
                tp.feed(msg.content)
                yield {"type": "token", "text": msg.content}

        await tp.finalize()
        tool_events = tp.drain_results()

        # Record the assistant turn.
        assistant_message: dict = {"role": "assistant", "content": assistant_text}
        if thinking_text:
            assistant_message["thinking"] = thinking_text
        conversation.append(assistant_message)

        if not tool_events:
            # Model has nothing more to do; flush any pending top-3 payload.
            for card in drain_top3():
                yield {"type": "top3", "card": card}
            yield {"type": "done"}
            return

        # Inject tool outputs and let the model react.
        for ev in tool_events:
            yield {
                "type": "tool_call",
                "name": ev["tool"],
                "args": ev["args"],
                "result": ev["result"],
            }
            conversation.append({
                "role": "tool",
                "content": f"<{ev['tool']}_result>{ev['result']}</{ev['tool']}_result>",
            })
        conversation[:] = await maybe_compact(model, conversation)

    # Round budget exhausted.
    for card in drain_top3():
        yield {"type": "top3", "card": card}
    yield {"type": "done"}
