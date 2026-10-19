#!/usr/bin/env python3
"""Unit tests for the gate-5 image attachments (inline base64 + degradation).

Covers the pure codec (suffix -> MIME, base64 correctness, size/unreadable
guards), the capability probe (``initialize`` -> ``promptCapabilities.image``),
the ACP block construction/order, and the adapter's attachment split.

Run: ~/.qwenpaw/venv/bin/python tests/test_images.py
"""

from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acp.schema import ImageContentBlock, TextContentBlock  # noqa: E402

from qwenpaw.harnesses.events import (  # noqa: E402
    HarnessAttachment,
    HarnessAttachmentKind,
)
from qwenpaw_dsh.core import images as image_codec  # noqa: E402
from qwenpaw_dsh.core.acp_client import (  # noqa: E402
    DshConnection,
    image_supported,
    prompt_capabilities_of,
)
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-body"


def attachment(kind: HarnessAttachmentKind, path: Path, name: str = "") -> HarnessAttachment:
    return HarnessAttachment(kind=kind, path=path, name=name)


class MimeMappingTests(unittest.TestCase):
    def test_supported_suffix_table(self) -> None:
        cases = {
            "a.png": "image/png",
            "a.jpeg": "image/jpeg",
            "a.jpg": "image/jpeg",
            "a.gif": "image/gif",
            "a.webp": "image/webp",
            "a.bmp": "image/bmp",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(image_codec.mime_for_path(name), expected)

    def test_suffix_match_is_case_insensitive(self) -> None:
        self.assertEqual(image_codec.mime_for_path("PHOTO.PNG"), "image/png")
        self.assertEqual(image_codec.mime_for_path("Photo.JpEg"), "image/jpeg")

    def test_unknown_suffix_is_not_guessed(self) -> None:
        for name in ("a.tiff", "a.svg", "a.heic", "noext", "a."):
            with self.subTest(name=name):
                self.assertIsNone(image_codec.mime_for_path(name))


class EncodeImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_base64_is_exact(self) -> None:
        path = self.dir / "red.png"
        path.write_bytes(PNG_BYTES)
        encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(reason)
        self.assertEqual(encoded.mime, "image/png")
        self.assertEqual(encoded.data, base64.b64encode(PNG_BYTES).decode("ascii"))
        self.assertEqual(base64.b64decode(encoded.data), PNG_BYTES)

    def test_unknown_suffix_degrades_without_reading(self) -> None:
        path = self.dir / "notes.tiff"
        path.write_bytes(PNG_BYTES)
        encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(encoded)
        self.assertEqual(reason, "unknown-suffix:.tiff")

    def test_missing_file_is_unreadable(self) -> None:
        encoded, reason = image_codec.encode_image(self.dir / "gone.png")
        self.assertIsNone(encoded)
        self.assertEqual(reason, "unreadable")

    def test_directory_is_unreadable(self) -> None:
        target = self.dir / "adir.png"
        target.mkdir()
        encoded, reason = image_codec.encode_image(target)
        self.assertIsNone(encoded)
        self.assertEqual(reason, "unreadable")

    def test_oversize_degrades(self) -> None:
        path = self.dir / "big.png"
        path.write_bytes(b"x" * 32)
        with mock.patch.object(image_codec, "MAX_IMAGE_BYTES", 16):
            encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(encoded)
        self.assertEqual(reason, "too-large")

    def test_size_exactly_at_limit_is_allowed(self) -> None:
        path = self.dir / "limit.png"
        path.write_bytes(b"y" * 16)
        with mock.patch.object(image_codec, "MAX_IMAGE_BYTES", 16):
            encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(reason)
        self.assertEqual(len(base64.b64decode(encoded.data)), 16)


class CapabilityProbeTests(unittest.TestCase):
    def test_extract_from_object(self) -> None:
        init = SimpleNamespace(
            agent_capabilities=SimpleNamespace(
                prompt_capabilities=SimpleNamespace(image=True, audio=False),
            ),
        )
        caps = prompt_capabilities_of(init)
        self.assertTrue(caps.image)
        self.assertTrue(image_supported(caps))

    def test_extract_from_dict_snake_and_camel(self) -> None:
        self.assertEqual(
            prompt_capabilities_of(
                {"agent_capabilities": {"prompt_capabilities": {"image": True}}},
            ),
            {"image": True},
        )
        self.assertEqual(
            prompt_capabilities_of(
                {"agentCapabilities": {"promptCapabilities": {"image": False}}},
            ),
            {"image": False},
        )

    def test_missing_blocks_are_unsupported(self) -> None:
        self.assertFalse(image_supported(prompt_capabilities_of(None)))
        self.assertFalse(image_supported(prompt_capabilities_of({})))
        self.assertFalse(
            image_supported(prompt_capabilities_of({"agentCapabilities": {}})),
        )
        self.assertFalse(image_supported({}))
        self.assertFalse(image_supported({"image": False}))
        self.assertFalse(image_supported(None))
        self.assertTrue(image_supported({"image": True}))


class _BlockRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list, str]] = []

    async def prompt(self, prompt, session_id):
        self.calls.append((prompt, session_id))
        return SimpleNamespace(stop_reason="end_turn")


