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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from qwenpaw.harnesses.events import (
    HarnessAttachment,
    HarnessEvent,
    HarnessEventKind,
    HarnessModel,
    HarnessProvider,
)

from ..host import HostContext
from . import event_mapper as mapper
from . import images as image_codec
from . import mcp as mcp_projection
from . import skills as skills_projection
from .acp_client import DshConnection
from .permissions import resolve_permission_mode

log = logging.getLogger("qwenpaw.dsh")

_STATE_FILE = "dsh_sessions.json"
_MODELS_TTL_SECONDS = 600


class DshAdapter:
    """A ``HarnessAdapter`` driving DeepSeek Harness over ACP."""

    PROVIDER_ID = "dsh"
    PROVIDER_NAME = "DeepSeek Harness"

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
        self._live: dict[str, tuple[tuple, str]] = {}
        self._records: dict[str, dict[str, Any]] = self._load_state()
        self._models_cache: tuple[float, list[HarnessModel], dict[str, str]] | None = None
        self._lock = asyncio.Lock()
        # Skills projection state (refreshed at the start of every turn). The
        # token is part of the pool key, so a skills change rebuilds the
        # connection; "" means "no skills" and no ``--patch`` arg.
        self._skills_token = ""
        self._skills_plan: skills_projection.SkillProjection | None = None
        # MCP projection state (refreshed at the start of every turn). MCP is
        # session-scoped — the servers ride ``session/new`` / ``session/resume``
        # — so unlike skills it is *not* part of the pool key.
        self._mcp_plan = mcp_projection.MCPProjection()

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

    def _save_state(self) -> None:
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
    def _pool_key(self) -> tuple:
        return (
            self._binary,
            self._profile,
            self._permission_mode,
            self._approval_policy,
            self._skills_token,
        )

    async def _connection(self) -> DshConnection:
        key = self._pool_key()
        async with self._lock:
            existing = self._connections.get(key)
            if existing is not None and existing.alive:
                return existing
            resolved = shutil.which(self._binary) or self._binary
            env = dict(os.environ)
            env["DSH_PERMISSION_MODE"] = self._permission_mode
            args = ["--profile", self._profile]
            if self._skills_plan is not None:
                args += self._skills_plan.command_args()
            connection = await DshConnection.open(
                binary=resolved,
                args=args,
                env=env,
                cwd=str(Path.home()),
                key=key,
                approval=self.host.approval,
            )
            self._connections[key] = connection
            return connection

    async def _refresh_skills(self, settings: dict[str, Any]) -> None:
        """Project this turn's ``_runtime_capabilities.skills`` for DSH.

        A changed snapshot (kebab names + revisions) closes every pooled
        connection so the next ``_connection()`` boots with the new
        ``--patch``. Projection failures are fail-soft: the turn runs with the
        previous (or no) skills rather than breaking.
        """
        try:
            plan = skills_projection.project_skills(
                self.state_dir,
                skills_projection.runtime_skills(settings),
            )
        except Exception:  # noqa: BLE001 — never fail a turn on projection
            log.warning("dsh: skills projection failed; continuing without it", exc_info=True)
            plan = skills_projection.SkillProjection()
        self._skills_plan = plan
        if plan.snapshot == self._skills_token:
            return
        self._skills_token = plan.snapshot
        await self._close_connections()

    def _refresh_mcp(self, settings: dict[str, Any]) -> None:
        """Project this turn's ``_runtime_capabilities.mcp_servers`` for DSH.

        Pure mapping (no ACP call, no pool impact): the projection is consumed
        when the session is created/resumed. Per-server failures are fail-soft
        and never fail a turn; secrets are never logged.
        """
        try:
            plan = mcp_projection.project_mcp_servers(
                mcp_projection.runtime_mcp_servers(settings),
            )
        except Exception:  # noqa: BLE001 — never fail a turn on projection
            log.warning("dsh: MCP projection failed; continuing without MCP", exc_info=True)
            plan = mcp_projection.MCPProjection()
        self._mcp_plan = plan

    async def _close_connections(self) -> None:
        async with self._lock:
            for connection in list(self._connections.values()):
                try:
                    await connection.aclose()
                except Exception:  # noqa: BLE001
                    log.debug("dsh: connection close failed", exc_info=True)
            self._connections.clear()
            self._live.clear()

    async def _ensure_session(
        self,
        connection: DshConnection,
        qwenpaw_session_id: str,
        cwd: Path,
    ) -> str:
        """Return a live ACP session id for this QwenPaw session."""
        live = self._live.get(qwenpaw_session_id)
        if live is not None and live[0] == connection.key:
            return live[1]

        record = self._records.get(qwenpaw_session_id) or {}
        acp_session_id = str(record.get("acpSessionId") or "")
        if acp_session_id:
            try:
                resumed = await connection.resume_session(
                    cwd=str(cwd),
                    acp_session_id=acp_session_id,
                    mcp_servers=self._mcp_plan.as_list(),
                )
                self._live[qwenpaw_session_id] = (connection.key, resumed)
                return resumed
            except Exception:  # noqa: BLE001 — fall back to a fresh session
                log.warning(
                    "dsh: resume failed for %s; creating a new session",
                    qwenpaw_session_id,
                )

        created = await self._new_session(connection, qwenpaw_session_id, cwd)
        self._live[qwenpaw_session_id] = (connection.key, created)
        self._records[qwenpaw_session_id] = {
            "acpSessionId": created,
            "cwd": str(cwd),
            "poolKey": list(connection.key),
            "createdAt": time.time(),
        }
        self._save_state()
        return created

    async def _new_session(
        self,
        connection: DshConnection,
        qwenpaw_session_id: str,
        cwd: Path,
    ) -> str:
        """Create an ACP session, retrying once without MCP on failure.

        DSH mounts ACP MCP servers with ``failOnStartupError: true`` (spike
        s05): one bad server fails the whole call. Dropping MCP keeps the
        session usable. No MCP definitions -> plain ``session/new``.
        """
        servers = self._mcp_plan.as_list()
        try:
            return await connection.new_session(cwd=str(cwd), mcp_servers=servers)
        except Exception:  # noqa: BLE001 — retried without MCP below
            if not servers:
                raise
            log.warning(
                "dsh: session/new failed with %d MCP server(s) [%s]; retrying "
                "without MCP — MCP disabled for session %s",
                len(servers),
                ", ".join(self._mcp_plan.names) or "<unnamed>",
                qwenpaw_session_id,
                exc_info=True,
            )
            return await connection.new_session(cwd=str(cwd), mcp_servers=[])

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

    # ------------------------------------------------------------------ models
    async def models(self) -> list[HarnessModel]:
        now = time.time()
        if self._models_cache and now - self._models_cache[0] < _MODELS_TTL_SECONDS:
            return self._models_cache[1]
        models: list[HarnessModel] = []
        value_map: dict[str, str] = {}
        try:
            connection = await self._connection()
            probe_cwd = str(Path.home())
            session_id = await connection.new_session(cwd=probe_cwd)
            try:
                # The bridge advertises the authoritative catalog (every model
                # plus its reasoning efforts) in ``_meta.qwenpaw.models``. Prefer
                # it; the ACP ``model`` config option only carries provider
                # groups and no per-model effort list (v1.0.1 bug: efforts were
                # empty in the console).
                models, value_map = self._models_from_bridge_meta(connection)
                if not models:
                    models, value_map = self._models_from_config_options(connection)
            finally:
                await connection.close_session(session_id)
        except Exception:  # noqa: BLE001
            log.warning("dsh: model catalog probe failed", exc_info=True)
        self._models_cache = (now, models, value_map)
        return models

    @staticmethod
    def _models_from_bridge_meta(
        connection: DshConnection,
    ) -> tuple[list[HarnessModel], dict[str, str]]:
        """Read ``_meta.qwenpaw.models`` from the last ``session/new``."""
        cached = getattr(connection, "_last_new_session_response", None)
        meta = _get(cached, "field_meta") or {}
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
        connection: DshConnection,
    ) -> tuple[list[HarnessModel], dict[str, str]]:
        """Fallback: read the ACP ``model`` config option groups."""
        models: list[HarnessModel] = []
        value_map: dict[str, str] = {}
        cached = getattr(connection, "_last_new_session_response", None)
        for option in list(_get(cached, "config_options", "configOptions") or []):
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

    async def _session_config_options(
        self,
        connection: DshConnection,
        session_id: str,
    ) -> list[Any]:
        """Best-effort configOptions lookup for the probe session.

        The ACP surface returns configOptions on ``session/new``; this helper
        simply re-asks via ``set_config_option``-free path by reusing the last
        new-session response cached on the connection.
        """
        cached = getattr(connection, "_last_new_session_response", None)
        if cached is None:
            return []
        return list(_get(cached, "config_options", "configOptions") or [])

    # ------------------------------------------------------------------ lifecycle
    async def start_login(self, device_code: bool = False) -> dict[str, Any]:
        del device_code
        from qwenpaw.harnesses.base import HarnessOperationNotSupportedError

        raise HarnessOperationNotSupportedError("DSH needs no interactive login")

    async def logout(self) -> None:
        return None

    async def reset_session(self, session_id: str) -> None:
        self._records.pop(session_id, None)
        self._save_state()
        live = self._live.pop(session_id, None)
        if live is not None:
            connection = self._connections.get(live[0])
            if connection is not None:
                connection.client.clear_approval_context(live[1])
                await connection.close_session(live[1])

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
        try:
            await self._refresh_skills(settings)
            self._refresh_mcp(settings)
            connection = await self._connection()
            acp_session_id = await self._ensure_session(connection, session_id, cwd)
            connection.client.set_approval_context(
                acp_session_id,
                self._approval_context(session_id, settings),
            )
            await self._apply_model(connection, acp_session_id, settings)

            while not connection.client.queue.empty():
                connection.client.queue.get_nowait()

            prompt_text, images = self._compose_prompt(
                prompt,
                attachments,
                supports_image=bool(getattr(connection, "supports_image", False)),
            )
            result: dict[str, Any] = {}
            settled = asyncio.Event()

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
                except Exception as exc:  # noqa: BLE001
                    result["error"] = exc
                finally:
                    settled.set()
                    connection.client.queue.put_nowait(("__done__", None))

            task = asyncio.create_task(_run_prompt())
            try:
                while True:
                    kind, payload = await connection.client.queue.get()
                    if kind == "__done__":
                        break
                    update_session_id, update = payload
                    if str(update_session_id) != acp_session_id:
                        continue
                    event = mapper.map_update(update)
                    if event is not None:
                        yield event
            finally:
                try:
                    await task
                except Exception:  # noqa: BLE001
                    pass

            error = result.get("error")
            if error is not None:
                yield HarnessEvent(
                    kind=HarnessEventKind.ERROR,
                    text=f"DSH turn failed: {error}",
                )
                return
            response = result.get("response")
            stop_reason = _get(response, "stop_reason", "stopReason")
            yield HarnessEvent(kind=mapper.map_stop_reason(stop_reason))
        except Exception as exc:  # noqa: BLE001 — surface as an event, never raise
            log.exception("dsh: run_turn failed")
            yield HarnessEvent(
                kind=HarnessEventKind.ERROR,
                text=f"DSH adapter error: {exc}",
            )

    async def _apply_model(
        self,
        connection: DshConnection,
        acp_session_id: str,
        settings: dict[str, Any],
    ) -> None:
        model_id = str((settings or {}).get("model") or "").strip()
        if not model_id or self._models_cache is None:
            return
        value = self._models_cache[2].get(model_id)
        if not value:
            return
        try:
            await connection.set_config_option(acp_session_id, "model", value)
        except Exception:  # noqa: BLE001 — switching is best-effort in v0.1
            log.warning("dsh: set model %s failed", model_id, exc_info=True)

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


def _parse_model_value(value: str, group_name: str) -> tuple[str, str]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list) and len(parsed) >= 2:
            return str(parsed[0]), str(parsed[1])
    except Exception:  # noqa: BLE001
        pass
    return group_name, value
