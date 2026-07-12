"""
ContextManager — keeps the messages list sent to the LLM under control.

Two-layer strategy
──────────────────
1. Tool-output truncation (instant, free)
   Long Athena results are cut to ``max_tool_chars`` characters with a clear
   notice so the model knows data was omitted.

2. Mid-run sliding-window compression (async, uses one LLM call)
   When the estimated character count of the whole conversation exceeds
   ``compress_threshold``, the "middle" messages (old tool rounds) are
   summarised by the LLM into a single compact UserMessage.  The system
   prompt and the most recent ``keep_recent`` messages are always kept intact.

Layout after compression:
  [SystemMessage]
  [UserMessage – original question]
  [UserMessage – "📋 Context summary: …" ]   ← replaces old rounds
  ... last ``keep_recent`` messages untouched ...
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from api.app.messages import AssistantMessage, Message, SystemMessage, ToolMessage, UserMessage

if TYPE_CHECKING:
    from api.app.clients.openrouter import OpenRouterClient as AIHubClient

_logger = logging.getLogger(__name__)

_SUMMARISE_PROMPT = (
    "You are a context compressor. "
    "The following messages are intermediate reasoning steps (tool calls and results) "
    "from an AI agent working on a user request. "
    "Produce a CONCISE, information-dense summary that preserves every key fact, "
    "number, table snippet, and conclusion so the agent can continue without needing "
    "to re-run any tool. Output plain text only — no headings, no JSON."
)

_TRUNCATION_NOTICE = (
    "\n\n… [output truncated to {limit} chars — {omitted} chars omitted] …"
)


class ContextManager:
    """
    Args:
        max_tool_chars:     Hard cap on any single ToolMessage content (chars).
        compress_threshold: Total chars of all messages before compression fires.
        keep_recent:        Number of most-recent messages to always keep verbatim.
    """

    def __init__(
        self,
        max_tool_chars: int = 6_000,
        compress_threshold: int = 40_000,
        keep_recent: int = 6,
    ) -> None:
        self.max_tool_chars = max_tool_chars
        self.compress_threshold = compress_threshold
        self.keep_recent = keep_recent

    # ── Public API ────────────────────────────────────────────────────────────

    def truncate_tool_output(self, content: str) -> str:
        """Truncate a single tool output to ``max_tool_chars``."""
        if len(content) <= self.max_tool_chars:
            return content
        omitted = len(content) - self.max_tool_chars
        notice = _TRUNCATION_NOTICE.format(limit=self.max_tool_chars, omitted=omitted)
        truncated = content[: self.max_tool_chars] + notice
        _logger.debug("Tool output truncated: %d → %d chars", len(content), len(truncated))
        return truncated

    async def maybe_compress(
        self,
        messages: list[Message],
        client: "AIHubClient",
    ) -> list[Message]:
        """
        If the total character count exceeds the threshold, summarise the
        middle messages and return a shorter list.  Otherwise return as-is.
        """
        total = _total_chars(messages)
        if total <= self.compress_threshold:
            return messages

        _logger.info(
            "Context size %d chars exceeds threshold %d — compressing.",
            total, self.compress_threshold,
        )

        # Partition: always keep head (system + first user) and tail (recent rounds)
        head = _head_messages(messages)        # [SystemMessage, UserMessage]
        tail = messages[-self.keep_recent:]    # last N messages verbatim
        middle = messages[len(head): len(messages) - self.keep_recent]

        if not middle:
            # Nothing to compress (conversation is very short but already big)
            _logger.warning("Nothing to compress — tool outputs may be too large.")
            return messages

        summary = await self._summarise(middle, client)
        summary_msg = UserMessage(content=f"📋 Context summary from previous steps:\n{summary}")

        compressed = head + [summary_msg] + tail
        after = _total_chars(compressed)
        _logger.info(
            "Compressed %d → %d chars (%d messages → %d).",
            total, after, len(messages), len(compressed),
        )
        return compressed

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _summarise(
        self,
        middle: list[Message],
        client: "AIHubClient",
    ) -> str:
        """Call the LLM once to compress ``middle`` into a text summary."""
        summary_messages: list[Message] = [
            SystemMessage(content=_SUMMARISE_PROMPT),
            UserMessage(
                content="Messages to summarise:\n\n"
                + "\n\n".join(_render_message(m) for m in middle)
            ),
        ]
        try:
            response = await client.ainvoke(summary_messages)  # no tools
            return response.get("content", "") or "(summary unavailable)"
        except Exception:
            _logger.exception("Summarisation call failed — keeping middle messages.")
            return "(summarisation failed — some context may be missing)"


# ── Module-level helpers ──────────────────────────────────────────────────────

def _total_chars(messages: list[Message]) -> int:
    return sum(len(_render_message(m)) for m in messages)


def _head_messages(messages: list[Message]) -> list[Message]:
    """Return [SystemMessage, first UserMessage] from the front of the list."""
    head: list[Message] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            head.append(m)
        elif isinstance(m, UserMessage) and len(head) == 1:
            head.append(m)
            break
    return head


def _render_message(m: Message) -> str:
    """Flatten a message to a plain string for token counting / summarisation."""
    if isinstance(m, SystemMessage):
        return f"[system]: {m.content}"
    if isinstance(m, UserMessage):
        return f"[user]: {m.content}"
    if isinstance(m, ToolMessage):
        return f"[tool:{m.name}]: {m.content}"
    if isinstance(m, AssistantMessage):
        if m.content:
            return f"[assistant]: {m.content}"
        if m.tool_calls:
            calls = ", ".join(
                f"{tc.function.get('name', '?')}({tc.function.get('arguments', '')})"
                for tc in m.tool_calls
            )
            return f"[assistant tool_calls]: {calls}"
    return ""