def connection(recorder: _BlockRecorder, capabilities=None) -> DshConnection:
    return DshConnection(
        stack=None,
        conn=recorder,
        process=SimpleNamespace(returncode=None),
        client=None,
        key=("dsh", "acp"),
        prompt_capabilities=capabilities,
    )


class PromptBlockTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_images_behaviour_is_unchanged(self) -> None:
        recorder = _BlockRecorder()
        conn = connection(recorder, {"image": True})
        await conn.prompt("sess", "hello")
        blocks, session_id = recorder.calls[0]
        self.assertEqual(session_id, "sess")
        self.assertEqual(len(blocks), 1)
        self.assertIsInstance(blocks[0], TextContentBlock)
        self.assertEqual(blocks[0].text, "hello")

    async def test_empty_image_list_behaviour_is_unchanged(self) -> None:
        recorder = _BlockRecorder()
        conn = connection(recorder)
        await conn.prompt("sess", "hello", images=[])
        (blocks, _), = recorder.calls
        self.assertEqual([type(block) for block in blocks], [TextContentBlock])

    async def test_images_follow_text_in_order(self) -> None:
        recorder = _BlockRecorder()
        conn = connection(recorder, {"image": True})
        await conn.prompt(
            "sess",
            "what colour?",
            images=[("AAA", "image/png"), ("BBB", "image/jpeg")],
        )
        blocks, _ = recorder.calls[0]
        self.assertEqual(
            [type(block) for block in blocks],
            [TextContentBlock, ImageContentBlock, ImageContentBlock],
        )
        self.assertEqual(blocks[0].text, "what colour?")
        self.assertEqual(blocks[1].data, "AAA")
        self.assertEqual(blocks[1].mime_type, "image/png")
        self.assertEqual(blocks[2].data, "BBB")
        self.assertEqual(blocks[2].mime_type, "image/jpeg")

    def test_supports_image_reflects_probe(self) -> None:
        self.assertTrue(connection(_BlockRecorder(), {"image": True}).supports_image)
        self.assertFalse(connection(_BlockRecorder(), {"image": False}).supports_image)
        self.assertFalse(connection(_BlockRecorder(), None).supports_image)


class ComposePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _png(self, name: str = "red.png") -> tuple[Path, str]:
        path = self.dir / name
        path.write_bytes(PNG_BYTES)
        return path, base64.b64encode(PNG_BYTES).decode("ascii")

    def test_no_attachments_is_unchanged(self) -> None:
        self.assertEqual(DshAdapter._compose_prompt("hi", None), ("hi", []))
        self.assertEqual(DshAdapter._compose_prompt("hi", []), ("hi", []))

    def test_file_attachment_keeps_text_path(self) -> None:
        path = self.dir / "notes.txt"
        text, images = DshAdapter._compose_prompt(
            "see this",
            [attachment(HarnessAttachmentKind.FILE, path, "notes.txt")],
            supports_image=True,
        )
        self.assertEqual(images, [])
        self.assertEqual(text, f"see this\n\nAttached file notes.txt: {path}")

    def test_image_on_capable_connection_is_inlined_without_extra_text(self) -> None:
        path, encoded = self._png()
        text, images = DshAdapter._compose_prompt(
            "colour?",
            [attachment(HarnessAttachmentKind.IMAGE, path, "red.png")],
            supports_image=True,
        )
        self.assertEqual(text, "colour?")
        self.assertEqual(images, [(encoded, "image/png")])

    def test_capability_false_degrades_to_text_path(self) -> None:
        path, _ = self._png()
        with self.assertLogs("qwenpaw.dsh", level="INFO") as logs:
            text, images = DshAdapter._compose_prompt(
                "colour?",
                [attachment(HarnessAttachmentKind.IMAGE, path, "red.png")],
                supports_image=False,
            )
        self.assertEqual(images, [])
        self.assertEqual(text, f"colour?\n\nAttached file red.png: {path}")
        self.assertIn("does not advertise image prompts", "\n".join(logs.output))

    def test_unknown_suffix_degrades_with_warning(self) -> None:
        path = self.dir / "photo.tiff"
        path.write_bytes(PNG_BYTES)
        with self.assertLogs("qwenpaw.dsh", level="WARNING") as logs:
            text, images = DshAdapter._compose_prompt(
                "colour?",
                [attachment(HarnessAttachmentKind.IMAGE, path, "photo.tiff")],
                supports_image=True,
            )
        self.assertEqual(images, [])
        self.assertIn(f"Attached file photo.tiff: {path}", text)
        joined = "\n".join(logs.output)
        self.assertIn("unknown-suffix:.tiff", joined)

    def test_unreadable_image_degrades_with_warning(self) -> None:
        path = self.dir / "missing.png"
        with self.assertLogs("qwenpaw.dsh", level="WARNING") as logs:
            text, images = DshAdapter._compose_prompt(
                "colour?",
                [attachment(HarnessAttachmentKind.IMAGE, path, "missing.png")],
                supports_image=True,
            )
        self.assertEqual(images, [])
        self.assertIn("unreadable", "\n".join(logs.output))
        self.assertIn(f"Attached file missing.png: {path}", text)

    def test_oversize_image_degrades_with_warning(self) -> None:
        path = self.dir / "big.png"
        path.write_bytes(b"z" * 32)
        with mock.patch.object(image_codec, "MAX_IMAGE_BYTES", 16):
            with self.assertLogs("qwenpaw.dsh", level="WARNING") as logs:
                text, images = DshAdapter._compose_prompt(
                    "colour?",
                    [attachment(HarnessAttachmentKind.IMAGE, path, "big.png")],
                    supports_image=True,
                )
        self.assertEqual(images, [])
        self.assertIn("too-large", "\n".join(logs.output))
        self.assertIn(f"Attached file big.png: {path}", text)

    def test_multiple_images_keep_attachment_order(self) -> None:
        first, first_b64 = self._png("first.png")
        second, second_b64 = self._png("second.jpeg")
        text, images = DshAdapter._compose_prompt(
            "two",
            [
                attachment(HarnessAttachmentKind.IMAGE, first, "first.png"),
                attachment(HarnessAttachmentKind.IMAGE, second, "second.jpeg"),
            ],
            supports_image=True,
        )
        self.assertEqual(text, "two")
        self.assertEqual(
            images,
            [(first_b64, "image/png"), (second_b64, "image/jpeg")],
        )

    def test_mixed_attachments_inline_image_and_text_file(self) -> None:
        image_path, encoded = self._png()
        file_path = self.dir / "doc.pdf"
        text, images = DshAdapter._compose_prompt(
            "look",
            [
                attachment(HarnessAttachmentKind.FILE, file_path, "doc.pdf"),
                attachment(HarnessAttachmentKind.IMAGE, image_path, "red.png"),
            ],
            supports_image=True,
        )
        self.assertEqual(images, [(encoded, "image/png")])
        self.assertIn(f"Attached file doc.pdf: {file_path}", text)
        self.assertNotIn("red.png", text)

    def test_fallback_name_uses_path_basename(self) -> None:
        file_path = self.dir / "plain.txt"
        text, _ = DshAdapter._compose_prompt(
            "x",
            [attachment(HarnessAttachmentKind.FILE, file_path)],
        )
        self.assertIn("Attached file plain.txt:", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
