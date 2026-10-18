# -*- coding: utf-8 -*-
"""Map the host's approval level onto DSH's permission mode.

DSH reads ``DSH_PERMISSION_MODE`` (``read-only`` / ``workspace-write`` /
``danger-full-access``). QwenPaw has its own four-step ladder
(``STRICT`` / ``SMART`` / ``AUTO`` / ``OFF``). This module is the single place
that translates between them, so the adapter and the loader's
``adapter_config_key`` agree on when a rebuild is required.

Precedence (highest first):

1. an explicit ``settings["permission_mode"]`` — the escape hatch the provider
   presets (and existing agent configs) use;
2. the session-level ``settings["_request_context"]["approval_level"]``;
3. the agent-level ``approval_level`` from ``load_agent_config(agent_id)``;
4. ``"AUTO"``.

Only the host dependency (``load_agent_config``) is imported lazily, so this
module stays importable without QwenPaw.
"""

from __future__ import annotations

from typing import Any

READ_ONLY = "read-only"
WORKSPACE_WRITE = "workspace-write"
DANGER_FULL_ACCESS = "danger-full-access"

_DEFAULT_MODE = WORKSPACE_WRITE
_DEFAULT_LEVEL = "AUTO"

# QwenPaw approval_level → DSH permission mode.
_LEVEL_TO_MODE = {
    "OFF": DANGER_FULL_ACCESS,
    "AUTO": WORKSPACE_WRITE,
    "SMART": WORKSPACE_WRITE,
    "STRICT": READ_ONLY,
}


def resolve_permission_mode(settings: dict[str, Any] | None) -> str:
    """Return the DSH permission mode for one adapter/request.

    Never raises: an unknown or unreadable approval level falls back to
    ``workspace-write``, and the host config lookup is best-effort.
    """
    values = settings or {}

    explicit = str(values.get("permission_mode") or "").strip()
    if explicit:
        return explicit

    level = _session_level(values)
    if not level:
        level = _agent_level(values)
    if not level:
        level = _DEFAULT_LEVEL
    return _LEVEL_TO_MODE.get(level.strip().upper(), _DEFAULT_MODE)


def _session_level(settings: dict[str, Any]) -> str:
    context = settings.get("_request_context")
    if not isinstance(context, dict):
        return ""
    return str(context.get("approval_level") or "").strip()


def _agent_level(settings: dict[str, Any]) -> str:
    context = settings.get("_request_context")
    agent_id = ""
    if isinstance(context, dict):
        agent_id = str(context.get("agent_id") or "").strip()
    if not agent_id:
        return ""
    try:
        from qwenpaw.config.config import load_agent_config

        config = load_agent_config(agent_id)
    except Exception:  # noqa: BLE001 — mapping must never break a turn
        return ""
    return str(getattr(config, "approval_level", "") or "").strip()
