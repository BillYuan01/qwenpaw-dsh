#!/usr/bin/env python3
"""Unit tests for the DSH approval bridge (gate 2).

Covers:
  * QwenPawApprovalPort decision mapping + real context routing;
  * DshTurnClient.request_permission: approve → allow_once, reject/timeout →
    reject_once, port failure → conservative reject, no port → v0.1 reject,
    no cross-request memory.

Run: ~/.qwenpaw/venv/bin/python tests/test_approval_port.py
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.approvals import QwenPawApprovalPort  # noqa: E402
from qwenpaw_dsh.core.acp_client import DshTurnClient  # noqa: E402
from qwenpaw_dsh.host import ApprovalDecision, ApprovalRequest  # noqa: E402

_OPTIONS = [
    {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
    {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
]
_TOOL_CALL = {
    "toolCallId": "tc-1",
    "title": "Run shell command",
    "kind": "execute",
    "rawInput": {"command": "ls -la"},
}
_CONTEXT = {
    "session_id": "qp-session",
    "root_session_id": "qp-session",
    "agent_id": "agent-1",
    "user_id": "user-1",
    "channel": "console",
}


def _run(coro):
    return asyncio.run(coro)


class _FakePending:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds


class _FakeService:
    def __init__(self, decision: str = "approved", timeout: float = 60.0) -> None:
        self.decision = decision
        self.timeout = timeout
        self.created: list[dict] = []
        self.waited: list[tuple] = []

    async def create_pending_summary(self, **kwargs):
        self.created.append(kwargs)
        return _FakePending(float(kwargs.get("timeout_seconds", self.timeout)))

    async def wait_for_approval(self, pending, timeout_seconds):
        self.waited.append((pending, timeout_seconds))
        return self.decision


class _FakePort:
    """Minimal ApprovalPort stand-in."""

    def __init__(self, decision: ApprovalDecision, error: Exception | None = None):
        self.decision = decision
        self.error = error
        self.requests: list[ApprovalRequest] = []

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.decision


class ApprovalPortTests(unittest.TestCase):
    def _call(self, request: ApprovalRequest, service: _FakeService):
        import qwenpaw.app.approvals as approvals_pkg

        with mock.patch.object(
            approvals_pkg,
            "get_approval_service",
            return_value=service,
        ):
            return _run(QwenPawApprovalPort().request(request))

    def test_approved_maps_and_routes_context(self) -> None:
        service = _FakeService(decision="approved")
        request = ApprovalRequest(
            title="Run shell command",
            body="ls -la",
            severity="high",
            payload={"provider": "dsh"},
            context=_CONTEXT,
        )
        decision = self._call(request, service)
        self.assertEqual(decision.status, "approved")
        created = service.created[0]
        self.assertEqual(created["session_id"], "qp-session")
        self.assertEqual(created["root_session_id"], "qp-session")
        self.assertEqual(created["agent_id"], "agent-1")
        self.assertEqual(created["user_id"], "user-1")
        self.assertEqual(created["channel"], "console")
        self.assertEqual(created["summary"].source_type, "dsh")
        self.assertEqual(created["summary"].name, "Run shell command")
        self.assertEqual(created["summary"].severity, "high")
        self.assertEqual(created["summary"].result_summary, "ls -la")
        self.assertEqual(created["summary"].payload["provider"], "dsh")
        # wait_for_approval is called with the pending's own timeout.
        self.assertEqual(service.waited[0][1], service.waited[0][0].timeout_seconds)

    def test_denied_maps_to_rejected(self) -> None:
        decision = self._call(
            ApprovalRequest(title="x"),
            _FakeService(decision="denied"),
        )
        self.assertEqual(decision.status, "rejected")

    def test_timeout_maps_to_timeout(self) -> None:
        decision = self._call(
            ApprovalRequest(title="x"),
            _FakeService(decision="timeout"),
        )
        self.assertEqual(decision.status, "timeout")

    def test_qwenpaw_enum_maps_too(self) -> None:
        from qwenpaw.security.tool_guard.approval import (
            ApprovalDecision as QwenPawDecision,
        )

        decision = self._call(
            ApprovalRequest(title="x"),
            _FakeService(decision=QwenPawDecision.APPROVED),
        )
        self.assertEqual(decision.status, "approved")

    def test_timeout_seconds_forwarded(self) -> None:
        service = _FakeService(timeout=99.0)
        request = ApprovalRequest(title="x", timeout_seconds=12.5)
        self._call(request, service)
        self.assertEqual(service.created[0]["timeout_seconds"], 12.5)
        self.assertEqual(service.waited[0][1], 12.5)

    def test_default_timeout_is_host_managed(self) -> None:
        service = _FakeService(timeout=99.0)
        self._call(ApprovalRequest(title="x"), service)
        self.assertNotIn("timeout_seconds", service.created[0])

    def test_missing_context_uses_defaults(self) -> None:
        service = _FakeService()
        request = ApprovalRequest(title="x")
        decision = self._call(request, service)
        self.assertEqual(decision.status, "approved")
        created = service.created[0]
        self.assertEqual(created["session_id"], "default")
        self.assertEqual(created["agent_id"], "default")
        self.assertEqual(created["channel"], "console")

    def test_service_failure_is_rejected_not_raised(self) -> None:
        service = _FakeService()
        service.create_pending_summary = mock.AsyncMock(
            side_effect=RuntimeError("approval backend down"),
        )
        decision = self._call(ApprovalRequest(title="x"), service)
        self.assertEqual(decision.status, "rejected")

    def test_missing_service_module_is_rejected(self) -> None:
        import qwenpaw.app.approvals as approvals_pkg

        with mock.patch.object(
            approvals_pkg,
            "get_approval_service",
            side_effect=ImportError("no approvals"),
        ):
            decision = _run(QwenPawApprovalPort().request(ApprovalRequest(title="x")))
        self.assertEqual(decision.status, "rejected")


class RequestPermissionTests(unittest.TestCase):
    def _client(self, port) -> DshTurnClient:
        client = DshTurnClient(cwd="/tmp", approval=port)
        client.set_approval_context("sess-1", _CONTEXT)
        return client

    def _request(self, client: DshTurnClient, options=None, session_id="sess-1"):
        return _run(
            client.request_permission(
                options=_OPTIONS if options is None else options,
                session_id=session_id,
                tool_call=_TOOL_CALL,
            ),
        )

    def test_approved_selects_allow_once(self) -> None:
        port = _FakePort(ApprovalDecision(status="approved"))
        client = self._client(port)
        response = self._request(client)
        self.assertEqual(response.outcome.outcome, "selected")
        self.assertEqual(response.outcome.option_id, "allow-once")
        # The card carries the real context and command details.
        request = port.requests[0]
        self.assertEqual(request.context["session_id"], "qp-session")
        self.assertEqual(request.severity, "high")
        self.assertEqual(request.body, "ls -la")
        self.assertEqual(request.payload["provider"], "dsh")

    def test_rejected_selects_reject_once(self) -> None:
        port = _FakePort(ApprovalDecision(status="rejected"))
        response = self._request(self._client(port))
        self.assertEqual(response.outcome.option_id, "reject-once")

    def test_timeout_selects_reject_once(self) -> None:
        port = _FakePort(ApprovalDecision(status="timeout"))
        response = self._request(self._client(port))
        self.assertEqual(response.outcome.option_id, "reject-once")

    def test_port_failure_is_conservative(self) -> None:
        port = _FakePort(
            ApprovalDecision(status="approved"),
            error=RuntimeError("card exploded"),
        )
        response = self._request(self._client(port))
        self.assertEqual(response.outcome.option_id, "reject-once")

    def test_no_port_keeps_v01_behaviour(self) -> None:
        client = DshTurnClient(cwd="/tmp", approval=None)
        response = self._request(client)
        self.assertEqual(response.outcome.option_id, "reject-once")

    def test_approved_without_one_shot_allow_rejects(self) -> None:
        port = _FakePort(ApprovalDecision(status="approved"))
        options = [
            {"optionId": "allow-always", "name": "Always", "kind": "allow_always"},
            {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
        ]
        response = self._request(self._client(port), options=options)
        self.assertEqual(response.outcome.option_id, "reject-once")

    def test_each_escalation_is_a_new_card(self) -> None:
        port = _FakePort(ApprovalDecision(status="approved"))
        client = self._client(port)
        first = self._request(client)
        port.decision = ApprovalDecision(status="rejected")
        second = self._request(client)
        self.assertEqual(first.outcome.option_id, "allow-once")
        self.assertEqual(second.outcome.option_id, "reject-once")
        self.assertEqual(len(port.requests), 2)

    def test_trusted_client_still_routes_through_port(self) -> None:
        port = _FakePort(ApprovalDecision(status="approved"))
        client = DshTurnClient(cwd="/tmp", trusted=True, approval=port)
        client.set_approval_context("sess-1", _CONTEXT)
        response = self._request(client)
        self.assertEqual(response.outcome.option_id, "allow-once")
        # trusted is still handed to the host permission adapter.
        self.assertTrue(client._permission_adapter()._trusted)

    def test_cancelled_when_no_reject_option(self) -> None:
        port = _FakePort(ApprovalDecision(status="rejected"))
        options = [{"optionId": "allow-once", "kind": "allow_once"}]
        response = self._request(self._client(port), options=options)
        self.assertEqual(response.outcome.outcome, "cancelled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
