# -*- coding: utf-8 -*-
"""Minimal ACP client + connection wrapper for harness turns.

Built on the official ``acp`` SDK (``ClientSideConnection`` /
``spawn_agent_process``) and QwenPaw's own ``ACPPermissionAdapter`` for
permission responses. One connection drives one ``dsh --profile <p>`` process;
the adapter layer owns pooling and session mapping.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.schema import ClientCapabilities, Implementation

log = logging.getLogger("qwenpaw.dsh")

_STDIO_LIMIT = 50 * 1024 * 1024
_CLIENT_NAME = "qwenpaw-dsh"
_CLIENT_VERSION = "0.1.0"


def kill_process_tree(pid: int) -> None:
    """Best-effort recursive kill (mirrors agents/acp/service.py pattern)."""
    try:
        import psutil

        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            try:
                child.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _unsupported(name: str) -> Exception:
    try:
        from acp.exceptions import RequestError

        return RequestError(code=-32601, message=f"dsh client does not support {name}")
    except Exception:  # noqa: BLE001
        return RuntimeError(f"dsh client does not support {name}")


class DshTurnClient:
    """Client-side callbacks for one dsh process.

    * ``session_update`` pushes every update onto ``self.queue`` for the
      adapter's turn loop to consume (kind tag: ``"update"``).
    * ``request_permission`` is conservative in v0.1: it always rejects
      (approval cards are wired in a follow-up; the seam is ``HostContext``).
    """

    def __init__(self, *, cwd: str, trusted: bool = False) -> None:
        self.cwd = cwd
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._trusted = trusted
        self._permissions: Any = None

    # -- permissions -------------------------------------------------------
    def _permission_adapter(self) -> Any:
        if self._permissions is None:
            from qwenpaw.agents.acp.permissions import ACPPermissionAdapter

            self._permissions = ACPPermissionAdapter(
                self.cwd,
                trusted=self._trusted,
            )
        return self._permissions

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,
        tool_call: Any,
        **_: Any,
    ) -> Any:
        del session_id
        try:
            adapter = self._permission_adapter()
            suspended = adapter.build_suspended_permission(
                agent="dsh",
                tool_call=tool_call,
                options=list(options or []),
                prior_state=None,
            )
            rejected = next(
                (
                    option
                    for option in suspended.options
                    if "reject" in str(option.get("kind", "")).lower()
                ),
                None,
            )
            log.info(
                "dsh: auto-rejected permission (v0.1 conservative): %s",
                suspended.summary or suspended.tool_name,
            )
            if rejected is not None:
                return adapter.selected_response(rejected)
            return adapter.cancelled_response()
        except Exception:  # noqa: BLE001 — never crash a turn on plumbing
            log.exception("dsh: permission handling failed; cancelling")
            try:
                adapter = self._permission_adapter()
                return adapter.cancelled_response()
            except Exception:  # noqa: BLE001
                raise _unsupported("session/request_permission") from None

    # -- updates -----------------------------------------------------------
    async def session_update(
        self,
        session_id: str,
        update: Any,
        **_: Any,
    ) -> None:
        await self.queue.put(("update", (session_id, update)))

    # -- unsupported client surfaces --------------------------------------
    async def write_text_file(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise _unsupported("fs/write_text_file")

    async def read_text_file(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise _unsupported("fs/read_text_file")

    async def create_terminal(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise _unsupported("terminal/create")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise _unsupported(f"ext/{method}")


class DshConnection:
    """One live ``dsh --profile <p>`` process + its ACP connection."""

    def __init__(
        self,
        *,
        stack: AsyncExitStack,
        conn: Any,
        process: Any,
        client: DshTurnClient,
        key: tuple,
    ) -> None:
        self.stack = stack
        self.conn = conn
        self.process = process
        self.client = client
        self.key = key
        self._last_new_session_response: Any = None

    @property
    def alive(self) -> bool:
        return getattr(self.process, "returncode", None) is None

    @classmethod
    async def open(
        cls,
        *,
        binary: str,
        args: list[str],
        env: dict[str, str],
        cwd: str,
        key: tuple,
        trusted: bool = False,
    ) -> "DshConnection":
        client = DshTurnClient(cwd=cwd, trusted=trusted)
        stack = AsyncExitStack()
        try:
            conn, process = await stack.enter_async_context(
                spawn_agent_process(
                    client,
                    binary,
                    *args,
                    cwd=cwd,
                    env=env,
                    transport_kwargs={"limit": _STDIO_LIMIT},
                ),
            )
            initialized = await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(
                    name=_CLIENT_NAME,
                    version=_CLIENT_VERSION,
                ),
            )
            if int(getattr(initialized, "protocol_version", 0)) != PROTOCOL_VERSION:
                raise RuntimeError(
                    "DSH ACP protocol mismatch: "
                    f"{getattr(initialized, 'protocol_version', None)}",
                )
            return cls(stack=stack, conn=conn, process=process, client=client, key=key)
        except Exception:
            await stack.aclose()
            raise

    # -- session helpers ---------------------------------------------------
    async def new_session(self, cwd: str) -> str:
        response = await self.conn.new_session(cwd=str(cwd))
        self._last_new_session_response = response
        return str(getattr(response, "session_id", ""))

    async def resume_session(self, cwd: str, acp_session_id: str) -> str:
        response = await self.conn.resume_session(
            cwd=str(cwd),
            session_id=acp_session_id,
        )
        return str(getattr(response, "session_id", acp_session_id) or acp_session_id)

    async def close_session(self, acp_session_id: str) -> None:
        try:
            await self.conn.close_session(session_id=acp_session_id)
        except Exception:  # noqa: BLE001 — closing is best-effort
            log.debug("dsh: close_session failed", exc_info=True)

    async def set_config_option(
        self,
        acp_session_id: str,
        config_id: str,
        value: Any,
    ) -> Any:
        return await self.conn.set_config_option(
            config_id=config_id,
            session_id=acp_session_id,
            value=value,
        )

    async def prompt(self, acp_session_id: str, text: str) -> Any:
        return await self.conn.prompt(
            prompt=[text_block(text)],
            session_id=acp_session_id,
        )

    async def aclose(self) -> None:
        try:
            await self.stack.aclose()
        finally:
            try:
                kill_process_tree(int(self.process.pid))
            except Exception:  # noqa: BLE001
                pass
