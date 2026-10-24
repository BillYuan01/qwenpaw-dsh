#!/usr/bin/env python3
"""G3 host-contract regressions: slash commands and session history (R08).

Offline only. No dsh process, no profile, no credentials: every spawn path is
either patched out or asserted to be untouched. ``status()`` is exercised with
``_binary_version`` patched, because ``dsh --version`` is a real subprocess.

What is pinned here
-------------------
* ``DshAdapter`` satisfies the stable ``HarnessAdapter`` contract, so host calls
  to ``run_command``/``history``/``discover_*`` no longer raise AttributeError;
* ``/status`` returns ``TEXT_DELTA`` then ``COMPLETED`` from adapter memory and
  never starts DSH, probes models, prompts, or reads credentials;
* unknown commands return a visible ERROR (never fake success);
* ``history()`` returns a list, mutates nothing, and does not fabricate history;
* status text carries no absolute paths, state directories, session ids or env
  values.

Run: ~/.qwenpaw/venv/bin/python tests/test_host_contract.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw.harnesses.base import HarnessAdapter  # noqa: E402
from qwenpaw.harnesses.events import HarnessEventKind  # noqa: E402

from qwenpaw_dsh.core import adapter as adapter_module  # noqa: E402
from qwenpaw_dsh.core.acp_client import DshConnection  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter, LiveSession  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402

SECRET_ENV_VALUE = "sentinel-env-value-not-a-token"
SECRET_BINARY_DIR = "/very/private/place"


def kinds(events) -> list[str]:
    return [event.kind.value for event in events]


def text_of(events) -> str:
    return "".join(event.text for event in events)


class HostContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "harnesses"
        self.cwd = root / "ws"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.dsh_home = root / "dsh-home"
        self.adapter = DshAdapter(
            state_dir=self.state,
            settings={
                "profile": "acp",
                "binary": f"{SECRET_BINARY_DIR}/dsh",
                "permission_mode": "workspace-write",
            },
            host=HostContext(state_dir=self.state),
        )
        self._logs = logging.Handler()
        self.messages: list[str] = []
        self._logs.emit = lambda record: self.messages.append(record.getMessage())
        logging.getLogger("qwenpaw.dsh").addHandler(self._logs)

    async def asyncTearDown(self) -> None:
        try:
            await self.adapter.stop()
        finally:
            logging.getLogger("qwenpaw.dsh").removeHandler(self._logs)
            self.tmp.cleanup()

    # ------------------------------------------------------------------
    def test_adapter_inherits_the_stable_host_contract(self) -> None:
        self.assertIsInstance(self.adapter, HarnessAdapter)
        for name in (
            "status",
            "start_login",
            "logout",
            "models",
            "history",
            "run_command",
            "discover_mcp",
            "discover_skills",
            "reset_session",
            "run_turn",
            "stop",
        ):
            self.assertTrue(
                hasattr(self.adapter, name),
                f"DshAdapter is missing the host contract method {name}",
            )
        self.assertIn(HarnessAdapter, type(self.adapter).__mro__)

    async def test_status_command_is_local_and_capability_honest(self) -> None:
        self.adapter._records["qp-secret-1"] = {
            "acpSessionId": "acp-secret-9",
            "cwd": "/secret/workspace",
        }
        self.adapter._live["qp-secret-1"] = LiveSession(
            connection=object(),
            acp_session_id="acp-secret-9",
            fingerprint="deadbeef",
        )
        records_before = json.loads(json.dumps(self.adapter._records))
        live_before = dict(self.adapter._live)
        # A failed discovery may carry a private path in its message; /status
        # must not echo it.
        self.adapter._models_cache = adapter_module.ModelCatalog(
            fetched_at=0.0,
            models=[],
            value_map={},
            error=f"FileNotFoundError: {SECRET_BINARY_DIR}/dsh",
        )

        spawn = mock.patch.object(
            asyncio,
            "create_subprocess_exec",
            side_effect=AssertionError("status must not spawn a process"),
        )
        opened = mock.patch.object(
            DshConnection,
            "open",
            new=mock.AsyncMock(
                side_effect=AssertionError("status must not open a connection"),
            ),
        )
        probed = mock.patch.object(
            DshAdapter,
            "models",
            new=mock.AsyncMock(
                side_effect=AssertionError("status must not probe models"),
            ),
        )
        status_method = mock.patch.object(
            DshAdapter,
            "status",
            new=mock.AsyncMock(
                side_effect=AssertionError("status must not call status()"),
            ),
        )
        prompted = mock.patch.object(
            DshConnection,
            "prompt",
            new=mock.AsyncMock(
                side_effect=AssertionError("status must not prompt"),
            ),
        )
        with spawn, opened, probed, status_method, prompted, mock.patch.dict(
            os.environ,
            {"DSH_API_KEY": SECRET_ENV_VALUE},
        ):
            events = await self.adapter.run_command(
                session_id="qp-secret-1",
                command="status",
                arguments="",
                cwd=self.cwd,
                settings={},
            )

        self.assertEqual(kinds(events), ["text_delta", "completed"])
        text = text_of(events)
        self.assertIn("DeepSeek Harness", text)
        self.assertIn("profile: acp", text)
        self.assertIn("permission mode: workspace-write", text)
        self.assertIn("dsh", text)
        self.assertIn("last discovery failed", text)
        self.assertIn("details in logs", text)
        for forbidden in (
            SECRET_BINARY_DIR,
            str(self.state),
            str(self.cwd),
            "qp-secret-1",
            "acp-secret-9",
            SECRET_ENV_VALUE,
            "deadbeef",
        ):
            self.assertNotIn(forbidden, text)
        self.assertEqual(self.adapter._records, records_before)
        self.assertEqual(self.adapter._live, live_before)

    async def test_unknown_command_is_a_visible_error(self) -> None:
        events = await self.adapter.run_command(
            session_id="qp-1",
            command="reload",
            arguments="",
            cwd=self.cwd,
            settings={},
        )

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("not supported", text_of(events))

    async def test_history_is_empty_pure_and_does_not_claim_replay(self) -> None:
        records_before = {"qp-1": {"acpSessionId": "acp-secret-9"}}
        self.adapter._records = dict(records_before)
        self.adapter._live["qp-1"] = LiveSession(
            connection=object(),
            acp_session_id="acp-secret-9",
            fingerprint="deadbeef",
        )

        history = await self.adapter.history("qp-1")

        self.assertIsInstance(history, list)
        self.assertEqual(history, [])
        self.assertEqual(self.adapter._records, records_before)
        self.assertEqual(len(self.adapter._live), 1)
        self.assertFalse((self.state / "dsh_sessions.json").exists())
        doc = DshAdapter.history.__doc__ or ""
        self.assertIn("history", doc.lower())

    async def test_status_method_does_not_spawn_when_version_is_patched(
        self,
    ) -> None:
        (self.dsh_home / "profiles" / "acp").mkdir(parents=True)
        fake_binary = self.cwd / "dsh"
        fake_binary.write_text("#!/bin/sh\n", encoding="utf-8")

        version = mock.AsyncMock(return_value="0.2.1")
        with mock.patch.object(
            adapter_module.shutil,
            "which",
            return_value=str(fake_binary),
        ), mock.patch.object(
            DshAdapter,
            "_binary_version",
            new=version,
        ), mock.patch.object(
            asyncio,
            "create_subprocess_exec",
            side_effect=AssertionError("status() must not spawn here"),
        ), mock.patch.dict(
            os.environ,
            {"DSH_HOME": str(self.dsh_home)},
        ):
            provider = await self.adapter.status()

        self.assertTrue(provider.installed)
        self.assertTrue(provider.available)
        self.assertIsNone(provider.error)
        version.assert_awaited_once()

    async def test_capabilities_declare_only_implemented_commands(self) -> None:
        capabilities = self.adapter._capabilities()
        declared = {item.name for item in capabilities.commands}
        self.assertEqual(declared, {"status"})

        events = await self.adapter.run_command(
            session_id="qp-1",
            command="status",
            arguments="now",
            cwd=self.cwd,
            settings={},
        )
        self.assertEqual(kinds(events)[-1], "completed")

    async def test_discovery_defaults_are_inherited(self) -> None:
        self.assertEqual(await self.adapter.discover_mcp(self.cwd), [])
        self.assertEqual(await self.adapter.discover_skills(self.cwd), [])

    async def test_commands_and_history_touch_no_session_state_file(self) -> None:
        before = sorted(path.name for path in self.state.iterdir())
        await self.adapter.run_command(
            session_id="qp-1",
            command="status",
            arguments="",
            cwd=self.cwd,
            settings={},
        )
        await self.adapter.history("qp-1")
        after = sorted(path.name for path in self.state.iterdir())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
