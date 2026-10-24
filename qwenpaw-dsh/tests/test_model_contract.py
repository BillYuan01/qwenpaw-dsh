#!/usr/bin/env python3
"""G3 model-contract regressions: explicit model / reasoning_effort (R07).

Offline only: a fake ACP connection is driven through the real
``DshConnection`` / ``DshTurnClient`` / ``DshAdapter`` code. No dsh process, no
profile, no credentials, no model call, no live catalog probe.

What is pinned here
-------------------
* an explicit ``model`` is resolved from a *real* catalog source (this session's
  ``session/new``/``resume`` config options first, then the catalog cache /
  one single-flight discovery) — never from a hand-built ``["provider","model"]``;
* ``model`` is applied before ``reasoning_effort``, and each write is confirmed
  from the response ``configOptions.currentValue``; unconfirmed -> visible ERROR
  and *no prompt*;
* effort values are only accepted when advertised for the actual model, never
  from the union of all models; ``off``/``false`` are ordinary strings;
* missing/expired cache triggers exactly one discovery; cache hits do not probe;
* no explicit selection leaves the profile default untouched.

Run: ~/.qwenpaw/venv/bin/python tests/test_model_contract.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core import acp_client as acp_client_module  # noqa: E402
from qwenpaw_dsh.core import adapter as adapter_module  # noqa: E402
from qwenpaw_dsh.core.acp_client import DshConnection, DshTurnClient  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402

_DEFAULT = object()

MODEL = '["p1","m1"]'
MODEL_ALT = '["p1","m2"]'
MODEL_OTHER_PROVIDER = '["p2","m1"]'


# ---------------------------------------------------------------- fixtures
def selection_value(provider: str, model: str) -> str:
    """Compact JSON exactly like dsh-acp's ``modelValue()``."""
    return json.dumps([provider, model], separators=(",", ":"))


def entry(
    provider: str,
    model: str,
    *,
    name: str | None = None,
    provider_name: str | None = None,
    efforts=(),
    selection=_DEFAULT,
):
    return {
        "provider": provider,
        "id": model,
        "name": name or model,
        "provider_name": provider_name or provider.upper(),
        "efforts": list(efforts),
        "selection": (
            selection_value(provider, model) if selection is _DEFAULT else selection
        ),
    }


DEFAULT_ENTRIES = [
    entry("p1", "m1", efforts=("low", "high")),
    entry("p1", "m2", efforts=("low", "high")),
    entry("p2", "m1", efforts=("high",)),
]


def selection_of(item: dict) -> str | None:
    return item["selection"]


def model_options(entries, current=("p1", "m1"), effort: str = "") -> list[dict]:
    """ACP ``configOptions`` shaped exactly like dsh-acp's model control."""
    groups: list[dict] = []
    by_provider: dict[str, dict] = {}
    for item in entries:
        value = selection_of(item)
        if not value:
            continue
        group = by_provider.get(item["provider"])
        if group is None:
            group = {
                "group": item["provider"],
                "name": item["provider_name"],
                "options": [],
            }
            by_provider[item["provider"]] = group
            groups.append(group)
        group["options"].append(
            {
                "value": value,
                "name": item["name"],
                "description": "",
            },
        )
    options: list[dict] = [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": selection_value(*current),
            "options": groups,
        },
    ]
    current_entry = next(
        (
            item
            for item in entries
            if (item["provider"], item["id"]) == tuple(current)
        ),
        None,
    )
    if current_entry is not None and current_entry["efforts"]:
        options.append(
            {
                "id": "reasoning_effort",
                "name": "Reasoning effort",
                "category": "thought_level",
                "type": "select",
                "currentValue": effort,
                "options": [
                    {"value": "", "name": "Provider default"},
                    *[
                        {"value": effort_id, "name": effort_id}
                        for effort_id in current_entry["efforts"]
                    ],
                ],
            },
        )
    return options


