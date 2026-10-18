# -*- coding: utf-8 -*-
"""Map ACP ``session/update`` payloads to provider-neutral HarnessEvents.

Pure functions — no I/O, no host imports beyond the stable event contract.
"""

from __future__ import annotations

from typing import Any

from qwenpaw.harnesses.events import HarnessEvent, HarnessEventKind


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Return the first present attribute (snake/camel tolerant)."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _plain(value: Any) -> str:
    """Normalize enums / strings to a lowercase plain string."""
    if value is None:
        return ""
    value = getattr(value, "value", value)
    return str(value).lower()


def text_of(content: Any) -> str:
    """Best-effort text extraction from an ACP content block (or list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "".join(text_of(item) for item in content)
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(content, dict):
        value = content.get("text")
        return value if isinstance(value, str) else ""
    return ""


def map_update(update: Any) -> HarnessEvent | None:
    """Convert one ACP update object into a HarnessEvent (None = skip)."""
    from acp.schema import (
        AgentMessageChunk,
        AgentThoughtChunk,
        ToolCallProgress,
        ToolCallStart,
    )

    if isinstance(update, AgentMessageChunk):
        return HarnessEvent(
            kind=HarnessEventKind.TEXT_DELTA,
            text=text_of(_attr(update, "content")),
        )
    if isinstance(update, AgentThoughtChunk):
        return HarnessEvent(
            kind=HarnessEventKind.REASONING_DELTA,
            text=text_of(_attr(update, "content")),
        )
    if isinstance(update, ToolCallStart):
        return HarnessEvent(
            kind=HarnessEventKind.TOOL_STARTED,
            item_id=str(_attr(update, "tool_call_id", "toolCallId", default="")),
            tool_name=str(_attr(update, "title", default="")),
            data={
                "arguments": _attr(update, "raw_input", "rawInput"),
                "kind": _plain(_attr(update, "kind")),
                "status": _plain(_attr(update, "status")),
            },
        )
    if isinstance(update, ToolCallProgress):
        status = _plain(_attr(update, "status"))
        finished = status in ("completed", "failed")
        return HarnessEvent(
            kind=(
                HarnessEventKind.TOOL_COMPLETED
                if finished
                else HarnessEventKind.TOOL_PROGRESS
            ),
            item_id=str(_attr(update, "tool_call_id", "toolCallId", default="")),
            tool_name=str(_attr(update, "title", default="")),
            text=text_of(_attr(update, "content")),
            data={"status": status},
        )
    # usage_update / config_option_update / plan / mode updates: no channel yet.
    return None


def map_stop_reason(stop_reason: Any) -> HarnessEventKind:
    """Map an ACP prompt stopReason to the terminal HarnessEvent kind."""
    value = _plain(stop_reason)
    if value in ("cancelled", "canceled"):
        return HarnessEventKind.CANCELLED
    if value in ("error", "refusal", "max_tokens"):
        return HarnessEventKind.ERROR
    return HarnessEventKind.COMPLETED
