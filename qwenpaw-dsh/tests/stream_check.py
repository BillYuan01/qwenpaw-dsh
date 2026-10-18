#!/usr/bin/env python3
"""Gate check: verify live streaming through the DSH adapter.

Usage:
  ~/.qwenpaw/venv/bin/python tests/stream_check.py [--profile qwenpaw]

PASS = a long answer arrives as many text chunks (>10).
Stock `acp` profile (committed mode) yields ~1 chunk for comparison:
  ... tests/stream_check.py --profile acp
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402


async def main(profile: str) -> int:
    work = Path("/tmp/dsh-stream-check")
    work.mkdir(parents=True, exist_ok=True)
    host = HostContext(state_dir=work)
    adapter = DshAdapter(state_dir=work, settings={"profile": profile}, host=host)
    try:
        status = await adapter.status()
        print(f"[status] available={status.available} error={status.error}")
        text_chunks = 0
        think_chunks = 0
        first_preview = ""
        async for event in adapter.run_turn(
            session_id="stream-check-1",
            prompt=(
                "Write a single plain-prose paragraph of about 200 words "
                "describing the ocean. No headings, no lists, no tools."
            ),
            cwd=work,
            settings={},
        ):
            if event.kind.value == "text_delta":
                text_chunks += 1
                if not first_preview:
                    first_preview = event.text[:40]
            elif event.kind.value == "reasoning_delta":
                think_chunks += 1
        print(f"[stream] text_chunks={text_chunks} think_chunks={think_chunks}")
        print(f"[stream] first_chunk={first_preview!r}")
        passed = text_chunks > 10
        print(f"[gate] profile={profile} streaming={'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1
    finally:
        await adapter.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="qwenpaw")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.profile)))
