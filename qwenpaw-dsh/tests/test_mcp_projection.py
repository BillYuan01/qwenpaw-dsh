#!/usr/bin/env python3
"""Standalone live check for the gate-4 MCP projection (no QwenPaw host).

Pipeline exercised end to end against the real ``dsh``:

1. deterministic: ``HarnessCapabilityResolver`` resolves the local workspace
   ``drivers/mcp/spike-mcp.yaml`` card into one stdio definition;
2. deterministic: that definition maps to an ACP ``McpServerStdio`` with an
   absolute command, its args preserved, and env plaintext carried through;
3. live: a turn on a session created with the good MCP server reaches the
   ``ping`` tool and gets ``PONG`` back;
4. live: a good+bad mix still creates a usable session (drop-MCP retry);
5. live: a plain turn with no MCP definitions still works.

Environment:
    DSH_INTEGRATION_PROFILE  dsh profile (default ``qwenpaw``)
    DSH_INTEGRATION_HOME     DSH_HOME (default ``~/.dsh``)
    DSH_MCP_WORKSPACE        QwenPaw workspace with the spike-mcp card
                             (default ``~/.qwenpaw/workspaces/dsh``)

Run: ~/.qwenpaw/venv/bin/python tests/test_mcp_projection.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core import mcp as mc  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402

PROFILE = os.environ.get("DSH_INTEGRATION_PROFILE", "qwenpaw")
DSH_HOME = Path(os.environ.get("DSH_INTEGRATION_HOME", str(Path.home() / ".dsh")))
DSH_BIN = shutil.which("dsh") or str(Path.home() / ".local/bin/dsh")
WORKSPACE = Path(
    os.environ.get("DSH_MCP_WORKSPACE", str(Path.home() / ".qwenpaw" / "workspaces" / "dsh")),
)
PING_PROMPT = (
    "Call the MCP tool named 'ping' (it may be namespaced, e.g. "
    "mcp__spike__ping) and reply with its exact output, then say DONE."
)

_results: list[tuple[str, bool | None, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    _results.append((name, ok, detail))
    marker = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{marker}] {name}" + (f" — {detail}" if detail else ""))


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
) -> tuple[str, str]:
    chunks: list[str] = []
    terminal = "none"
    async for event in adapter.run_turn(
        session_id=session_id,
        prompt=prompt,
        cwd=cwd,
        settings=settings,
    ):
        terminal = event.kind.value
        if terminal == "text_delta" and event.text:
            chunks.append(event.text)
    return "".join(chunks), terminal


async def resolve_spike_card() -> tuple[list, bool]:
    """Resolve the real workspace card; returns (definitions, spike_found)."""
    try:
        from qwenpaw.harnesses.capabilities.resolver import (
            HarnessCapabilityResolver,
        )
    except Exception as exc:  # noqa: BLE001 — host package only where installed
        print(f"[resolver] unavailable: {exc!r}")
        return [], False
    if not (WORKSPACE / "drivers" / "mcp").is_dir():
        print(f"[resolver] no MCP driver dir under {WORKSPACE}")
        return [], False
    resolver = HarnessCapabilityResolver(WORKSPACE)
    capabilities = await resolver.resolve({"channel": "console", "session_id": "gate4-precheck"})
    definitions = list(getattr(capabilities, "mcp_servers", []) or [])
    spike = [item for item in definitions if getattr(item, "name", "") == "spike-mcp"]
    return definitions, bool(spike)


def fake_capabilities(servers: list) -> SimpleNamespace:
    return SimpleNamespace(mcp_servers=list(servers), skills=[])


def bad_server():
    from qwenpaw.harnesses.capabilities.models import HarnessMCPServerDefinition

    return HarnessMCPServerDefinition(
        name="bad-mcp",
        display_name="bad-mcp",
        transport="stdio",
        command="/nonexistent/gate4-bad-mcp-binary",
        args=[],
    )


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
    try:
        import mcp  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"the 'mcp' package is required by the fixture server: {exc!r}")
        return 2

    definitions, spike_found = await resolve_spike_card()
    check("resolver yields the workspace spike-mcp stdio card", spike_found)
    if not spike_found:
        check("good-server live checks", None, "spike-mcp card unavailable")
        check("mixed good+bad live check", None, "spike-mcp card unavailable")
        return _summary()

    good = [item for item in definitions if getattr(item, "name", "") == "spike-mcp"][0]
    plan = mc.project_mcp_servers([good])
    check("spike-mcp maps to an ACP stdio server", len(plan.servers) == 1, repr(plan.skipped))
    if plan.servers:
        server = plan.servers[0]
        check("command projected as an absolute path", Path(server.command).is_absolute(), server.command)
        check("args preserved", [str(a) for a in good.args] == server.args, repr(server.args))

    from pydantic import SecretStr

    from qwenpaw.harnesses.capabilities.models import HarnessMCPServerDefinition

    secret = "gate4-plaintext-secret"
    env_good = HarnessMCPServerDefinition(
        name="spike-mcp-env",
        display_name="spike-mcp-env",
        transport="stdio",
        command=str(good.command),
        args=[str(item) for item in good.args],
        env={"GATE4_ENV": SecretStr(secret)},
    )
    env_plan = mc.project_mcp_servers([env_good])
    check(
        "env plaintext carried through revealed_env()",
        [
            (item.name, item.value)
            for item in (env_plan.servers[0].env if env_plan.servers else [])
        ]
        == [("GATE4_ENV", secret)],
        repr(env_plan.skipped),
    )

    work = Path(tempfile.mkdtemp(prefix="gate4-mcp-"))
    state_dir = work / "harnesses"
    host = HostContext(state_dir=state_dir, binary=DSH_BIN, profile=PROFILE)
    settings = {"binary": DSH_BIN, "profile": PROFILE}
    adapter = DshAdapter(state_dir=state_dir, settings=settings, host=host)
    activation = {"_runtime_capabilities": fake_capabilities([good])}

    try:
        key_before = adapter._pool_key()
        projected = adapter._refresh_mcp(
            {"_runtime_capabilities": fake_capabilities([good, bad_server()])}
        )
        check("MCP projection never joins the pool key", adapter._pool_key() == key_before)
        check(
            "mixed projection maps both servers (bad fails at DSH startup)",
            [server.name for server in projected.servers] == ["spike-mcp", "bad-mcp"],
            repr(projected.names),
        )

        # -- live: good MCP server reaches the tool -------------------------
        answer, terminal = await collect(
            adapter, "gate4-good", PING_PROMPT, work, activation,
        )
        print(f"[good] terminal={terminal} answer={answer.strip()[-200:]!r}")
        check("good server turn completed", terminal == "completed", terminal)
        check("MCP ping result PONG reached the model", "PONG" in answer.upper(), repr(answer[-120:]))

        # -- live: session/resume carries mcpServers ------------------------
        record = adapter._records.get("gate4-good", {})
        pooled = adapter._connections.get(adapter._pool_key())
        if record.get("acpSessionId") and pooled is not None:
            await pooled.close_session(record["acpSessionId"])
            await adapter._close_connections()
            with _LogCapture() as resume_logs:
                answer, terminal = await collect(
                    adapter, "gate4-good", "Reply with exactly: RESUMED", work, activation,
                )
            fell_back = any("resume failed" in line for line in resume_logs)
            print(
                f"[resume] terminal={terminal} answer={answer.strip()[:80]!r} "
                f"fell_back={fell_back}",
            )
            check(
                "resume turn stayed usable with MCP definitions",
                terminal == "completed" and "RESUMED" in answer.upper(),
                terminal,
            )
            check(
                "session/resume accepted mcpServers (no fallback to session/new)",
                not fell_back,
                "fell back to session/new" if fell_back else "resumed in place",
            )
        else:
            check("resume turn stayed usable with MCP definitions", None, "no session record")

        # -- live: good+bad mix still creates a session ---------------------
        mixed = {"_runtime_capabilities": fake_capabilities([good, bad_server()])}
        logging.getLogger("qwenpaw.dsh").setLevel(logging.WARNING)
        with _LogCapture() as captured:
            answer, terminal = await collect(
                adapter, "gate4-mixed", "Reply with exactly: READY", work, mixed,
            )
        print(f"[mixed] terminal={terminal} answer={answer.strip()[-160:]!r}")
        check("mixed good+bad turn completed (retry dropped MCP)", terminal == "completed", terminal)
        check("mixed turn answer reached the model", "READY" in answer.upper(), repr(answer[:120]))
        disabled = [line for line in captured if "MCP disabled" in line]
        check("bad server logged the MCP-disabled warning", bool(disabled), disabled[:1])
        check(
            "warning names the attempted servers",
            bool(disabled) and "spike-mcp" in disabled[0] and "bad-mcp" in disabled[0],
            disabled[:1],
        )

        # -- live: no definitions still works -------------------------------
        answer, terminal = await collect(
            adapter, "gate4-none", "Reply with exactly: NOMCP", work, {},
        )
        check("no-MCP turn works", "NOMCP" in answer.upper() and terminal == "completed", repr(answer[:80]))
        check(
            "no-MCP projection is empty",
            adapter._refresh_mcp({}).servers == (),
        )
    finally:
        await adapter.stop()

    return _summary()


class _LogCapture:
    """Capture qwenpaw.dsh log messages (including warnings) for assertions."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self._handler: logging.Handler | None = None

    def __enter__(self) -> list[str]:
        handler = logging.Handler()
        handler.emit = lambda record: self.messages.append(record.getMessage())  # type: ignore[method-assign]
        self._handler = handler
        logging.getLogger("qwenpaw.dsh").addHandler(handler)
        return self.messages

    def __exit__(self, *exc) -> None:
        if self._handler is not None:
            logging.getLogger("qwenpaw.dsh").removeHandler(self._handler)


def _summary() -> int:
    failures = [name for name, ok, _ in _results if ok is False]
    skips = [name for name, ok, _ in _results if ok is None]
    print(
        f"\n[mcp-projection] {len(_results) - len(failures) - len(skips)} passed, "
        f"{len(failures)} failed, {len(skips)} skipped",
    )
    for name in failures:
        print(f"  FAIL: {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
