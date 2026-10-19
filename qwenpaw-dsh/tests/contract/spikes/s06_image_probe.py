# -*- coding: utf-8 -*-
"""Spike 06 — image capability probe.

Answers two questions:

* does ``initialize`` advertise ``promptCapabilities.image == true`` for the
  active profile/route?  (It depends on the durable attachment store being
  mounted *and* the exact selected route declaring image input.)
* if yes, does an inline base64 PNG actually reach the model?

When the capability is false the spike still *tries* an image prompt once, to
document the failure mode a QwenPaw adapter must guard against.

Run:
    ~/.qwenpaw/venv/bin/python spikes/s06_image_probe.py --seed user
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

from spike_lib import (
    Result,
    SpikeClient,
    initialize,
    jsonable,
    open_acp,
    run_spike,
    safe_prompt,
    text_block,
)
from acp import image_block


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Hand-rolled solid-colour PNG (no Pillow dependency)."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


async def runner(args, result: Result, home: Path, work: Path) -> None:
    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    png = solid_png(64, 64, (255, 0, 0))
    png_path = work / "red.png"
    png_path.write_bytes(png)
    result.observe("png_bytes", len(png))

    client = SpikeClient()
    async with open_acp(home=home, workdir=ws, client=client, profile=args.profile) as (conn, process):
        init = await initialize(conn)
        caps = jsonable(init)
        prompt_caps = (caps.get("agentCapabilities") or {}).get("promptCapabilities") or {}
        result.observe("promptCapabilities", prompt_caps)
        image_ok = bool(prompt_caps.get("image"))
        result.check("initialize advertises promptCapabilities.image", image_ok,
                     f"image={prompt_caps.get('image')} (route-dependent)", required=False)

        new = await conn.new_session(cwd=str(ws))
        sid = new.session_id
        config_options = [jsonable(o) for o in (new.config_options or [])]
        model_opt = next((o for o in config_options if o.get("id") == "model"), None)
        result.observe("selected_model", (model_opt or {}).get("currentValue"))
        result.observe("route_note",
                       "image needs BOTH the attachment store and an image-capable route")

        data_b64 = base64.b64encode(png).decode("ascii")
        image = image_block(data_b64, "image/png")
        prompt = [
            text_block("What single colour fills this image? Answer with one word."),
            image,
        ]
        try:
            response = await safe_prompt(conn, session_id=sid, result=result, label="image prompt", prompt=prompt)
        except Exception as exc:  # noqa: BLE001
            result.observe("image_prompt_error", f"{type(exc).__name__}: {str(exc)[:300]}")
            if image_ok:
                result.check("inline PNG prompt is accepted", False,
                             f"{type(exc).__name__}: {str(exc)[:200]}")
            else:
                result.check("image prompt rejected while capability is false", True,
                             f"{type(exc).__name__}: {str(exc)[:160]} "
                             "(confirms the adapter must feature-detect first)", required=False)
        else:
            if response is None:
                result.check("inline PNG prompt is accepted", None, "model unavailable (billing/quota)",
                             required=image_ok)
                result.check("model identifies the image colour (red)", None, "model unavailable",
                             required=image_ok)
            else:
                answer = client.assistant_text()
                result.observe("image_prompt_stop_reason", response.stop_reason)
                result.observe("image_answer", answer.strip()[:200])
                result.check("inline PNG prompt is accepted", True, f"stopReason={response.stop_reason}",
                             required=image_ok)
                result.check("model identifies the image colour (red)", "red" in answer.lower(),
                             repr(answer[:120].strip()), required=image_ok)

        await conn.close_session(session_id=sid)
    result.observe("dsh_returncode", process.returncode)


if __name__ == "__main__":
    raise SystemExit(run_spike(
        "s06_image_probe",
        "image capability probe (promptCapabilities.image + real inline PNG prompt)",
        runner,
    ))
