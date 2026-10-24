#!/usr/bin/env python3
"""Unit tests for the gate-4 MCP projection.

Covers the pure mapping (stdio/http/sse/missing fields/absolute path/env and
header plaintext), the runtime extraction, and the adapter wiring including the
"drop MCP and retry once" recovery. Secrets must never reach the log.

Run: ~/.qwenpaw/venv/bin/python tests/test_mcp.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acp.schema import HttpMcpServer, McpServerStdio  # noqa: E402

from qwenpaw_dsh.core import mcp as mc  # noqa: E402
from qwenpaw_dsh.core.acp_client import DshConnection  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402


def stdio(name="srv", command="python3", args=None, env=None, **_):
    return SimpleNamespace(
        name=name,
        transport="stdio",
        command=command,
        args=list(args or []),
        env=dict(env or {}),
    )


def http(name="remote", url="https://mcp.example/x", headers=None, transport="http", **_):
    return SimpleNamespace(
        name=name,
        transport=transport,
        url=url,
        headers=dict(headers or {}),
    )


class MappingTests(unittest.TestCase):
    def test_stdio_resolves_command_to_absolute_path(self) -> None:
        server = stdio(command="mcp-bin", args=["--flag", "value"])
        with mock.patch.object(mc.shutil, "which", return_value="/abs/mcp-bin"):
            mapped, reason = mc.map_server(server)
        self.assertIsNone(reason)
        self.assertIsInstance(mapped, McpServerStdio)
        self.assertEqual(mapped.command, "/abs/mcp-bin")
        self.assertEqual(mapped.args, ["--flag", "value"])
        self.assertEqual(mapped.name, "srv")
        self.assertEqual(mapped.env, [])

    def test_stdio_command_not_found_keeps_original_and_warns(self) -> None:
        server = stdio(command="does-not-exist")
        with mock.patch.object(mc.shutil, "which", return_value=None):
            with self.assertLogs("qwenpaw.dsh", level="WARNING") as logs:
                mapped, reason = mc.map_server(server)
        self.assertIsNone(reason)
        self.assertEqual(mapped.command, "does-not-exist")
        self.assertIn("not found on PATH", "\n".join(logs.output))

    def test_stdio_absolute_command_is_not_rewritten(self) -> None:
        server = stdio(command="/opt/bin/mcp")
        with mock.patch.object(mc.shutil, "which", return_value="/opt/bin/mcp"):
            mapped, _ = mc.map_server(server)
        self.assertEqual(mapped.command, "/opt/bin/mcp")

    def test_env_revealed_from_method(self) -> None:
        server = SimpleNamespace(
            name="with-secrets",
            transport="stdio",
            command="python3",
            args=[],
            revealed_env=lambda: {"TOKEN": "s3cr3t", "ZED": "last"},
        )
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            mapped, _ = mc.map_server(server)
        self.assertEqual(
            [(item.name, item.value) for item in mapped.env],
            [("TOKEN", "s3cr3t"), ("ZED", "last")],
        )

    def test_env_revealed_from_secret_str_field(self) -> None:
        class FakeSecret:
            def __init__(self, value: str) -> None:
                self._value = value

            def get_secret_value(self) -> str:
                return self._value

        server = SimpleNamespace(
            name="secret-str",
            transport="stdio",
            command="python3",
            args=[],
            env={"API_KEY": FakeSecret("hunter2"), "PLAIN": "visible"},
        )
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            mapped, _ = mc.map_server(server)
        self.assertEqual(
            {item.name: item.value for item in mapped.env},
            {"API_KEY": "hunter2", "PLAIN": "visible"},
        )

    def test_http_maps_to_http_server(self) -> None:
        server = http(headers={"Authorization": "Bearer abc"})
        mapped, reason = mc.map_server(server)
        self.assertIsNone(reason)
        self.assertIsInstance(mapped, HttpMcpServer)
        self.assertEqual(mapped.type, "http")
        self.assertEqual(mapped.url, "https://mcp.example/x")
        self.assertEqual(
            [(h.name, h.value) for h in mapped.headers],
            [("Authorization", "Bearer abc")],
        )

    def test_streamable_http_maps_to_http_server(self) -> None:
        mapped, reason = mc.map_server(http(transport="streamable_http"))
        self.assertIsNone(reason)
        self.assertIsInstance(mapped, HttpMcpServer)

    def test_http_headers_revealed_from_method(self) -> None:
        server = SimpleNamespace(
            name="remote",
            transport="http",
            url="https://mcp.example/y",
            headers={"X-Static": "1"},
            revealed_headers=lambda: {"X-Secret": "shh", "X-Static": "1"},
        )
        mapped, _ = mc.map_server(server)
        self.assertEqual(
            {h.name: h.value for h in mapped.headers},
            {"X-Secret": "shh", "X-Static": "1"},
        )

    def test_sse_is_skipped_with_reason(self) -> None:
        with self.assertLogs("qwenpaw.dsh", level="WARNING") as logs:
            plan = mc.project_mcp_servers([http(name="legacy-sse", transport="sse")])
        self.assertEqual(plan.skipped, (("legacy-sse", "sse-unsupported"),))
        self.assertFalse(plan.has_servers)
        self.assertIn("sse-unsupported", "\n".join(logs.output))

    def test_missing_command_is_skipped(self) -> None:
        mapped, reason = mc.map_server(stdio(command=""))
        self.assertIsNone(mapped)
        self.assertEqual(reason, "missing-command")

    def test_missing_url_is_skipped(self) -> None:
        mapped, reason = mc.map_server(http(url=""))
        self.assertIsNone(mapped)
        self.assertEqual(reason, "missing-url")

    def test_missing_name_is_skipped(self) -> None:
        mapped, reason = mc.map_server(stdio(name=""))
        self.assertIsNone(mapped)
        self.assertEqual(reason, "missing-name")

    def test_unknown_transport_is_skipped(self) -> None:
        mapped, reason = mc.map_server(
            SimpleNamespace(name="weird", transport="carrier-pigeon"),
        )
        self.assertIsNone(mapped)
        self.assertEqual(reason, "unsupported-transport:carrier-pigeon")

    def test_dict_input_is_accepted(self) -> None:
        server = {
            "name": "dict-srv",
            "transport": "stdio",
            "command": "python3",
            "args": ["a"],
            "env": {"K": "V"},
        }
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            mapped, reason = mc.map_server(server)
        self.assertIsNone(reason)
        self.assertEqual(mapped.command, "/usr/bin/python3")
        self.assertEqual([(e.name, e.value) for e in mapped.env], [("K", "V")])

    def test_non_list_args_is_treated_as_empty(self) -> None:
        server = SimpleNamespace(
            name="weird-args",
            transport="stdio",
            command="python3",
            args="not-a-list",
            env={},
        )
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            mapped, reason = mc.map_server(server)
        self.assertIsNone(reason)
        self.assertEqual(mapped.args, [])

    def test_one_bad_server_does_not_drop_the_others(self) -> None:
        servers = [
            stdio(name="good-1", command="python3"),
            stdio(name="broken", command=""),
            http(name="sse-only", transport="sse"),
            http(name="good-2"),
        ]
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            with self.assertLogs("qwenpaw.dsh", level="WARNING"):
                plan = mc.project_mcp_servers(servers)
        self.assertEqual(plan.names, ("good-1", "good-2"))
        self.assertEqual(len(plan.servers), 2)
        self.assertEqual(
            set(plan.skipped),
            {("broken", "missing-command"), ("sse-only", "sse-unsupported")},
        )
        self.assertTrue(plan.has_servers)

    def test_mapping_exception_is_isolated(self) -> None:
        class Boom:
            name = "boom"
            transport = "stdio"

            @property
            def command(self):  # noqa: D401 — raises on purpose
                raise RuntimeError("kaboom")

        with self.assertLogs("qwenpaw.dsh", level="WARNING"):
            plan = mc.project_mcp_servers([Boom(), stdio(name="ok", command="python3")])
        self.assertEqual(plan.names, ("ok",))
        self.assertEqual(plan.skipped, (("boom", "mapping-error"),))

    def test_secrets_never_reach_the_log(self) -> None:
        secret = "super-secret-token-value"
        server = SimpleNamespace(
            name="leaky",
            transport="stdio",
            command="",
            args=[],
            revealed_env=lambda: {"TOKEN": secret},
        )
        with self.assertLogs("qwenpaw.dsh", level="WARNING") as logs:
            plan = mc.project_mcp_servers([server])
        self.assertEqual(plan.skipped, (("leaky", "missing-command"),))
        self.assertNotIn(secret, "\n".join(logs.output))
        # success path must not log the value either
        server.command = "python3"
        records: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]
        logger = logging.getLogger("qwenpaw.dsh")
        logger.addHandler(handler)
        try:
            with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
                mc.map_server(server)
            mc.project_mcp_servers([server])
            logger.warning("sentinel")
        finally:
            logger.removeHandler(handler)
        self.assertNotIn(secret, "\n".join(records))
        self.assertIn("sentinel", records)


class ExtractionTests(unittest.TestCase):
    def test_runtime_from_object(self) -> None:
        server = stdio()
        settings = {"_runtime_capabilities": SimpleNamespace(mcp_servers=[server])}
        self.assertEqual(mc.runtime_mcp_servers(settings), [server])

    def test_runtime_from_dict(self) -> None:
        payload = {"name": "x", "transport": "stdio", "command": "python3"}
        settings = {"_runtime_capabilities": {"mcp_servers": [payload]}}
        self.assertEqual(mc.runtime_mcp_servers(settings), [payload])

    def test_runtime_camel_case_fallback(self) -> None:
        payload = {"name": "x", "transport": "stdio", "command": "python3"}
        self.assertEqual(mc.mcp_sources({"mcpServers": [payload]}), [payload])

    def test_missing_or_broken_inputs(self) -> None:
        self.assertEqual(mc.mcp_sources(None), [])
        self.assertEqual(mc.mcp_sources({}), [])
        self.assertEqual(mc.mcp_sources({"mcp_servers": "nope"}), [])
        self.assertEqual(mc.runtime_mcp_servers({}), [])
        self.assertEqual(mc.runtime_mcp_servers(None), [])


class _FakeConnection:
    """Records session calls and lets tests script the two outcomes."""

    def __init__(self, key=("dsh", "acp", "read-only", "ask", "")) -> None:
        self.key = key
        self.alive = True
        self.calls: list[tuple] = []
        self.closed = False
        self._new = None
        self._resume = None
        self._client = None

    async def new_session(self, cwd, mcp_servers=None):
        self.calls.append(("new", str(cwd), mcp_servers))
        if self._new is not None:
            return await self._new(cwd, mcp_servers)
        return "acp-new"

    async def resume_session(self, cwd, acp_session_id, mcp_servers=None):
        self.calls.append(("resume", str(cwd), acp_session_id, mcp_servers))
        if self._resume is not None:
            return await self._resume(cwd, acp_session_id, mcp_servers)
        return acp_session_id

    async def aclose(self) -> None:
        self.alive = False
        self.closed = True


def caps(*servers) -> SimpleNamespace:
    return SimpleNamespace(mcp_servers=list(servers))


class AdapterMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "harnesses"
        self.cwd = Path(self.tmp.name) / "ws"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.adapter = DshAdapter(
            state_dir=self.state,
            settings={"profile": "qwenpaw"},
            host=HostContext(state_dir=self.state),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _good(self, name="good", command="python3") -> SimpleNamespace:
        return stdio(name=name, command=command)

    async def test_refresh_does_not_touch_pool_key(self) -> None:
        before = self.adapter._pool_key()
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            plan = self.adapter._refresh_mcp(
                {"_runtime_capabilities": caps(self._good())},
            )
        self.assertTrue(plan.has_servers)
        self.assertEqual(plan.names, ("good",))
        self.assertEqual(self.adapter._pool_key(), before)

    async def test_no_definitions_is_empty_list(self) -> None:
        plan = self.adapter._refresh_mcp({})
        self.assertEqual(plan.servers, ())
        connection = _FakeConnection()
        await self.adapter._ensure_session(connection, "qp-none", self.cwd, plan)
        self.assertEqual(connection.calls, [("new", str(self.cwd), [])])

    async def test_new_session_receives_projected_servers(self) -> None:
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            plan = self.adapter._refresh_mcp(
                {"_runtime_capabilities": caps(self._good(name="a"), self._good(name="b"))},
            )
        connection = _FakeConnection()
        session = await self.adapter._ensure_session(connection, "qp-1", self.cwd, plan)
        self.assertEqual(session.acp_session_id, "acp-new")
        (kind, cwd, servers), = connection.calls
        self.assertEqual(kind, "new")
        self.assertEqual([s.name for s in servers], ["a", "b"])
        self.assertEqual(servers[0].command, "/usr/bin/python3")

    async def test_resume_session_receives_projected_servers(self) -> None:
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            plan = self.adapter._refresh_mcp(
                {"_runtime_capabilities": caps(self._good())},
            )
        self.adapter._records["qp-2"] = {"acpSessionId": "old-acp"}
        connection = _FakeConnection()
        session = await self.adapter._ensure_session(connection, "qp-2", self.cwd, plan)
        self.assertEqual(session.acp_session_id, "old-acp")
        (kind, _cwd, acp_id, servers), = connection.calls
        self.assertEqual(kind, "resume")
        self.assertEqual(acp_id, "old-acp")
        self.assertEqual([s.name for s in servers], ["good"])

    async def test_bad_server_retries_without_mcp(self) -> None:
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            plan = self.adapter._refresh_mcp(
                {"_runtime_capabilities": caps(self._good(name="good"), self._good(name="bad"))},
            )
        connection = _FakeConnection()

        async def new_session(cwd, mcp_servers):
            if mcp_servers:
                raise RuntimeError("Internal error")
            return "acp-fallback"

        connection._new = new_session
        with self.assertLogs("qwenpaw.dsh", level="WARNING") as logs:
            session = await self.adapter._ensure_session(connection, "qp-3", self.cwd, plan)
        self.assertEqual(session.acp_session_id, "acp-fallback")
        self.assertEqual(
            connection.calls,
            [
                ("new", str(self.cwd), mock.ANY),
                ("new", str(self.cwd), []),
            ],
        )
        self.assertEqual(len(connection.calls[0][2]), 2)
        joined = "\n".join(logs.output)
        self.assertIn("MCP disabled", joined)
        self.assertIn("good", joined)
        self.assertIn("bad", joined)

    async def test_bad_server_retry_also_failing_propagates(self) -> None:
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            plan = self.adapter._refresh_mcp(
                {"_runtime_capabilities": caps(self._good())},
            )
        connection = _FakeConnection()

        async def new_session(cwd, mcp_servers):
            raise RuntimeError("still broken")

        connection._new = new_session
        with self.assertLogs("qwenpaw.dsh", level="WARNING"):
            with self.assertRaises(RuntimeError):
                await self.adapter._ensure_session(connection, "qp-4", self.cwd, plan)
        self.assertEqual(len(connection.calls), 2)
        self.assertEqual(connection.calls[1][2], [])

    async def test_no_mcp_failure_is_not_retried(self) -> None:
        plan = self.adapter._refresh_mcp({})
        connection = _FakeConnection()

        async def new_session(cwd, mcp_servers):
            raise RuntimeError("plain failure")

        connection._new = new_session
        with self.assertRaises(RuntimeError):
            await self.adapter._ensure_session(connection, "qp-5", self.cwd, plan)
        self.assertEqual(len(connection.calls), 1)


class _FakeClient:
    def __init__(self) -> None:
        self.contexts: list[tuple] = []
        self._queues: dict[str, asyncio.Queue] = {}

    def attach_session(self, session_id) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[str(session_id)] = queue
        return queue

    def detach_session(self, session_id, queue=None) -> None:
        key = str(session_id)
        if queue is None or self._queues.get(key) is queue:
            self._queues.pop(key, None)

    def set_approval_context(self, session_id, context) -> None:
        self.contexts.append((session_id, context))

    def clear_approval_context(self, session_id) -> None:
        self._queues.pop(str(session_id), None)


class _TurnConnection(_FakeConnection):
    def __init__(self, key) -> None:
        super().__init__(key=key)
        self._client = _FakeClient()

    @property
    def client(self) -> _FakeClient:
        return self._client

    async def prompt(self, session_id, text):
        return SimpleNamespace(stop_reason="end_turn")


class RunTurnMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "harnesses"
        self.cwd = Path(self.tmp.name) / "ws"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.adapter = DshAdapter(
            state_dir=self.state,
            settings={"profile": "qwenpaw"},
            host=HostContext(state_dir=self.state),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    async def test_run_turn_projects_mcp_before_session(self) -> None:
        connection = _TurnConnection(self.adapter._pool_key())

        async def fake_open(**kwargs):
            return connection

        settings = {
            "_runtime_capabilities": caps(stdio(name="good", command="python3")),
        }
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            with mock.patch.object(DshConnection, "open", side_effect=fake_open):
                kinds = [
                    event.kind.value
                    async for event in self.adapter.run_turn(
                        session_id="qp-turn",
                        prompt="hi",
                        cwd=self.cwd,
                        settings=settings,
                    )
                ]
        self.assertIn("completed", kinds)
        (kind, _cwd, servers), = connection.calls
        self.assertEqual(kind, "new")
        self.assertEqual([s.name for s in servers], ["good"])

    async def test_run_turn_survives_bad_server(self) -> None:
        connection = _TurnConnection(self.adapter._pool_key())

        async def fake_open(**kwargs):
            return connection

        async def new_session(cwd, mcp_servers):
            if mcp_servers:
                raise RuntimeError("Internal error")
            return "acp-fallback"

        connection._new = new_session
        settings = {
            "_runtime_capabilities": caps(stdio(name="bad", command="python3")),
        }
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            with mock.patch.object(DshConnection, "open", side_effect=fake_open):
                with self.assertLogs("qwenpaw.dsh", level="WARNING"):
                    kinds = [
                        event.kind.value
                        async for event in self.adapter.run_turn(
                            session_id="qp-turn-2",
                            prompt="hi",
                            cwd=self.cwd,
                            settings=settings,
                        )
                    ]
        self.assertIn("completed", kinds)
        self.assertNotIn("error", kinds)
        self.assertEqual(len(connection.calls), 2)
        self.assertEqual(connection.calls[1][2], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
