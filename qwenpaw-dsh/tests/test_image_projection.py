#!/usr/bin/env python3
"""Standalone live check for the gate-5 image attachments (no QwenPaw host).

Pipeline exercised end to end against the real ``dsh``:

1. deterministic: suffix -> MIME and base64 for a hand-rolled red PNG;
2. live: ``initialize`` advertises ``promptCapabilities.image`` and the adapter
   sends a 64x64 solid-red PNG; the model answers "red";
3. live: a text-only turn is unaffected;
4. live: a ``FILE`` attachment still travels the v0.1 text path (the model reads
   the path and returns the file's token);
5. live (when the route does *not* advertise image): the same image attachment
   degrades to the text path without failing the turn.

Environment:
    DSH_INTEGRATION_PROFILE  dsh profile (default ``qwenpaw``)
    DSH_INTEGRATION_HOME     DSH_HOME (default ``~/.dsh``)

Run: ~/.qwenpaw/venv/bin/python tests/test_image_projection.py
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw.harnesses.events import (  # noqa: E402
    HarnessAttachment,
    HarnessAttachmentKind,
)
from qwenpaw_dsh.core import images as image_codec  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402

PROFILE = os.environ.get("DSH_INTEGRATION_PROFILE", "qwenpaw")
DSH_HOME = Path(os.environ.get("DSH_INTEGRATION_HOME", str(Path.home() / ".dsh")))
DSH_BIN = shutil.which("dsh") or str(Path.home() / ".local/bin/dsh")
IMAGE_PROMPT = "What single colour fills this image? Answer with one word."
FILE_TOKEN = "FILE-TOKEN-OK"

_results: list[tuple[str, bool | None, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    _results.append((name, ok, detail))
    marker = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{marker}] {name}" + (f" — {detail}" if detail else ""))


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Hand-rolled solid-colour PNG (no Pillow dependency; mirrors spike s06)."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def credentials_present() -> bool:
    if (DSH_HOME / ".credentials.yaml").exists():
        return True
    return any(
        os.environ.get(key)
        for key in ("DEEPSEEK_API_KEY", "OPENCODE_GO_API_KEY", "ANTHROPIC_API_KEY")
    )


async def collect(
    adapter: DshAdapter,
    session_id: str,
    prompt: str,
    cwd: Path,
    settings: dict,
    attachments: list[HarnessAttachment] | None = None,
) -> tuple[str, str]:
    chunks: list[str] = []
    terminal = "none"
    async for event in adapter.run_turn(
        session_id=session_id,
        prompt=prompt,
        cwd=cwd,
        settings=settings,
        attachments=attachments,
    ):
        terminal = event.kind.value
        if terminal == "text_delta" and event.text:
            chunks.append(event.text)
    return "".join(chunks), terminal


async def main() -> int:
    if not Path(DSH_BIN).exists():
        print("dsh binary not found; cannot run the live integration check")
        return 2
    if not (DSH_HOME / "profiles" / PROFILE).exists():
        print(f"profile {PROFILE!r} missing under {DSH_HOME}; skipping")
        return 2
    if not credentials_present():
        print("no model credentials found; skipping the live check")
        return 2

    # -- deterministic: the PNG helper and the codec ------------------------
    png = solid_png(64, 64, (255, 0, 0))
    work = Path(tempfile.mkdtemp(prefix="gate5-image-"))
    png_path = work / "red.png"
    png_path.write_bytes(png)
    encoded, reason = image_codec.encode_image(png_path)
    check("64x64 PNG generated", len(png) > 0, f"{len(png)} bytes")
    check("red.png -> image/png", image_codec.mime_for_path(png_path) == "image/png")
    check(
        "base64 round-trips to the PNG bytes",
        encoded is not None and reason is None and base64.b64decode(encoded.data) == png,
        repr(reason),
    )

    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    state_dir = work / "harnesses"
    host = HostContext(state_dir=state_dir, binary=DSH_BIN, profile=PROFILE)
    settings = {
        "binary": DSH_BIN,
        "profile": PROFILE,
        "permission_mode": "danger-full-access",
    }
    adapter = DshAdapter(state_dir=state_dir, settings=settings, host=host)
    image_attachment = HarnessAttachment(
        kind=HarnessAttachmentKind.IMAGE,
        path=png_path,
        name="red.png",
    )

    try:
        connection = await adapter._connection()
        supports_image = bool(connection.supports_image)
        check(
            "initialize advertises promptCapabilities.image",
            supports_image,
            f"promptCapabilities={connection.prompt_capabilities!r}",
        )

        # -- live: inline image (or graceful degrade) -----------------------
        answer, terminal = await collect(
            adapter,
            "gate5-image",
            IMAGE_PROMPT,
            ws,
            settings,
            [image_attachment],
        )
        print(f"[image] terminal={terminal} answer={answer.strip()[:200]!r}")
        check("image turn completed", terminal == "completed", terminal)
        if supports_image:
            check(
                "model identifies the image colour (red)",
                "red" in answer.lower(),
                repr(answer[:120].strip()),
            )
        else:
            check(
                "image turn degrades gracefully when the route has no image support",
                terminal == "completed",
                terminal,
            )
            check("model identifies the image colour (red)", None, "route image=false")

        # -- live: text-only path unaffected --------------------------------
        answer, terminal = await collect(
            adapter,
            "gate5-text",
            "Reply with exactly: TEXT-OK",
            ws,
            settings,
        )
        print(f"[text] terminal={terminal} answer={answer.strip()[:120]!r}")
        check(
            "text-only turn unaffected",
            terminal == "completed" and "TEXT-OK" in answer.upper(),
            repr(answer[:120]),
        )

        # -- live: FILE attachment still uses the text path -----------------
        token_path = ws / "token.txt"
        token_path.write_text(FILE_TOKEN, encoding="utf-8")
        file_attachment = HarnessAttachment(
            kind=HarnessAttachmentKind.FILE,
            path=token_path,
            name="token.txt",
        )
        answer, terminal = await collect(
            adapter,
            "gate5-file",
            "Read the attached file and reply with its exact contents.",
            ws,
            settings,
            [file_attachment],
        )
        print(f"[file] terminal={terminal} answer={answer.strip()[:160]!r}")
        check(
            "FILE attachment text path unaffected",
            terminal == "completed" and FILE_TOKEN in answer.upper(),
            repr(answer[:160]),
        )
    finally:
        await adapter.stop()

    return _summary()


def _summary() -> int:
    failures = [name for name, ok, _ in _results if ok is False]
    skips = [name for name, ok, _ in _results if ok is None]
    print(
        f"\n[image-projection] {len(_results) - len(failures) - len(skips)} passed, "
        f"{len(failures)} failed, {len(skips)} skipped",
    )
    for name in failures:
        print(f"  FAIL: {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    logging.getLogger("qwenpaw.dsh").setLevel(logging.WARNING)
    raise SystemExit(asyncio.run(main()))
