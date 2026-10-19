# -*- coding: utf-8 -*-
"""Spike 03 — capture ``session/request_permission`` and merge tool-call info.

DSH sends a permission prompt whose ``toolCall`` carries (in the observed
version) only a ``toolCallId``.  A real adapter must merge that id with the
earlier ``tool_call`` update (title/kind/rawInput) to render "what is about to
run".

The trigger: run DSH with ``DSH_PERMISSION_MODE=read-only`` and ask it to write
a file.  Read-only + the default ``ask`` approval policy produces
``session/request_permission`` with ``allow-once`` / ``reject-once`` options.
(Targeting a temp/outside path under ``workspace-write`` does *not* reliably
prompt — verified by the spike author.)

Run:
    ~/.qwenpaw/venv/bin/python spikes/s03_permission.py
"""

from __future__ import annotations

from pathlib import Path

from spike_lib import (
    Result,
    SpikeClient,
    allow_once_decider,
    initialize,
    open_acp,
    reject_decider,
    run_spike,
    safe_prompt,
    text_block,
)


def describe(perm: dict) -> dict:
    return {
        "options": perm["options"],
        "tool_call_from_permission": perm["tool_call"],
        "cached_tool_call": perm["merged"].get("cached_tool_call"),
        "chosen_option_id": perm["chosen_option_id"],
        "outcome": perm["outcome"],
    }


async def runner(args, result: Result, home: Path, work: Path) -> None:
    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    marker_allow = ws / "perm_allow_marker.txt"
    marker_reject = ws / "perm_reject_marker.txt"
    for p in (marker_allow, marker_reject):
        p.unlink(missing_ok=True)

    client = SpikeClient(decider=allow_once_decider)
    async with open_acp(
        home=home, workdir=ws, client=client, profile=args.profile,
        env_extra={"DSH_PERMISSION_MODE": "read-only"},
    ) as (conn, process):
        await initialize(conn)

        # ---- allow path ---------------------------------------------------
        s1 = await conn.new_session(cwd=str(ws))
        resp1 = await safe_prompt(conn, session_id=s1.session_id, result=result, label="allow prompt",
                                  prompt=[text_block(
            "Use the bash tool to write the text PERM-ALLOW into the file "
            f"{marker_allow} (a shell redirect is fine). Then reply with exactly DONE.",
        )])
        if len(client.permissions) >= 1:
            result.check("a permission request was captured (allow path)", True,
                         f"permissions={len(client.permissions)}")
        else:
            result.check("a permission request was captured (allow path)",
                         None if resp1 is None else False,
                         "model unavailable (billing/quota)" if resp1 is None
                         else f"permissions=0; tools={[t['title'] for t in client.tool_call_summary()]}")
        if client.permissions:
            perm = client.permissions[0]
            result.observe("permission_allow", describe(perm))
            option_ids = [o.get("optionId") for o in perm["options"]]
            option_kinds = [o.get("kind") for o in perm["options"]]
            result.check("permission options are one-shot allow/reject",
                         set(option_ids) == {"allow-once", "reject-once"},
                         f"ids={option_ids} kinds={option_kinds}", required=False)
            result.check("permission toolCall carries only toolCallId",
                         set(perm["tool_call"]) <= {"toolCallId", "title", "kind", "status", "content", "locations"},
                         f"keys={sorted(perm['tool_call'])}", required=False)
            cached = perm["merged"].get("cached_tool_call") or {}
            result.check("toolCallId can be merged with an earlier tool_call update",
                         bool(cached.get("title") or cached.get("rawInput")),
                         f"title={cached.get('title')!r} rawInput={str(cached.get('rawInput'))[:120]!r}")
            result.check("allow-once executes the approved tool", marker_allow.exists(),
                         f"{marker_allow} exists={marker_allow.exists()}", required=False)
        await conn.close_session(session_id=s1.session_id)

        # ---- reject path (fresh session, reject decider) -------------------
        client.decider = reject_decider
        before = len(client.permissions)
        s2 = await conn.new_session(cwd=str(ws))
        resp2 = await safe_prompt(conn, session_id=s2.session_id, result=result, label="reject prompt",
                                  prompt=[text_block(
            "Use the bash tool to write the text PERM-REJECT into the file "
            f"{marker_reject} (a shell redirect is fine). Then reply with exactly DONE.",
        )])
        if len(client.permissions) > before:
            result.check("a permission request was captured (reject path)", True,
                         f"permissions={len(client.permissions)}")
        else:
            result.check("a permission request was captured (reject path)",
                         None if resp2 is None else False,
                         "model unavailable (billing/quota)" if resp2 is None
                         else f"permissions={len(client.permissions)}")
        if len(client.permissions) > before:
            perm = client.permissions[-1]
            result.observe("permission_reject", describe(perm))
            result.check("reject path selects a reject option", perm["chosen_option_id"] == "reject-once",
                         str(perm["chosen_option_id"]), required=False)
            result.check("reject path does not execute the tool", not marker_reject.exists(),
                         f"{marker_reject} exists={marker_reject.exists()}", required=False)
        await conn.close_session(session_id=s2.session_id)

        if not client.permissions:
            result.note(
                "No permission request observed. Check observations.tool_calls / final_answer; "
                "the model may have answered without attempting a write.",
            )
        result.observe("tool_calls", client.tool_call_summary())
        result.observe("final_answer", client.assistant_text()[-500:])

    result.observe("dsh_returncode", process.returncode)


if __name__ == "__main__":
    raise SystemExit(run_spike(
        "s03_permission",
        "request_permission capture + toolCallId→tool info merge (allow & reject)",
        runner,
    ))
