# -*- coding: utf-8 -*-
"""Spike 02 — session resume across a process restart.

Process A creates a session, plants a codeword, closes it and exits.
Process B starts a *fresh* ``dsh acp``, lists persisted sessions, resumes the
same ACP session id, and asks for the codeword back.  Negative cases check the
cwd-identity guard and the unknown-session error.

Run:
    ~/.qwenpaw/venv/bin/python spikes/s02_session_resume.py
"""

from __future__ import annotations

import time
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

CODEWORD = "BANANA-7734"


async def runner(args, result: Result, home: Path, work: Path) -> None:
    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)

    # ---- Process A: create + plant + close --------------------------------
    client_a = SpikeClient()
    async with open_acp(home=home, workdir=ws, client=client_a, profile=args.profile) as (conn, proc_a):
        await initialize(conn)
        new = await conn.new_session(cwd=str(ws))
        session_id = new.session_id
        result.observe("process_a_session_id", session_id)
        resp = await safe_prompt(conn, session_id=session_id, result=result, label="plant prompt",
                                 prompt=[text_block(
            f"Remember the codeword {CODEWORD}. Reply with exactly OK.",
        )])
        result.check("process A prompt completed", (resp.stop_reason == "end_turn") if resp else None,
                     str(getattr(resp, "stop_reason", "model unavailable")))
        await conn.close_session(session_id=session_id)
        result.check("process A closed session (persistence flush)", True)
    result.observe("process_a_returncode", proc_a.returncode)
    result.check("process A really exited", proc_a.returncode is not None, str(proc_a.returncode))

    # ---- Process B: list + resume + recall -------------------------------
    client_b = SpikeClient()
    async with open_acp(home=home, workdir=ws, client=client_b, profile=args.profile) as (conn, proc_b):
        await initialize(conn)

        listed = await conn.list_sessions(cwd=str(ws))
        listed_ids = [s.session_id for s in listed.sessions]
        result.observe("process_b_listed_sessions", [jsonable(s) for s in listed.sessions])
        result.check("session/list includes the closed session", session_id in listed_ids,
                     f"listed={listed_ids}")

        # Negative cases MUST run before the successful resume: resume activates
        # the session, after which any further resume reports "already active"
        # (not a cwd/id verdict).
        try:
            await conn.resume_session(cwd=str(ws), session_id="00000000-0000-0000-0000-000000000000")
            result.check("resume of unknown session id is rejected", False, "no error raised")
        except Exception as exc:  # noqa: BLE001
            result.check("resume of unknown session id is rejected", True,
                         f"{type(exc).__name__}: {str(exc)[:140]}")

        other = work / "other-ws"
        other.mkdir(parents=True, exist_ok=True)
        try:
            await conn.resume_session(cwd=str(other), session_id=session_id)
            result.check("resume with a different cwd is rejected", False, "no error raised")
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {str(exc)[:140]}"
            result.check("resume with a different cwd is rejected", True, detail)
            if "already active" in str(exc):
                result.note("wrong-cwd resume reported 'already active'; the cwd guard was "
                            "not isolated. Re-run order matters.")

        # resume
        updates_before = len(client_b.updates)
        t0 = time.monotonic()
        resumed = await conn.resume_session(cwd=str(ws), session_id=session_id)
        resume_s = round(time.monotonic() - t0, 3)
        result.observe("resume_latency_s", resume_s)
        result.observe("resume_response", jsonable(resumed))
        replayed = len(client_b.updates) - updates_before
        result.check("session/resume succeeds for same cwd", True, f"{resume_s}s")
        result.check("resume does not replay historical updates", replayed == 0, f"{replayed} updates during resume")

        recall_resp = await safe_prompt(conn, session_id=session_id, result=result, label="recall prompt",
                                        prompt=[text_block("What was the codeword? Reply with only the codeword.")])
        answer = client_b.assistant_text()
        result.observe("recall_answer", answer.strip()[:200])
        result.check("resumed session restored conversation context (codeword recalled)",
                     (CODEWORD.lower() in answer.lower()) if recall_resp is not None else None,
                     repr(answer[:120].strip()) if recall_resp is not None else "model unavailable")

    result.observe("process_b_returncode", proc_b.returncode)


if __name__ == "__main__":
    raise SystemExit(run_spike(
        "s02_session_resume",
        "session resume across a process restart (+ negative cwd/id cases)",
        runner,
    ))
