# -*- coding: utf-8 -*-
"""Host-facing ports.

The adapter *core* talks to QwenPaw only through these protocols/data holders,
so it survives the 2.2 → 2.3 loader change (and can be unit-tested without the
host at all).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class ApprovalRequest:
    """Provider-neutral description of one approval card."""

    title: str
    body: str = ""
    severity: str = "medium"
    payload: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None


@dataclass
class ApprovalDecision:
    """Outcome of one approval request."""

    status: str  # "approved" | "rejected" | "timeout"
    option_id: str | None = None


@runtime_checkable
class ApprovalPort(Protocol):
    """Bridge a provider approval into the host's approval UI/service.

    2.2.x wiring: ``qwenpaw.app.approvals.get_approval_service()``
    (``create_pending_summary`` + ``wait_for_approval``), the same precedent the
    Codex adapter uses at ``harnesses/codex/adapter.py:640-710``.
    2.3+ wiring: whatever the official mechanism offers; only this port changes.
    """

    async def request(self, request: ApprovalRequest) -> ApprovalDecision: ...


class NullApprovalPort:
    """Fallback used by tests and by hosts without an approval channel."""

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        del request
        return ApprovalDecision(status="rejected")


@dataclass
class HostContext:
    """Everything the core needs to know about the running host."""

    state_dir: Path
    approval: ApprovalPort = field(default_factory=NullApprovalPort)
    binary: str | None = None
    profile: str = "acp"
    host_version: str | None = None
    # Where the host can look for the harness-contract module. Kept as a string
    # so the skeleton does not import QwenPaw at module import time.
    host_api: str = "qwenpaw-2.2"
