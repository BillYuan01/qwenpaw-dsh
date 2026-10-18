# -*- coding: utf-8 -*-
"""Live end-to-end probe for @local/dsh-qwenpaw-bridge.

Checks the v0.1 acceptance criteria against a real isolated DSH home:

  1. a long answer arrives as many live chunks (not one committed block);
  2. committed text is suppressed, so the transcript is not duplicated;
  3. `_meta['qwenpaw']` carries the complete model catalog + efforts;
  4. permission presets are advertised and switchable without a process restart;
  5. with live streaming disabled, the stock committed projection still works.

Run with the QwenPaw venv (it is the only interpreter with the ACP SDK):

    ~/.qwenpaw/venv/bin/python dsh-qwenpaw-bridge/test/probe.py \
        --dsh-home /tmp/dsh-bridge-probe --profile qwenpaw --seed user
    ~/.qwenpaw/venv/bin/python dsh-qwenpaw-bridge/test/probe.py ... --committed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "spikes"))

from acp import PROTOCOL_VERSION, text_block  # noqa: E402
from acp.schema import ClientCapabilities, Implementation  # noqa: E402
from spike_lib import (  # noqa: E402
    SpikeClient,
    allow_once_decider,
    dsh_version,
    initialize,
    jsonable,
    open_acp,
    seed_dsh_home,
)

LONG_PROMPT = (
    "Write a single plain-prose paragraph of about 300 words describing the ocean. "
    "No headings, no lists, no tools."
)


def text_chunks(client: SpikeClient) -> list[dict]:
    return [
        item for item in client.updates_of("agent_message_chunk")
        if (item["update"].get("content") or {}).get("type") == "text"
        and (item["update"].get("content") or {}).get("text")
    ]


async def main(args: argparse.Namespace) -> int:
    home = Path(args.dsh_home).expanduser().resolve()
    seed = seed_dsh_home(home, args.seed)
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)

    client = SpikeClient(decider=allow_once_decider)
    report: dict = {
        "dsh_version": dsh_version(),
        "profile": args.profile,
        "dsh_home": str(home),
        "seed": seed,
        "committed_mode_requested": args.committed,
        "checks": {},
        "observations": {},
    }

    async with open_acp(home=home, workdir=ws, client=client, profile=args.profile) as (conn, process):
        pid_before = process.pid
        if args.committed:
            init = await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="qwenpaw-dsh-bridge-probe", version="0.1.0"),
                qwenpaw={"streaming": False},
            )
        else:
            init = await initialize(conn)
        init_dump = jsonable(init)
        meta = ((init_dump.get("_meta") or {}).get("qwenpaw") or {})
        models = meta.get("models") or []
        report["observations"]["initialize_meta"] = meta
        report["checks"]["initialize_meta_present"] = bool(meta)
        report["checks"]["models_nonempty"] = len(models) > 0
        report["checks"]["every_model_has_efforts"] = all(
            isinstance(model.get("reasoning_efforts"), list) for model in models
        )
        report["checks"]["every_model_has_provider_and_selection"] = all(
            model.get("provider") and model.get("selection_value") for model in models
        )
        report["checks"]["presets_advertised"] = bool(meta.get("permission_presets"))
        report["checks"]["streaming_advertised"] = meta.get("streaming") is (not args.committed)

        new = await conn.new_session(cwd=str(ws))
        new_dump = jsonable(new)
        session_meta = ((new_dump.get("_meta") or {}).get("qwenpaw") or {})
        options = new_dump.get("configOptions") or []
        permission_option = next((o for o in options if o.get("id") == "permission_mode"), None)
        report["observations"]["session_meta"] = session_meta
        report["observations"]["config_option_ids"] = [o.get("id") for o in options]
        report["checks"]["session_meta_present"] = bool(session_meta)
        report["checks"]["session_meta_repeats_models"] = len(session_meta.get("models") or []) == len(models)
        report["checks"]["permission_config_option_present"] = permission_option is not None
        report["checks"]["permission_current_matches_meta"] = (
            permission_option is not None
            and permission_option.get("currentValue") == session_meta.get("permission_preset")
        )

        # --- long answer: live chunks + no committed duplicate -------------
        started = time.monotonic()
        response = await conn.prompt(session_id=new.session_id, prompt=[text_block(LONG_PROMPT)])
        elapsed = time.monotonic() - started
        chunks = text_chunks(client)
        live = [c for c in chunks if str(c["update"].get("messageId", "")).startswith("qwenpaw-live-")]
        committed = [c for c in chunks if c not in live]
        answer = client.assistant_text()
        report["observations"]["long_prompt"] = {
            "stop_reason": getattr(response, "stop_reason", None),
            "elapsed_s": round(elapsed, 2),
            "text_chunk_count": len(chunks),
            "live_chunk_count": len(live),
            "committed_text_chunk_count": len(committed),
            "answer_chars": len(answer),
            "chunk_lengths": [len(c["update"]["content"]["text"]) for c in chunks][:20],
            "first_chunk_offsets_s": [round(c["t"], 3) for c in chunks[:8]],
        }
        report["checks"]["answer_nonempty"] = len(answer) > 200
        if args.committed:
            report["checks"]["committed_mode_used"] = len(committed) == len(chunks) and len(committed) > 0
            report["checks"]["committed_mode_whole_step"] = len(committed) == 1
        else:
            report["checks"]["long_answer_incremental"] = len(chunks) > 5
            report["checks"]["no_committed_text_duplicate"] = len(committed) == 0
            report["checks"]["live_message_ids"] = all(
                str(c["update"].get("messageId", "")).startswith("qwenpaw-live-") for c in live
            )
        report["observations"]["answer_preview"] = answer[:240]

        # --- permission preset hot switch (same process) -------------------
        catalog = meta.get("permission_presets") or []
        target = next((p["id"] for p in catalog if p.get("id") != (permission_option or {}).get("currentValue")), None)
        if target is None:
            target = "read-only"
        before_value = (permission_option or {}).get("currentValue")
        switched = await conn.set_config_option(
            config_id="permission_mode",
            session_id=new.session_id,
            value=target,
        )
        switched_dump = jsonable(switched)
        switched_option = next(
            (o for o in (switched_dump.get("configOptions") or []) if o.get("id") == "permission_mode"),
            None,
        )
        report["observations"]["permission_switch"] = {
            "from": before_value,
            "to": target,
            "result_option": switched_option,
            "pid_before": pid_before,
            "pid_after": process.pid,
        }
        report["checks"]["permission_switch_result"] = (
            switched_option is not None and switched_option.get("currentValue") == target
        )
        report["checks"]["permission_switch_no_restart"] = process.pid == pid_before

        # switch back through the private method as well, proving both surfaces
        back = await conn.ext_method(
            "qwenpaw/permission/set",
            {"sessionId": new.session_id, "preset": before_value},
        )
        back_option = next(
            (o for o in (back.get("configOptions") or []) if o.get("id") == "permission_mode"),
            None,
        )
        report["checks"]["permission_switch_back"] = (
            back_option is not None
            and back_option.get("currentValue") == before_value
            and back.get("permission_preset") == before_value
        )

        # --- requested initial preset on session/new ------------------------
        requested_session = await conn.new_session(
            cwd=str(ws),
            qwenpaw={"permission_preset": target},
        )
        requested_dump = jsonable(requested_session)
        requested_meta = ((requested_dump.get("_meta") or {}).get("qwenpaw") or {})
        requested_option = next(
            (o for o in (requested_dump.get("configOptions") or []) if o.get("id") == "permission_mode"),
            None,
        )
        report["observations"]["requested_initial_preset"] = {
            "requested": target,
            "meta": requested_meta.get("permission_preset"),
            "option": (requested_option or {}).get("currentValue"),
        }
        report["checks"]["requested_initial_preset_applied"] = (
            requested_meta.get("permission_preset") == target
            and (requested_option or {}).get("currentValue") == target
        )
        await conn.close_session(session_id=requested_session.session_id)

        await conn.close_session(session_id=new.session_id)

    report["status"] = "pass" if all(report["checks"].values()) else "fail"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsh-home", default=os.environ.get("DSH_BRIDGE_HOME", str(ROOT / ".bridge-test" / "probe-home")))
    parser.add_argument("--work", default=os.environ.get("DSH_BRIDGE_WORK", str(ROOT / ".bridge-test" / "probe-work")))
    parser.add_argument("--seed", choices=("user", "fresh"), default="user")
    parser.add_argument("--profile", default="qwenpaw")
    parser.add_argument("--committed", action="store_true", help="ask the bridge for committed mode and assert the fallback")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
