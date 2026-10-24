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
from itertools import count
from typing import Any

from acp import PROTOCOL_VERSION, image_block, spawn_agent_process, text_block
from acp.schema import ClientCapabilities, Implementation

from ..host import ApprovalDecision, ApprovalPort, ApprovalRequest

log = logging.getLogger("qwenpaw.dsh")

_STDIO_LIMIT = 50 * 1024 * 1024
_CLIENT_NAME = "qwenpaw-dsh"
_CLIENT_VERSION = "1.1.0"
_GENERATIONS = count(1)

# Upper bound for closing a half-open spawn stack after a setup failure.
# ``wait_for`` is not an upper bound when the close swallows cancellation, so
# the close runs as its own task and is only awaited until this deadline.
_SETUP_CLOSE_TIMEOUT = 5.0
# Close tasks that outlived their caller (deadline expired, or a second
# cancellation interrupted the wait). Kept referenced so neither the task nor
# its exception is dropped; they are never awaited past the deadline.
_FAILED_SETUP_TASKS: set[asyncio.Task] = set()

# Sentinel pushed into every attached turn queue when the connection is
# retired/stopped. Consumers must not depend on the transport stack failing a
# pending prompt: a fake (or half-dead) stack may never notify anyone.
TURN_CLOSED = "__connection_closed__"


class SessionLifecycleError(RuntimeError):
    """The ACP session could not be moved to the requested configuration."""


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


def _reap_failed_setup(task: asyncio.Task) -> None:
    """Consume a setup-close task's outcome so it never leaks as unretrieved."""
    _FAILED_SETUP_TASKS.discard(task)
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:  # noqa: BLE001 — retrieval is best effort
        pass


def _track_failed_setup(task: asyncio.Task) -> None:
    _FAILED_SETUP_TASKS.add(task)
    task.add_done_callback(_reap_failed_setup)


def _kill_setup_process(process: Any) -> bool:
    """Best-effort kill of a child spawned by a setup that never completed.

    ``True`` only means a pid was handed to the killer; it does not prove the
    process is gone. Callers must not report "terminated" on this alone.
    """
    try:
        pid = int(getattr(process, "pid", 0) or 0)
    except Exception:  # noqa: BLE001
        return False
    if not pid:
        return False
    try:
        kill_process_tree(pid)
    except Exception:  # noqa: BLE001
        return False
    return True


async def _bounded_stack_close(stack: AsyncExitStack, process: Any) -> bool:
    """Close a half-open spawn stack with a hard wait deadline.

    ``AsyncExitStack.aclose()`` may be swallowed by a transport/server that
    ignores cancellation, so awaiting it directly is unbounded. The close runs
    as its own task: ``asyncio.wait`` never cancels it and we stop *waiting*
    for it at the deadline. On expiry the child process tree is killed best
    effort and ``False`` is returned — closure was not confirmed, and saying
    so is the only honest report available.

    A finished close task is not proof of closure either: a normal return (no
    exception, not cancelled) is the only confirmed outcome. An exception or a
    cancellation is reported as unconfirmed and the child process tree is
    killed best effort; the task is finished, so it is never described as
    still running.
    """
    closer = asyncio.ensure_future(stack.aclose())
    try:
        done, _pending = await asyncio.wait(
            {closer},
            timeout=_SETUP_CLOSE_TIMEOUT,
        )
    except asyncio.CancelledError:
        # A second cancellation must not drop the close task or the child: the
        # task stays tracked (and reaped) and the process is killed.
        _track_failed_setup(closer)
        _kill_setup_process(process)
        raise
    if closer in done:
        error: BaseException | None = None
        if closer.cancelled():
            error = asyncio.CancelledError()
        else:
            try:
                error = closer.exception()
            except asyncio.CancelledError:  # defensive: raced cancellation
                error = asyncio.CancelledError()
        if error is None:
            return True
        if isinstance(error, asyncio.CancelledError):
            log.warning(
                "dsh: half-open spawn stack close was cancelled before "
                "confirming closure; killing the child process tree "
                "(termination is not verified)",
            )
        else:
            log.warning(
                "dsh: half-open spawn stack close failed before confirming "
                "closure (%s); killing the child process tree (termination is "
                "not verified)",
                type(error).__name__,
            )
            log.debug("dsh: half-open spawn stack close error", exc_info=error)
        _kill_setup_process(process)
        return False
    _track_failed_setup(closer)
    log.warning(
        "dsh: half-open spawn stack did not confirm closure within %.1fs; "
        "killing the child process tree (the close task is still owned and "
        "may still be running)",
        _SETUP_CLOSE_TIMEOUT,
    )
    _kill_setup_process(process)
    return False


