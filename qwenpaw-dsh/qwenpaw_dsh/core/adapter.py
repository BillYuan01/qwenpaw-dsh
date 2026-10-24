# -*- coding: utf-8 -*-
"""DSH harness adapter — drives ``dsh --profile <p>`` over ACP v1.

Concerns in this file:
  * status / models (catalog probe) / lifecycle
  * process pool keyed by (binary, profile, permission_mode, approval_policy)
  * QwenPaw sessionId <-> ACP sessionId mapping with resume + persistence
  * run_turn: send prompt, stream mapped events until settlement

Nothing here imports ``qwenpaw.harnesses.registry`` or app routers — the loader
seam owns those.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qwenpaw.harnesses.base import HarnessAdapter
from qwenpaw.harnesses.events import (
    HarnessAttachment,
    HarnessEvent,
    HarnessEventKind,
    HarnessHistoryItem,
    HarnessModel,
    HarnessProvider,
)

from ..host import HostContext
from . import event_mapper as mapper
from . import images as image_codec
from . import mcp as mcp_projection
from . import skills as skills_projection
from .acp_client import (
    TURN_CLOSED,
    DshConnection,
    SessionLifecycleError,
    kill_process_tree,
)
from .permissions import resolve_permission_mode

log = logging.getLogger("qwenpaw.dsh")

_STATE_FILE = "dsh_sessions.json"
_MODELS_TTL_SECONDS = 600
# A failed catalog discovery is remembered briefly so a broken route is not
# probed on every turn. The error itself is never treated as a usable catalog.
_CATALOG_RETRY_SECONDS = 30.0

_CONTEXT_REBUILT_NOTICE = (
    "[DSH] The previous ACP session could not be resumed, so a new DSH "
    "session was created. Earlier conversation context is not available in "
    "this session."
)


class ModelSelectionError(RuntimeError):
    """An explicit model/effort selection could not be resolved or confirmed.

    Raised *before* the prompt is sent: the user asked for a specific model or
    reasoning effort, and quietly continuing with the profile default would be
    a silent downgrade.
    """


@dataclass(frozen=True)
class ModelCatalog:
    """One catalog snapshot: models, their selection values, and any failure.

    ``error`` is non-empty when discovery failed; ``models`` is then empty (the
    public ``models()`` contract stays a list), and selection code reports the
    failure instead of pretending an unknown model is simply absent.
    """

    fetched_at: float
    models: list[HarnessModel]
    value_map: dict[str, str]
    error: str = ""

    def fresh(self, now: float) -> bool:
        ttl = _CATALOG_RETRY_SECONDS if self.error else _MODELS_TTL_SECONDS
        return 0 <= now - self.fetched_at < ttl


@dataclass
class LiveSession:
    """One QwenPaw session bound to a concrete connection instance.

    ``fingerprint`` is a memory-only MCP digest (it may cover env/header
    secrets, so it is never persisted). ``degraded`` records that the session
    was created *without* the requested MCP servers after the retry path.
    """

    connection: Any
    acp_session_id: str
    fingerprint: str
    degraded: bool = False


@dataclass
class OpenSession:
    acp_session_id: str
    rebuilt: bool = False
    degraded: bool = False


def _active_turns(connection: Any) -> int:
    return int(getattr(connection, "active_turns", 0) or 0)


def _bump_turns(connection: Any, delta: int) -> None:
    try:
        connection.active_turns = _active_turns(connection) + delta
    except Exception:  # noqa: BLE001 — fakes in older tests may be read-only
        pass


def _bind_skills_snapshot(
    connection: Any,
    plan: skills_projection.SkillProjection | None,
) -> None:
    """Record which projection this connection was spawned with (memory only).

    Snapshots are never rewritten or deleted by another turn, so this is an
    ownership/diagnostic marker rather than a lifetime helper. Fakes used by
    older tests may not accept attributes; binding is best-effort.
    """
    try:
        connection.skills_snapshot = plan.snapshot if plan is not None else ""
        connection.skills_root = plan.shadow_root if plan is not None else None
    except Exception:  # noqa: BLE001 — duck-typed fakes
        pass


def _retrieve_task_error(task: asyncio.Task) -> None:
    """Consume a finished prompt task's outcome.

    Attached as a done callback so a prompt task that ends after the consumer
    stopped waiting (cancel/retire) can never surface as
    "Task exception was never retrieved".
    """
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:  # noqa: BLE001 — retrieval is best effort
        pass


def _clear_approval(connection: Any, acp_session_id: str) -> None:
    client = getattr(connection, "client", None)
    clear = getattr(client, "clear_approval_context", None)
    if callable(clear):
        try:
            clear(acp_session_id)
        except Exception:  # noqa: BLE001
            log.debug("dsh: approval context cleanup failed", exc_info=True)


def _kill_connection(connection: Any) -> bool:
    """Best-effort process-tree kill after a close that did not confirm.

    ``True`` only means a pid was handed to the killer; it is not proof the
    process stopped, and callers must not report it as terminated.
    """
    try:
        pid = int(getattr(getattr(connection, "process", None), "pid", 0) or 0)
    except Exception:  # noqa: BLE001
        return False
    if not pid:
        return False
    try:
        kill_process_tree(pid)
    except Exception:  # noqa: BLE001
        return False
    return True


def _register_turn(connection: Any, queue: Any) -> None:
    """Attach a consumer queue when the connection supports it (duck-typed)."""
    register = getattr(connection, "register_turn", None)
    if callable(register):
        try:
            register(queue)
        except Exception:  # noqa: BLE001 — routing must not fail a turn
            log.debug("dsh: could not register turn queue", exc_info=True)


def _unregister_turn(connection: Any, queue: Any) -> None:
    unregister = getattr(connection, "unregister_turn", None)
    if callable(unregister):
        try:
            unregister(queue)
        except Exception:  # noqa: BLE001
            log.debug("dsh: could not unregister turn queue", exc_info=True)


class DshAdapter(HarnessAdapter):
    """A ``HarnessAdapter`` driving DeepSeek Harness over ACP."""

    PROVIDER_ID = "dsh"
    PROVIDER_NAME = "DeepSeek Harness"

    # Bounded cleanup after a cancelled turn (seconds).
    cancel_notify_timeout = 2.0
    cancel_settle_timeout = 5.0
    retire_timeout = 5.0

    def __init__(
        self,
        *,
        state_dir: Path,
        settings: dict[str, Any],
        host: HostContext,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.settings = dict(settings or {})
        self.host = host
        self._binary = str(self.settings.get("binary") or host.binary or "dsh")
        self._profile = str(self.settings.get("profile") or host.profile or "acp")
        self._permission_mode = resolve_permission_mode(self.settings)
        self._approval_policy = str(self.settings.get("approval_policy") or "ask")
        self._connections: dict[tuple, DshConnection] = {}
        # Live mapping holds the *connection object*, not just the pool key: a
        # replacement process reuses the key but not this identity.
        self._live: dict[str, LiveSession] = {}
        # Sessions whose ACP state must not be resumed again (for example a
        # session that could not be closed while its MCP list changed). Kept
        # in memory only: no ids or secrets are added to the state file.
        self._pending_rebuild: set[str] = set()
        self._records: dict[str, dict[str, Any]] = self._load_state()
        self._models_cache: ModelCatalog | None = None
        # Serializes catalog discovery so concurrent turns/models() calls share
        # one probe. Cancelling the holder releases the lock (``async with``):
        # waiters re-check the cache and retry, never inheriting half a result.
        self._catalog_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        # Connections removed from the pool whose close is not confirmed yet.
        # They are owned here (not dropped) so a failed/cancelled replacement
        # and ``stop()`` can still reclaim them; keyed by ``id(connection)``.
        self._detached: dict[int, DshConnection] = {}
        self._closing: dict[int, asyncio.Task] = {}
        self._unconfirmed: set[int] = set()
        # One turn at a time per QwenPaw session; different sessions may run
        # concurrently on the same connection (own queue per ACP session).
        self._turn_locks: dict[str, asyncio.Lock] = {}
        # Skills projection state (refreshed at the start of every turn). The
        # token is part of the pool key, so a skills change rebuilds the
        # connection; "" means "no skills" and no ``--patch`` arg.
        self._skills_token = ""
        self._skills_plan: skills_projection.SkillProjection | None = None

    # ------------------------------------------------------------------ state
    def _state_path(self) -> Path:
        return self.state_dir / _STATE_FILE

    def _load_state(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self._state_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {
                    str(key): value
                    for key, value in raw.items()
                    if isinstance(value, dict)
                }
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001 — corrupt state must not kill the agent
            log.warning("dsh: unreadable session state; starting fresh", exc_info=True)
        return {}

    async def _save_state(self) -> None:
        async with self._state_lock:
            try:
                tmp = self._state_path().with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(self._records, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
                tmp.replace(self._state_path())
            except Exception:  # noqa: BLE001
                log.warning("dsh: could not persist session state", exc_info=True)

    # ------------------------------------------------------------------ pool
    def _pool_key(self, skills_token: str | None = None) -> tuple:
        return (
            self._binary,
            self._profile,
            self._permission_mode,
            self._approval_policy,
            self._skills_token if skills_token is None else skills_token,
        )

    async def _open_connection_locked(
        self,
        key: tuple,
        skills_plan: skills_projection.SkillProjection | None,
    ) -> DshConnection:
        """Return the pooled connection for ``key``, opening one if needed.

        The caller holds ``self._lock``; ``skills_plan`` is this turn's own
        projection (never the last one another turn happened to write).
        """
        existing = self._connections.get(key)
        if existing is not None and existing.alive and not getattr(
            existing,
            "retiring",
            False,
        ):
            _bind_skills_snapshot(existing, skills_plan)
            return existing
        if existing is not None:
            self._connections.pop(key, None)
            if not existing.alive:
                self._drop_live_for(existing)
                # Already out of the pool: keep an owner for its close.
                self._own_detached([existing])
        resolved = shutil.which(self._binary) or self._binary
        env = dict(os.environ)
        env["DSH_PERMISSION_MODE"] = self._permission_mode
        args = ["--profile", self._profile]
        if skills_plan is not None:
            args += skills_plan.command_args()
        connection = await DshConnection.open(
            binary=resolved,
            args=args,
            env=env,
            cwd=str(Path.home()),
            key=key,
            approval=self.host.approval,
        )
        _bind_skills_snapshot(connection, skills_plan)
        self._connections[key] = connection
        return connection

    async def _connection(
        self,
        skills_plan: skills_projection.SkillProjection | None = None,
    ) -> DshConnection:
        async with self._lock:
            token = skills_plan.snapshot if skills_plan is not None else None
            plan = skills_plan if skills_plan is not None else self._skills_plan
            return await self._open_connection_locked(self._pool_key(token), plan)

    async def _hold_connection(
        self,
        skills_plan: skills_projection.SkillProjection | None = None,
    ) -> DshConnection:
        """Open/reuse a connection and mark it busy before releasing the lock.

        The increment must happen under the same lock as the lookup: otherwise
        a concurrent skills recycle could observe ``active_turns == 0`` and
        close the connection in the gap.
        """
        async with self._lock:
            token = skills_plan.snapshot if skills_plan is not None else None
            plan = skills_plan if skills_plan is not None else self._skills_plan
            connection = await self._open_connection_locked(
                self._pool_key(token),
                plan,
            )
            _bump_turns(connection, 1)
            return connection

    def _project_skills(
        self,
        settings: dict[str, Any],
    ) -> skills_projection.SkillProjection:
        try:
            return skills_projection.project_skills(
                self.state_dir,
                skills_projection.runtime_skills(settings),
            )
        except Exception:  # noqa: BLE001 — never fail a turn on projection
            log.warning(
                "dsh: skills projection failed; continuing without it",
                exc_info=True,
            )
            return skills_projection.SkillProjection()

    async def _prepare_connection(
        self,
        settings: dict[str, Any],
    ) -> tuple[DshConnection, skills_projection.SkillProjection]:
        """Project this turn's skills and open its connection atomically.

        Each distinct skills input gets its own snapshot directory and patch
        file, so a concurrent turn with different skills cannot rewrite the
        files this process was spawned with. Projection and spawn still happen
        under the same lock so the returned plan is exactly the one used for
        ``--patch`` (and the connection records its snapshot id).
        """
        to_close: list[DshConnection] = []
        async with self._lock:
            plan = self._project_skills(settings)
            changed = plan.snapshot != self._skills_token
            self._skills_plan = plan
            self._skills_token = plan.snapshot
            if changed:
                to_close = self._recycle_locked()
            try:
                connection = await self._open_connection_locked(
                    self._pool_key(),
                    plan,
                )
            except BaseException:
                # The recycled connections are already out of the pool: this is
                # the only place that can still own their shutdown. Starting
                # the closes is synchronous, so a second cancellation cannot
                # strand them; nothing is awaited while the lock is held.
                self._own_detached(to_close)
                raise
            _bump_turns(connection, 1)
        # Own every detached connection before the first await: cancelling one
        # close (or the consumer) must not drop the remaining ones.
        self._own_detached(to_close)
        try:
            for closed in to_close:
                await self._close_quietly(closed)
        except BaseException:
            # Cancelled while closing idle connections: the caller never
            # receives the connection, so release the turn slot here.
            _bump_turns(connection, -1)
            raise
        return connection, plan

    async def _refresh_skills(self, settings: dict[str, Any]) -> None:
        """Project this turn's ``_runtime_capabilities.skills`` for DSH.

        A changed snapshot retires every **idle** pooled connection so the next
        ``_connection()`` boots with the new ``--patch``. Connections with an
        active turn are only marked ``retiring``: closing them mid-turn would
        kill another session's prompt. Projection failures are fail-soft: the
        turn runs with the previous (or no) skills rather than breaking.

        ``run_turn`` uses :meth:`_prepare_connection` (projection + spawn under
        one lock); this helper remains for direct callers/tests.
        """
        async with self._lock:
            plan = self._project_skills(settings)
            changed = plan.snapshot != self._skills_token
            self._skills_plan = plan
            self._skills_token = plan.snapshot
            to_close = self._recycle_locked() if changed else []
        self._own_detached(to_close)
        for connection in to_close:
            await self._close_quietly(connection)

    def _recycle_locked(self) -> list[DshConnection]:
        """Detach idle connections (caller holds the lock; closes later)."""
        to_close: list[DshConnection] = []
        for key, connection in list(self._connections.items()):
            if _active_turns(connection) > 0:
                connection.retiring = True
                continue
            self._connections.pop(key, None)
            self._drop_live_for(connection)
            to_close.append(connection)
        return to_close

    def _refresh_mcp(
        self,
        settings: dict[str, Any],
    ) -> mcp_projection.MCPProjection:
        """Project this turn's ``_runtime_capabilities.mcp_servers`` for DSH.

        Pure mapping (no ACP call, no pool impact). The returned projection is
        the *only* MCP source for this turn; it is passed down to
        ``_ensure_session`` so a concurrent turn can never swap it.
        Per-server failures are fail-soft and never fail a turn; secrets are
        never logged.
        """
        try:
            plan = mcp_projection.project_mcp_servers(
                mcp_projection.runtime_mcp_servers(settings),
            )
        except Exception:  # noqa: BLE001 — never fail a turn on projection
            log.warning("dsh: MCP projection failed; continuing without MCP", exc_info=True)
            plan = mcp_projection.MCPProjection()
        return plan

    async def _close_connections(self) -> None:
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            for connection in connections:
                self._drop_live_for(connection)
            detached = [
                connection
                for connection in self._detached.values()
                if all(connection is not pooled for pooled in connections)
            ]
        for connection in connections + detached:
            await self._close_quietly(connection)
        leftover = [
            connection
            for connection in self._detached.values()
            if id(connection) in self._unconfirmed
        ]
        if leftover:
            log.warning(
                "dsh: %d detached connection(s) could not be confirmed closed "
                "at stop; their close tasks were kept owned and the child "
                "process tree was killed best effort (not verified stopped)",
                len(leftover),
            )
            for connection in leftover:
                self._detached.pop(id(connection), None)

    # ------------------------------------------------------- close ownership
    def _quarantine(self, connection: Any) -> None:
        """Make ``connection`` unreusable *before* the first await.

        A cancelled turn's cleanup can itself be cancelled at any ``await``, so
        the decision to stop using this connection — and to forget its ACP
        session mapping — must already be visible synchronously. Closing is a
        separate task owned by the adapter (:meth:`_own_detached`).
        """
        connection.retiring = True
        for key, pooled in list(self._connections.items()):
            if pooled is connection:
                self._connections.pop(key, None)
        self._drop_live_for(connection)

    def _own_detached(self, connections: Iterable[Any]) -> None:
        """Take ownership of detached connections (sync; closes run as tasks)."""
        for connection in connections:
            if connection is None:
                continue
            self._detached[id(connection)] = connection
            if id(connection) not in self._closing:
                self._spawn_close(connection)

    def _spawn_close(self, connection: Any) -> asyncio.Task | None:
        """Start (and own) a bounded close task for one connection."""
        try:
            task = asyncio.get_running_loop().create_task(
                self._close_bounded(connection)
            )
        except RuntimeError:  # no running loop: nothing we can own
            return None
        self._closing[id(connection)] = task
        task.add_done_callback(
            lambda done, cid=id(connection): self._close_finished(cid, done)
        )
        return task

    def _close_finished(self, key: int, task: asyncio.Task) -> None:
        self._closing.pop(key, None)
        confirmed = False
        if not task.cancelled():
            try:
                confirmed = bool(task.result())
            except Exception:  # noqa: BLE001
                confirmed = False
        # An unconfirmed close stays tracked (it must never be reported as
        # closed just because a later no-op aclose() returned).
        if confirmed and key not in self._unconfirmed:
            self._detached.pop(key, None)

    async def _close_quietly(self, connection: Any) -> None:
        """Await an owned, bounded close; errors and stalls stay contained.

        Awaiting a ``Task`` (unlike ``wait_for``) never cancels it: if the
        caller is cancelled here the close keeps running under its owner.
        """
        task = self._closing.get(id(connection))
        if task is None or task.done():
            task = self._spawn_close(connection)
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.debug("dsh: connection close failed", exc_info=True)

    async def _close_bounded(self, connection: Any) -> bool:
        """Close one connection with a hard wait deadline.

        ``wait_for`` only bounds the waiter when the awaitable honours
        cancellation; a transport that swallows it keeps ``wait_for`` waiting
        forever. Here the close is its own task and ``asyncio.wait`` stops
        waiting at the deadline (without cancelling the task). On expiry the
        process tree is killed best effort and ``False`` is returned: closure
        is not confirmed and is not reported as confirmed.

        A finished close task is not proof either: only a normal return (no
        exception, not cancelled) confirms closure. An exception or a
        cancellation marks the connection unconfirmed, keeps it owned (so
        ``stop()`` can process it again) and kills the process tree best
        effort — the task is finished, so it is never reported as still
        running.
        """
        closer = asyncio.ensure_future(connection.aclose())
        closer.add_done_callback(_retrieve_task_error)
        try:
            done, _pending = await asyncio.wait(
                {closer},
                timeout=self.retire_timeout,
            )
        except asyncio.CancelledError:
            self._unconfirmed.add(id(connection))
            _kill_connection(connection)
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
            self._unconfirmed.add(id(connection))
            if isinstance(error, asyncio.CancelledError):
                log.warning(
                    "dsh: connection close was cancelled before confirming "
                    "closure; killing the child process tree (termination is "
                    "not verified)",
                )
            else:
                log.warning(
                    "dsh: connection close failed before confirming closure "
                    "(%s); killing the child process tree (termination is not "
                    "verified)",
                    type(error).__name__,
                )
                log.debug("dsh: connection close error", exc_info=error)
            _kill_connection(connection)
            return False
        self._unconfirmed.add(id(connection))
        log.warning(
            "dsh: connection close did not confirm within %.1fs; killing the "
            "child process tree (the close task is still owned and may still "
            "be running; termination is not verified)",
            self.retire_timeout,
        )
        _kill_connection(connection)
        return False

    def _drop_live_for(self, connection: Any) -> None:
        for session_id, live in list(self._live.items()):
            if live.connection is connection:
                self._live.pop(session_id, None)
                _clear_approval(connection, live.acp_session_id)

    async def _retire_connection(self, connection: Any) -> None:
        """Remove a connection from the pool and close it (bounded, owned)."""
        self._quarantine(connection)
        self._own_detached([connection])
        await self._close_quietly(connection)

    async def _maybe_retire(self, connection: Any) -> None:
        if getattr(connection, "retiring", False) and _active_turns(connection) == 0:
            await self._retire_connection(connection)

    # ------------------------------------------------------------------ sessions
    @staticmethod
    def _same_connection(live: LiveSession, connection: Any) -> bool:
        if live.connection is not connection:
            return False
        generation = getattr(connection, "generation", None)
        if generation is None:
            return True
        return getattr(live.connection, "generation", None) == generation

    async def _ensure_session(
        self,
        connection: DshConnection,
        qwenpaw_session_id: str,
        cwd: Path,
        plan: mcp_projection.MCPProjection,
    ) -> OpenSession:
        """Return a live ACP session id for this QwenPaw session.

        * same connection + same MCP digest -> reuse without touching DSH;
        * same connection + different digest -> ``session/close`` then
          ``session/resume`` with the new list (DSH mounts exactly the request
          list; resume on an active session is rejected by DSH);
        * different connection (rebuilt process) -> resume from the persisted
          id, never prompt it directly.
        """
        fingerprint = plan.fingerprint
        live = self._live.get(qwenpaw_session_id)
        if live is not None and self._same_connection(live, connection):
            if live.fingerprint == fingerprint:
                return OpenSession(live.acp_session_id, degraded=live.degraded)
            closed = await connection.close_session(live.acp_session_id)
            _clear_approval(connection, live.acp_session_id)
            self._live.pop(qwenpaw_session_id, None)
            if not closed:
                # Fail closed: never prompt a session that may still carry the
                # previous MCP servers, and never resume it later — the next
                # turn creates a fresh session and says so.
                self._records.pop(qwenpaw_session_id, None)
                self._pending_rebuild.add(qwenpaw_session_id)
                await self._save_state()
                await self._retire_connection(connection)
                raise SessionLifecycleError(
                    "the live ACP session could not be closed, so the new MCP "
                    "configuration was not applied; the connection was retired "
                    "and the turn did not run",
                )
            return await self._reopen(
                connection,
                qwenpaw_session_id,
                cwd,
                plan,
                resume_from=live.acp_session_id,
                had_context=True,
            )

        record = self._records.get(qwenpaw_session_id) or {}
        if live is not None:
            # Bound to a different (rebuilt) connection: the ACP session on
            # the old process is unreachable. Drop the stale mapping before
            # resuming from the persisted id.
            self._live.pop(qwenpaw_session_id, None)
            _clear_approval(live.connection, live.acp_session_id)
        resume_from = str(record.get("acpSessionId") or "")
        rebuilt_hint = qwenpaw_session_id in self._pending_rebuild
        self._pending_rebuild.discard(qwenpaw_session_id)
        return await self._reopen(
            connection,
            qwenpaw_session_id,
            cwd,
            plan,
            resume_from=resume_from,
            had_context=bool(resume_from) or live is not None or rebuilt_hint,
        )

    async def _reopen(
        self,
        connection: DshConnection,
        qwenpaw_session_id: str,
        cwd: Path,
        plan: mcp_projection.MCPProjection,
        *,
        resume_from: str,
        had_context: bool,
    ) -> OpenSession:
        if resume_from:
            try:
                resumed = await connection.resume_session(
                    cwd=str(cwd),
                    acp_session_id=resume_from,
                    mcp_servers=plan.as_list(),
                )
            except Exception:  # noqa: BLE001 — fall back to a fresh session
                log.warning(
                    "dsh: resume failed for %s; creating a new session "
                    "(earlier context is lost)",
                    qwenpaw_session_id,
                )
            else:
                self._live[qwenpaw_session_id] = LiveSession(
                    connection=connection,
                    acp_session_id=resumed,
                    fingerprint=plan.fingerprint,
                )
                await self._store_record(qwenpaw_session_id, resumed, cwd, connection)
                return OpenSession(resumed)

        created, degraded = await self._new_session(
            connection,
            qwenpaw_session_id,
            cwd,
            plan,
        )
        self._live[qwenpaw_session_id] = LiveSession(
            connection=connection,
            acp_session_id=created,
            fingerprint=plan.fingerprint,
            degraded=degraded,
        )
        await self._store_record(qwenpaw_session_id, created, cwd, connection)
        return OpenSession(created, rebuilt=had_context, degraded=degraded)

    async def _new_session(
        self,
        connection: DshConnection,
        qwenpaw_session_id: str,
        cwd: Path,
        plan: mcp_projection.MCPProjection,
    ) -> tuple[str, bool]:
        """Create an ACP session, retrying once without MCP on failure.

        DSH mounts ACP MCP servers with ``failOnStartupError: true`` (spike
        s05): one bad server fails the whole call. Dropping MCP keeps the
        session usable. No MCP definitions -> plain ``session/new``.
        """
        servers = plan.as_list()
        try:
            created = await connection.new_session(cwd=str(cwd), mcp_servers=servers)
            return created, False
        except Exception:  # noqa: BLE001 — retried without MCP below
            if not servers:
                raise
            log.warning(
                "dsh: session/new failed with %d MCP server(s) [%s]; retrying "
                "without MCP — MCP disabled for session %s",
                len(servers),
                ", ".join(plan.names) or "<unnamed>",
                qwenpaw_session_id,
                exc_info=True,
            )
            created = await connection.new_session(cwd=str(cwd), mcp_servers=[])
            return created, True

    async def _store_record(
        self,
        qwenpaw_session_id: str,
        acp_session_id: str,
        cwd: Path,
        connection: DshConnection,
    ) -> None:
        previous = self._records.get(qwenpaw_session_id) or {}
        created_at = previous.get("createdAt") or time.time()
        self._records[qwenpaw_session_id] = {
            "acpSessionId": acp_session_id,
            "cwd": str(cwd),
            "poolKey": list(connection.key),
            "createdAt": created_at,
            "updatedAt": time.time(),
        }
        await self._save_state()

    # ------------------------------------------------------------------ status
    @property
    def capability_unavailable_message(self) -> str | None:
        """Explain why provider capability discovery is unavailable.

        The host calls this before ``models()``; it must exist on every
        adapter (the base class declares a method, while shipped providers
        expose it as a property). Missing it made ``GET /harnesses/<id>/models``
        raise ``AttributeError`` → 500 → the console fell back to the current
        model only (v1.0.1 bug).
        """
        if not shutil.which(self._binary):
            return f"'{self._binary}' not found on PATH"
        return None

    async def status(self) -> HarnessProvider:
        resolved = shutil.which(self._binary) or self._binary
        installed = bool(resolved) and Path(resolved).exists()
        error: str | None = None
        if not installed:
            error = f"'{self._binary}' not found on PATH"
        else:
            version = await self._binary_version(resolved)
            profile_dir = (
                Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
                / "profiles"
                / self._profile
            )
            if not profile_dir.exists():
                error = (
                    f"profile '{self._profile}' not initialized "
                    f"({profile_dir} missing); run `dsh {self._profile} --help` once"
                )
            elif version and not self._version_supported(version):
                error = f"unsupported dsh version: {version}"
        return HarnessProvider(
            id=self.PROVIDER_ID,
            name=self.PROVIDER_NAME,
            available=installed and error is None,
            installed=installed,
            authenticated=True,  # authMethods: [] — avoid a login button
            runtime_path=resolved,
            runtime_source="PATH" if installed else "missing",
            error=error,
            capabilities=self._capabilities(),
        )

    async def _binary_version(self, resolved: str) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                resolved,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
            return stdout.decode("utf-8", "replace").strip().splitlines()[-1]
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _version_supported(version: str) -> bool:
        digits = "".join(ch if ch.isdigit() else "." for ch in version)
        parts = [int(piece) for piece in digits.split(".") if piece][:3]
        if not parts:
            return True
        major, minor = parts[0], parts[1] if len(parts) > 1 else 0
        return (major, minor) >= (0, 2) and major < 1

    # --------------------------------------------------------------- commands
    async def run_command(
        self,
        *,
        session_id: str,
        command: str,
        arguments: str,
        cwd: Path,
        settings: dict[str, Any],
    ) -> list[HarnessEvent]:
        """Run one provider-owned slash command (only ``status`` is declared).

        ``/status`` is answered from adapter memory: it does not start DSH,
        probe models, read credentials, create a session, or send a prompt.
        Unknown commands fall back to the contract default, which is a visible
        ERROR rather than a fake success.
        """
        name = str(command or "").strip()
        if name != "status":
            return await super().run_command(
                session_id=session_id,
                command=command,
                arguments=arguments,
                cwd=cwd,
                settings=settings,
            )
        del session_id, arguments, cwd, settings
        return [
            HarnessEvent(
                kind=HarnessEventKind.TEXT_DELTA,
                text=self._status_text(),
            ),
            HarnessEvent(kind=HarnessEventKind.COMPLETED),
        ]

    def _status_text(self) -> str:
        """Local, privacy-clean summary (no paths, ids, env, credentials)."""
        catalog = self._models_cache
        if catalog is None:
            catalog_state = "not fetched"
        elif catalog.error:
            # Never echo the probe error here: transport/profile failures can
            # embed private paths in their message. Details stay in the log.
            catalog_state = "last discovery failed (details in logs)"
        else:
            catalog_state = f"{len(catalog.models)} model(s) cached"
        binary = Path(self._binary).name or "unset"
        return "\n".join(
            (
                f"{self.PROVIDER_NAME} (ACP v1/stdio)",
                f"profile: {self._profile}",
                f"permission mode: {self._permission_mode}",
                f"approval policy: {self._approval_policy}",
                f"binary: {binary}",
                f"pooled connections: {len(self._connections)}",
                f"live sessions: {len(self._live)}",
                f"model catalog: {catalog_state}",
                "limits: no interactive login; no ACP context-usage channel; "
                "provider history is not replayed (history() returns an empty "
                "list); this status is read from adapter memory and does not "
                "start DSH or call a model",
            ),
        )

    async def history(self, session_id: str) -> list[HarnessHistoryItem]:
        """Return no provider history: ACP resume is not host replay.

        ``session_resume`` means the DSH ACP session can be resumed; it does not
        mean the QwenPaw transcript can be rebuilt from DSH. Reconstructing
        provider history or replaying model output here would be invented data,
        and clearing the persisted session mapping would break resume. This
        therefore returns an empty list and touches no state.
        """
        del session_id
        return []

    # ------------------------------------------------------------------ models
    async def models(self) -> list[HarnessModel]:
        """Return the advertised catalog, at most one discovery per window.

        Discovery starts DSH and creates+closes a probe ACP session, so it is
        **not** side-effect free. Failures are kept in the snapshot (not
        silently replaced by a default model) and surfaced by selection code.
        """
        catalog = await self._catalog()
        return catalog.models

    async def _catalog(self, *, force: bool = False) -> ModelCatalog:
        """Return a usable catalog snapshot, discovering it at most once.

        The lock makes concurrent turns/``models()`` calls single-flight. A
        cancelled holder releases the lock and leaves no cache entry: the next
        caller re-checks and retries instead of inheriting a partial result.
        """
        cached = self._models_cache
        if not force and cached is not None and cached.fresh(time.time()):
            return cached
        async with self._catalog_lock:
            cached = self._models_cache
            if not force and cached is not None and cached.fresh(time.time()):
                return cached
            return await self._discover_catalog()

    async def _discover_catalog(self) -> ModelCatalog:
        now = time.time()
        models: list[HarnessModel] = []
        value_map: dict[str, str] = {}
        error = ""
        try:
            connection = await self._hold_connection()
            try:
                probe_cwd = str(Path.home())
                session_id = await connection.new_session(cwd=probe_cwd)
                try:
                    response = connection.last_new_session_response(session_id)
                    # The bridge advertises the authoritative catalog (every
                    # model plus its reasoning efforts) in
                    # ``_meta.qwenpaw.models``. Prefer it; the ACP ``model``
                    # config option only carries provider groups and no
                    # per-model effort list (v1.0.1 bug).
                    models, value_map = self._models_from_bridge_meta(response)
                    if not models:
                        models, value_map = self._models_from_config_options(response)
                finally:
                    await connection.close_session(session_id)
            finally:
                _bump_turns(connection, -1)
                await self._maybe_retire(connection)
        except asyncio.CancelledError:
            # Do not cache a cancellation: the caller's turn is over, and the
            # next turn must be free to discover again.
            raise
        except Exception as exc:  # noqa: BLE001 — reported to selection code
            error = _brief_error(exc)
            log.warning("dsh: model catalog probe failed (%s)", error, exc_info=True)
            models, value_map = [], {}
        catalog = ModelCatalog(now, models, value_map, error)
        self._models_cache = catalog
        return catalog

    @staticmethod
    def _models_from_bridge_meta(
        response: Any,
    ) -> tuple[list[HarnessModel], dict[str, str]]:
        """Read ``_meta.qwenpaw.models`` from one ``session/new`` response."""
        meta = _get(response, "field_meta") or {}
        qwenpaw = _get(meta, "qwenpaw") or {}
        entries = _get(qwenpaw, "models") or []
        current = _get(qwenpaw, "current_model") or {}
        current_provider = str(_get(current, "provider") or "")
        current_model = str(_get(current, "model") or "")
        models: list[HarnessModel] = []
        value_map: dict[str, str] = {}
        for entry in entries:
            provider = str(_get(entry, "provider") or "")
            bare = str(_get(entry, "id") or "")
            model_id = f"{provider}/{bare}" if provider else bare
            if not model_id:
                continue
            selection = str(_get(entry, "selection_value") or "")
            if selection:
                value_map[model_id] = selection
            efforts = [
                str(_get(effort, "id"))
                for effort in (_get(entry, "reasoning_efforts") or [])
                if _get(effort, "id")
            ]
            display_name = str(_get(entry, "name") or bare)
            provider_label = str(_get(entry, "provider_name") or "")
            # The console renders only ``HarnessModel.name`` (no provider
            # column), so surface the provider in the label — otherwise two
            # providers with similarly named models are indistinguishable.
            if provider_label:
                display_name = f"{provider_label} · {display_name}"
            models.append(
                HarnessModel(
                    id=model_id,
                    name=display_name,
                    description=str(_get(entry, "description") or ""),
                    is_default=bool(
                        _get(entry, "default")
                        or (
                            provider == current_provider
                            and bare == current_model
                        )
                    ),
                    reasoning_efforts=efforts,
                    default_reasoning_effort=(
                        str(_get(entry, "default_reasoning_effort"))
                        if _get(entry, "default_reasoning_effort")
                        else None
                    ),
                ),
            )
        return models, value_map

    @classmethod
    def _models_from_config_options(
        cls,
        response: Any,
    ) -> tuple[list[HarnessModel], dict[str, str]]:
        """Fallback: read the ACP ``model`` config option groups."""
        models: list[HarnessModel] = []
        value_map: dict[str, str] = {}
        for option in list(_get(response, "config_options", "configOptions") or []):
            if str(_get(option, "id")) != "model":
                continue
            current = str(_get(option, "current_value", "currentValue") or "")
            for group in _get(option, "options") or []:
                group_name = str(_get(group, "name") or "")
                for item in _get(group, "options") or []:
                    value = str(_get(item, "value") or "")
                    if not value:
                        continue
                    provider, model = _parse_model_value(value, group_name)
                    model_id = f"{provider}/{model}" if provider else model
                    value_map[model_id] = value
                    models.append(
                        HarnessModel(
                            id=model_id,
                            name=str(_get(item, "name") or model),
                            description=str(_get(item, "description") or ""),
                            is_default=bool(value == current),
                        ),
                    )
        return models, value_map

    # ------------------------------------------------------------------ lifecycle
    async def start_login(self, device_code: bool = False) -> dict[str, Any]:
        del device_code
        from qwenpaw.harnesses.base import HarnessOperationNotSupportedError

        raise HarnessOperationNotSupportedError("DSH needs no interactive login")

    async def logout(self) -> None:
        return None

    async def reset_session(self, session_id: str) -> None:
        """Forget one session and close its ACP counterpart.

        Takes the same per-session turn lock as ``run_turn``: reset never
        closes an ACP session while a prompt for it is running. A reset that
        is requested during a turn therefore waits for that turn to finish
        (ordering is explicit, not a race). The persisted id is dropped under
        the lock, so the next turn cannot resume the reset session id.
        """
        key = str(session_id)
        lock = self._turn_locks.setdefault(key, asyncio.Lock())
        async with lock:
            live = self._live.pop(key, None)
            if live is not None:
                _clear_approval(live.connection, live.acp_session_id)
                if not await live.connection.close_session(live.acp_session_id):
                    log.warning(
                        "dsh: reset_session could not close %s; the connection "
                        "will be retired on its next failed use",
                        live.acp_session_id,
                    )
            self._records.pop(key, None)
            self._pending_rebuild.discard(key)
            await self._save_state()

    async def stop(self) -> None:
        await self._close_connections()

    # ------------------------------------------------------------------ turns
    async def run_turn(
        self,
        *,
        session_id: str,
        prompt: str,
        cwd: Path,
        settings: dict[str, Any],
        attachments: list[HarnessAttachment] | None = None,
    ) -> AsyncIterator[HarnessEvent]:
        lock = self._turn_locks.setdefault(str(session_id), asyncio.Lock())
        async with lock:
            connection: DshConnection | None = None
            queue: asyncio.Queue[tuple[str, Any]] | None = None
            acp_session_id = ""
            prompt_task: asyncio.Task | None = None
            try:
                plan = self._refresh_mcp(settings)
                connection, _skills = await self._prepare_connection(settings)
                try:
                    opened = await self._ensure_session(
                        connection,
                        session_id,
                        cwd,
                        plan,
                    )
                except Exception as exc:  # noqa: BLE001 — surface as an event
                    log.exception("dsh: session setup failed")
                    yield HarnessEvent(
                        kind=HarnessEventKind.ERROR,
                        text=f"DSH adapter error: {exc}",
                    )
                    return
                acp_session_id = opened.acp_session_id
                if opened.rebuilt:
                    log.warning("dsh: %s", _CONTEXT_REBUILT_NOTICE)
                    yield HarnessEvent(
                        kind=HarnessEventKind.TEXT_DELTA,
                        text=f"\n{_CONTEXT_REBUILT_NOTICE}\n",
                    )
                connection.client.set_approval_context(
                    acp_session_id,
                    self._approval_context(session_id, settings),
                )
                try:
                    await self._apply_model(connection, acp_session_id, settings)
                except ModelSelectionError as exc:
                    # The user asked for a specific model/effort and it could
                    # not be applied or confirmed: report it and do not prompt,
                    # otherwise the turn would silently run on the default.
                    log.warning("dsh: %s", exc)
                    yield HarnessEvent(
                        kind=HarnessEventKind.ERROR,
                        text=f"DSH model selection failed: {exc}",
                    )
                    return

                prompt_text, images = self._compose_prompt(
                    prompt,
                    attachments,
                    supports_image=bool(getattr(connection, "supports_image", False)),
                )
                queue = connection.client.attach_session(acp_session_id)
                _register_turn(connection, queue)
                result: dict[str, Any] = {}

                async def _run_prompt() -> None:
                    try:
                        if images:
                            result["response"] = await connection.prompt(
                                acp_session_id,
                                prompt_text,
                                images=images,
                            )
                        else:
                            result["response"] = await connection.prompt(
                                acp_session_id,
                                prompt_text,
                            )
                    except asyncio.CancelledError:
                        # The prompt task itself was cancelled (SDK shutdown or
                        # an external cancel). ``finally`` still fires, so the
                        # consumer must be told this was not a success.
                        result["cancelled"] = True
                        raise
                    except Exception as exc:  # noqa: BLE001
                        result["error"] = exc
                    finally:
                        queue.put_nowait(("__done__", acp_session_id))

                prompt_task = asyncio.create_task(_run_prompt())
                prompt_task.add_done_callback(_retrieve_task_error)
                closed_during_turn = False
                try:
                    while True:
                        kind, payload = await queue.get()
                        if kind == "__done__":
                            break
                        if kind == TURN_CLOSED:
                            # The connection was retired/stopped underneath
                            # this turn. A prompt that already settled wins;
                            # otherwise fail closed instead of waiting for a
                            # transport that may never notify.
                            if prompt_task.done():
                                break
                            closed_during_turn = True
                            break
                        update_session_id, update = payload
                        if str(update_session_id) != acp_session_id:
                            continue
                        event = mapper.map_update(update)
                        if event is not None:
                            yield event
                except (asyncio.CancelledError, GeneratorExit):
                    if prompt_task is not None and not prompt_task.done():
                        await self._cancel_active_turn(
                            connection,
                            acp_session_id,
                            prompt_task,
                        )
                    raise
                except BaseException:
                    # Unexpected loop failure: do not leave the prompt running.
                    await self._cancel_active_turn(
                        connection,
                        acp_session_id,
                        prompt_task,
                    )
                    raise

                if closed_during_turn:
                    self._abandon(prompt_task)
                    log.warning(
                        "dsh: connection closed while a turn was running on %s",
                        acp_session_id,
                    )
                    yield HarnessEvent(
                        kind=HarnessEventKind.ERROR,
                        text=(
                            "DSH connection was closed before the turn produced "
                            "a response; the turn did not complete."
                        ),
                    )
                    return
                if result.get("cancelled"):
                    # Nobody cancelled this turn from the consumer side, yet no
                    # prompt response exists: the ACP turn may still be alive
                    # inside DSH. Retire rather than reuse unknown state.
                    log.warning(
                        "dsh: prompt for %s was cancelled independently; "
                        "retiring the connection instead of reporting success",
                        acp_session_id,
                    )
                    await self._retire_connection(connection)
                    yield HarnessEvent(
                        kind=HarnessEventKind.ERROR,
                        text=(
                            "DSH turn was cancelled before it produced a "
                            "response (the prompt was cancelled independently, "
                            "not by the caller); the connection was retired."
                        ),
                    )
                    return
                error = result.get("error")
                if error is not None:
                    yield HarnessEvent(
                        kind=HarnessEventKind.ERROR,
                        text=f"DSH turn failed: {error}",
                    )
                    return
                response = result.get("response")
                if response is None:
                    log.warning(
                        "dsh: prompt for %s ended without a response",
                        acp_session_id,
                    )
                    yield HarnessEvent(
                        kind=HarnessEventKind.ERROR,
                        text=(
                            "DSH turn ended without a response; refusing to "
                            "report success."
                        ),
                    )
                    return
                stop_reason = _get(response, "stop_reason", "stopReason")
                plain_stop = str(getattr(stop_reason, "value", stop_reason) or "")
                if not plain_stop.strip():
                    log.warning(
                        "dsh: prompt for %s returned no stop reason",
                        acp_session_id,
                    )
                    yield HarnessEvent(
                        kind=HarnessEventKind.ERROR,
                        text=(
                            "DSH turn response carried no stop reason; refusing "
                            "to report success."
                        ),
                    )
                    return
                yield HarnessEvent(kind=mapper.map_stop_reason(stop_reason))
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as exc:  # noqa: BLE001 — surface as an event, never raise
                log.exception("dsh: run_turn failed")
                yield HarnessEvent(
                    kind=HarnessEventKind.ERROR,
                    text=f"DSH adapter error: {exc}",
                )
            finally:
                if connection is not None:
                    if queue is not None and acp_session_id:
                        connection.client.detach_session(acp_session_id, queue)
                        _unregister_turn(connection, queue)
                    if acp_session_id:
                        _clear_approval(connection, acp_session_id)
                    if prompt_task is not None and not prompt_task.done():
                        # Never leave a prompt running unobserved; its done
                        # callback consumes whatever the cancellation yields.
                        prompt_task.cancel()
                    _bump_turns(connection, -1)
                    await self._maybe_retire(connection)

    async def _cancel_active_turn(
        self,
        connection: DshConnection,
        acp_session_id: str,
        prompt_task: asyncio.Task,
    ) -> None:
        """Best-effort ACP cancel + bounded settle; quarantine on unknown state.

        The notification is sent at most once per turn. A prompt that will not
        settle is abandoned, and once we stop waiting for it the connection is
        no longer reusable (unknown DSH state, late frames possible).

        Every path that gives up on the prompt must first :meth:`_quarantine`
        the connection *synchronously*: this coroutine can be cancelled again
        at any ``await`` below, and a cancellation must never leave the old
        connection pooled / its ACP session id mapped. The close itself is a
        bounded task owned by the adapter, so it survives that cancellation.
        """
        try:
            await asyncio.wait_for(
                connection.cancel_session(acp_session_id),
                self.cancel_notify_timeout,
            )
        except asyncio.CancelledError:
            # Second cancellation while notifying: the turn's state is unknown
            # and this coroutine dies here, so no later ``await`` can be relied
            # on. Quarantine first, then hand the close to its owner.
            self._quarantine(connection)
            self._abandon(prompt_task)
            self._own_detached([connection])
            raise
        except Exception:  # noqa: BLE001 — notification failure is not fatal
            log.warning("dsh: session/cancel notification failed", exc_info=True)
        try:
            await asyncio.wait_for(
                asyncio.shield(prompt_task),
                self.cancel_settle_timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "dsh: prompt %s did not settle after cancel; retiring the "
                "connection so late frames are dropped",
                acp_session_id,
            )
            self._quarantine(connection)
            self._abandon(prompt_task)
            await self._retire_connection(connection)
        except asyncio.CancelledError:
            self._quarantine(connection)
            self._abandon(prompt_task)
            self._own_detached([connection])
            raise
        except Exception:  # noqa: BLE001 — the cancelled turn's error is moot
            pass

    @staticmethod
    def _abandon(prompt_task: asyncio.Task) -> None:
        """Stop waiting for a prompt task; cancellation may not be honoured."""
        if not prompt_task.done():
            prompt_task.cancel()

    async def _apply_model(
        self,
        connection: DshConnection,
        acp_session_id: str,
        settings: dict[str, Any],
    ) -> None:
        """Apply the user's explicit model/effort, or refuse the turn.

        Order is fixed (model, then effort). Every write is confirmed from the
        response ``configOptions``: dsh-acp's
        ``session/set_config_option`` returns the complete option state with
        ``currentValue`` (``dsh-acp/lib/index.js:1332``), so a value that did
        not change is detected instead of being reported as applied.

        No explicit selection -> nothing is touched (profile default stays).
        Anything that cannot be resolved or confirmed raises
        :class:`ModelSelectionError`, so ``run_turn`` emits a visible ERROR and
        never prompts with the default model behind the user's back.
        """
        model_requested = _requested_setting(settings, "model")
        effort_requested = _requested_setting(settings, "reasoning_effort")
        if model_requested is None and effort_requested is None:
            return

        options = connection.current_config_options(acp_session_id)
        model_id = ""
        if model_requested is not None:
            # Prefer this session's own new/resume response: it already carries
            # the advertised option values, so no extra probe is needed.
            resolved = _resolve_model_in_options(options, model_requested)
            if resolved is None:
                catalog = await self._catalog()
                resolved = _resolve_model_in_catalog(catalog, model_requested)
            value, model_id = resolved
            options = await self._apply_option_confirmed(
                connection,
                acp_session_id,
                "model",
                value,
                what=f"model '{model_requested}'",
            )

        if effort_requested is None:
            return

        current_key = _current_model_key(options)
        allowed = _effort_values(options)
        if allowed is None:
            if options and _find_option(options, "model") is not None:
                # The session advertises its full option state: no reasoning
                # option means this model has no efforts (dsh-acp builds the
                # option only when the model declares them).
                raise ModelSelectionError(
                    f"model '{model_id or current_key or 'the current model'}' "
                    "does not advertise reasoning efforts; refusing to apply "
                    f"'{effort_requested}'",
                )
            if not model_id:
                raise ModelSelectionError(
                    "cannot determine the current model for this session, so "
                    f"reasoning effort '{effort_requested}' cannot be "
                    "validated; refusing to guess",
                )
            catalog = await self._catalog()
            entry = next(
                (item for item in catalog.models if item.id == model_id),
                None,
            )
            allowed = list(entry.reasoning_efforts) if entry is not None else []
            if not allowed:
                raise ModelSelectionError(
                    f"model '{model_id}' does not advertise reasoning efforts; "
                    f"refusing to apply '{effort_requested}'",
                )
        if effort_requested not in allowed:
            raise ModelSelectionError(
                f"reasoning effort '{effort_requested}' is not supported by "
                f"'{model_id or current_key or 'the current model'}' "
                f"(allowed: {', '.join(sorted(allowed)) or 'none'})",
            )
        await self._apply_option_confirmed(
            connection,
            acp_session_id,
            "reasoning_effort",
            effort_requested,
            what=f"reasoning effort '{effort_requested}'",
        )

    async def _apply_option_confirmed(
        self,
        connection: Any,
        acp_session_id: str,
        config_id: str,
        value: str,
        *,
        what: str,
    ) -> list[Any]:
        """Write one config option and confirm ``currentValue`` from the reply."""
        try:
            response = await connection.set_config_option(
                acp_session_id,
                config_id,
                value,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — reported as a selection error
            raise ModelSelectionError(
                f"{what} could not be applied: {_brief_error(exc)}",
            ) from exc
        options = _config_options_of(response)
        if options is None:
            raise ModelSelectionError(
                f"{what} could not be confirmed: DSH returned no configOptions",
            )
        current = _option_current_value(options, config_id)
        if current is None:
            raise ModelSelectionError(
                f"{what} could not be confirmed: the response did not include "
                f"the '{config_id}' option",
            )
        if str(current) != str(value):
            raise ModelSelectionError(
                f"{what} was not applied: DSH reports {config_id}={current!r}",
            )
        remember = getattr(connection, "remember_config_options", None)
        if callable(remember):
            remember(acp_session_id, options)
        return options

    @staticmethod
    def _approval_context(
        session_id: str,
        settings: dict[str, Any],
    ) -> dict[str, str]:
        """Routing context for approval cards raised during this turn."""
        context = dict((settings or {}).get("_request_context") or {})
        resolved_session = str(context.get("session_id") or session_id or "default")
        return {
            "session_id": resolved_session,
            "root_session_id": resolved_session,
            "agent_id": str(context.get("agent_id") or "default"),
            "user_id": str(context.get("user_id") or "default"),
            "channel": str(context.get("channel") or "console"),
        }

    @staticmethod
    def _compose_prompt(
        prompt: str,
        attachments: list[HarnessAttachment] | None,
        *,
        supports_image: bool = False,
    ) -> tuple[str, list[tuple[str, str]]]:
        """Split attachments into prompt text plus inline ``(base64, mime)`` images.

        * an ``image`` attachment on an image-capable connection is read and
          inlined (no extra text line — matches spike s06);
        * every other case (``file``, probe says no, unknown suffix, unreadable,
          larger than :data:`qwenpaw_dsh.core.images.MAX_IMAGE_BYTES`) keeps the
          v0.1 text-path forwarding with a log line on the degrade paths.
        """
        text = prompt or ""
        images: list[tuple[str, str]] = []
        had_image = False
        for attachment in attachments or []:
            name = attachment.name or Path(attachment.path).name
            kind = str(getattr(attachment.kind, "value", attachment.kind))
            if kind == "image":
                had_image = True
                if supports_image:
                    encoded, reason = image_codec.encode_image(attachment.path)
                    if encoded is not None:
                        images.append((encoded.data, encoded.mime))
                        continue
                    log.warning(
                        "dsh: image attachment %r forwarded as a text path: %s",
                        name,
                        reason,
                    )
            text += f"\n\nAttached file {name}: {attachment.path}"
        if had_image and not supports_image:
            log.info(
                "dsh: connection does not advertise image prompts; image "
                "attachments forwarded as text paths",
            )
        return text, images

    # ------------------------------------------------------------------ misc
    def _capabilities(self) -> Any:
        from ..provider import build_capabilities

        return build_capabilities()


def _get(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
        if isinstance(obj, dict) and name in obj:
            return obj[name]
    return None


def _brief_error(exc: BaseException) -> str:
    """Short, single-line reason for a visible error (never a traceback)."""
    text = str(exc).strip().replace("\n", " ")
    if len(text) > 200:
        text = text[:197] + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _requested_setting(settings: dict[str, Any], key: str) -> str | None:
    """Return an explicit non-empty string selection, or ``None``.

    ``None``/missing/blank means "not specified" — the profile (or the session's
    current value) stays untouched. A non-string (including ``False``) is an
    explicit error: it is never coerced.
    """
    raw = (settings or {}).get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ModelSelectionError(
            f"settings['{key}'] must be a string, got {type(raw).__name__}",
        )
    value = raw.strip()
    return value or None


def _config_options_of(response: Any) -> list[Any] | None:
    options = _get(response, "config_options", "configOptions")
    if isinstance(options, (list, tuple)):
        return list(options)
    return None


def _find_option(options: list[Any], option_id: str) -> Any | None:
    for option in options or []:
        if str(_get(option, "id") or "") == option_id:
            return option
    return None


def _option_current_value(options: list[Any], option_id: str) -> Any | None:
    option = _find_option(options, option_id)
    if option is None:
        return None
    return _get(option, "current_value", "currentValue")


def _option_values(option: Any) -> list[str]:
    """Flatten select options (plain entries and provider groups)."""
    values: list[str] = []
    for item in _get(option, "options") or []:
        nested = _get(item, "options")
        if nested is not None:
            for child in nested:
                value = str(_get(child, "value") or "")
                if value:
                    values.append(value)
            continue
        value = str(_get(item, "value") or "")
        if value:
            values.append(value)
    return values


def _effort_values(options: list[Any]) -> list[str] | None:
    """Advertised efforts for the *current* model, or ``None`` if absent."""
    option = _find_option(options or [], "reasoning_effort")
    if option is None:
        return None
    return _option_values(option)


def _current_model_key(options: list[Any]) -> str:
    current = _option_current_value(options or [], "model")
    if not current:
        return ""
    provider, model = _parse_model_value(str(current), "")
    if provider and model:
        return f"{provider}/{model}"
    return str(current)


def _resolve_model_in_options(
    options: list[Any],
    requested: str,
) -> tuple[str, str] | None:
    """Resolve one explicit model against this session's advertised list.

    Matching order: exact selection value, exact ``provider/model`` id, then a
    bare model name only when it is unique. Two providers sharing a name are
    never silently merged.
    """
    option = _find_option(options or [], "model")
    if option is None:
        return None
    matches: list[tuple[str, str]] = []
    for group in _get(option, "options") or []:
        group_name = str(_get(group, "name") or "")
        for item in _get(group, "options") or []:
            value = str(_get(item, "value") or "")
            if not value:
                continue
            provider, model = _parse_model_value(value, group_name)
            model_id = f"{provider}/{model}" if provider else model
            matches.append((value, model_id))
    exact = [match for match in matches if match[0] == requested]
    if len(exact) == 1:
        return exact[0]
    by_id = [match for match in matches if match[1] == requested]
    if len(by_id) == 1:
        return by_id[0]
    if len(by_id) > 1:
        raise ModelSelectionError(
            f"model '{requested}' is ambiguous in this session: "
            f"{', '.join(sorted({match[1] for match in by_id}))}",
        )
    bare = [
        match
        for match in matches
        if match[1].split("/", 1)[-1] == requested
    ]
    if len(bare) == 1:
        return bare[0]
    if len(bare) > 1:
        raise ModelSelectionError(
            f"model name '{requested}' is ambiguous: "
            f"{', '.join(sorted({match[1] for match in bare}))}",
        )
    return None


def _resolve_model_in_catalog(
    catalog: ModelCatalog,
    requested: str,
) -> tuple[str, str]:
    """Resolve one explicit model against the discovered catalog.

    The selection value always comes from the catalog (bridge
    ``selection_value`` / ACP option value); it is never rebuilt from the
    display name or bare model id. A failed discovery is an explicit error, not
    a silent fallback to the profile default.
    """
    value_map = catalog.value_map
    if requested in value_map:
        return value_map[requested], requested
    exact = [
        (value, model_id)
        for model_id, value in value_map.items()
        if value == requested
    ]
    if len(exact) == 1:
        return exact[0]
    bare = [
        (value, model_id)
        for model_id, value in value_map.items()
        if model_id.split("/", 1)[-1] == requested
    ]
    if len(bare) == 1:
        return bare[0]
    if len(bare) > 1:
        raise ModelSelectionError(
            f"model name '{requested}' is ambiguous across providers: "
            f"{', '.join(sorted({model_id for _value, model_id in bare}))}",
        )
    if catalog.error:
        raise ModelSelectionError(
            f"model catalog discovery failed ({catalog.error}); refusing to "
            f"fall back to the profile default for '{requested}'",
        )
    known = {item.id for item in catalog.models}
    if requested in known or any(
        model_id.split("/", 1)[-1] == requested for model_id in known
    ):
        raise ModelSelectionError(
            f"the catalog advertises no selection value for '{requested}'; "
            "refusing to build one from its name",
        )
    if not catalog.models:
        raise ModelSelectionError(
            f"the model catalog is empty; explicit model '{requested}' cannot "
            "be applied",
        )
    raise ModelSelectionError(
        f"unknown model '{requested}' in the advertised catalog",
    )


def _parse_model_value(value: str, group_name: str) -> tuple[str, str]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list) and len(parsed) >= 2:
            return str(parsed[0]), str(parsed[1])
    except Exception:  # noqa: BLE001
        pass
    return group_name, value
