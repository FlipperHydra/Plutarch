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
from .prompts import (
    SYSTEM_PROMPT,
    REDUCE_PROMPT,
    TOP3_PROMPT,
    COT_PROMPT,
    tool_prompt,
)
from .tool_processor import ToolProcessor
from .tool_registry import ToolRegistry
from .tools import drain_top3


MAX_TOOL_ROUNDS = 4


def build_system_messages(registry: ToolRegistry, cot: bool) -> list[dict]:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": tool_prompt(registry)},
        {"role": "system", "content": REDUCE_PROMPT},
        {"role": "system", "content": TOP3_PROMPT},
    ]
    if cot:
        msgs.append({"role": "system", "content": COT_PROMPT})
    return msgs


class _ThinkStripper:
    """Splits a streaming token feed into (visible, thinking) chunks.

    The chain-of-thought toggle asks the model to wrap its reasoning in
    <think>...</think> before the answer. That block must NOT be fed to the
    tool processor (which would treat 'think' as an unknown tool) and must
    NOT be surfaced as an answer token. Instead we route its content to a
    separate 'think' event that the frontend gates on the tool-disclosure
    toggle.

    Design notes:
      * The parser is char-by-char and streaming-safe: token chunks can
        split any tag anywhere.
      * Nested <think> blocks are ignored (Ollama models don't emit them,
        and treating them as literal keeps the parser tiny).
      * The final assistant transcript stored in the conversation history
        excludes the think blocks — the model must not re-read its own
        prior reasoning on the next turn.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._in_think = False
        # Buffer holds the tail of the visible-mode stream that MIGHT still
        # be the start of "<think>" (or, when inside a think block, might
        # be the start of "</think>"). We only emit chars once we're sure
        # they're not part of a boundary tag.
        self._buf = ""

    def feed(self, chunk: str) -> tuple[str, str]:
        """Consume a raw token chunk. Returns (visible, thinking)."""
        visible_out: list[str] = []
        thinking_out: list[str] = []
        self._buf += chunk

        while self._buf:
            if not self._in_think:
                idx = self._buf.find(self.OPEN)
                if idx >= 0:
                    visible_out.append(self._buf[:idx])
                    self._buf = self._buf[idx + len(self.OPEN):]
                    self._in_think = True
                    continue
                # No full <think> yet. Emit everything except a possible
                # trailing partial-match tail so a future chunk can complete it.
                keep = self._partial_tail(self._buf, self.OPEN)
                if keep > 0:
                    visible_out.append(self._buf[:-keep])
                    self._buf = self._buf[-keep:]
                else:
                    visible_out.append(self._buf)
                    self._buf = ""
                break
            else:
                idx = self._buf.find(self.CLOSE)
                if idx >= 0:
                    thinking_out.append(self._buf[:idx])
                    self._buf = self._buf[idx + len(self.CLOSE):]
                    self._in_think = False
                    continue
                keep = self._partial_tail(self._buf, self.CLOSE)
                if keep > 0:
                    thinking_out.append(self._buf[:-keep])
                    self._buf = self._buf[-keep:]
                else:
                    thinking_out.append(self._buf)
                    self._buf = ""
                break

        return "".join(visible_out), "".join(thinking_out)

    def flush(self) -> tuple[str, str]:
        """End-of-stream: emit whatever is buffered, closing any open block."""
        visible = "" if self._in_think else self._buf
        thinking = self._buf if self._in_think else ""
        self._buf = ""
        self._in_think = False
        return visible, thinking

    @staticmethod
    def _partial_tail(buf: str, needle: str) -> int:
        """Return the length of the longest suffix of buf that is a proper
        prefix of needle. Used to withhold characters that might complete a
        boundary tag on the next chunk."""
        maxlen = min(len(buf), len(needle) - 1)
        for k in range(maxlen, 0, -1):
            if buf.endswith(needle[:k]):
                return k
        return 0


async def run_turn(
    model: str,
    registry: ToolRegistry,
    conversation: list[dict],
    user_message: str,
) -> AsyncIterator[dict]:
    """Run one user turn to completion, yielding SSE-style events."""
    conversation.append({"role": "user", "content": user_message})
    conversation[:] = await maybe_compact(model, conversation)

    # Chain-of-thought toggle. Universal — works on any model by asking it
    # to wrap reasoning in <think>...</think>. We strip those blocks before
    # feeding tokens into the tool processor or storing them in history.
    cot_setting = (await db.get_setting("show_steps_enabled")) or "off"
    cot_enabled = cot_setting == "on"

    system_messages = build_system_messages(registry, cot=cot_enabled)

    for _round in range(MAX_TOOL_ROUNDS):
        tp = ToolProcessor(registry)
        stripper = _ThinkStripper()
        assistant_text = ""
        thinking_text = ""

        messages = list(system_messages) + conversation
        try:
            response = await ollama_client.chat_stream(
                model, messages, NUM_CTX, think=False
            )
        except Exception as e:
            yield {"type": "error", "message": f"chat failed: {e}"}
            return

        async for chunk in response:
            msg = getattr(chunk, "message", None)
            if msg is None:
                continue
            # Some models still surface a native `thinking` field even when
            # we didn't ask for it; treat it the same as a <think> block.
            native_think = getattr(msg, "thinking", None)
            if native_think:
                thinking_text += native_think
                yield {"type": "think", "text": native_think}
            content = getattr(msg, "content", None)
            if content:
                visible, thinking = stripper.feed(content)
                if thinking:
                    thinking_text += thinking
                    yield {"type": "think", "text": thinking}
                if visible:
                    assistant_text += visible
                    tp.feed(visible)
                    yield {"type": "token", "text": visible}

        # Flush any buffered tail (unterminated <think> or trailing chars).
        vtail, ttail = stripper.flush()
        if ttail:
            thinking_text += ttail
            yield {"type": "think", "text": ttail}
        if vtail:
            assistant_text += vtail
            tp.feed(vtail)
            yield {"type": "token", "text": vtail}

        await tp.finalize()
        tool_events = tp.drain_results()

        # Record the assistant turn. History stores only the visible answer
        # (no <think> blocks), so the model can't get confused re-reading
        # its own reasoning on the next round.
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
