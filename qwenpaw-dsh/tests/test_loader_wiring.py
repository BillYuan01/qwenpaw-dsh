#!/usr/bin/env python3
"""Wiring tests for the monkeypatch loader (gate 2).

Verifies that ``create_adapter`` hands the adapter a real approval port and
that ``adapter_config_key`` tracks the resolved permission mode (so an
approval-level change rebuilds the adapter and its process pool).

Mounts against the installed QwenPaw (2.2.x); skipped when unavailable.

Run: ~/.qwenpaw/venv/bin/python tests/test_loader_wiring.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.approvals import QwenPawApprovalPort  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402
from qwenpaw_dsh.loaders.monkeypatch import MonkeypatchLoader  # noqa: E402
from qwenpaw_dsh.provider import build_provider_spec  # noqa: E402


class LoaderWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loader = MonkeypatchLoader()
        if not self.loader.probe().mounted:
            self.skipTest("monkeypatch loader cannot mount in this environment")

    def tearDown(self) -> None:
        self.loader.unmount()

    def test_create_adapter_injects_approval_port_and_mode(self) -> None:
        import qwenpaw.harnesses.registry as registry

        spec = build_provider_spec(HostContext(state_dir=Path(tempfile.mkdtemp())))
        self.loader.mount(spec)
        settings = {
            "_request_context": {
                "agent_id": "agent-1",
                "approval_level": "STRICT",
            },
        }
        adapter = registry.create_adapter(
            "dsh",
            Path(tempfile.mkdtemp()),
            settings,
        )
        self.assertIsInstance(adapter.host.approval, QwenPawApprovalPort)
        self.assertEqual(adapter._permission_mode, "read-only")
        self.assertEqual(
            registry.adapter_config_key("dsh", settings),
            ("", "acp", "read-only", "ask"),
        )

    def test_explicit_mode_still_wins_in_config_key(self) -> None:
        import qwenpaw.harnesses.registry as registry

        spec = build_provider_spec(HostContext(state_dir=Path(tempfile.mkdtemp())))
        self.loader.mount(spec)
        settings = {
            "permission_mode": "danger-full-access",
            "_request_context": {"approval_level": "STRICT"},
        }
        adapter = registry.create_adapter(
            "dsh",
            Path(tempfile.mkdtemp()),
            settings,
        )
        self.assertEqual(adapter._permission_mode, "danger-full-access")
        self.assertEqual(
            registry.adapter_config_key("dsh", settings)[2],
            "danger-full-access",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
