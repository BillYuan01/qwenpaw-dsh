#!/usr/bin/env python3
"""Standalone smoke test for the DSH adapter core (no QwenPaw host needed).

Run: ~/.qwenpaw/venv/bin/python tests/smoke_adapter.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402

WORK = Path("/tmp/dsh-adapter-smoke")


async def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    host = HostContext(state_dir=WORK)
    adapter = DshAdapter(state_dir=WORK, settings={}, host=host)
    try:
        status = await adapter.status()
        print(
            f"[status] available={status.available} installed={status.installed} "
            f"error={status.error}",
        )

        models = await adapter.models()
        default = [m.id for m in models if m.is_default]
        print(f"[models] {len(models)} entries; default={default}")

        events = []
        async for event in adapter.run_turn(
            session_id="smoke-1",
            prompt="Reply with exactly: PONG",
            cwd=WORK,
            settings={},
        ):
            events.append(event)
            preview = (event.text or "").replace("\n", " ")[:70]
            print(f"[event] {event.kind.value:16s} {preview}")

        text = "".join(e.text for e in events if e.kind.value == "text_delta")
        terminal = events[-1].kind.value if events else "none"
        print(f"[result] terminal={terminal} assistant_text={text!r}")
        ok = "PONG" in text and terminal == "completed"
        print("[smoke]", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        await adapter.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
