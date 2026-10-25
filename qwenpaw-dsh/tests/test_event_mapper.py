#!/usr/bin/env python3
"""v1.1.1 regression: ACP tool-result text projection.

The defect: ``ToolCallProgress.content`` carries ``ContentToolCallContent``
wrappers whose inner ``TextContentBlock`` was dropped by ``text_of``, so the
host saw empty tool output. These tests pin the stable ACP shapes (real SDK
models + equivalent dicts), ordering, non-text safety, bounded recursion and
the tool-only truncation marker.

All cases are offline: no DSH process, no profile, no credentials, no model.
Run: python -m unittest discover -s tests -p 'test_event_mapper.py' -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acp.schema import (  # noqa: E402
    AgentMessageChunk,
    AgentThoughtChunk,
    AudioContentBlock,
    BlobResourceContents,
    ContentToolCallContent,
    EmbeddedResourceContentBlock,
    FileEditToolCallContent,
    ImageContentBlock,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from qwenpaw_dsh.core import event_mapper  # noqa: E402
from qwenpaw_dsh.core.event_mapper import map_update, text_of  # noqa: E402


def text_block(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def content_wrapper(*blocks) -> list[ContentToolCallContent]:
    return [
        ContentToolCallContent(type="content", content=block) for block in blocks
    ]


def progress(
    *blocks,
    status: str = "completed",
    call_id: str = "call_1",
    title: str = "read",
    **extra,
) -> ToolCallProgress:
    return ToolCallProgress(
        sessionUpdate="tool_call_update",
        toolCallId=call_id,
        title=title,
        status=status,
        content=list(blocks) if blocks else None,
        **extra,
    )


class ToolContentMappingTests(unittest.TestCase):
    def test_content_wrapper_completed_uses_inner_text(self) -> None:
        update = progress(*content_wrapper(text_block("FAKE-READ-NONCE")))
        event = map_update(update)
        self.assertEqual(event.kind.value, "tool_completed")
        self.assertEqual(event.text, "FAKE-READ-NONCE")
        self.assertEqual(event.item_id, "call_1")
        self.assertEqual(event.tool_name, "read")
        self.assertEqual(event.data["status"], "completed")

    def test_failed_status_still_tool_completed(self) -> None:
        update = progress(*content_wrapper(text_block("boom")), status="failed")
        event = map_update(update)
        self.assertEqual(event.kind.value, "tool_completed")
        self.assertEqual(event.data["status"], "failed")
        self.assertEqual(event.text, "boom")

    def test_in_progress_status_is_tool_progress(self) -> None:
        update = progress(*content_wrapper(text_block("partial")), status="in_progress")
        event = map_update(update)
        self.assertEqual(event.kind.value, "tool_progress")
        self.assertEqual(event.data["status"], "in_progress")
        self.assertEqual(event.text, "partial")

    def test_dict_payload_extracts_nested_text(self) -> None:
        payload = [
            {"type": "content", "content": {"type": "text", "text": "alpha"}},
            {"type": "content", "content": {"type": "text", "text": "beta"}},
        ]
        self.assertEqual(text_of(payload), "alphabeta")

    def test_multiple_blocks_preserve_order(self) -> None:
        update = progress(
            *content_wrapper(
                text_block("first"),
                text_block("second"),
                text_block("third"),
            )
        )
        self.assertEqual(map_update(update).text, "firstsecondthird")

    def test_empty_and_null_content_safe(self) -> None:
        self.assertEqual(map_update(progress()).text, "")
        self.assertEqual(map_update(progress(*[])).text, "")
        self.assertEqual(text_of(None), "")
        self.assertEqual(text_of([]), "")
        self.assertEqual(text_of({}), "")

    def test_unknown_type_is_ignored(self) -> None:
        self.assertEqual(text_of({"type": "mystery", "text": "FAKE-SECRET"}), "")
        self.assertEqual(
            text_of(progress(*content_wrapper(text_block("ok")), status="completed")),
            "",
        )
        wrapper = {
            "type": "content",
            "content": {"type": "mystery", "text": "FAKE-SECRET"},
        }
        self.assertEqual(text_of(wrapper), "")

    def test_image_and_audio_base64_not_in_text(self) -> None:
        image = ImageContentBlock(
            type="image",
            data="RkFLRS1JTUFHRS1TRUNSRVQ=",
            mimeType="image/png",
        )
        audio = AudioContentBlock(
            type="audio",
            data="RkFLRS1BVURJTy1TRUNSRVQ=",
            mimeType="audio/wav",
        )
        update = progress(*content_wrapper(image, text_block("caption"), audio))
        event = map_update(update)
        self.assertEqual(event.text, "caption")
        self.assertNotIn("RkFLRQ", event.text)
        self.assertNotIn("image/png", event.text)
        self.assertNotIn("audio/wav", event.text)

    def test_resource_blob_not_in_text(self) -> None:
        blob = EmbeddedResourceContentBlock(
            type="resource",
            resource=BlobResourceContents(
                uri="file:///tmp/fake.bin",
                blob="RkFLRS1CTE9CLVNFQ1JFVA==",
                mimeType="application/octet-stream",
            ),
        )
        update = progress(*content_wrapper(blob, text_block("after")))
        event = map_update(update)
        self.assertEqual(event.text, "after")
        self.assertNotIn("RkFLRQ", event.text)

    def test_terminal_and_diff_not_rendered(self) -> None:
        update = progress(
            TerminalToolCallContent(type="terminal", terminalId="term-1"),
            FileEditToolCallContent(
                type="diff",
                path="/tmp/fake.txt",
                oldText="OLD-FAKE",
                newText="NEW-FAKE",
            ),
            *content_wrapper(text_block("visible")),
            FileEditToolCallContent(
                type="diff",
                path="/tmp/second.txt",
                oldText="OLD2-FAKE",
                newText="NEW2-FAKE",
            ),
        )
        event = map_update(update)
        self.assertEqual(event.text, "visible")
        for leaked in ("term-1", "OLD-FAKE", "NEW-FAKE", "OLD2-FAKE", "NEW2-FAKE"):
            self.assertNotIn(leaked, event.text)

    def test_object_repr_secret_not_leaked(self) -> None:
        class SecretBlock:
            def __str__(self) -> str:
                return "FAKE-SECRET-REPR"

            __repr__ = __str__

        self.assertEqual(text_of(SecretBlock()), "")
        self.assertEqual(text_of([SecretBlock()]), "")
        event = map_update(progress(rawOutput={"stdout": "FAKE-SECRET-RAW"}))
        self.assertEqual(event.text, "")
        self.assertNotIn("FAKE-SECRET", event.text)

    def test_self_referential_list_returns(self) -> None:
        loop: list[object] = []
        loop.append(loop)
        self.assertEqual(text_of(loop), "")

    def test_self_referential_mapping_returns(self) -> None:
        loop: dict[str, object] = {"type": "content"}
        loop["content"] = loop
        self.assertEqual(text_of(loop), "")

    def test_deep_nesting_bounded(self) -> None:
        nested: object = {"type": "text", "text": "too-deep"}
        for _ in range(50):
            nested = {"type": "content", "content": nested}
        self.assertEqual(text_of(nested), "")

    def test_tool_text_truncated_with_marker(self) -> None:
        long_text = "x" * 40_000
        update = progress(*content_wrapper(text_block(long_text)))
        event = map_update(update)
        limit = event_mapper.MAX_TOOL_TEXT_CHARS
        omitted = 40_000 - limit
        expected_marker = event_mapper.TRUNCATION_TEMPLATE.format(omitted=omitted)
        self.assertTrue(event.text.startswith("x" * limit))
        self.assertTrue(event.text.endswith(expected_marker))
        self.assertEqual(len(event.text), limit + len(expected_marker))
        self.assertIn("truncated", event.text)

    def test_tool_text_at_limit_not_truncated(self) -> None:
        at_limit = "y" * event_mapper.MAX_TOOL_TEXT_CHARS
        update = progress(*content_wrapper(text_block(at_limit)))
        self.assertEqual(map_update(update).text, at_limit)

    def test_agent_text_and_thought_not_truncated(self) -> None:
        big = "z" * 100_000
        message = AgentMessageChunk(
            sessionUpdate="agent_message_chunk", content=text_block(big)
        )
        thought = AgentThoughtChunk(
            sessionUpdate="agent_thought_chunk", content=text_block(big)
        )
        self.assertEqual(map_update(message).text, big)
        self.assertEqual(map_update(thought).text, big)

    def test_agent_text_real_sdk_shape(self) -> None:
        message = AgentMessageChunk(
            sessionUpdate="agent_message_chunk", content=text_block("hello")
        )
        event = map_update(message)
        self.assertEqual(event.kind.value, "text_delta")
        self.assertEqual(event.text, "hello")

    def test_tool_started_metadata_preserved(self) -> None:
        update = ToolCallStart(
            sessionUpdate="tool_call",
            toolCallId="call_9",
            title="read fixture",
            kind="read",
            status="in_progress",
            rawInput={"file_path": "/tmp/fake.txt"},
        )
        event = map_update(update)
        self.assertEqual(event.kind.value, "tool_started")
        self.assertEqual(event.item_id, "call_9")
        self.assertEqual(event.tool_name, "read fixture")
        self.assertEqual(event.data["kind"], "read")
        self.assertEqual(event.data["status"], "in_progress")
        self.assertEqual(event.data["arguments"], {"file_path": "/tmp/fake.txt"})

    def test_text_of_plain_string_and_legacy_no_type_text(self) -> None:
        class LegacyBlock:
            text = "legacy-object"

        self.assertEqual(text_of("plain"), "plain")
        self.assertEqual(text_of({"text": "legacy-dict"}), "legacy-dict")
        self.assertEqual(text_of(LegacyBlock()), "legacy-object")
        self.assertEqual(
            text_of({"text": {"type": "text", "text": "legacy-nested"}}),
            "legacy-nested",
        )


if __name__ == "__main__":
    unittest.main()
