# -*- coding: utf-8 -*-
"""The shared provider description — the one thing both loaders consume.

This module must not import ``qwenpaw.harnesses.registry`` (that is the loader's
job). It imports only the *contract* types the host promises to keep stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .host import HostContext
from .core.adapter import DshAdapter

PROVIDER_ID = "dsh"
PROVIDER_NAME = "DeepSeek Harness"


def build_capabilities():
    """Capabilities for the DSH backend."""
    from qwenpaw.harnesses.events import (  # imported lazily: host package
        HarnessApprovalPreset,
        HarnessCapabilities,
        HarnessCommand,
    )

    return HarnessCapabilities(
        authentication=False,        # authMethods: [] — no login surface
        model_selection=True,        # configOptions.model + set_config_option
        reasoning_effort=True,       # configOptions.reasoning_effort
        reasoning_stream=True,       # committed thought chunks (v1)
        tool_stream=True,            # tool_call / tool_call_update
        session_resume=True,         # session/list + session/resume
        attachments=True,            # image blocks when the route allows
        context_usage=False,         # ACP has usage_update, contract has no channel
        qwenpaw_skills_projection=True,
        qwenpaw_mcp_projection=True,
        commands=[HarnessCommand(name="status", description="Show DSH runtime status")],
        approval_presets=[
            HarnessApprovalPreset(
                id="ask", name="Ask before changes",
                description="Workspace writes are allowed; elevated actions ask.",
                settings={"permission_mode": "workspace-write"},
            ),
            HarnessApprovalPreset(
                id="read-only", name="Read only",
                description="Inspect without changing anything.",
                settings={"permission_mode": "read-only"},
            ),
            HarnessApprovalPreset(
                id="workspace", name="Workspace access",
                description="Allow workspace changes; out-of-workspace actions are refused.",
                settings={"permission_mode": "workspace-write", "approval_policy": "never"},
            ),
            HarnessApprovalPreset(
                id="full-access", name="Full access",
                description="Unrestricted local execution without confirmation.",
                settings={"permission_mode": "danger-full-access"},
            ),
        ],
    )


@dataclass(frozen=True)
class ProviderSpec:
    """Loader-agnostic description of the DSH provider."""

    id: str
    name: str
    coming_soon: bool
    capabilities: Any
    factory: Callable[[Path, dict[str, Any] | None, HostContext], Any]
    config_key: Callable[[dict[str, Any] | None], tuple]


def _factory(state_dir: Path, settings: dict[str, Any] | None, host: HostContext) -> DshAdapter:
    return DshAdapter(state_dir=state_dir, settings=settings or {}, host=host)


def _config_key(settings: dict[str, Any] | None) -> tuple:
    values = settings or {}
    return (
        str(values.get("binary") or "").strip(),
        str(values.get("profile") or "acp").strip(),
        str(values.get("permission_mode") or "workspace-write").strip(),
        str(values.get("approval_policy") or "ask").strip(),
    )


def build_provider_spec(host: HostContext) -> ProviderSpec:
    """Build the spec the active loader will mount. Same call for every loader."""
    return ProviderSpec(
        id=PROVIDER_ID,
        name=PROVIDER_NAME,
        coming_soon=False,
        capabilities=build_capabilities(),
        factory=_factory,
        config_key=_config_key,
    )
