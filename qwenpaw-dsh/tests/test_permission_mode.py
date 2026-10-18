#!/usr/bin/env python3
"""Unit tests for approval_level → DSH permission mode mapping.

Run: ~/.qwenpaw/venv/bin/python tests/test_permission_mode.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core.permissions import (  # noqa: E402
    DANGER_FULL_ACCESS,
    READ_ONLY,
    WORKSPACE_WRITE,
    resolve_permission_mode,
)


class _AgentConfig:
    def __init__(self, approval_level: str) -> None:
        self.approval_level = approval_level


class MappingTests(unittest.TestCase):
    def test_explicit_permission_mode_wins(self) -> None:
        settings = {
            "permission_mode": "danger-full-access",
            "_request_context": {"approval_level": "STRICT"},
        }
        self.assertEqual(resolve_permission_mode(settings), DANGER_FULL_ACCESS)

    def test_session_level_mapping(self) -> None:
        cases = {
            "OFF": DANGER_FULL_ACCESS,
            "AUTO": WORKSPACE_WRITE,
            "SMART": WORKSPACE_WRITE,
            "STRICT": READ_ONLY,
        }
        for level, expected in cases.items():
            with self.subTest(level=level):
                settings = {"_request_context": {"approval_level": level}}
                self.assertEqual(resolve_permission_mode(settings), expected)

    def test_session_level_is_case_insensitive(self) -> None:
        for raw in ("strict", "Strict", "sTrIcT", " strict "):
            with self.subTest(raw=raw):
                settings = {"_request_context": {"approval_level": raw}}
                self.assertEqual(resolve_permission_mode(settings), READ_ONLY)

    def test_unknown_level_falls_back_to_workspace_write(self) -> None:
        settings = {"_request_context": {"approval_level": "MYSTERY"}}
        self.assertEqual(resolve_permission_mode(settings), WORKSPACE_WRITE)

    def test_missing_level_defaults_to_auto(self) -> None:
        self.assertEqual(resolve_permission_mode({}), WORKSPACE_WRITE)
        self.assertEqual(resolve_permission_mode(None), WORKSPACE_WRITE)
        self.assertEqual(
            resolve_permission_mode({"_request_context": {}}),
            WORKSPACE_WRITE,
        )

    def test_agent_level_used_when_session_level_absent(self) -> None:
        def fake_load(agent_id: str) -> _AgentConfig:
            self.assertEqual(agent_id, "agent-7")
            return _AgentConfig("STRICT")

        with mock.patch(
            "qwenpaw.config.config.load_agent_config",
            side_effect=fake_load,
        ):
            settings = {"_request_context": {"agent_id": "agent-7"}}
            self.assertEqual(resolve_permission_mode(settings), READ_ONLY)

    def test_session_level_beats_agent_level(self) -> None:
        with mock.patch(
            "qwenpaw.config.config.load_agent_config",
            return_value=_AgentConfig("STRICT"),
        ):
            settings = {
                "_request_context": {"agent_id": "agent-7", "approval_level": "OFF"},
            }
            self.assertEqual(resolve_permission_mode(settings), DANGER_FULL_ACCESS)

    def test_agent_config_failure_is_ignored(self) -> None:
        with mock.patch(
            "qwenpaw.config.config.load_agent_config",
            side_effect=RuntimeError("boom"),
        ):
            settings = {"_request_context": {"agent_id": "agent-7"}}
            self.assertEqual(resolve_permission_mode(settings), WORKSPACE_WRITE)

    def test_no_agent_id_skips_agent_config(self) -> None:
        with mock.patch(
            "qwenpaw.config.config.load_agent_config",
            side_effect=AssertionError("must not be called"),
        ):
            self.assertEqual(resolve_permission_mode({}), WORKSPACE_WRITE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
