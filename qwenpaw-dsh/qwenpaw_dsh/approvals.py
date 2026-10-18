# -*- coding: utf-8 -*-
"""QwenPaw approval-card bridge for DSH permission requests.

``QwenPawApprovalPort`` implements :class:`qwenpaw_dsh.host.ApprovalPort` on top
of QwenPaw's approval service, following the Codex/Qoder adapters'
(``harnesses/codex/adapter.py``, ``harnesses/qoder/adapter.py``):
``get_approval_service().create_pending_summary(...)`` then
``wait_for_approval(...)``, with the decision mapped back to the neutral
``approved`` / ``rejected`` / ``timeout`` vocabulary.

Every QwenPaw import is lazy (inside :meth:`QwenPawApprovalPort.request`), so the
plugin still imports — and behaves like :class:`NullApprovalPort` — when the
host or its approval service is unavailable. A failure here must never crash a
turn.
"""

from __future__ import annotations

import logging

from .host import ApprovalDecision, ApprovalPort, ApprovalRequest

log = logging.getLogger("qwenpaw.dsh")

_DEFAULT_AGENT_ID = "default"
_DEFAULT_USER_ID = "default"
_DEFAULT_CHANNEL = "console"


class QwenPawApprovalPort(ApprovalPort):
    """Route one DSH permission request through a QwenPaw approval card.

    Stateless: the routing context (``session_id`` / ``agent_id`` /
    ``user_id`` / ``channel``) travels on the :class:`ApprovalRequest`, so one
    instance can safely serve every pooled DSH session.
    """

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        try:
            return await self._request(request)
        except Exception:  # noqa: BLE001 — plumbing must never kill a turn
            log.warning("dsh: approval card failed; rejecting", exc_info=True)
            return ApprovalDecision(status="rejected")

    async def _request(self, request: ApprovalRequest) -> ApprovalDecision:
        from qwenpaw.app.approvals import (
            ApprovalRequestSummary,
            get_approval_service,
        )

        service = get_approval_service()
        if service is None:
            log.warning("dsh: no approval service available; rejecting")
            return ApprovalDecision(status="rejected")

        context = dict(request.context or {})
        session_id = str(context.get("session_id") or "default")
        root_session_id = str(context.get("root_session_id") or session_id)
        agent_id = str(context.get("agent_id") or _DEFAULT_AGENT_ID)
        user_id = str(context.get("user_id") or _DEFAULT_USER_ID)
        channel = str(context.get("channel") or _DEFAULT_CHANNEL)

        summary = ApprovalRequestSummary(
            source_type="dsh",
            name=str(request.title or "DSH permission request"),
            severity=str(request.severity or "medium"),
            result_summary=str(request.body or ""),
            payload=dict(request.payload or {}),
        )
        kwargs = {}
        if request.timeout_seconds is not None:
            kwargs["timeout_seconds"] = float(request.timeout_seconds)
        pending = await service.create_pending_summary(
            session_id=session_id,
            root_session_id=root_session_id,
            owner_agent_id=agent_id,
            user_id=user_id,
            channel=channel,
            agent_id=agent_id,
            summary=summary,
            **kwargs,
        )
        decision = await service.wait_for_approval(
            pending,
            pending.timeout_seconds,
        )
        return _map_decision(decision)


def _map_decision(decision: object) -> ApprovalDecision:
    """Map QwenPaw's ApprovalDecision (or a plain string) to the host one."""
    value = str(getattr(decision, "value", decision) or "").strip().lower()
    if value == "approved":
        return ApprovalDecision(status="approved")
    if value == "timeout":
        return ApprovalDecision(status="timeout")
    return ApprovalDecision(status="rejected")
