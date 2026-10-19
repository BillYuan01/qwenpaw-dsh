# -*- coding: utf-8 -*-
"""Spike 01 — ACP full chain + streaming granularity.

Covers: initialize → session/new → prompt → set_config_option → cancel →
session/close, measuring latency and, above all, the *granularity* of
``session/update`` (is text delivered per token, per segment, or as one whole
committed message per model step?).

Run:
    ~/.qwenpaw/venv/bin/python spikes/s01_acp_roundtrip.py
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from spike_lib import (
    Result,
    SpikeClient,
    allow_once_decider,
    initialize,
    is_billing_error,
    jsonable,
    open_acp,
    run_spike,
    safe_prompt,
)


def analyze_granularity(updates: list[dict]) -> dict:
    def chunks(kind: str):
        return [
            (u["t"], (u["update"].get("content") or {}).get("text", ""))
            for u in updates
            if u["kind"] == kind and (u["update"].get("content") or {}).get("type") == "text"
        ]

    text_chunks = chunks("agent_message_chunk")
    thought_chunks = chunks("agent_thought_chunk")
    lengths = [len(t) for _, t in text_chunks]
    gaps = [
        round(text_chunks[i][0] - text_chunks[i - 1][0], 4)
        for i in range(1, len(text_chunks))
    ]
    total = sum(lengths)
    if not text_chunks:
        verdict = "no-text-chunks"
    elif len(text_chunks) == 1:
        verdict = f"whole-step / committed (1 chunk of {total} chars)"
    elif max(lengths) >= 0.9 * total:
        verdict = f"whole-step / committed (1 dominant chunk of {max(lengths)} chars of {total})"
    else:
        avg = total / len(text_chunks)
        verdict = f"incremental (many chunks: n={len(text_chunks)}, avg={avg:.0f} chars, max={max(lengths)})"
    return {
        "text_chunk_count": len(text_chunks),
        "text_total_chars": total,
        "text_chunk_lengths": lengths,
        "text_chunk_gaps_s": gaps,
        "text_chunk_max_len": max(lengths) if lengths else 0,
        "text_chunk_median_len": statistics.median(lengths) if lengths else 0,
        "thought_chunk_count": len(thought_chunks),
        "thought_total_chars": sum(len(t) for _, t in thought_chunks),
        "verdict": verdict,
        "timeline": [u["kind"] for u in updates],
    }


async def _exercise_cancel(conn, session_id: str, client: SpikeClient) -> dict:
    prompt = (
        "Write a 1500-word essay on the history of computing. "
        "Output only the essay text; do not use any tools."
    )
    from acp import text_block

    before = len(client.updates)
    task = asyncio.create_task(conn.prompt(session_id=session_id, prompt=[text_block(prompt)]))
    await asyncio.sleep(2.0)
    cancelled_notification = True
    try:
        await conn.cancel(session_id=session_id)
    except Exception as exc:  # noqa: BLE001
        cancelled_notification = False
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = None
    try:
        response = await asyncio.wait_for(task, timeout=45)
        stop_reason = response.stop_reason
    except asyncio.TimeoutError:
        task.cancel()
        stop_reason = "<timeout>"
    except Exception as exc:  # noqa: BLE001
        stop_reason = None if is_billing_error(exc) else f"<error: {type(exc).__name__}: {exc}>"
    return {
        "cancel_notification_sent": cancelled_notification,
        "cancel_error": error,
        "stop_reason": stop_reason,
        "chunks_before_cancel": before,
        "chunks_after_cancel": len(client.updates) - before,
    }


async def runner(args, result: Result, home: Path, work: Path) -> None:
    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    client = SpikeClient(decider=allow_once_decider)

    async with open_acp(
        home=home, workdir=ws, client=client, profile=args.profile,
    ) as (conn, process):
        # 1) initialize -----------------------------------------------------
        t0 = time.monotonic()
        init = await initialize(conn)
        init_s = round(time.monotonic() - t0, 3)
        caps = jsonable(init)
        result.observe("initialize", caps)
        result.observe("latency_initialize_s", init_s)
        result.check("initialize.protocolVersion == 1", init.protocol_version == 1, str(init.protocol_version))
        result.check("initialize.authMethods empty (no login)", caps.get("authMethods") == [],
                     f"authMethods={caps.get('authMethods')}")
        session_caps = (caps.get("agentCapabilities") or {}).get("sessionCapabilities") or {}
        result.check("sessionCapabilities has list+resume+close",
                     all(k in session_caps for k in ("list", "resume", "close")),
                     f"keys={sorted(session_caps)}")
        prompt_caps = (caps.get("agentCapabilities") or {}).get("promptCapabilities") or {}
        result.observe("promptCapabilities", prompt_caps)

        # 2) session/new ----------------------------------------------------
        t0 = time.monotonic()
        new = await conn.new_session(cwd=str(ws))
        new_s = round(time.monotonic() - t0, 3)
        result.observe("latency_session_new_s", new_s)
        result.check("session/new returns a sessionId", bool(new.session_id), new.session_id)
        config_options = [jsonable(o) for o in (new.config_options or [])]
        result.observe("configOptions", config_options)
        ids = {o.get("id") for o in config_options}
        result.check("configOptions advertises 'model'", "model" in ids, f"ids={sorted(ids)}")

        # 3a) short prompt: correctness + basic latency ---------------------
        from acp import text_block
        t0 = time.monotonic()
        response = await safe_prompt(conn, session_id=new.session_id, result=result, label="short prompt",
                                     prompt=[text_block("Reply with exactly the single word PONG and nothing else.")])
        prompt_s = round(time.monotonic() - t0, 3)
        answer = client.assistant_text()
        result.observe("prompt_stop_reason", getattr(response, "stop_reason", None))
        result.observe("latency_first_prompt_s", prompt_s)
        result.observe("answer", answer.strip()[:200])
        model_ok = response is not None
        result.check("prompt stopReason == end_turn",
                     (response.stop_reason == "end_turn") if model_ok else None,
                     str(getattr(response, "stop_reason", "model unavailable")))
        result.check("assistant text contains PONG", ("PONG" in answer.upper()) if model_ok else None,
                     repr(answer[:80]) if model_ok else "model unavailable")
        result.observe("short_prompt_granularity", analyze_granularity(list(client.updates)))

        # 3b) long prompt: this is what actually distinguishes committed-message
        #     delivery (1 big chunk) from token/segment streaming (many chunks).
        mark = len(client.updates)
        t0 = time.monotonic()
        long_response = await safe_prompt(conn, session_id=new.session_id, result=result, label="long prompt",
                                          prompt=[text_block(
            "Write a single plain-prose paragraph of about 300 words describing the ocean. "
            "No headings, no lists, no tools.",
        )])
        long_s = round(time.monotonic() - t0, 3)
        result.observe("latency_long_prompt_s", long_s)
        result.observe("long_prompt_stop_reason", getattr(long_response, "stop_reason", None))
        gran = analyze_granularity(client.updates[mark:])
        result.observe("streaming_granularity", gran)
        usage = [
            u for u in client.updates[mark:] if u["kind"] == "usage_update"
        ]
        result.observe("usage_updates_during_long_prompt", [u["update"] for u in usage])
        result.check(
            "streaming granularity measured (non-empty long answer)",
            (gran["text_total_chars"] > 200) if long_response is not None else None,
            f"{gran['text_total_chars']} chars in {gran['text_chunk_count']} chunks",
        )
        result.check(
            "long-answer delivery is incremental (token/segment streaming)",
            gran["verdict"].startswith("incremental") if long_response is not None else None,
            gran["verdict"],
            required=False,
        )

        # 4) session/set_config_option round-trip --------------------------
        effort = next((o for o in config_options if o.get("id") == "reasoning_effort"), None)
        if effort:
            values = [v.get("value") for v in (effort.get("options") or [])]
            current = effort.get("currentValue")
            target = next((v for v in values if v != current), None)
            if target is not None:
                try:
                    updated = await conn.set_config_option(
                        config_id="reasoning_effort", session_id=new.session_id, value=target,
                    )
                    updated_opts = [jsonable(o) for o in (updated.config_options or [])]
                    new_current = next(
                        (o.get("currentValue") for o in updated_opts if o.get("id") == "reasoning_effort"), None,
                    )
                    result.observe("reasoning_effort_before_after", [current, new_current])
                    result.check(
                        "set_config_option(reasoning_effort) changes currentValue",
                        new_current == target, f"{current} -> {new_current} (target {target})",
                        required=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    result.check("set_config_option(reasoning_effort)", False,
                                 f"{type(exc).__name__}: {exc}", required=False)
            else:
                result.check("set_config_option(reasoning_effort)", None, "only one option value", required=False)
        else:
            result.check("set_config_option(reasoning_effort)", None, "option not advertised", required=False)

        # 5) cancel ---------------------------------------------------------
        cancel = await _exercise_cancel(conn, new.session_id, client)
        result.observe("cancel", cancel)
        result.check(
            "cancel path yields stopReason 'cancelled'",
            (cancel["stop_reason"] == "cancelled") if cancel["stop_reason"] is not None else None,
            f"stop_reason={cancel['stop_reason']}",
            required=False,
        )

        # 6) close ----------------------------------------------------------
        try:
            await conn.close_session(session_id=new.session_id)
            result.check("session/close succeeds", True)
        except Exception as exc:  # noqa: BLE001
            result.check("session/close succeeds", False, f"{type(exc).__name__}: {exc}")

    result.check("dsh process exited after close", process.returncode is not None,
                 f"returncode={process.returncode}", required=False)
    result.raw["update_kinds_ordered"] = [
        u["kind"] for u in client.updates
    ]


if __name__ == "__main__":
    raise SystemExit(run_spike(
        "s01_acp_roundtrip",
        "ACP full chain (initialize→new→prompt→cancel→close) + streaming granularity",
        runner,
    ))
