# -*- coding: utf-8 -*-
"""Project QwenPaw MCP servers into ACP ``session/new.mcpServers`` entries.

Why this exists
---------------
QwenPaw resolves the workspace's enabled MCP driver cards into
``HarnessMCPServerDefinition`` objects (``transport``/``command``/``args``/
``url``/``env``/``headers``...). ACP, in turn, transports a union of three
server flavours: ``McpServerStdio``, ``HttpMcpServer`` and ``SseMcpServer``.

DSH 0.2.0-rc.2 mounts these with ``failOnStartupError: true`` (spike s05), so
one unusable server makes the whole ``session/new`` call fail; the adapter owns
the "retry without MCP" recovery. This module is the pure mapping half:

* ``stdio`` -> :class:`McpServerStdio`. The command is resolved to an absolute
  path with :func:`shutil.which` (DSH spawns it directly); on a miss the value
  is kept verbatim and a warning is logged.
* ``http`` / ``streamable_http`` -> :class:`HttpMcpServer`.
* ``sse`` -> skipped with a log line (DSH does not support SSE).
* missing required fields / unknown transports -> skipped with a log line.

Every server is mapped independently: one failure never drops the rest. Secret
values (``env`` / ``headers``) are read through ``revealed_env()`` /
``revealed_headers()`` when the host offers them, and are **never logged**.

The host definitions are consumed duck-typed (pydantic model or plain ``dict``
/ ``SimpleNamespace``), mirroring :mod:`qwenpaw_dsh.core.skills`.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import Any, Iterable

from acp.schema import EnvVariable, HttpHeader, HttpMcpServer, McpServerStdio

log = logging.getLogger("qwenpaw.dsh")

STDIO_TRANSPORTS = frozenset({"stdio"})
HTTP_TRANSPORTS = frozenset({"http", "streamable_http"})
SSE_TRANSPORTS = frozenset({"sse"})


# --------------------------------------------------------------------------- host input
def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def mcp_sources(capabilities: Any) -> list[Any]:
    """Extract MCP server definitions from a runtime-capabilities object."""
    if capabilities is None:
        return []
    raw = _field(capabilities, "mcp_servers")
    if raw is None:
        raw = _field(capabilities, "mcpServers")
    if not isinstance(raw, (list, tuple)):
        return []
    return list(raw)


def runtime_mcp_servers(settings: dict[str, Any] | None) -> list[Any]:
    """MCP servers carried by ``settings["_runtime_capabilities"]``."""
    return mcp_sources((settings or {}).get("_runtime_capabilities"))


def _secret(value: Any) -> str:
    """Reveal one secret at the provider boundary (``SecretStr`` or plain)."""
    if hasattr(value, "get_secret_value"):
        return str(value.get_secret_value())
    return str(value)


def _reveal(obj: Any, field_name: str, method_name: str) -> dict[str, str]:
    """Return ``{name: plaintext}`` via ``revealed_*`` when present, else field."""
    method = getattr(obj, method_name, None)
    values = method() if callable(method) else _field(obj, field_name)
    if not isinstance(values, dict):
        return {}
    return {str(key): _secret(value) for key, value in values.items()}


# --------------------------------------------------------------------------- result
@dataclass(frozen=True)
class MCPProjection:
    """Outcome of one :func:`project_mcp_servers` call.

    ``names`` lists the servers that *mounted* (for the retry warning), while
    ``skipped`` holds ``(server_name, reason)`` pairs. No secret values are
    retained anywhere in this object.
    """

    servers: tuple[Any, ...] = ()
    names: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def has_servers(self) -> bool:
        return bool(self.servers)

    def as_list(self) -> list[Any]:
        """Fresh list for the ACP call (never share the tuple with SDK state)."""
        return list(self.servers)


# --------------------------------------------------------------------------- mapping
def map_server(server: Any) -> tuple[Any | None, str | None]:
    """Map one host definition to an ACP MCP server.

    Returns ``(acp_server, None)`` on success or ``(None, reason)`` when the
    server must be skipped. Never raises: all expected problems are signalled
    through ``reason`` so one bad server cannot break the others.
    """
    name = str(_field(server, "name") or "").strip()
    transport = str(_field(server, "transport") or "stdio").strip().lower()
    label = name or "<unnamed>"

    if not name:
        return None, "missing-name"

    if transport in SSE_TRANSPORTS:
        return None, "sse-unsupported"

    raw_args = _field(server, "args")
    args = (
        [str(item) for item in raw_args]
        if isinstance(raw_args, (list, tuple))
        else []
    )

    if transport in STDIO_TRANSPORTS:
        command = str(_field(server, "command") or "").strip()
        if not command:
            return None, "missing-command"
        resolved = shutil.which(command)
        if resolved is None:
            log.warning(
                "dsh: MCP server %r command %r not found on PATH; keeping as-is",
                label,
                command,
            )
            resolved = command
        env = _reveal(server, "env", "revealed_env")
        return (
            McpServerStdio(
                name=name,
                command=resolved,
                args=args,
                env=[
                    EnvVariable(name=key, value=value)
                    for key, value in sorted(env.items())
                ],
            ),
            None,
        )

    if transport in HTTP_TRANSPORTS:
        url = str(_field(server, "url") or "").strip()
        if not url:
            return None, "missing-url"
        headers = _reveal(server, "headers", "revealed_headers")
        return (
            HttpMcpServer(
                name=name,
                url=url,
                headers=[
                    HttpHeader(name=key, value=value)
                    for key, value in sorted(headers.items())
                ],
                type="http",
            ),
            None,
        )

    return None, f"unsupported-transport:{transport or 'unknown'}"


def project_mcp_servers(servers: Iterable[Any]) -> MCPProjection:
    """Map every host definition, isolating per-server failures."""
    built: list[Any] = []
    names: list[str] = []
    skipped: list[tuple[str, str]] = []
    for server in servers:
        label = str(_field(server, "name") or "").strip() or "<unnamed>"
        try:
            mapped, reason = map_server(server)
        except Exception:  # noqa: BLE001 — one bad server must not drop the rest
            log.warning("dsh: MCP server %r mapping failed; skipped", label, exc_info=True)
            skipped.append((label, "mapping-error"))
            continue
        if mapped is None:
            log.warning("dsh: MCP server %r skipped: %s", label, reason)
            skipped.append((label, reason or "skipped"))
            continue
        built.append(mapped)
        names.append(label)
    return MCPProjection(
        servers=tuple(built),
        names=tuple(names),
        skipped=tuple(skipped),
    )
