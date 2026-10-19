# -*- coding: utf-8 -*-
"""Spike 05 — ``session/new`` with MCP servers: good server, bad server, rollback.

DSH mounts ACP-provided MCP servers with ``failOnStartupError: true``: one bad
server makes the whole ``session/new`` fail.  The adapter strategy in plan.md
§3.5 is therefore "capture the failure, retry without MCP, report the bad
servers".  This spike proves both halves:

  1. good stdio MCP server → ``session/new`` succeeds and its tool is callable;
  2. bad server (missing binary) → ``session/new`` fails;
  3. retry with no MCP on the same connection → succeeds.

Run:
    ~/.qwenpaw/venv/bin/python spikes/s05_mcp.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from spike_lib import (
    Result,
    SpikeClient,
    initialize,
    open_acp,
    run_spike,
    safe_prompt,
    text_block,
)
from acp.schema import McpServerStdio

SERVER = Path(__file__).resolve().parent / "spike_mcp_server.py"


async def runner(args, result: Result, home: Path, work: Path) -> None:
    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)

    # Probe that the MCP server dependency exists with this interpreter.
    try:
        import mcp  # noqa: F401
        result.check("python env has the 'mcp' package (for the good server)", True, sys.executable)
    except Exception as exc:  # noqa: BLE001
        result.check("python env has the 'mcp' package (for the good server)", False,
                     f"{exc!r} — good-server checks will be inconclusive", required=False)

    good_server = McpServerStdio(
        name="spike", command=sys.executable, args=[str(SERVER)], env=[],
    )
    bad_server = McpServerStdio(
        name="bad", command="/nonexistent/dsh-spike-mcp-binary", args=[], env=[],
    )

    client = SpikeClient()
    async with open_acp(home=home, workdir=ws, client=client, profile=args.profile) as (conn, process):
        await initialize(conn)

        # 1) good server ----------------------------------------------------
        good = None
        try:
            good = await conn.new_session(cwd=str(ws), mcp_servers=[good_server])
            result.check("session/new with a good stdio MCP server succeeds",
                         bool(good.session_id), good.session_id)
        except Exception as exc:  # noqa: BLE001
            result.check("session/new with a good stdio MCP server succeeds", False,
                         f"{type(exc).__name__}: {str(exc)[:300]}")

        if good is not None:
            response = await safe_prompt(conn, session_id=good.session_id, result=result,
                                         label="MCP ping prompt", prompt=[text_block(
                "Call the MCP tool named 'ping' (it may be namespaced) and reply with its "
                "exact output, then say DONE.",
            )])
            answer = client.assistant_text()
            tools = client.tool_call_summary()
            result.observe("good_server_tool_calls", tools)
            result.observe("good_server_answer", answer.strip()[-400:])
            model_ok = response is not None
            result.check("MCP tool appears in the tool lifecycle",
                         (any("ping" in (t.get("title") or "").lower() or
                              "spike" in (t.get("title") or "").lower() for t in tools)) if model_ok else None,
                         f"titles={[t['title'] for t in tools]}" if model_ok else "model unavailable",
                         required=False)
            result.check("MCP tool result PONG reached the model",
                         ("PONG" in answer.upper()) if model_ok else None,
                         repr(answer[-120:].strip()) if model_ok else "model unavailable",
                         required=False)
            await conn.close_session(session_id=good.session_id)

        # 2) bad server -----------------------------------------------------
        bad_error = None
        try:
            await conn.new_session(cwd=str(ws), mcp_servers=[bad_server])
            result.check("session/new with a bad MCP server fails (failOnStartupError)", False,
                         "no error raised — rollback strategy would be unnecessary")
        except Exception as exc:  # noqa: BLE001
            bad_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            result.check("session/new with a bad MCP server fails (failOnStartupError)", True, bad_error)
        result.observe("bad_server_error", bad_error)

        # 3) rollback: same connection, no MCP ------------------------------
        try:
            fallback = await conn.new_session(cwd=str(ws))
            result.check("retry without MCP succeeds (rollback is viable)",
                         bool(fallback.session_id), fallback.session_id)
            await conn.close_session(session_id=fallback.session_id)
        except Exception as exc:  # noqa: BLE001
            result.check("retry without MCP succeeds (rollback is viable)", False,
                         f"{type(exc).__name__}: {str(exc)[:200]}")

    result.observe("dsh_returncode", process.returncode)


if __name__ == "__main__":
    raise SystemExit(run_spike(
        "s05_mcp",
        "MCP session/new with a good and a bad stdio server + failOnStartupError rollback",
        runner,
    ))