def _unsupported(name: str) -> Exception:
    try:
        from acp.exceptions import RequestError

        return RequestError(code=-32601, message=f"dsh client does not support {name}")
    except Exception:  # noqa: BLE001
        return RuntimeError(f"dsh client does not support {name}")


def _field(obj: Any, *names: str) -> Any:
    """Read the first present attribute/key (duck-typed object or dict)."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
        if isinstance(obj, dict) and name in obj:
            return obj[name]
    return None


def prompt_capabilities_of(initialize_response: Any) -> Any:
    """Extract ``agentCapabilities.promptCapabilities`` from ``initialize``.

    Dict/object compatible; a missing block returns ``None`` (unsupported).
    """
    agent = _field(initialize_response, "agent_capabilities", "agentCapabilities")
    return _field(agent, "prompt_capabilities", "promptCapabilities")


def image_supported(prompt_capabilities: Any) -> bool:
    """True only when the probe explicitly advertises inline image prompts."""
    return bool(_field(prompt_capabilities, "image"))


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

    * ``session_update`` routes every update to the queue attached for that
      ACP session (kind tag: ``"update"``); frames for a session with no
      attached turn are dropped rather than delivered to another turn.
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
        self.approval = approval
        self._trusted = trusted
        self._permissions: Any = None
        # One queue per *attached turn*, keyed by ACP session id. A pooled
        # connection may carry concurrent QwenPaw sessions; frames for a
        # session with no attached turn are dropped instead of leaking into
        # another session's turn.
        self._turn_queues: dict[str, asyncio.Queue[tuple[str, Any]]] = {}
        # Routing context per ACP session id (one connection is pooled across
        # QwenPaw sessions, so context cannot live on the request itself until
        # the permission callback arrives).
        self._approval_contexts: dict[str, dict[str, Any]] = {}

    # -- turn routing ------------------------------------------------------
    def attach_session(self, acp_session_id: str) -> asyncio.Queue[tuple[str, Any]]:
        """Register the queue that receives updates for one running turn."""
        key = str(acp_session_id or "")
        if not key:
            raise ValueError("attach_session requires an ACP session id")
        existing = self._turn_queues.get(key)
        if existing is not None:
            # The adapter serializes turns per QwenPaw session, so a second
            # attach means a stale queue survived: replace it and warn.
            log.warning("dsh: replacing a stale turn queue for %s", key)
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._turn_queues[key] = queue
        return queue

    def detach_session(
        self,
        acp_session_id: str,
        queue: asyncio.Queue[tuple[str, Any]] | None = None,
    ) -> None:
        key = str(acp_session_id or "")
        if queue is not None and self._turn_queues.get(key) is not queue:
            return
        self._turn_queues.pop(key, None)

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

    def clear_turn_state(self) -> None:
        """Drop every queue and approval route (connection is going away)."""
        self._turn_queues.clear()
        self._approval_contexts.clear()

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
        queue = self._turn_queues.get(str(session_id or ""))
        if queue is None:
            # Late frame after cancel/timeout, or a session without an active
            # turn (for example a models() probe): never route it elsewhere.
            log.debug("dsh: dropping update for unattached session %s", session_id)
            return
        await queue.put(("update", (session_id, update)))

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
        prompt_capabilities: Any = None,
    ) -> None:
        self.stack = stack
        self.conn = conn
        self.process = process
        self.client = client
        self.key = key
        # Monotonic identity: a replacement process reuses the pool key but
        # never this generation, so session mappings can tell them apart.
        self.generation = next(_GENERATIONS)
        self.active_turns = 0
        self.retiring = False
        self.retired = False
        # Consumers (turn queues) currently attached to this connection. When
        # the connection is retired every one of them is woken explicitly:
        # closing the stack alone is not a notification the client can rely on.
        self._turn_queues: list[asyncio.Queue[tuple[str, Any]]] = []
        self._closed = asyncio.Event()
        self._last_new_session_responses: dict[str, Any] = {}
        # Last known full config-option state per ACP session (memory only).
        # Updated from session/new, session/resume and every confirmed
        # ``set_config_option`` response, so a turn can read the *actual*
        # current model/effort without starting a probe session.
        self._config_options: dict[str, list[Any]] = {}
        # ``initialize`` -> ``agentCapabilities.promptCapabilities`` (None when
        # the agent did not advertise one; treated as "no image prompts").
        self._prompt_capabilities = prompt_capabilities

    @property
    def alive(self) -> bool:
        return (
            not self.retired
            and getattr(self.process, "returncode", None) is None
        )

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
        process: Any = None
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
            return cls(
                stack=stack,
                conn=conn,
                process=process,
                client=client,
                key=key,
                prompt_capabilities=prompt_capabilities_of(initialized),
            )
        except BaseException:
            # Setup failed or was cancelled mid-spawn: close what was already
            # entered. The wait is bounded and survives a second cancellation
            # (the close task stays tracked, the child is killed best effort).
            # A failing/cancelled *close* is reported as unconfirmed inside the
            # helper and must never replace the original setup error; only a
            # cancellation of this task itself keeps propagating.
            try:
                await _bounded_stack_close(stack, process)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — cleanup must not mask the cause
                log.warning(
                    "dsh: half-open spawn stack cleanup failed; keeping the "
                    "original setup error",
                    exc_info=True,
                )
            raise

    @property
    def closed(self) -> bool:
        """True once the connection has been closed or retired."""
        return self._closed.is_set()

    # -- turn consumers ----------------------------------------------------
    def register_turn(self, queue: asyncio.Queue[tuple[str, Any]]) -> None:
        """Attach a turn queue so ``aclose`` can wake it explicitly.

        Registering after the connection is already closed pushes the sentinel
        immediately: a consumer must never wait on a dead connection.
        """
        if self._closed.is_set():
            queue.put_nowait((TURN_CLOSED, None))
            return
        if queue not in self._turn_queues:
            self._turn_queues.append(queue)

    def unregister_turn(self, queue: asyncio.Queue[tuple[str, Any]]) -> None:
        try:
            self._turn_queues.remove(queue)
        except ValueError:
            pass

    def _signal_closed(self) -> None:
        """Wake every attached turn exactly once (idempotent)."""
        if self._closed.is_set():
            return
        self._closed.set()
        queues = self._turn_queues
        self._turn_queues = []
        for queue in queues:
            queue.put_nowait((TURN_CLOSED, None))

    @property
    def prompt_capabilities(self) -> Any:
        """The raw prompt-capability probe (object or dict); read-only."""
        return self._prompt_capabilities

    @property
    def supports_image(self) -> bool:
        """Whether inline base64 image blocks may be sent on this connection."""
        return image_supported(self._prompt_capabilities)

    # -- session helpers ---------------------------------------------------
    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
    ) -> str:
        response = await self.conn.new_session(
            cwd=str(cwd),
            mcp_servers=mcp_servers,
        )
        session_id = str(getattr(response, "session_id", ""))
        if session_id:
            # One entry per ACP session: a concurrent models() probe must not
            # clobber the response a turn is about to read.
            self._last_new_session_responses[session_id] = response
            self._remember_options(session_id, response)
        return session_id

    def last_new_session_response(self, acp_session_id: str) -> Any:
        """The ``session/new`` response for this ACP session, if still held."""
        return self._last_new_session_responses.get(str(acp_session_id or ""))

    def current_config_options(self, acp_session_id: str) -> list[Any] | None:
        """Last known config options for this ACP session (``None`` if unknown).

        Sourced from ``session/new``, ``session/resume`` or a confirmed
        ``set_config_option`` response for *this* session — never guessed.
        """
        options = self._config_options.get(str(acp_session_id or ""))
        return list(options) if options is not None else None

    def remember_config_options(
        self,
        acp_session_id: str,
        options: list[Any],
    ) -> None:
        """Keep a confirmed option snapshot available to later turns."""
        key = str(acp_session_id or "")
        if key and isinstance(options, (list, tuple)):
            self._config_options[key] = list(options)

    def _remember_options(self, acp_session_id: str, response: Any) -> None:
        options = _field(response, "config_options", "configOptions")
        if isinstance(options, (list, tuple)):
            self._config_options[acp_session_id] = list(options)

    async def resume_session(
        self,
        cwd: str,
        acp_session_id: str,
        mcp_servers: list[Any] | None = None,
    ) -> str:
        response = await self.conn.resume_session(
            cwd=str(cwd),
            session_id=acp_session_id,
            mcp_servers=mcp_servers,
        )
        session_id = str(
            getattr(response, "session_id", acp_session_id) or acp_session_id,
        )
        if session_id:
            # dsh-acp's resume response carries ``configOptions`` but no
            # ``sessionId``; keep it so this session's actual model/effort is
            # known without a probe.
            self._last_new_session_responses.setdefault(session_id, response)
            self._remember_options(session_id, response)
        return session_id

    async def close_session(self, acp_session_id: str) -> bool:
        """Close one ACP session; ``False`` means it may still be live."""
        try:
            await self.conn.close_session(session_id=acp_session_id)
            self._last_new_session_responses.pop(str(acp_session_id or ""), None)
            self._config_options.pop(str(acp_session_id or ""), None)
            return True
        except Exception:  # noqa: BLE001 — caller decides how to fail closed
            log.warning("dsh: close_session failed", exc_info=True)
            return False

    async def cancel_session(self, acp_session_id: str) -> None:
        """Send one standard ACP ``session/cancel`` notification."""
        await self.conn.cancel(session_id=acp_session_id)

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

    async def prompt(
        self,
        acp_session_id: str,
        text: str,
        images: list[tuple[str, str]] | None = None,
    ) -> Any:
        """Send text plus optional inline ``(base64, mime)`` image blocks.

        Block order matches spike s06: the text block first, then images in
        attachment order. ``images=None`` / ``[]`` reproduces the v0.1 call
        exactly (a single text block).
        """
        blocks: list[Any] = [text_block(text)]
        for data, mime in images or []:
            blocks.append(image_block(data, mime))
        return await self.conn.prompt(
            prompt=blocks,
            session_id=acp_session_id,
        )

    async def aclose(self) -> None:
        if self.retired:
            return
        self.retired = True
        self.retiring = True
        # Wake consumers *before* touching the transport: this is what proves
        # a turn ended (with an error), independent of the stack failing the
        # pending prompt. Turn queues and approval routing are torn down here
        # as well; a late frame has nowhere to go.
        self._signal_closed()
        try:
            self.client.clear_turn_state()
        except Exception:  # noqa: BLE001 — teardown must not hide the close
            log.debug("dsh: could not clear turn state on close", exc_info=True)
        try:
            await self.stack.aclose()
        finally:
            try:
                kill_process_tree(int(self.process.pid))
            except Exception:  # noqa: BLE001
                pass