def bridge_meta(entries, current=("p1", "m1")) -> dict:
    """``_meta.qwenpaw`` payload as the bridge advertises it."""
    models = []
    for item in entries:
        payload = {
            "provider": item["provider"],
            "id": item["id"],
            "name": item["name"],
            "provider_name": item["provider_name"],
            "reasoning_efforts": [
                {"id": effort_id, "name": effort_id}
                for effort_id in item["efforts"]
            ],
        }
        if item["selection"]:
            payload["selection_value"] = item["selection"]
        models.append(payload)
    return {
        "qwenpaw": {
            "models": models,
            "current_model": {
                "provider": current[0],
                "model": current[1],
            },
        },
    }


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
    """Scriptable ACP connection with DSH-like config-option semantics."""

    def __init__(self, entries=None) -> None:
        self.entries = list(DEFAULT_ENTRIES if entries is None else entries)
        self.calls: list[tuple] = []
        self.set_calls: list[dict] = []
        self.prompt_calls: list[str] = []
        self.closed: list[str] = []
        self.options_by_session: dict[str, list] = {}
        self.counter = 0
        self.turn_options_mode: object = "full"  # "full" | "none" | list
        self.probe_meta_mode = "meta"  # "meta" | "none"
        self.probe_gate: asyncio.Event | None = None
        self.set_gate: asyncio.Event | None = None
        self.set_impl = None
        self.prompt_impl = None
        self._current: dict[str, tuple[str, str]] = {}

    # -- session lifecycle -------------------------------------------
    def _next_id(self) -> str:
        self.counter += 1
        return f"acp-{self.counter}"

    async def new_session(self, cwd, mcp_servers=None):
        self.calls.append(("new", str(cwd)))
        is_probe = Path(str(cwd)) == Path.home()
        if is_probe and self.probe_gate is not None:
            await self.probe_gate.wait()
        session_id = self._next_id()
        if is_probe:
            if self.probe_meta_mode == "meta":
                response = SimpleNamespace(
                    session_id=session_id,
                    config_options=model_options(self.entries),
                    field_meta=bridge_meta(self.entries),
                )
            else:
                response = SimpleNamespace(
                    session_id=session_id,
                    config_options=[],
                )
        else:
            if self.turn_options_mode == "full":
                options = model_options(self.entries)
            elif self.turn_options_mode == "none":
                options = []
            else:
                options = copy.deepcopy(self.turn_options_mode)
            response = SimpleNamespace(
                session_id=session_id,
                config_options=options,
            )
        self.options_by_session[session_id] = copy.deepcopy(
            list(response.config_options),
        )
        self._current[session_id] = ("p1", "m1")
        return response

    async def resume_session(self, cwd, session_id, mcp_servers=None):
        self.calls.append(("resume", str(cwd), session_id))
        options = model_options(self.entries)
        self.options_by_session[session_id] = copy.deepcopy(options)
        self._current[session_id] = ("p1", "m1")
        return SimpleNamespace(session_id=session_id, config_options=options)

    async def close_session(self, session_id):
        self.calls.append(("close", session_id))
        self.closed.append(session_id)
        self.options_by_session.pop(session_id, None)
        return {}

    async def cancel(self, session_id):
        self.calls.append(("cancel", session_id))

    # -- config options ----------------------------------------------
    def _apply(self, session_id: str, config_id: str, value: str) -> list[dict]:
        current = self._current.get(session_id, ("p1", "m1"))
        effort = ""
        if config_id == "model":
            parsed = json.loads(value)
            current = (str(parsed[0]), str(parsed[1]))
        else:
            effort = str(value)
        options = model_options(self.entries, current=current, effort=effort)
        self._current[session_id] = current
        self.options_by_session[session_id] = copy.deepcopy(options)
        return options

    async def set_config_option(self, config_id, session_id, value):
        self.set_calls.append(
            {"config": config_id, "session": session_id, "value": value},
        )
        if self.set_gate is not None:
            await self.set_gate.wait()
        if self.set_impl is not None:
            return await self.set_impl(self, config_id, session_id, value)
        return SimpleNamespace(
            config_options=self._apply(session_id, config_id, value),
        )

    # -- prompt -------------------------------------------------------
    async def prompt(self, prompt, session_id):
        self.prompt_calls.append(session_id)
        if self.prompt_impl is not None:
            return await self.prompt_impl(self, session_id, prompt)
        return SimpleNamespace(stop_reason="end_turn")


