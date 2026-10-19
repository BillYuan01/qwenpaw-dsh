#!/usr/bin/env python3
"""Unit tests for the gate-3 skills projection (kebab normalization + mapping).

Run: ~/.qwenpaw/venv/bin/python tests/test_skills.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core import skills as sk  # noqa: E402
from qwenpaw_dsh.core.acp_client import DshConnection  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402


def write_skill(root: Path, dirname: str, *, name: str | None = None, body: str = "") -> Path:
    directory = root / dirname
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name if name is not None else dirname}\n"
        f'description: "test skill {dirname}"\n'
        "---\n\n"
        f"{body or ('body of ' + dirname)}\n",
        encoding="utf-8",
    )
    return directory


class ToKebabTests(unittest.TestCase):
    def test_normalization_table(self) -> None:
        cases = {
            "hello-kebab": "hello-kebab",
            "snake_case_skill": "snake-case-skill",
            "Spike-Uppercase": "spike-uppercase",
            "with spaces": "with-spaces",
            "dots.and.dots": "dots-and-dots",
            "a--b__c": "a-b-c",
            "-lead-trail-": "lead-trail",
            "中文": "",
            "技能 name": "name",
            "": "",
            "---": "",
            "UPPER_SNAKE_9": "upper-snake-9",
            "mixed.Case_Name": "mixed-case-name",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(sk.to_kebab(raw), expected)

    def test_output_always_matches_dsh_grammar_unless_empty(self) -> None:
        samples = [
            "snake_case_skill", "Spike-Uppercase", "a--b__c", "中文", "x",
            "9-lives", "a.b.c", "  spaced  ",
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                normalized = sk.to_kebab(raw)
                if normalized:
                    self.assertTrue(sk.is_kebab(normalized), normalized)

    def test_none_is_empty(self) -> None:
        self.assertEqual(sk.to_kebab(None), "")  # type: ignore[arg-type]


class PlanTests(unittest.TestCase):
    def test_collision_suffixes_in_source_order(self) -> None:
        sources = [
            sk.SkillSource("snake_case_skill", Path("/s/snake_case_skill")),
            sk.SkillSource("snake.case.skill", Path("/s/snake.case.skill")),
            sk.SkillSource("snake-case-skill", Path("/s/snake-case-skill")),
        ]
        entries, skipped = sk.plan_skills(sources)
        self.assertEqual(skipped, [])
        # sorted: "snake-case-skill" < "snake.case.skill" < "snake_case_skill"
        self.assertEqual(
            [(e.source.name, e.target, e.suffix) for e in entries],
            [
                ("snake-case-skill", "snake-case-skill", 0),
                ("snake.case.skill", "snake-case-skill-2", 2),
                ("snake_case_skill", "snake-case-skill-3", 3),
            ],
        )

    def test_collision_avoids_pre_existing_suffixed_name(self) -> None:
        sources = [
            sk.SkillSource("a_b", Path("/s/a_b")),
            sk.SkillSource("a-b", Path("/s/a-b")),
            sk.SkillSource("a.b.2", Path("/s/a.b.2")),
        ]
        entries, _ = sk.plan_skills(sources)
        targets = {e.source.name: e.target for e in entries}
        self.assertEqual(targets["a-b"], "a-b")
        self.assertEqual(targets["a.b.2"], "a-b-2")
        self.assertEqual(targets["a_b"], "a-b-3")

    def test_empty_kebab_is_skipped_with_reason(self) -> None:
        entries, skipped = sk.plan_skills(
            [sk.SkillSource("中文", Path("/s/cn")), sk.SkillSource("ok-name", Path("/s/ok"))],
        )
        self.assertEqual([e.target for e in entries], ["ok-name"])
        self.assertEqual(skipped, [("中文", "empty-kebab")])

    def test_order_is_independent_of_input_order(self) -> None:
        sources = [
            sk.SkillSource("b_b", Path("/s/b_b")),
            sk.SkillSource("a_a", Path("/s/a_a")),
            sk.SkillSource("b.b", Path("/s/b.b")),
        ]
        forward = sk.plan_skills(sources)[0]
        backward = sk.plan_skills(list(reversed(sources)))[0]
        self.assertEqual(
            [(e.source.name, e.target) for e in forward],
            [(e.source.name, e.target) for e in backward],
        )

    def test_token_sensitive_to_name_and_revision(self) -> None:
        base = [sk.SkillSource("a_b", Path("/s/a_b"), revision="r1")]
        self.assertNotEqual(
            sk.plan_token(sk.plan_skills(base)[0]),
            sk.plan_token(sk.plan_skills([sk.SkillSource("a_b", Path("/s/a_b"), revision="r2")])[0]),
        )
        self.assertEqual(sk.plan_token([]), "")
        self.assertNotEqual(sk.plan_token(sk.plan_skills(base)[0]), "")


class RewriteTests(unittest.TestCase):
    def test_replaces_frontmatter_name(self) -> None:
        text = "---\nname: snake_case_skill\ndescription: d\n---\n\nbody\n"
        self.assertEqual(
            sk.rewrite_skill_name(text, "snake-case-skill"),
            "---\nname: snake-case-skill\ndescription: d\n---\n\nbody\n",
        )

    def test_keeps_other_frontmatter_and_metadata(self) -> None:
        text = (
            "---\n"
            "name: old\n"
            "description: d\n"
            "metadata:\n"
            "  qwenpaw:\n"
            "    emoji: x\n"
            "---\n\nbody\n"
        )
        out = sk.rewrite_skill_name(text, "new-name")
        self.assertIn("name: new-name\n", out)
        self.assertIn("  qwenpaw:\n", out)
        self.assertNotIn("name: old", out)

    def test_ignores_nested_name(self) -> None:
        text = "---\ndescription: d\nmetadata:\n  name: inner\nname: outer\n---\nbody\n"
        out = sk.rewrite_skill_name(text, "kebab")
        self.assertIn("name: kebab\n", out)
        self.assertIn("  name: inner\n", out)

    def test_adds_name_when_missing(self) -> None:
        out = sk.rewrite_skill_name("---\ndescription: d\n---\nbody\n", "kebab")
        self.assertTrue(out.startswith("---\nname: kebab\ndescription: d\n"))


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "harnesses"
        self.skills = self.root / "skills"
        self.skills.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sources(self, specs: list[tuple[str, str]]) -> list[sk.SkillSource]:
        out = []
        for dirname, revision in specs:
            directory = self.skills / dirname
            out.append(sk.SkillSource(dirname, directory, revision=revision))
        return out

    def test_projects_shadow_tree_mapping_and_patch(self) -> None:
        write_skill(self.skills, "hello-kebab")
        write_skill(self.skills, "snake_case_skill")
        (self.skills / "snake_case_skill" / "ref.txt").write_text("resource")
        projection = sk.project_skills(
            self.state,
            self._sources([("hello-kebab", "r1"), ("snake_case_skill", "r1")]),
        )
        self.assertTrue(projection.has_skills)
        self.assertEqual(
            sorted(e.target for e in projection.entries),
            ["hello-kebab", "snake-case-skill"],
        )
        # shadow SKILL.md carries the kebab name
        body = (self.state / "skills_shadow" / "snake-case-skill" / "SKILL.md").read_text()
        self.assertIn("name: snake-case-skill", body)
        self.assertNotIn("name: snake_case_skill", body)
        # resource is linked and readable
        resource = self.state / "skills_shadow" / "snake-case-skill" / "ref.txt"
        self.assertTrue(resource.exists())
        self.assertEqual(resource.read_text(), "resource")
        # mapping file
        mapping = sk.load_map(self.state)
        self.assertEqual(mapping["snake-case-skill"]["source_name"], "snake_case_skill")
        self.assertEqual(mapping["snake-case-skill"]["suffix"], 0)
        self.assertEqual(sk.target_for(mapping, "snake_case_skill"), "snake-case-skill")
        # patch file
        patch = (self.state / "skills.patch.yml").read_text()
        self.assertIn("- id: skill-filesystem", patch)
        self.assertIn(f"      - '{self.state / 'skills_shadow'}'", patch)
        # command args
        self.assertEqual(
            projection.command_args(),
            ["--patch", str(self.state / "skills.patch.yml")],
        )

    def test_idempotent_by_revision(self) -> None:
        write_skill(self.skills, "snake_case_skill")
        sources = self._sources([("snake_case_skill", "r1")])
        first = sk.project_skills(self.state, sources)
        self.assertEqual(first.materialized, ("snake-case-skill",))
        second = sk.project_skills(self.state, sources)
        self.assertEqual(second.materialized, ())
        self.assertEqual(second.reused, ("snake-case-skill",))

    def test_revision_change_rebuilds(self) -> None:
        write_skill(self.skills, "snake_case_skill")
        sk.project_skills(self.state, self._sources([("snake_case_skill", "r1")]))
        second = sk.project_skills(self.state, self._sources([("snake_case_skill", "r2")]))
        self.assertEqual(second.materialized, ("snake-case-skill",))
        self.assertEqual(second.reused, ())

    def test_no_host_revision_falls_back_to_content_hash(self) -> None:
        write_skill(self.skills, "snake_case_skill")
        sources = self._sources([("snake_case_skill", "")])
        sk.project_skills(self.state, sources)
        # same content -> reused
        second = sk.project_skills(self.state, sources)
        self.assertEqual(second.reused, ("snake-case-skill",))
        # changed content -> rebuilt
        (self.skills / "snake_case_skill" / "SKILL.md").write_text(
            "---\nname: snake_case_skill\ndescription: d\n---\nchanged\n",
            encoding="utf-8",
        )
        third = sk.project_skills(self.state, sources)
        self.assertEqual(third.materialized, ("snake-case-skill",))

    def test_removed_skill_cleans_shadow_and_map(self) -> None:
        write_skill(self.skills, "one_skill")
        write_skill(self.skills, "two_skill")
        sk.project_skills(
            self.state,
            self._sources([("one_skill", "r1"), ("two_skill", "r1")]),
        )
        projection = sk.project_skills(self.state, self._sources([("one_skill", "r1")]))
        self.assertEqual(projection.removed, ("two-skill",))
        self.assertFalse((self.state / "skills_shadow" / "two-skill").exists())
        self.assertNotIn("two-skill", sk.load_map(self.state))

    def test_no_skills_removes_patch_and_writes_empty_map(self) -> None:
        write_skill(self.skills, "one_skill")
        sk.project_skills(self.state, self._sources([("one_skill", "r1")]))
        projection = sk.project_skills(self.state, [])
        self.assertFalse(projection.has_skills)
        self.assertIsNone(projection.patch_path)
        self.assertEqual(projection.command_args(), [])
        self.assertFalse((self.state / "skills.patch.yml").exists())
        self.assertEqual(sk.load_map(self.state), {})
        self.assertFalse((self.state / "skills_shadow" / "one-skill").exists())

    def test_missing_skill_md_is_skipped(self) -> None:
        directory = self.skills / "broken_skill"
        directory.mkdir(parents=True)
        projection = sk.project_skills(
            self.state,
            [sk.SkillSource("broken_skill", directory, revision="r1")],
        )
        self.assertFalse(projection.has_skills)
        self.assertIn(("broken_skill", "missing-skill-md"), projection.skipped)

    def test_patch_quotes_single_quote_in_path(self) -> None:
        weird = self.root / "it's state"
        write_skill(self.skills, "one_skill")
        projection = sk.project_skills(
            weird,
            self._sources([("one_skill", "r1")]),
        )
        patch = Path(projection.patch_path).read_text()
        self.assertIn("''", patch)
        self.assertIn("it''s state", patch)


class SourceExtractionTests(unittest.TestCase):
    def test_extracts_from_object(self) -> None:
        from types import SimpleNamespace

        skill = SimpleNamespace(
            name="snake_case_skill",
            directory=Path("/s/snake_case_skill"),
            revision="r1",
            description="d",
        )
        caps = SimpleNamespace(skills=[skill])
        sources = sk.skill_sources(caps)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].name, "snake_case_skill")
        self.assertEqual(sources[0].revision, "r1")

    def test_extracts_from_dict(self) -> None:
        caps = {
            "skills": [
                {"name": "a_b", "directory": "/s/a_b", "revision": "r2"},
            ],
        }
        sources = sk.skill_sources(caps)
        self.assertEqual([s.name for s in sources], ["a_b"])
        self.assertEqual(sources[0].directory, Path("/s/a_b"))

    def test_runtime_skills_from_settings(self) -> None:
        settings = {
            "_runtime_capabilities": {
                "skills": [{"name": "x_y", "directory": "/s/x_y", "revision": "r"}],
            },
        }
        self.assertEqual([s.name for s in sk.runtime_skills(settings)], ["x_y"])

    def test_missing_or_broken_inputs(self) -> None:
        self.assertEqual(sk.skill_sources(None), [])
        self.assertEqual(sk.skill_sources({}), [])
        self.assertEqual(sk.skill_sources({"skills": "nope"}), [])
        self.assertEqual(sk.runtime_skills({}), [])


class _FakeConnection:
    def __init__(self, key: tuple) -> None:
        self.key = key
        self.alive = True

    async def aclose(self) -> None:
        self.alive = False


class AdapterWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "harnesses"
        self.skills = self.root / "skills"
        self.skills.mkdir(parents=True)
        self.adapter = DshAdapter(
            state_dir=self.state,
            settings={"profile": "qwenpaw"},
            host=HostContext(state_dir=self.state),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _settings(self, dirname: str, revision: str) -> dict:
        return {
            "_runtime_capabilities": {
                "skills": [
                    {"name": dirname, "directory": str(self.skills / dirname), "revision": revision},
                ],
            },
        }

    async def test_pool_key_tracks_skills_snapshot(self) -> None:
        write_skill(self.skills, "snake_case_skill")
        before = self.adapter._pool_key()
        await self.adapter._refresh_skills(self._settings("snake_case_skill", "r1"))
        after = self.adapter._pool_key()
        self.assertNotEqual(before[-1], after[-1])
        self.assertEqual(after[-1], self.adapter._skills_plan.snapshot)
        # same revision -> key stable
        await self.adapter._refresh_skills(self._settings("snake_case_skill", "r1"))
        self.assertEqual(self.adapter._pool_key(), after)
        # changed revision -> key changes
        await self.adapter._refresh_skills(self._settings("snake_case_skill", "r2"))
        self.assertNotEqual(self.adapter._pool_key(), after)

    async def test_connection_args_include_patch_only_with_skills(self) -> None:
        write_skill(self.skills, "snake_case_skill")
        captured: list[dict] = []

        async def fake_open(**kwargs):
            captured.append(kwargs)
            return _FakeConnection(kwargs["key"])

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            # no skills -> no --patch
            await self.adapter._connection()
            self.assertNotIn("--patch", captured[-1]["args"])
            await self.adapter._close_connections()
            # skills -> --patch <file>
            await self.adapter._refresh_skills(self._settings("snake_case_skill", "r1"))
            await self.adapter._connection()
            args = captured[-1]["args"]
            self.assertIn("--patch", args)
            self.assertEqual(args[args.index("--patch") + 1], str(self.state / "skills.patch.yml"))
            self.assertEqual(args[:2], ["--profile", "qwenpaw"])

    async def test_skill_change_closes_pooled_connections(self) -> None:
        write_skill(self.skills, "snake_case_skill")

        async def fake_open(**kwargs):
            return _FakeConnection(kwargs["key"])

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            await self.adapter._refresh_skills(self._settings("snake_case_skill", "r1"))
            connection = await self.adapter._connection()
            self.assertTrue(self.adapter._connections)
            # no change -> connection kept
            await self.adapter._refresh_skills(self._settings("snake_case_skill", "r1"))
            self.assertTrue(connection.alive)
            # change -> connection closed + dropped
            await self.adapter._refresh_skills(self._settings("snake_case_skill", "r2"))
            self.assertFalse(connection.alive)
            self.assertEqual(self.adapter._connections, {})

    async def test_projection_failure_is_fail_soft(self) -> None:
        with mock.patch.object(
            sk, "project_skills", side_effect=RuntimeError("disk on fire"),
        ):
            await self.adapter._refresh_skills(self._settings("snake_case_skill", "r1"))
        self.assertFalse(self.adapter._skills_plan.has_skills)
        self.assertEqual(self.adapter._pool_key()[-1], "")

    async def test_stop_closes_connections(self) -> None:
        async def fake_open(**kwargs):
            return _FakeConnection(kwargs["key"])

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            connection = await self.adapter._connection()
        await self.adapter.stop()
        self.assertFalse(connection.alive)
        self.assertEqual(self.adapter._connections, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
