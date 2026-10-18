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
from .acp_client import DshConnection

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
        self._permission_mode = str(
            self.settings.get("permission_mode") or "workspace-write",
        )
        self._approval_policy = str(self.settings.get("approval_policy") or "ask")
        self._connections: dict[tuple, DshConnection] = {}
        self._live: dict[str, tuple[tuple, str]] = {}
        self._records: dict[str, dict[str, Any]] = self._load_state()
        self._models_cache: tuple[float, list[HarnessModel], dict[str, str]] | None = None
        self._lock = asyncio.Lock()

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
            connection = await DshConnection.open(
                binary=resolved,
                args=["--profile", self._profile],
                env=env,
                cwd=str(Path.home()),
                key=key,
            )
            self._connections[key] = connection
            return connection

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
                )
                self._live[qwenpaw_session_id] = (connection.key, resumed)
                return resumed
            except Exception:  # noqa: BLE001 — fall back to a fresh session
                log.warning(
                    "dsh: resume failed for %s; creating a new session",
                    qwenpaw_session_id,
                )

        created = await connection.new_session(cwd=str(cwd))
        self._live[qwenpaw_session_id] = (connection.key, created)
        self._records[qwenpaw_session_id] = {
            "acpSessionId": created,
            "cwd": str(cwd),
            "poolKey": list(connection.key),
            "createdAt": time.time(),
        }
        self._save_state()
        return created

    # ------------------------------------------------------------------ status
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
                options = await self._session_config_options(connection, session_id)
                for option in options:
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
            finally:
                await connection.close_session(session_id)
        except Exception:  # noqa: BLE001
            log.warning("dsh: model catalog probe failed", exc_info=True)
        self._models_cache = (now, models, value_map)
        return models

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
                await connection.close_session(live[1])

    async def stop(self) -> None:
        for connection in list(self._connections.values()):
            try:
                await connection.aclose()
            except Exception:  # noqa: BLE001
                log.debug("dsh: connection close failed", exc_info=True)
        self._connections.clear()
        self._live.clear()

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
            connection = await self._connection()
            acp_session_id = await self._ensure_session(connection, session_id, cwd)
            await self._apply_model(connection, acp_session_id, settings)

            while not connection.client.queue.empty():
                connection.client.queue.get_nowait()

            prompt_text = self._compose_prompt(prompt, attachments)
            result: dict[str, Any] = {}
            settled = asyncio.Event()

            async def _run_prompt() -> None:
                try:
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
    def _compose_prompt(
        prompt: str,
        attachments: list[HarnessAttachment] | None,
    ) -> str:
        text = prompt or ""
        for attachment in attachments or []:
            name = attachment.name or Path(attachment.path).name
            text += f"\n\nAttached file {name}: {attachment.path}"
        return text

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
