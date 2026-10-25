# -*- coding: utf-8 -*-
"""Map ACP ``session/update`` payloads to provider-neutral HarnessEvents.

Pure functions — no I/O, no host imports beyond the stable event contract.
"""

from __future__ import annotations

from typing import Any

from qwenpaw.harnesses.events import HarnessEvent, HarnessEventKind

# Tool output can be arbitrarily large. Host events are a UI projection only
# (they never feed the model context), so a bounded preview with an explicit
# marker is enough. Agent text / reasoning is *not* capped here.
MAX_TOOL_TEXT_CHARS = 32 * 1024
TRUNCATION_TEMPLATE = "\n…[qwenpaw-dsh: tool text truncated; {omitted} chars omitted]"

# ``type=content`` wrappers are shallow in ACP (one wrapper around one block).
# A small bound keeps hostile/self-referential payloads from recursing; it is
# not a general-purpose object dumper.
_MAX_CONTENT_DEPTH = 6

# Only these discriminators are text-bearing. Everything else (image, audio,
# resource blobs, terminal references, diffs) is deliberately projected as "".
_TEXT_TYPE = "text"
_WRAPPER_TYPE = "content"


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


def _kind(value: Any) -> str:
    """Read a content discriminator without stringifying objects.

    ``str()`` / ``repr()`` of an unknown object can carry secrets or binary
    payloads, so only real strings and enums are accepted here.
    """
    if isinstance(value, str):
        return value.lower()
    raw = getattr(value, "value", None)
    return raw.lower() if isinstance(raw, str) else ""


def _text_value(value: Any, depth: int, seen: set[int]) -> str:
    """Extract text from a ``text`` field (legacy payloads without ``type``)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return ""
    if isinstance(value, (dict, list, tuple)):
        return _walk(value, depth + 1, seen)
    if value is None:
        return ""
    # A text field that is itself a text block/object: allow one bounded hop.
    if hasattr(value, "text") or hasattr(value, "content"):
        return _walk(value, depth + 1, seen)
    return ""


def _from_mapping(value: dict[Any, Any], depth: int, seen: set[int]) -> str:
    kind = _kind(value.get("type"))
    if kind == _TEXT_TYPE:
        text = value.get("text")
        return text if isinstance(text, str) else ""
    if kind == _WRAPPER_TYPE:
        return _walk(value.get("content"), depth + 1, seen)
    if kind:
        # image / audio / resource / resource_link / diff / terminal / unknown
        return ""
    # Legacy payload with no discriminator: accept its ``text`` field only.
    return _text_value(value.get("text"), depth, seen)


def _from_object(value: Any, depth: int, seen: set[int]) -> str:
    kind = _kind(getattr(value, "type", None))
    if kind == _TEXT_TYPE:
        text = getattr(value, "text", None)
        return text if isinstance(text, str) else ""
    if kind == _WRAPPER_TYPE:
        return _walk(getattr(value, "content", None), depth + 1, seen)
    if kind:
        # Non-text block (image/audio/resource/diff/terminal/unknown): no text.
        return ""
    # Legacy payload with no discriminator: accept its ``text`` field only.
    return _text_value(getattr(value, "text", None), depth, seen)


def _walk(value: Any, depth: int, seen: set[int]) -> str:
    if value is None or depth > _MAX_CONTENT_DEPTH:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return ""
    if isinstance(value, (int, float, bool)):
        return ""

    ident = id(value)
    if ident in seen:  # self-reference / cycle
        return ""
    if isinstance(value, (list, tuple)):
        seen.add(ident)
        try:
            # ACP content lists are ordered; preserve that order.
            return "".join(_walk(item, depth + 1, seen) for item in value)
        finally:
            seen.discard(ident)
    if isinstance(value, dict):
        seen.add(ident)
        try:
            return _from_mapping(value, depth, seen)
        finally:
            seen.discard(ident)
    if not hasattr(value, "text") and not hasattr(value, "content") and not hasattr(value, "type"):
        return ""
    seen.add(ident)
    try:
        return _from_object(value, depth, seen)
    finally:
        seen.discard(ident)


def text_of(content: Any, *, max_chars: int | None = None) -> str:
    """Extract text from one ACP content block, a list of blocks, or a string.

    Text is only taken from the stable, text-bearing shapes:

    * ``ContentToolCallContent(type="content")`` → its ``content`` block;
    * ``TextContentBlock(type="text")`` → its ``text``;
    * legacy payloads without ``type`` that carry a ``text`` field.

    Image/audio/resource/terminal/diff blocks contribute nothing: they are
    never stringified, expanded, or read from ``rawInput``/``rawOutput``.
    Nested lists keep their order; recursion is depth- and cycle-bounded.
    """
    text = _walk(content, 0, set())
    if max_chars is not None and len(text) > max_chars:
        omitted = len(text) - max_chars
        return text[:max_chars] + TRUNCATION_TEMPLATE.format(omitted=omitted)
    return text


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
            text=text_of(_attr(update, "content"), max_chars=MAX_TOOL_TEXT_CHARS),
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
