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

from ..host import ApprovalDecision, ApprovalPort, ApprovalRequest

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


# ACP option ids are agent-chosen; the protocol ``kind`` carries the semantics.
# Approvals must pick the *narrowest* allow option: a one-shot grant is what the
# card asked for, while ``allow_always`` / persistent grants are not ours to
# hand out. DSH today offers exactly ``allow_once`` / ``reject_once``.
_PLAIN_ALLOW_TOKENS = frozenset({"allow", "yes", "approve"})
_PERSISTENT_MARKERS = ("always", "persist", "forever", "session")


def _option_field(option: Any, *names: str) -> str:
    for name in names:
        value = getattr(option, name, None)
        if value is None and isinstance(option, dict):
            value = option.get(name)
        if value is not None:
            return str(value)
    return ""


def _option_kind(option: Any) -> str:
    return _option_field(option, "kind").strip().lower()


def _option_id(option: Any) -> str:
    return _option_field(option, "optionId", "option_id").strip()


def _pick_allow_once(options: list[Any]) -> Any:
    """Pick the narrowest non-persistent allow option (allow-once first)."""
    for option in options:
        if _option_kind(option) == "allow_once":
            return option
    for option in options:
        if (
            _option_kind(option) in _PLAIN_ALLOW_TOKENS
            or _option_id(option).lower() in _PLAIN_ALLOW_TOKENS
        ):
            return option
    for option in options:
        text = f"{_option_kind(option)} {_option_id(option)}".lower()
        if "allow" in text and not any(marker in text for marker in _PERSISTENT_MARKERS):
            return option
    return None


def _pick_reject_option(options: list[Any]) -> Any:
    for option in options:
        if "reject" in _option_kind(option):
            return option
    for option in options:
        if "reject" in _option_id(option).lower():
            return option
    return None


class DshTurnClient:
    """Client-side callbacks for one dsh process.

    * ``session_update`` pushes every update onto ``self.queue`` for the
      adapter's turn loop to consume (kind tag: ``"update"``).
    * ``request_permission`` forwards the request to the host ``approval`` port
      (one approval card per escalation). Without a port it stays conservative
      and rejects, the v0.1 behaviour.
    """

    def __init__(
        self,
        *,
        cwd: str,
        trusted: bool = False,
        approval: ApprovalPort | None = None,
    ) -> None:
        self.cwd = cwd
        self.queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.approval = approval
        self._trusted = trusted
        self._permissions: Any = None
        # Routing context per ACP session id (one connection is pooled across
        # QwenPaw sessions, so context cannot live on the request itself until
        # the permission callback arrives).
        self._approval_contexts: dict[str, dict[str, Any]] = {}

    # -- approval routing --------------------------------------------------
    def set_approval_context(
        self,
        acp_session_id: str,
        context: dict[str, Any],
    ) -> None:
        key = str(acp_session_id or "")
        if key and context:
            self._approval_contexts[key] = dict(context)

    def clear_approval_context(self, acp_session_id: str) -> None:
        self._approval_contexts.pop(str(acp_session_id or ""), None)

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
        try:
            adapter = self._permission_adapter()
            suspended = adapter.build_suspended_permission(
                agent="dsh",
                tool_call=tool_call,
                options=list(options or []),
                prior_state=None,
            )
            reject_option = _pick_reject_option(suspended.options)
            label = suspended.summary or suspended.tool_name

            if self.approval is None:
                log.info(
                    "dsh: no approval port; auto-rejected permission (v0.1 "
                    "conservative): %s",
                    label,
                )
                return self._rejected(adapter, reject_option)

            decision = await self._request_approval(
                session_id,
                suspended,
                label=label,
            )
            status = str(getattr(decision, "status", decision) or "").lower()
            if status == "approved":
                allow_option = _pick_allow_once(suspended.options)
                if allow_option is not None:
                    return adapter.selected_response(allow_option)
                log.warning(
                    "dsh: approval granted but no one-shot allow option; "
                    "rejecting instead: %s",
                    label,
                )
            else:
                log.info("dsh: permission %s: %s", status or "rejected", label)
            return self._rejected(adapter, reject_option)
        except Exception:  # noqa: BLE001 — never crash a turn on plumbing
            log.exception("dsh: permission handling failed; cancelling")
            try:
                adapter = self._permission_adapter()
                return adapter.cancelled_response()
            except Exception:  # noqa: BLE001
                raise _unsupported("session/request_permission") from None

    async def _request_approval(
        self,
        session_id: str,
        suspended: Any,
        *,
        label: str,
    ) -> ApprovalDecision:
        """Send one card; a broken port is a rejection, never an exception."""
        approval = self.approval
        if approval is None:
            return ApprovalDecision(status="rejected")
        try:
            return await approval.request(
                ApprovalRequest(
                    title=label or "DSH permission request",
                    body=self._permission_body(suspended),
                    severity=self._permission_severity(suspended),
                    payload=self._permission_payload(suspended),
                    context=dict(
                        self._approval_contexts.get(str(session_id or "")) or {},
                    ),
                ),
            )
        except Exception:  # noqa: BLE001 — conservative reject on port failure
            log.exception("dsh: approval port failed; rejecting")
            return ApprovalDecision(status="rejected")

    @staticmethod
    def _rejected(adapter: Any, reject_option: Any) -> Any:
        if reject_option is not None:
            return adapter.selected_response(reject_option)
        return adapter.cancelled_response()

    @staticmethod
    def _permission_body(suspended: Any) -> str:
        if suspended.command:
            return str(suspended.command)
        if suspended.paths:
            return ", ".join(str(path) for path in suspended.paths)
        return str(suspended.summary or suspended.tool_name or "")

    @staticmethod
    def _permission_severity(suspended: Any) -> str:
        kind = str(suspended.tool_kind or "").lower()
        if suspended.command or kind in {
            "execute",
            "edit",
            "delete",
            "move",
            "write",
        }:
            return "high"
        return "medium"

    @staticmethod
    def _permission_payload(suspended: Any) -> dict[str, Any]:
        payload = suspended.payload if isinstance(suspended.payload, dict) else {}
        tool_call = payload.get("toolCall")
        return {
            "provider": "dsh",
            "tool_name": suspended.tool_name,
            "tool_kind": suspended.tool_kind,
            "target": suspended.target,
            "command": suspended.command,
            "paths": list(suspended.paths or []),
            "options": list(suspended.options or []),
            "tool_call": tool_call if isinstance(tool_call, dict) else {},
        }

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
        approval: ApprovalPort | None = None,
    ) -> "DshConnection":
        client = DshTurnClient(cwd=cwd, trusted=trusted, approval=approval)
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