def kinds(events) -> list[str]:
    return [event.kind.value for event in events]


def text_of(events) -> str:
    return "".join(event.text for event in events)


class ModelContractBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "harnesses"
        self.cwd = root / "ws"
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.adapter = DshAdapter(
            state_dir=self.state,
            settings={"profile": "acp"},
            host=HostContext(state_dir=self.state),
        )
        self._kill = mock.patch.object(acp_client_module, "kill_process_tree")
        self._kill.start()
        self._logs = logging.Handler()
        self.messages: list[str] = []
        self._logs.emit = lambda record: self.messages.append(record.getMessage())
        logging.getLogger("qwenpaw.dsh").addHandler(self._logs)
        logging.getLogger("qwenpaw.dsh").setLevel(logging.DEBUG)

    async def asyncTearDown(self) -> None:
        try:
            await self.adapter.stop()
        finally:
            logging.getLogger("qwenpaw.dsh").removeHandler(self._logs)
            self._kill.stop()
            self.tmp.cleanup()

    # -- wiring -------------------------------------------------------
    def install(self, acp: FakeACP) -> DshConnection:
        client = DshTurnClient(cwd=str(self.cwd), trusted=False, approval=None)
        connection = DshConnection(
            stack=FakeStack(),
            conn=acp,
            process=FakeProcess(),
            client=client,
            key=self.adapter._pool_key(),
            prompt_capabilities=SimpleNamespace(image=False),
        )
        self.adapter._connections[connection.key] = connection
        return connection

    def probe_count(self, acp: FakeACP) -> int:
        home = Path.home()
        return sum(
            1
            for call in acp.calls
            if call[0] == "new" and Path(call[1]) == home
        )

    async def turn(self, session_id: str, settings: dict, *, timeout=3.0):
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

        task = asyncio.create_task(run())
        await asyncio.wait({task}, timeout=timeout)
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=0.5)
            self.fail(f"turn {session_id} did not finish within {timeout}s")
        task.result()
        return events

    async def wait_for(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition was not reached in time")

    def stale_catalog(self, *, value: str, age: float):
        catalog_cls = getattr(adapter_module, "ModelCatalog", None)
        self.assertIsNotNone(catalog_cls, "ModelCatalog is missing")
        return catalog_cls(
            fetched_at=time.time() - age,
            models=[
                adapter_module.HarnessModel(
                    id="p1/m2",
                    name="stale m2",
                    reasoning_efforts=["low"],
                ),
            ],
            value_map={"p1/m2": value},
        )


class ModelResolutionTests(ModelContractBase):
    async def test_missing_cache_probes_once_then_applies_model_before_effort(
        self,
    ) -> None:
        acp = FakeACP()
        acp.turn_options_mode = "none"  # no usable session catalog -> discover
        self.install(acp)

        events = await self.turn(
            "qp-probe",
            {"model": "p1/m2", "reasoning_effort": "high"},
        )

        self.assertEqual(self.probe_count(acp), 1)
        self.assertEqual(
            [(item["config"], item["value"]) for item in acp.set_calls],
            [("model", '["p1","m2"]'), ("reasoning_effort", "high")],
        )
        self.assertEqual(acp.set_calls[0]["session"], acp.set_calls[1]["session"])
        self.assertEqual(acp.prompt_calls, [acp.set_calls[0]["session"]])
        self.assertEqual(kinds(events)[-1], "completed")
        self.assertNotIn("error", kinds(events))

    async def test_session_response_catalog_avoids_probe(self) -> None:
        acp = FakeACP()
        self.install(acp)

        events = await self.turn("qp-session", {"model": "p2/m1"})

        self.assertEqual(self.probe_count(acp), 0)
        self.assertEqual(
            [(item["config"], item["value"]) for item in acp.set_calls],
            [("model", '["p2","m1"]')],
        )
        self.assertEqual(len(acp.prompt_calls), 1)
        self.assertEqual(kinds(events).count("error"), 0)

    async def test_cache_hit_does_not_probe_again(self) -> None:
        acp = FakeACP()
        self.install(acp)
        await self.adapter.models()
        self.assertEqual(self.probe_count(acp), 1)

        acp.turn_options_mode = "none"  # force catalog use in the turn
        await self.turn(
            "qp-cache",
            {"model": "p1/m2", "reasoning_effort": "low"},
        )

        self.assertEqual(self.probe_count(acp), 1)
        self.assertEqual(
            [(item["config"], item["value"]) for item in acp.set_calls],
            [("model", '["p1","m2"]'), ("reasoning_effort", "low")],
        )

    async def test_expired_cache_is_refreshed_and_old_value_is_not_used(self) -> None:
        acp = FakeACP()
        acp.turn_options_mode = "none"
        self.install(acp)
        self.adapter._models_cache = self.stale_catalog(
            value='["p1","m2","stale"]',
            age=adapter_module._MODELS_TTL_SECONDS + 1,
        )

        await self.turn("qp-expired", {"model": "p1/m2"})

        self.assertEqual(self.probe_count(acp), 1)
        self.assertEqual(
            [item["value"] for item in acp.set_calls],
            ['["p1","m2"]'],
        )

    async def test_unknown_model_is_an_error_without_prompt(self) -> None:
        acp = FakeACP()
        acp.turn_options_mode = "none"
        self.install(acp)

        events = await self.turn("qp-unknown", {"model": "p1/ghost"})

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("unknown model", text_of(events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])

    async def test_unknown_provider_is_an_error_without_prompt(self) -> None:
        acp = FakeACP()
        acp.turn_options_mode = "none"
        self.install(acp)

        events = await self.turn("qp-provider", {"model": "p9/m1"})

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("p9/m1", text_of(events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])

    async def test_same_bare_name_across_providers_is_ambiguous(self) -> None:
        acp = FakeACP()
        self.install(acp)

        events = await self.turn("qp-ambiguous", {"model": "m1"})

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("ambiguous", text_of(events))
        self.assertIn("p1/m1", text_of(events))
        self.assertIn("p2/m1", text_of(events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])

        # Same rule on the catalog path (no usable session options): the
        # discovery must not merge two providers that share a bare name.
        acp.turn_options_mode = "none"
        catalog_events = await self.turn(
            "qp-ambiguous-catalog",
            {"model": "m1"},
        )
        self.assertEqual(kinds(catalog_events), ["error"])
        self.assertIn("ambiguous", text_of(catalog_events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])

    async def test_provider_identity_is_preserved(self) -> None:
        acp = FakeACP()
        self.install(acp)

        await self.turn("qp-identity", {"model": "p2/m1"})

        self.assertEqual(
            [item["value"] for item in acp.set_calls],
            ['["p2","m1"]'],
        )
        self.assertEqual(acp.prompt_calls, ["acp-1"])

    async def test_missing_selection_value_is_not_synthesized(self) -> None:
        entries = [
            entry("p1", "m1", efforts=("low",)),
            entry("p1", "m9", selection=None),
        ]
        acp = FakeACP(entries)
        acp.turn_options_mode = "none"
        self.install(acp)

        events = await self.turn("qp-noselect", {"model": "p1/m9"})

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("selection", text_of(events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])

    async def test_no_explicit_selection_keeps_profile_default(self) -> None:
        acp = FakeACP()
        self.install(acp)

        events = await self.turn("qp-default", {})
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(len(acp.prompt_calls), 1)
        self.assertEqual(kinds(events)[-1], "completed")

        blank = await self.turn(
            "qp-blank",
            {"model": "", "reasoning_effort": ""},
        )
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(len(acp.prompt_calls), 2)
        self.assertEqual(kinds(blank)[-1], "completed")


class EffortContractTests(ModelContractBase):
    async def test_invalid_effort_is_an_error_after_model_and_no_prompt(self) -> None:
        acp = FakeACP()
        self.install(acp)

        events = await self.turn(
            "qp-effort-invalid",
            {"model": "p1/m2", "reasoning_effort": "ultra"},
        )

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("ultra", text_of(events))
        self.assertEqual(
            [item["config"] for item in acp.set_calls],
            ["model"],
        )
        self.assertEqual(acp.prompt_calls, [])

    async def test_off_is_an_ordinary_string_not_false(self) -> None:
        acp = FakeACP()
        self.install(acp)

        events = await self.turn(
            "qp-effort-off",
            {"reasoning_effort": "off"},
        )

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("off", text_of(events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])

    async def test_non_string_effort_is_rejected(self) -> None:
        acp = FakeACP()
        self.install(acp)

        events = await self.turn(
            "qp-effort-bool",
            {"model": "p1/m1", "reasoning_effort": True},
        )

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("must be a string", text_of(events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])

    async def test_effort_only_uses_the_actual_current_model(self) -> None:
        acp = FakeACP()
        self.install(acp)

        events = await self.turn(
            "qp-effort-only",
            {"reasoning_effort": "high"},
        )

        self.assertEqual(
            [(item["config"], item["value"]) for item in acp.set_calls],
            [("reasoning_effort", "high")],
        )
        self.assertEqual(len(acp.prompt_calls), 1)
        self.assertEqual(kinds(events)[-1], "completed")

    async def test_effort_only_with_unknown_current_model_is_an_error(self) -> None:
        acp = FakeACP()
        acp.turn_options_mode = "none"
        self.install(acp)

        events = await self.turn(
            "qp-effort-unknown",
            {"reasoning_effort": "high"},
        )

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("current model", text_of(events))
        self.assertEqual(acp.set_calls, [])
        self.assertEqual(acp.prompt_calls, [])


class ConfirmationTests(ModelContractBase):
    async def test_current_value_mismatch_is_an_error_without_prompt(self) -> None:
        acp = FakeACP()

        async def ignore_change(fake, config_id, session_id, value):
            return SimpleNamespace(
                config_options=model_options(fake.entries),
            )

        acp.set_impl = ignore_change
        self.install(acp)

        events = await self.turn("qp-mismatch", {"model": "p1/m2"})

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("model", text_of(events))
        self.assertIn("reports", text_of(events))
        self.assertEqual(acp.prompt_calls, [])

    async def test_missing_config_options_is_an_error_without_prompt(self) -> None:
        acp = FakeACP()

        async def empty_response(fake, config_id, session_id, value):
            return SimpleNamespace()

        acp.set_impl = empty_response
        self.install(acp)

        events = await self.turn("qp-no-options", {"model": "p1/m2"})

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("configOptions", text_of(events))
        self.assertEqual(acp.prompt_calls, [])

    async def test_set_failure_is_an_error_without_prompt(self) -> None:
        acp = FakeACP()

        async def boom(fake, config_id, session_id, value):
            raise RuntimeError("fake set rejection")

        acp.set_impl = boom
        self.install(acp)

        events = await self.turn("qp-set-error", {"model": "p1/m2"})

        self.assertEqual(kinds(events), ["error"])
        self.assertIn("could not be applied", text_of(events))
        self.assertEqual(acp.prompt_calls, [])


class ConcurrencyAndCancelTests(ModelContractBase):
    async def test_concurrent_catalog_refresh_is_single_flight(self) -> None:
        acp = FakeACP()
        acp.probe_gate = asyncio.Event()
        self.install(acp)

        first = asyncio.create_task(self.adapter.models())
        second = asyncio.create_task(self.adapter.models())
        await self.wait_for(
            lambda: self.probe_count(acp) == 1,
        )
        acp.probe_gate.set()
        results = await asyncio.gather(first, second)

        self.assertEqual(self.probe_count(acp), 1)
        self.assertEqual([model.id for model in results[0]], [
            "p1/m1",
            "p1/m2",
            "p2/m1",
        ])
        self.assertEqual(
            [model.id for model in results[1]],
            [model.id for model in results[0]],
        )

    async def test_two_sessions_do_not_share_selected_values(self) -> None:
        acp = FakeACP()
        acp.turn_options_mode = "none"
        self.install(acp)

        first, second = await asyncio.gather(
            self.turn("qp-a", {"model": "p1/m2"}),
            self.turn("qp-b", {"model": "p2/m1"}),
        )

        self.assertEqual(self.probe_count(acp), 1)
        self.assertEqual(len(acp.prompt_calls), 2)
        by_value = {item["value"]: item["session"] for item in acp.set_calls}
        self.assertEqual(set(by_value), {'["p1","m2"]', '["p2","m1"]'})
        self.assertNotEqual(by_value['["p1","m2"]'], by_value['["p2","m1"]'])
        self.assertEqual(set(acp.prompt_calls), set(by_value.values()))
        self.assertEqual(kinds(first).count("error"), 0)
        self.assertEqual(kinds(second).count("error"), 0)

    async def test_cancel_during_discovery_leaves_no_lock_or_prompt(self) -> None:
        acp = FakeACP()
        acp.turn_options_mode = "none"
        acp.probe_gate = asyncio.Event()
        self.install(acp)

        async def consume() -> None:
            async for _event in self.adapter.run_turn(
                session_id="qp-cancel",
                prompt="hi",
                cwd=self.cwd,
                settings={"model": "p1/m2"},
                attachments=None,
            ):
                pass

        task = asyncio.create_task(consume())
        await self.wait_for(lambda: self.probe_count(acp) == 1)
        task.cancel()
        await asyncio.wait({task}, timeout=1.0)
        acp.probe_gate.set()
        await asyncio.sleep(0.05)

        self.assertTrue(task.cancelled())
        self.assertEqual(acp.prompt_calls, [])
        catalog_lock = getattr(self.adapter, "_catalog_lock", None)
        self.assertIsNotNone(catalog_lock, "_catalog_lock is missing")
        self.assertFalse(catalog_lock.locked())

        # A later turn still works: no stale lock, no half-applied prompt.
        acp.turn_options_mode = "full"
        events = await self.turn("qp-after-cancel", {"model": "p1/m1"})
        self.assertEqual([item["value"] for item in acp.set_calls], ['["p1","m1"]'])
        self.assertEqual(len(acp.prompt_calls), 1)
        self.assertEqual(kinds(events)[-1], "completed")


    async def test_cancel_during_config_write_leaves_no_prompt(self) -> None:
        acp = FakeACP()
        acp.set_gate = asyncio.Event()
        self.install(acp)

        async def consume() -> None:
            async for _event in self.adapter.run_turn(
                session_id="qp-cancel-write",
                prompt="hi",
                cwd=self.cwd,
                settings={"model": "p1/m2", "reasoning_effort": "high"},
                attachments=None,
            ):
                pass

        task = asyncio.create_task(consume())
        await self.wait_for(lambda: len(acp.set_calls) == 1)
        task.cancel()
        await asyncio.wait({task}, timeout=1.0)
        acp.set_gate.set()
        await asyncio.sleep(0.05)

        self.assertTrue(task.cancelled())
        self.assertEqual(acp.prompt_calls, [])

        # The cancelled write is not remembered as confirmed; a later turn
        # re-applies the explicit selection and then prompts.
        acp.set_gate = None
        events = await self.turn(
            "qp-cancel-write",
            {"model": "p1/m2", "reasoning_effort": "high"},
        )
        self.assertEqual(len(acp.prompt_calls), 1)
        self.assertEqual(
            [(item["config"], item["value"]) for item in acp.set_calls[-2:]],
            [("model", '["p1","m2"]'), ("reasoning_effort", "high")],
        )
        self.assertEqual(kinds(events)[-1], "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
