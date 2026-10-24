#!/usr/bin/env python3
"""G2 lifecycle regressions: cancel, concurrency, connection identity, MCP.

All tests are offline: a fake ACP connection is driven through the real
``DshConnection`` / ``DshTurnClient`` / ``DshAdapter`` code. No dsh process,
no profile, no credentials, no model call.

Run: ~/.qwenpaw/venv/bin/python tests/test_lifecycle.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acp.schema import AgentMessageChunk, TextContentBlock  # noqa: E402

from qwenpaw_dsh.core import acp_client as acp_client_module  # noqa: E402
from qwenpaw_dsh.core import adapter as adapter_module  # noqa: E402
from qwenpaw_dsh.core import mcp as mc  # noqa: E402
from qwenpaw_dsh.core.acp_client import DshConnection, DshTurnClient  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402


def chunk(text: str) -> AgentMessageChunk:
    return AgentMessageChunk(
        sessionUpdate="agent_message_chunk",
        content=TextContentBlock(type="text", text=text),
    )


def text_of(events) -> str:
    return "".join(e.text for e in events if e.kind.value == "text_delta")


def kinds(events) -> list[str]:
    return [e.kind.value for e in events]


def mcp_server(name: str, *, command="python3", args=None, env=None):
    return SimpleNamespace(
        name=name,
        transport="stdio",
        command=command,
        args=list(args or []),
        env=dict(env or {}),
    )


class FakeStack:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.pid = 4000000

    def kill(self) -> None:
        self.returncode = -9


class FakeACP:
    """Scriptable ACP connection (the object ``ClientSideConnection`` would be)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.prompt_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.new_error: Exception | None = None
        self.resume_error: Exception | None = None
        self.close_error: Exception | None = None
        self.prompt_impl = None
        self.counter = 0
        self.cancel_event = asyncio.Event()
        self.finish_event = asyncio.Event()
        self.prompt_started = asyncio.Event()

    async def new_session(self, cwd, mcp_servers=None):
        self.calls.append(("new", str(cwd), list(mcp_servers or [])))
        if self.new_error is not None:
            raise self.new_error
        self.counter += 1
        return SimpleNamespace(session_id=f"acp-{self.counter}", config_options=[])

    async def resume_session(self, cwd, session_id, mcp_servers=None):
        self.calls.append(("resume", str(cwd), session_id, list(mcp_servers or [])))
        if self.resume_error is not None:
            raise self.resume_error
        return SimpleNamespace(session_id=session_id, config_options=[])

    async def close_session(self, session_id):
        self.calls.append(("close", session_id))
        if self.close_error is not None:
            raise self.close_error
        return {}

    async def cancel(self, session_id):
        self.cancel_calls.append(session_id)
        self.cancel_event.set()

    async def prompt(self, prompt, session_id):
        self.calls.append(("prompt", session_id))
        self.prompt_calls.append(session_id)
        self.prompt_started.set()
        if self.prompt_impl is not None:
            return await self.prompt_impl(self, session_id, prompt)
        return SimpleNamespace(stop_reason="end_turn")

    async def set_config_option(self, config_id, session_id, value):
        self.calls.append(("config", config_id, session_id, value))
        return SimpleNamespace()


async def race(*events: asyncio.Event) -> None:
    waiters = [asyncio.create_task(event.wait()) for event in events]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


class LifecycleBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "harnesses"
        self.cwd = root / "ws"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.skills = root / "skills"
        self.skills.mkdir(parents=True, exist_ok=True)
        self.adapter = DshAdapter(
            state_dir=self.state,
            settings={"profile": "qwenpaw"},
            host=HostContext(state_dir=self.state),
        )
        # Never signal a fake PID: aclose() best-effort kill is neutralized.
        self._kill = mock.patch.object(acp_client_module, "kill_process_tree")
        self._kill.start()
        self._logs = logging.Handler()
        self._messages: list[str] = []
        self._logs.emit = lambda record: self._messages.append(record.getMessage())
        logging.getLogger("qwenpaw.dsh").addHandler(self._logs)
        logging.getLogger("qwenpaw.dsh").setLevel(logging.DEBUG)

    async def asyncTearDown(self) -> None:
        try:
            await self.adapter.stop()
        finally:
            logging.getLogger("qwenpaw.dsh").removeHandler(self._logs)
            self._kill.stop()
            self.tmp.cleanup()

    # -- helpers -------------------------------------------------------
    def make_connection(
        self,
        acp: FakeACP | None = None,
        *,
        key: tuple | None = None,
    ) -> tuple[DshConnection, FakeACP]:
        acp = acp or FakeACP()
        client = DshTurnClient(cwd=str(self.cwd), trusted=False, approval=None)
        connection = DshConnection(
            stack=FakeStack(),
            conn=acp,
            process=FakeProcess(),
            client=client,
            key=key or self.adapter._pool_key(),
            prompt_capabilities=SimpleNamespace(image=False),
        )
        return connection, acp

    def install(self, connection: DshConnection) -> None:
        self.adapter._connections[connection.key] = connection

    def settings(self, *servers, skills: list[dict] | None = None) -> dict:
        caps: dict = {"mcp_servers": list(servers)}
        if skills is not None:
            caps["skills"] = list(skills)
        return {"_runtime_capabilities": caps}

    @staticmethod
    def skill(name: str, path: Path, revision: str) -> dict:
        return {"name": name, "directory": str(path), "revision": revision}

    def write_skill(self, dirname: str) -> Path:
        directory = self.skills / dirname
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            "---\n"
            f"name: {dirname}\n"
            f'description: "lifecycle {dirname}"\n'
            "---\n\nbody\n",
            encoding="utf-8",
        )
        return directory

    async def collect(
        self,
        session_id: str,
        settings: dict,
        *,
        timeout=2.0,
        name: str | None = None,
    ):
        events = []

        async def run() -> None:
            async for event in self.adapter.run_turn(
                session_id=session_id,
                prompt="hi",
                cwd=self.cwd,
                settings=settings,
                attachments=None,
            ):
                events.append(event)

        task = asyncio.create_task(run(), name=name)
        await asyncio.wait({task}, timeout=timeout)
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=0.5)
            self.fail(f"turn {session_id} did not finish within {timeout}s")
        task.result()
        return events

    async def wait_done(self, task: asyncio.Task, timeout: float = 1.5) -> bool:
        """Wait without letting a stuck cleanup swallow the timeout.

        ``asyncio.wait`` never cancels the task, so a broken implementation
        that leaves the consumer pending is reported as a failure instead of
        hanging the test run.
        """
        await asyncio.wait({task}, timeout=timeout)
        if task.done():
            return True
        task.cancel()
        await asyncio.wait({task}, timeout=0.5)
        return task.done()

    async def run_turn_task(self, session_id: str, settings: dict, name: str):
        events: list = []

        async def run() -> None:
            async for event in self.adapter.run_turn(
                session_id=session_id,
                prompt="hi",
                cwd=self.cwd,
                settings=settings,
                attachments=None,
            ):
                events.append(event)

        task = asyncio.create_task(run(), name=name)
        return task, events

    async def started(self, acp: FakeACP, count: int = 1) -> None:
        for _ in range(200):
            if len(acp.prompt_calls) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail(f"prompt did not start (calls={acp.prompt_calls})")

    def call_names(self, acp: FakeACP) -> list[str]:
        return [call[0] for call in acp.calls]

    def mcp_names(self, call: tuple) -> list[str]:
        servers = call[-1]
        return [server.name for server in servers]


class CancelTests(LifecycleBase):
    async def test_cancel_sends_exactly_one_notification_and_exits(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def cooperative(acp, session_id, prompt):
            await race(acp.finish_event, acp.cancel_event)
            return SimpleNamespace(stop_reason="cancelled")

        acp.prompt_impl = cooperative
        events: list = []

        async def consume() -> None:
            async for event in self.adapter.run_turn(
                session_id="qp-cancel",
                prompt="hi",
                cwd=self.cwd,
                settings={},
                attachments=None,
            ):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await self.started(acp)
            task.cancel()
            self.assertTrue(await self.wait_done(task, 1.5))
            self.assertTrue(task.cancelled())
        finally:
            acp.finish_event.set()

        self.assertEqual(acp.cancel_calls, ["acp-1"])
        self.assertNotIn("completed", kinds(events))
        self.assertNotIn("cancelled", kinds(events))
        self.assertFalse(connection.stack.closed)
        pending = [
            item for item in asyncio.all_tasks()
            if item is not asyncio.current_task()
        ]
        self.assertEqual(pending, [])

    async def test_stubborn_prompt_is_abandoned_after_bounded_settle(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        self.adapter.cancel_settle_timeout = 0.1
        self.adapter.cancel_notify_timeout = 0.1

        async def stubborn(acp, session_id, prompt):
            try:
                await acp.finish_event.wait()
            except asyncio.CancelledError:
                # Deliberately swallows one cancellation: the adapter must not
                # wait forever for a coroutine that will not stop.
                await acp.finish_event.wait()
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = stubborn
        events: list = []

        async def consume() -> None:
            async for event in self.adapter.run_turn(
                session_id="qp-stub",
                prompt="hi",
                cwd=self.cwd,
                settings={},
                attachments=None,
            ):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await self.started(acp)
            task.cancel()
            self.assertTrue(await self.wait_done(task, 1.5))
            self.assertTrue(task.cancelled())
        finally:
            acp.finish_event.set()
            await asyncio.sleep(0)

        self.assertEqual(acp.cancel_calls, ["acp-1"])
        self.assertTrue(connection.stack.closed)
        self.assertNotIn("completed", kinds(events))

    async def test_cancel_while_waiting_for_same_session_lock_is_local(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def blocking(acp, session_id, prompt):
            await acp.finish_event.wait()
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = blocking
        first_task, first_events = await self.run_turn_task("qp-serial", {}, "turn-1")
        second_task, second_events = await self.run_turn_task("qp-serial", {}, "turn-2")
        try:
            await self.started(acp)
            await asyncio.sleep(0.05)
            self.assertEqual(acp.prompt_calls, ["acp-1"])
            second_task.cancel()
            self.assertTrue(await self.wait_done(second_task, 1.5))
            self.assertTrue(second_task.cancelled())
            # Cancelling a queued turn must not cancel the running one.
            self.assertEqual(acp.cancel_calls, [])
            self.assertFalse(first_task.done())
            acp.finish_event.set()
            self.assertTrue(await self.wait_done(first_task, 2.0))
            first_task.result()
        finally:
            acp.finish_event.set()
            for task in (first_task, second_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(acp.prompt_calls, ["acp-1"])
        self.assertEqual(kinds(first_events).count("completed"), 1)
        self.assertEqual(second_events, [])

    async def test_two_turns_on_one_session_are_serialized(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def blocking(acp, session_id, prompt):
            await acp.finish_event.wait()
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = blocking
        first_task, first_events = await self.run_turn_task("qp-order", {}, "turn-1")
        second_task, second_events = await self.run_turn_task("qp-order", {}, "turn-2")
        try:
            await self.started(acp)
            await asyncio.sleep(0.05)
            self.assertEqual(len(acp.prompt_calls), 1, "second prompt overlapped")
            acp.finish_event.set()
            self.assertTrue(await self.wait_done(first_task, 2.0))
            self.assertTrue(await self.wait_done(second_task, 2.0))
            first_task.result()
            second_task.result()
        finally:
            acp.finish_event.set()
            for task in (first_task, second_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(acp.prompt_calls, ["acp-1", "acp-1"])
        self.assertEqual(kinds(first_events).count("completed"), 1)
        self.assertEqual(kinds(second_events).count("completed"), 1)


class ConcurrencyTests(LifecycleBase):
    async def test_two_sessions_receive_every_frame_and_own_terminal(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        a1 = asyncio.Event()
        b_done = asyncio.Event()

        async def prompt(acp, session_id, blocks):
            if session_id == "acp-1":
                await connection.client.session_update(session_id, chunk("A1"))
                a1.set()
                await b_done.wait()
                await connection.client.session_update(session_id, chunk("A2"))
                await connection.client.session_update(session_id, chunk("A3"))
            else:
                await connection.client.session_update(session_id, chunk("B1"))
                await connection.client.session_update(session_id, chunk("B2"))
                b_done.set()
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = prompt
        a_task, a_events = await self.run_turn_task("qp-A", {}, "turn-A")
        await asyncio.wait_for(a1.wait(), 1.0)
        b_task, b_events = await self.run_turn_task("qp-B", {}, "turn-B")
        try:
            await asyncio.wait({a_task, b_task}, timeout=3.0)
            a_task.result()
            b_task.result()
        finally:
            b_done.set()
            for task in (a_task, b_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(text_of(a_events), "A1A2A3")
        self.assertEqual(text_of(b_events), "B1B2")
        self.assertEqual(kinds(a_events).count("completed"), 1)
        self.assertEqual(kinds(b_events).count("completed"), 1)

    async def test_mcp_plan_is_per_turn_not_shared(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        self.adapter._records["qp-A"] = {"acpSessionId": "a-old"}
        b_refreshed = asyncio.Event()
        a_arrived = asyncio.Event()

        original_connection = getattr(
            self.adapter,
            "_prepare_connection",
            None,
        ) or self.adapter._connection

        async def gated_connection(*args):
            result = await original_connection(*args)
            if asyncio.current_task().get_name() == "turn-A":
                a_arrived.set()
                await b_refreshed.wait()
            return result

        original_refresh = self.adapter._refresh_mcp

        def gated_refresh(settings):
            plan = original_refresh(settings)
            if asyncio.current_task().get_name() == "turn-B":
                b_refreshed.set()
            return plan

        if hasattr(self.adapter, "_prepare_connection"):
            self.adapter._prepare_connection = gated_connection
        else:  # baseline compatibility for the red run
            self.adapter._connection = gated_connection
        self.adapter._refresh_mcp = gated_refresh
        settings_a = self.settings(mcp_server("a-server"))
        settings_b = self.settings(mcp_server("b-server"))
        try:
            a_task, a_events = await self.run_turn_task("qp-A", settings_a, "turn-A")
            await asyncio.wait_for(a_arrived.wait(), 1.0)
            b_events = await self.collect("qp-B", settings_b, name="turn-B")
            self.assertTrue(await self.wait_done(a_task, 2.0))
            a_task.result()
        finally:
            b_refreshed.set()
            if not a_task.done():
                a_task.cancel()
                await asyncio.gather(a_task, return_exceptions=True)

        resumes = [call for call in acp.calls if call[0] == "resume"]
        news = [call for call in acp.calls if call[0] == "new"]
        self.assertEqual(len(resumes), 1)
        self.assertEqual(self.mcp_names(resumes[0]), ["a-server"])
        self.assertEqual(len(news), 1)
        self.assertEqual(self.mcp_names(news[0]), ["b-server"])
        self.assertEqual(kinds(b_events).count("completed"), 1)
        self.assertEqual(kinds(a_events).count("completed"), 1)


class IdentityTests(LifecycleBase):
    async def test_same_key_new_connection_resumes_instead_of_prompting_old_id(self) -> None:
        old, old_acp = self.make_connection()
        self.install(old)
        await self.collect("qp-id", {})
        self.assertEqual(old_acp.prompt_calls, ["acp-1"])
        old.process.returncode = 1

        new, new_acp = self.make_connection(key=old.key)

        async def fake_open(**kwargs):
            return new

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            events = await self.collect("qp-id", {})

        self.assertEqual(new_acp.prompt_calls, ["acp-1"])
        resumes = [c for c in new_acp.calls if c[0] == "resume"]
        self.assertEqual(len(resumes), 1, "old id must be resumed, not prompted")
        self.assertEqual(self.call_names(new_acp)[0], "resume")
        self.assertEqual(kinds(events).count("completed"), 1)

    async def test_unchanged_live_connection_is_reused_without_resume(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        await self.collect("qp-live", {})
        await self.collect("qp-live", {})
        self.assertEqual(acp.prompt_calls, ["acp-1", "acp-1"])
        self.assertEqual([c for c in acp.calls if c[0] in {"resume", "close"}], [])

    async def test_resume_failure_creates_new_session_with_visible_notice(self) -> None:
        old, old_acp = self.make_connection()
        self.install(old)
        await self.collect("qp-rebuild", {})
        old.process.returncode = 1

        new, new_acp = self.make_connection(key=old.key)
        new_acp.resume_error = RuntimeError("resume boom")

        async def fake_open(**kwargs):
            return new

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            events = await self.collect("qp-rebuild", {})

        self.assertEqual(len([c for c in new_acp.calls if c[0] == "resume"]), 1)
        self.assertEqual(new_acp.prompt_calls, ["acp-1"])
        notice = text_of(events)
        self.assertIn("context", notice.lower())
        self.assertIn("[DSH]", notice)
        self.assertEqual(kinds(events).count("completed"), 1)

    async def test_persisted_state_has_no_secret_or_connection_identity(self) -> None:
        secret = "g2-fake-secret-9f1c"
        connection, acp = self.make_connection()
        self.install(connection)
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            await self.collect(
                "qp-state",
                self.settings(mcp_server("srv", env={"TOKEN": secret})),
            )
        raw = (self.state / "dsh_sessions.json").read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertNotIn("fingerprint", raw)
        self.assertNotIn("generation", raw)
        self.assertNotIn("connection", raw)
        payload = json.loads(raw)
        self.assertEqual(payload["qp-state"]["acpSessionId"], "acp-1")
        self.assertTrue(
            set(payload["qp-state"]) <= {
                "acpSessionId",
                "cwd",
                "poolKey",
                "createdAt",
                "updatedAt",
            },
        )


class MCPLifecycleTests(LifecycleBase):
    async def test_mcp_change_closes_then_resumes_live_session(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            await self.collect("qp-mcp", self.settings(mcp_server("a")))
            await self.collect("qp-mcp", self.settings(mcp_server("b")))

        names = self.call_names(acp)
        self.assertIn("close", names)
        self.assertLess(names.index("close"), names.index("resume"))
        prompts = [index for index, name in enumerate(names) if name == "prompt"]
        self.assertEqual(len(prompts), 2)
        self.assertLess(names.index("resume"), prompts[-1], "prompted before applying MCP")
        resume = [c for c in acp.calls if c[0] == "resume"][0]
        self.assertEqual(self.mcp_names(resume), ["b"])
        self.assertEqual(acp.prompt_calls, ["acp-1", "acp-1"])

    async def test_mcp_removal_resumes_with_empty_list(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            await self.collect("qp-empty", self.settings(mcp_server("a")))
            await self.collect("qp-empty", self.settings())

        resume = [c for c in acp.calls if c[0] == "resume"]
        self.assertEqual(len(resume), 1)
        self.assertEqual(self.mcp_names(resume[0]), [])
        self.assertEqual(len([c for c in acp.calls if c[0] == "close"]), 1)
        self.assertEqual(acp.prompt_calls, ["acp-1", "acp-1"])

    async def test_secret_rotation_forces_reconnect_and_never_leaks(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        alpha = "g2-rotate-alpha-111"
        beta = "g2-rotate-beta-222"
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            await self.collect(
                "qp-secret",
                self.settings(mcp_server("srv", env={"TOKEN": alpha})),
            )
            await self.collect(
                "qp-secret",
                self.settings(mcp_server("srv", env={"TOKEN": beta})),
            )
        resume = [c for c in acp.calls if c[0] == "resume"]
        self.assertEqual(len(resume), 1, "secret rotation must reconnect")
        values = {entry.name: entry.value for entry in resume[0][-1][0].env}
        self.assertEqual(values["TOKEN"], beta)
        joined = "\n".join(self._messages)
        self.assertNotIn(alpha, joined)
        self.assertNotIn(beta, joined)
        raw = (self.state / "dsh_sessions.json").read_text(encoding="utf-8")
        self.assertNotIn(alpha, raw)
        self.assertNotIn(beta, raw)

    async def test_header_rotation_forces_reconnect_and_never_leaks(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        alpha = "g2-header-alpha-111"
        beta = "g2-header-beta-222"

        def remote(secret: str):
            return SimpleNamespace(
                name="remote",
                transport="http",
                url="https://mcp.example/x",
                headers={"Authorization": f"Bearer {secret}"},
            )

        await self.collect("qp-header", self.settings(remote(alpha)))
        await self.collect("qp-header", self.settings(remote(beta)))
        resume = [c for c in acp.calls if c[0] == "resume"]
        self.assertEqual(len(resume), 1, "header rotation must reconnect")
        headers = {entry.name: entry.value for entry in resume[0][-1][0].headers}
        self.assertEqual(headers["Authorization"], f"Bearer {beta}")
        joined = "\n".join(self._messages)
        self.assertNotIn(alpha, joined)
        self.assertNotIn(beta, joined)
        raw = (self.state / "dsh_sessions.json").read_text(encoding="utf-8")
        self.assertNotIn(alpha, raw)
        self.assertNotIn(beta, raw)

    async def test_command_and_url_rotation_reconnect(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        def remote(url: str):
            return SimpleNamespace(
                name="remote",
                transport="http",
                url=url,
                headers={},
            )

        def local(command: str):
            return mcp_server("local", command=command)

        which = {"cmd-a": "/opt/a", "cmd-b": "/opt/b"}
        with mock.patch.object(
            mc.shutil,
            "which",
            side_effect=lambda name: which.get(name),
        ):
            await self.collect(
                "qp-cmd",
                self.settings(local("cmd-a"), remote("https://a.example/mcp")),
            )
            await self.collect(
                "qp-cmd",
                self.settings(local("cmd-b"), remote("https://b.example/mcp")),
            )
        resume = [c for c in acp.calls if c[0] == "resume"]
        self.assertEqual(len(resume), 1, "command/url rotation must reconnect")
        servers = {server.name: server for server in resume[0][-1]}
        self.assertEqual(servers["local"].command, "/opt/b")
        self.assertEqual(servers["remote"].url, "https://b.example/mcp")

    async def test_order_only_change_does_not_reconnect(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        first = [
            mcp_server("a", env={"X": "1", "Y": "2"}),
            mcp_server("b"),
        ]
        second = [
            mcp_server("b"),
            mcp_server("a", env={"Y": "2", "X": "1"}),
        ]
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            await self.collect("qp-order-mcp", self.settings(*first))
            await self.collect("qp-order-mcp", self.settings(*second))
        self.assertEqual(
            [c for c in acp.calls if c[0] in {"resume", "close"}],
            [],
        )
        self.assertEqual(acp.prompt_calls, ["acp-1", "acp-1"])

    async def test_close_failure_blocks_prompt_and_retires_connection(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            await self.collect("qp-close", self.settings(mcp_server("a")))
            acp.close_error = RuntimeError("close boom")
            events = await self.collect("qp-close", self.settings(mcp_server("b")))

        self.assertEqual(acp.prompt_calls, ["acp-1"])
        self.assertTrue(connection.stack.closed)
        self.assertIn("error", kinds(events))
        message = "".join(e.text for e in events if e.kind.value == "error")
        self.assertIn("close", message.lower())

        # The un-closable ACP session must not be resumed by a later turn.
        fresh, fresh_acp = self.make_connection()

        async def fake_open(**kwargs):
            fresh.key = kwargs["key"]
            return fresh

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            follow_up = await self.collect("qp-close", self.settings(mcp_server("b")))
        self.assertEqual([c for c in fresh_acp.calls if c[0] == "resume"], [])
        self.assertEqual(fresh_acp.prompt_calls, ["acp-1"])
        self.assertIn("[DSH]", text_of(follow_up))

    async def test_resume_and_new_failure_never_reuse_the_old_session(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        with mock.patch.object(mc.shutil, "which", return_value="/usr/bin/python3"):
            await self.collect("qp-fail", self.settings(mcp_server("a")))
            acp.resume_error = RuntimeError("resume boom")
            acp.new_error = RuntimeError("new boom")
            events = await self.collect("qp-fail", self.settings(mcp_server("b")))

        self.assertEqual(acp.prompt_calls, ["acp-1"])
        self.assertEqual(
            len([c for c in acp.calls if c[0] == "close"]),
            1,
        )
        self.assertEqual(kinds(events).count("error"), 1)
        self.assertEqual(kinds(events).count("completed"), 0)


class SkillsBoundaryTests(LifecycleBase):
    async def test_skills_change_does_not_close_a_connection_mid_turn(self) -> None:
        directory = self.write_skill("snake_case_skill")
        knowledge = self.skill("snake_case_skill", directory, "r1")
        first, first_acp = self.make_connection()

        async def blocking(acp, session_id, prompt):
            await acp.finish_event.wait()
            return SimpleNamespace(stop_reason="end_turn")

        first_acp.prompt_impl = blocking
        second, second_acp = self.make_connection()
        opened: list[dict] = []

        async def fake_open(**kwargs):
            connection = first if not opened else second
            connection.key = kwargs["key"]
            opened.append(kwargs)
            return connection

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            task, events = await self.run_turn_task(
                "qp-skills-A",
                self.settings(skills=[knowledge]),
                "turn-A",
            )
            try:
                await self.started(first_acp)
                changed = self.skill("snake_case_skill", directory, "r2")
                b_events = await self.collect(
                    "qp-skills-B",
                    self.settings(skills=[changed]),
                )
                self.assertFalse(
                    first.stack.closed,
                    "skills refresh closed a connection with an active turn",
                )
                first_acp.finish_event.set()
                self.assertTrue(await self.wait_done(task, 2.0))
                task.result()
            finally:
                first_acp.finish_event.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(kinds(events).count("completed"), 1)
        self.assertEqual(kinds(b_events).count("completed"), 1)
        self.assertEqual(second_acp.prompt_calls, ["acp-1"])
        self.assertTrue(first.stack.closed)
        self.assertTrue(opened)
        self.assertIn("--patch", opened[0]["args"])


def terminals(events) -> list[str]:
    return [kind for kind in kinds(events) if kind in {"completed", "cancelled", "error"}]


class PromptOutcomeTests(LifecycleBase):
    """A prompt whose outcome is unknown must never be reported as success."""

    async def test_independent_prompt_cancellation_is_visible_error(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def cancelled(*args):
            raise asyncio.CancelledError("fake transport closed")

        acp.prompt_impl = cancelled
        events = await self.collect("fake-child-cancel", {}, timeout=2.0)

        self.assertEqual(terminals(events), ["error"])
        self.assertNotIn("completed", kinds(events))
        message = "".join(e.text for e in events if e.kind.value == "error").lower()
        self.assertIn("cancel", message)
        # The ACP turn may still be running inside DSH: fail closed.
        self.assertTrue(connection.stack.closed)
        await asyncio.sleep(0)
        self.assertEqual(
            [task for task in asyncio.all_tasks() if task is not asyncio.current_task()],
            [],
        )

    async def test_prompt_returning_none_is_error_not_completed(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def empty(*args):
            return None

        acp.prompt_impl = empty
        events = await self.collect("qp-none-response", {}, timeout=2.0)

        self.assertEqual(terminals(events), ["error"])
        message = "".join(e.text for e in events if e.kind.value == "error").lower()
        self.assertIn("response", message)

    async def test_prompt_without_stop_reason_is_error_not_completed(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def malformed(*args):
            return SimpleNamespace()

        acp.prompt_impl = malformed
        events = await self.collect("qp-no-stop-reason", {}, timeout=2.0)

        self.assertEqual(terminals(events), ["error"])
        self.assertNotIn("completed", kinds(events))

    async def test_normal_prompt_still_reports_completed(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        events = await self.collect("qp-normal", {}, timeout=2.0)
        self.assertEqual(terminals(events), ["completed"])

    async def test_double_cancellation_still_finishes_cleanly(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        self.adapter.cancel_settle_timeout = 0.2

        async def stubborn_once(*args):
            try:
                await acp.finish_event.wait()
            except asyncio.CancelledError:
                await acp.finish_event.wait()
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = stubborn_once
        task, events = await self.run_turn_task("qp-double-cancel", {}, "turn")
        try:
            await self.started(acp)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            self.assertTrue(await self.wait_done(task, 2.0))
            self.assertTrue(task.cancelled())
        finally:
            acp.finish_event.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(events, [])
        self.assertLessEqual(len(acp.cancel_calls), 2)
        self.assertTrue(connection.stack.closed)

    async def test_cancel_during_session_setup_finishes_cleanly(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)
        entered = asyncio.Event()

        async def slow_new(cwd, mcp_servers=None):
            entered.set()
            await asyncio.Event().wait()

        acp.new_session = slow_new
        task, events = await self.run_turn_task("qp-setup-cancel", {}, "turn")
        try:
            await asyncio.wait_for(entered.wait(), 1.0)
            task.cancel()
            self.assertTrue(await self.wait_done(task, 1.0))
            self.assertTrue(task.cancelled())
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(events, [])


class ResetBoundaryTests(LifecycleBase):
    async def test_reset_waits_for_the_running_turn_before_closing(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def blocking(*args):
            await acp.finish_event.wait()
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = blocking
        task, events = await self.run_turn_task("qp-reset", {}, "turn")
        try:
            await self.started(acp)
            reset = asyncio.create_task(self.adapter.reset_session("qp-reset"))
            await asyncio.sleep(0.05)
            self.assertFalse(reset.done(), "reset returned while a turn was running")
            self.assertEqual(
                [call for call in acp.calls if call[0] == "close"],
                [],
                "reset closed the ACP session underneath a running prompt",
            )
            acp.finish_event.set()
            self.assertTrue(await self.wait_done(task, 2.0))
            task.result()
            self.assertTrue(await self.wait_done(reset, 2.0))
            reset.result()
        finally:
            acp.finish_event.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if not reset.done():
                reset.cancel()
                await asyncio.gather(reset, return_exceptions=True)

        self.assertEqual(kinds(events).count("completed"), 1)
        self.assertEqual([call for call in acp.calls if call[0] == "close"], [("close", "acp-1")])
        # The reset id must not be reused by the next turn.
        await self.collect("qp-reset", {})
        self.assertEqual(acp.prompt_calls, ["acp-1", "acp-2"])
        self.assertEqual([call for call in acp.calls if call[0] == "resume"], [])
        raw = json.loads((self.state / "dsh_sessions.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["qp-reset"]["acpSessionId"], "acp-2")


class ConnectionCloseTests(LifecycleBase):
    async def _two_blocked_turns(self):
        connection, acp = self.make_connection()
        self.install(connection)

        async def blocking(*args):
            await acp.finish_event.wait()
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = blocking
        a_task, a_events = await self.run_turn_task("qp-retire-A", {}, "turn-A")
        b_task, b_events = await self.run_turn_task("qp-retire-B", {}, "turn-B")
        await self.started(acp, 2)
        return connection, acp, a_task, a_events, b_task, b_events

    async def test_retire_wakes_every_active_session_without_fake_completion(self) -> None:
        connection, acp, a_task, a_events, b_task, b_events = await self._two_blocked_turns()
        try:
            await asyncio.wait_for(self.adapter._retire_connection(connection), 2.0)
            self.assertTrue(await self.wait_done(a_task, 1.0), "A consumer hung on retire")
            self.assertTrue(await self.wait_done(b_task, 1.0), "B consumer hung on retire")
            a_task.result()
            b_task.result()
        finally:
            acp.finish_event.set()
            for task in (a_task, b_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        for label, events in (("A", a_events), ("B", b_events)):
            self.assertNotIn("completed", kinds(events), f"{label} faked success on retire")
            self.assertEqual(terminals(events), ["error"], f"{label} lacks one terminal error")
        self.assertTrue(connection.stack.closed)

    async def test_stop_wakes_active_sessions_and_closes_queues(self) -> None:
        connection, acp, a_task, a_events, b_task, b_events = await self._two_blocked_turns()
        try:
            await asyncio.wait_for(self.adapter.stop(), 2.0)
            self.assertTrue(await self.wait_done(a_task, 1.0), "A consumer hung on stop")
            self.assertTrue(await self.wait_done(b_task, 1.0), "B consumer hung on stop")
            a_task.result()
            b_task.result()
        finally:
            acp.finish_event.set()
            for task in (a_task, b_task):
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        for events in (a_events, b_events):
            self.assertNotIn("completed", kinds(events))
            self.assertEqual(terminals(events), ["error"])
        self.assertEqual(connection.client._turn_queues, {})
        self.assertEqual(connection.client._approval_contexts, {})


# ---------------------------------------------------------------------------
# G2c: cleanup ownership. Every scenario below is offline: fake stacks,
# fake processes, fake spawn, fake ACP. No dsh, no profile, no credentials.
# ---------------------------------------------------------------------------


class HangingStack:
    """Stack whose ``aclose`` never finishes on its own (swallows one cancel)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def aclose(self) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
        self.closed = True


class StuckInitialize:
    """Fake ACP connection whose ``initialize`` never answers."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def initialize(self, **kwargs):
        self.entered.set()
        await asyncio.Event().wait()


class FakeSpawn:
    """Stand-in for ``acp.spawn_agent_process`` (async context manager)."""

    def __init__(self, conn, process) -> None:
        self.conn = conn
        self.process = process
        self.entered = asyncio.Event()
        self.exit_calls = 0
        self.exited = asyncio.Event()
        self.release = asyncio.Event()

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        self.entered.set()
        return self.conn, self.process

    async def __aexit__(self, *exc_info):
        self.exit_calls += 1
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            # Deliberately swallows one cancellation, then keeps waiting: a
            # close like this is why ``wait_for`` is not an upper bound.
            await self.release.wait()
        self.exited.set()
        return False


class FailingCloseStack:
    """Stack whose ``aclose`` always ends in the given way."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        raise self.error


class FailingExitSpawn(FakeSpawn):
    """Spawn context whose ``__aexit__`` always ends in the given way."""

    def __init__(self, conn, process, error: BaseException) -> None:
        super().__init__(conn, process)
        self.error = error

    async def __aexit__(self, *exc_info):
        self.exit_calls += 1
        raise self.error


class FailingInitialize:
    """Fake ACP connection whose ``initialize`` always fails."""

    async def initialize(self, **kwargs):
        raise RuntimeError("initialize failed")


class CleanupOwnershipTests(LifecycleBase):
    """A cancellation must never leave an unreusable connection reachable."""

    async def _double_cancel_during_notification(self, session: str):
        connection, acp = self.make_connection()
        self.install(connection)
        notify_started = asyncio.Event()

        async def never_settles(*args):
            await asyncio.Event().wait()

        async def hanging_cancel(session_id):
            acp.cancel_calls.append(session_id)
            notify_started.set()
            await asyncio.Event().wait()

        acp.prompt_impl = never_settles
        acp.cancel = hanging_cancel
        task, events = await self.run_turn_task(session, {}, "turn")
        await self.started(acp)
        task.cancel()
        await asyncio.wait_for(notify_started.wait(), 2.0)
        # Second cancellation arrives while the cancel notification is in
        # flight: the cleanup coroutine dies inside its first await.
        task.cancel()
        self.assertTrue(
            await self.wait_done(task, 3.0),
            "consumer stuck after the second cancellation",
        )
        return connection, acp, task, events

    async def test_second_cancel_during_notification_quarantines_connection(self) -> None:
        connection, acp, task, events = await self._double_cancel_during_notification("qp-c1")
        self.assertTrue(task.cancelled())
        self.assertEqual(events, [])
        self.assertEqual(acp.cancel_calls, ["acp-1"], "cancel notification sent more than once")
        # The connection must be non-reusable and its mapping gone, even though
        # the cleanup coroutine was cancelled before it could await a close.
        self.assertTrue(connection.retiring, "connection still reusable after a second cancel")
        self.assertNotIn(connection.key, self.adapter._connections, "connection left in the pool")
        self.assertNotIn("qp-c1", self.adapter._live, "stale ACP session mapping survived")
        self.assertTrue(connection.retired, "owned close never ran")
        self.assertTrue(connection.stack.closed)
        self.assertEqual(self.adapter._detached, {}, "detached connection never reclaimed")
        pending = [
            item for item in asyncio.all_tasks()
            if item is not asyncio.current_task()
        ]
        self.assertEqual(pending, [], "cleanup task leaked")

    async def test_quarantined_connection_never_serves_the_next_turn(self) -> None:
        connection, acp, _task, _events = await self._double_cancel_during_notification(
            "qp-c1-next"
        )
        fresh, fresh_acp = self.make_connection()
        fresh_acp.counter = 100
        fresh_acp.resume_error = RuntimeError("unknown session on the new process")

        async def quick(_acp, session_id, prompt):
            # A late frame from the abandoned prompt, routed through the *old*
            # connection, must never reach the next turn.
            await connection.client.session_update("acp-1", chunk("LATE-OLD"))
            await fresh.client.session_update(session_id, chunk("NEW"))
            return SimpleNamespace(stop_reason="end_turn")

        acp.prompt_impl = quick
        fresh_acp.prompt_impl = quick

        async def fake_open(**kwargs):
            return fresh

        with mock.patch.object(DshConnection, "open", side_effect=fake_open) as opened:
            events2 = await self.collect("qp-c1-next", {}, timeout=3.0)

        self.assertTrue(opened.called, "the quarantined connection was reused instead of replaced")
        self.assertEqual(acp.prompt_calls, ["acp-1"], "old connection was prompted again")
        self.assertEqual(fresh_acp.prompt_calls, ["acp-101"])
        self.assertNotIn("LATE-OLD", text_of(events2), "late frame crossed into the next turn")
        self.assertIn("NEW", text_of(events2))
        self.assertEqual(terminals(events2), ["completed"])

    async def test_single_cooperative_cancel_keeps_connection_reusable(self) -> None:
        connection, acp = self.make_connection()
        self.install(connection)

        async def cooperative(_acp, session_id, prompt):
            await race(_acp.finish_event, _acp.cancel_event)
            return SimpleNamespace(stop_reason="cancelled")

        acp.prompt_impl = cooperative
        task, events = await self.run_turn_task("qp-reuse", {}, "turn")
        await self.started(acp)
        task.cancel()
        self.assertTrue(await self.wait_done(task, 2.0))
        self.assertTrue(task.cancelled())

        self.assertEqual(events, [])
        self.assertEqual(acp.cancel_calls, ["acp-1"])
        self.assertFalse(connection.retiring, "a settled cancel must stay reusable")
        self.assertIn(connection.key, self.adapter._connections)
        acp.prompt_impl = None  # next turn is an ordinary one
        events2 = await self.collect("qp-reuse", {})
        self.assertEqual(acp.prompt_calls, ["acp-1", "acp-1"])
        self.assertEqual(terminals(events2), ["completed"])


class ReplacementCleanupTests(LifecycleBase):
    """Recycled connections must stay owned while a replacement spawns."""

    def _skills_settings(self):
        directory = self.write_skill("alpha")
        return self.settings(skills=[self.skill("alpha", directory, "r1")])

    async def drain_closes(self) -> None:
        """Let every owned close task finish (they are bounded)."""
        for _ in range(50):
            pending = [
                task
                for task in getattr(self.adapter, "_closing", {}).values()
                if not task.done()
            ]
            if not pending:
                await asyncio.sleep(0)
                return
            await asyncio.wait(pending, timeout=1.0)
            await asyncio.sleep(0)

    async def test_failed_replacement_still_closes_recycled_connection(self) -> None:
        old, _old_acp = self.make_connection()
        self.install(old)

        with mock.patch.object(
            DshConnection,
            "open",
            side_effect=RuntimeError("spawn failed"),
        ):
            events = await self.collect("qp-c2", self._skills_settings())

        await self.drain_closes()
        self.assertEqual(terminals(events), ["error"])
        self.assertTrue(old.stack.closed, "the recycled connection leaked")
        self.assertTrue(old.retired)
        self.assertNotIn(old.key, self.adapter._connections)
        self.assertEqual(self.adapter._detached, {})
        pending = [
            item for item in asyncio.all_tasks()
            if item is not asyncio.current_task()
        ]
        self.assertEqual(pending, [], "cleanup task leaked")

    async def test_cancelled_replacement_still_closes_recycled_connection(self) -> None:
        old, _old_acp = self.make_connection()
        self.install(old)
        entered = asyncio.Event()
        hang = asyncio.Event()

        async def slow_open(**kwargs):
            entered.set()
            await hang.wait()
            raise RuntimeError("never reached")

        with mock.patch.object(DshConnection, "open", side_effect=slow_open):
            task, events = await self.run_turn_task(
                "qp-c2-cancel",
                self._skills_settings(),
                "turn",
            )
            try:
                await asyncio.wait_for(entered.wait(), 2.0)
                task.cancel()
                self.assertTrue(await self.wait_done(task, 2.0))
                self.assertTrue(task.cancelled())
            finally:
                hang.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        await self.drain_closes()
        self.assertEqual(events, [])
        self.assertTrue(old.stack.closed, "cancelled replacement leaked the recycled connection")
        self.assertNotIn(old.key, self.adapter._connections)
        self.assertEqual(self.adapter._detached, {})

    async def test_one_stuck_close_does_not_strand_the_others(self) -> None:
        self.adapter.retire_timeout = 0.1
        first, _ = self.make_connection(key=("fake", "first"))
        second, _ = self.make_connection(key=("fake", "second"))
        stuck = HangingStack()
        second.stack = stuck
        self.adapter._connections[first.key] = first
        self.adapter._connections[second.key] = second

        try:
            with mock.patch.object(
                DshConnection,
                "open",
                side_effect=RuntimeError("spawn failed"),
            ):
                events = await self.collect("qp-c2-many", self._skills_settings())

            await self.drain_closes()
            self.assertEqual(terminals(events), ["error"])
            self.assertTrue(first.stack.closed, "a stuck sibling close stranded the others")
            # The close that timed out is still owned (not silently dropped)
            # and the process kill is the documented best-effort fallback.
            self.assertIn(id(second), self.adapter._detached)
            self.assertTrue(
                any("did not confirm" in message for message in self._messages),
                f"no honest timeout report: {self._messages}",
            )
            self.assertFalse(stuck.closed, "reported a stuck stack as closed")
            self.assertTrue(acp_client_module.kill_process_tree.called)
            await asyncio.wait_for(self.adapter.stop(), 2.0)
            self.assertTrue(
                any("could not be confirmed closed" in message for message in self._messages),
                f"stop hid an unconfirmed close: {self._messages}",
            )
            self.assertEqual(self.adapter._detached, {}, "stop could not reclaim the leftover")
        finally:
            stuck.release.set()
            await asyncio.sleep(0)


class FailingClose:
    """Minimal connection whose ``aclose`` always ends in the given way.

    ``DshConnection.aclose`` is deliberately not used here: this isolates the
    adapter's close bookkeeping (``_close_bounded``) from the connection's own
    best-effort kill, so a kill call can only come from the adapter.
    """

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.process = FakeProcess()
        self.retiring = False
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1
        raise self.error


class CloseConfirmationTests(LifecycleBase):
    """A close task that raises or is cancelled is *not* a confirmed close.

    ``task.done()`` only says the attempt ended. Only a normal return (no
    exception) may confirm closure; otherwise the connection stays owned and
    unconfirmed so ``stop()`` can process it again, and the process tree is
    killed best effort (never reported as "terminated").
    """

    async def assert_failed_close_is_owned(
        self,
        error: BaseException,
        *,
        expected_message: str,
    ) -> None:
        connection = FailingClose(error)
        with mock.patch.object(adapter_module, "kill_process_tree") as killer:
            self.adapter._own_detached([connection])
            task = self.adapter._closing[id(connection)]
            await asyncio.wait({task}, timeout=1.0)
            await asyncio.sleep(0)

            self.assertTrue(task.done(), "owned close task never finished")
            self.assertIs(
                task.result(),
                False,
                "a close that raised/was cancelled was reported as confirmed",
            )
            self.assertIn(
                id(connection),
                self.adapter._detached,
                "failed close lost its owner",
            )
            self.assertIn(
                id(connection),
                self.adapter._unconfirmed,
                "failed close was not marked unconfirmed",
            )
            self.assertEqual(killer.call_count, 1, "no best-effort process kill")
            self.assertTrue(
                any(expected_message in message for message in self._messages),
                f"no honest failed-close report: {self._messages}",
            )
            self.assertFalse(
                any("may still be running" in message for message in self._messages),
                f"a finished close task was reported as running: {self._messages}",
            )

            # ``stop()`` must still own and re-process the unconfirmed close.
            await asyncio.wait_for(self.adapter.stop(), 2.0)
            self.assertEqual(
                connection.close_calls,
                2,
                "stop did not retry the unconfirmed close",
            )
            self.assertEqual(killer.call_count, 2, "stop did not retry the kill")
            self.assertEqual(
                self.adapter._detached,
                {},
                "stop could not reclaim the unconfirmed leftover",
            )
            self.assertTrue(
                any("could not be confirmed closed" in message for message in self._messages),
                f"stop hid an unconfirmed close: {self._messages}",
            )

    async def test_close_exception_is_not_reported_as_confirmed(self) -> None:
        await self.assert_failed_close_is_owned(
            RuntimeError("fake close failure"),
            expected_message="failed before confirming closure",
        )

    async def test_close_cancellation_is_not_reported_as_confirmed(self) -> None:
        await self.assert_failed_close_is_owned(
            asyncio.CancelledError(),
            expected_message="cancelled before confirming closure",
        )


class SpawnSetupCleanupTests(LifecycleBase):
    """``DshConnection.open`` must bound cleanup of a half-open spawn."""

    async def _drain_setup_tasks(self) -> None:
        tasks = list(getattr(acp_client_module, "_FAILED_SETUP_TASKS", set()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        getattr(acp_client_module, "_FAILED_SETUP_TASKS", set()).clear()

    async def test_setup_cancel_bounds_a_stuck_stack_close(self) -> None:
        conn = StuckInitialize()
        spawn = FakeSpawn(conn, FakeProcess())
        with mock.patch.object(acp_client_module, "spawn_agent_process", spawn), mock.patch.object(
            acp_client_module, "_SETUP_CLOSE_TIMEOUT", 0.1, create=True
        ):
            task = asyncio.create_task(
                DshConnection.open(
                    binary="dsh",
                    args=[],
                    env={},
                    cwd=str(self.cwd),
                    key=("fake",),
                )
            )
            try:
                await asyncio.wait_for(conn.entered.wait(), 2.0)
                task.cancel()
                self.assertTrue(
                    await self.wait_done(task, 2.0),
                    "setup cleanup hung on a stack close that swallows cancellation",
                )
                self.assertTrue(task.cancelled())
                self.assertEqual(spawn.exit_calls, 1)
                self.assertTrue(acp_client_module.kill_process_tree.called)
                self.assertTrue(
                    getattr(acp_client_module, "_FAILED_SETUP_TASKS", set()),
                    "close task was dropped",
                )
                self.assertFalse(spawn.exited.is_set(), "claimed a stuck stack was closed")
            finally:
                spawn.release.set()
                await self._drain_setup_tasks()

    async def test_second_cancel_during_setup_cleanup_still_owns_the_close(self) -> None:
        conn = StuckInitialize()
        spawn = FakeSpawn(conn, FakeProcess())
        with mock.patch.object(acp_client_module, "spawn_agent_process", spawn), mock.patch.object(
            acp_client_module, "_SETUP_CLOSE_TIMEOUT", 0.3, create=True
        ):
            task = asyncio.create_task(
                DshConnection.open(
                    binary="dsh",
                    args=[],
                    env={},
                    cwd=str(self.cwd),
                    key=("fake",),
                )
            )
            try:
                await asyncio.wait_for(conn.entered.wait(), 2.0)
                task.cancel()
                for _ in range(300):
                    if spawn.exit_calls:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(spawn.exit_calls, 1, "cleanup never started")
                task.cancel()
                self.assertTrue(await self.wait_done(task, 2.0))
                self.assertTrue(task.cancelled())
                self.assertTrue(
                    acp_client_module.kill_process_tree.called,
                    "second cancellation bypassed the process kill",
                )
                self.assertTrue(
                    getattr(acp_client_module, "_FAILED_SETUP_TASKS", set()),
                    "second cancellation dropped the close task",
                )
            finally:
                spawn.release.set()
                await self._drain_setup_tasks()

    async def _bounded_close_outcome(self, stack) -> BaseException | bool:
        """Run ``_bounded_stack_close`` in its own task and return its outcome."""
        task = asyncio.create_task(
            acp_client_module._bounded_stack_close(stack, FakeProcess())
        )
        results = await asyncio.gather(task, return_exceptions=True)
        return results[0]

    async def _open_outcome(self, spawn) -> BaseException | None:
        """Run ``DshConnection.open`` and return what it raised/returned."""
        with mock.patch.object(acp_client_module, "spawn_agent_process", spawn):
            task = asyncio.create_task(
                DshConnection.open(
                    binary="dsh",
                    args=[],
                    env={},
                    cwd=str(self.cwd),
                    key=("fake",),
                )
            )
            await asyncio.wait({task}, timeout=2.0)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.fail("DshConnection.open did not finish after setup cleanup")
            results = await asyncio.gather(task, return_exceptions=True)
        return results[0]

    async def test_setup_close_exception_is_not_confirmed(self) -> None:
        stack = FailingCloseStack(RuntimeError("fake stack close failure"))
        with mock.patch.object(acp_client_module, "kill_process_tree") as killer:
            result = await self._bounded_close_outcome(stack)
        self.assertIs(result, False, "a raising stack close was reported as confirmed")
        self.assertEqual(stack.close_calls, 1)
        self.assertEqual(killer.call_count, 1, "no best-effort process kill")
        self.assertTrue(
            any("failed before confirming closure" in message for message in self._messages),
            f"no honest failed-close report: {self._messages}",
        )
        self.assertFalse(
            any("may still be running" in message for message in self._messages),
            f"a finished close task was reported as running: {self._messages}",
        )

    async def test_setup_close_cancellation_is_not_confirmed(self) -> None:
        stack = FailingCloseStack(asyncio.CancelledError())
        with mock.patch.object(acp_client_module, "kill_process_tree") as killer:
            result = await self._bounded_close_outcome(stack)
        self.assertIs(result, False, "a cancelled stack close was reported as confirmed")
        self.assertEqual(stack.close_calls, 1)
        self.assertEqual(killer.call_count, 1, "no best-effort process kill")
        self.assertTrue(
            any("cancelled before confirming closure" in message for message in self._messages),
            f"no honest cancelled-close report: {self._messages}",
        )
        self.assertFalse(
            any("may still be running" in message for message in self._messages),
            f"a finished close task was reported as running: {self._messages}",
        )

    async def test_setup_close_failure_keeps_original_error(self) -> None:
        spawn = FailingExitSpawn(
            FailingInitialize(),
            FakeProcess(),
            RuntimeError("fake stack close failure"),
        )
        with mock.patch.object(acp_client_module, "kill_process_tree") as killer:
            result = await self._open_outcome(spawn)
        self.assertEqual(spawn.exit_calls, 1)
        self.assertIsInstance(result, RuntimeError, "cleanup replaced the setup error")
        self.assertEqual(str(result), "initialize failed")
        self.assertEqual(killer.call_count, 1, "cleanup did not kill the child process")

    async def test_setup_cancelled_close_keeps_original_error(self) -> None:
        spawn = FailingExitSpawn(
            FailingInitialize(),
            FakeProcess(),
            asyncio.CancelledError(),
        )
        with mock.patch.object(acp_client_module, "kill_process_tree") as killer:
            result = await self._open_outcome(spawn)
        self.assertEqual(spawn.exit_calls, 1)
        self.assertIsInstance(
            result,
            RuntimeError,
            "a cancelled close task replaced the setup error",
        )
        self.assertEqual(str(result), "initialize failed")
        self.assertEqual(killer.call_count, 1, "cleanup did not kill the child process")


if __name__ == "__main__":
    unittest.main(verbosity=2)
