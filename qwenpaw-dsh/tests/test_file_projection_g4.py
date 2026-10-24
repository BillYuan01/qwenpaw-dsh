#!/usr/bin/env python3
"""G4 regression tests: bounded image reads, skills snapshot isolation, YAML names.

Everything here is offline: temporary directories, fake adapters/connections,
synthetic images and frontmatter. No DSH process, model, credential or real
state directory is used.

Run: ~/.qwenpaw/venv/bin/python -m unittest tests.test_file_projection_g4
"""

from __future__ import annotations

import base64
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core import images as image_codec  # noqa: E402
from qwenpaw_dsh.core import skills as sk  # noqa: E402
from qwenpaw_dsh.core.acp_client import DshConnection  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"g4"


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


class BoundedImageReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dsh-g4-img-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_regular_image_is_encoded(self) -> None:
        path = self.dir / "ok.png"
        path.write_bytes(PNG)
        encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(reason)
        self.assertEqual(encoded.mime, "image/png")
        self.assertEqual(base64.b64decode(encoded.data), PNG)

    def test_exact_limit_is_allowed(self) -> None:
        path = self.dir / "limit.png"
        path.write_bytes(b"x" * 16)
        with mock.patch.object(image_codec, "MAX_IMAGE_BYTES", 16):
            encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(reason)
        self.assertEqual(len(base64.b64decode(encoded.data)), 16)

    def test_fifo_is_rejected_without_blocking(self) -> None:
        path = self.dir / "pipe.png"
        os.mkfifo(path)
        result: list[tuple[object, object]] = []

        def run() -> None:
            result.append(image_codec.encode_image(path))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(2.0)
        self.assertFalse(thread.is_alive(), "encode_image blocked on a FIFO")
        self.assertEqual(result, [(None, "not-regular")])

    def test_symlink_is_rejected_and_target_untouched(self) -> None:
        target = self.dir / "real.png"
        target.write_bytes(PNG)
        link = self.dir / "link.png"
        link.symlink_to(target)
        encoded, reason = image_codec.encode_image(link)
        self.assertIsNone(encoded)
        self.assertEqual(reason, "not-regular")
        self.assertEqual(target.read_bytes(), PNG)

    def test_directory_keeps_the_historical_reason(self) -> None:
        path = self.dir / "dir.png"
        path.mkdir()
        self.assertEqual(image_codec.encode_image(path), (None, "unreadable"))

    def test_size_lie_is_caught_by_fstat(self) -> None:
        path = self.dir / "grow.png"
        path.write_bytes(b"z" * 64)
        fake = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_size=1,
            st_dev=1,
            st_ino=1,
        )
        with mock.patch.object(image_codec, "MAX_IMAGE_BYTES", 16):
            with mock.patch.object(image_codec.os, "lstat", return_value=fake):
                encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(encoded)
        self.assertEqual(reason, "too-large")

    def test_short_reads_are_looped_until_eof(self) -> None:
        path = self.dir / "short.png"
        path.write_bytes(PNG)
        real_read = os.read
        calls: list[int] = []

        def one_byte(fd: int, size: int) -> bytes:
            calls.append(fd)
            return real_read(fd, 1)

        with mock.patch.object(image_codec.os, "read", side_effect=one_byte):
            encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(reason)
        self.assertEqual(base64.b64decode(encoded.data), PNG)
        self.assertGreater(len(calls), 1, "a single short read was treated as EOF")

    def test_fd_is_closed_when_read_fails(self) -> None:
        path = self.dir / "boom.png"
        path.write_bytes(PNG)
        closed: list[int] = []
        real_close = os.close

        def track_close(fd: int) -> None:
            closed.append(fd)
            real_close(fd)

        with mock.patch.object(image_codec.os, "read", side_effect=OSError("boom")):
            with mock.patch.object(image_codec.os, "close", side_effect=track_close):
                encoded, reason = image_codec.encode_image(path)
        self.assertIsNone(encoded)
        self.assertEqual(reason, "unreadable")
        self.assertEqual(len(closed), 1, "descriptor was not closed on error")


class SkillSnapshotIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dsh-g4-skills-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "harnesses"
        self.skills = self.root / "skills"
        self.skills.mkdir()

    def _source(self, dirname: str, revision: str = "r1") -> sk.SkillSource:
        write_skill(self.skills, dirname)
        (self.skills / dirname / "ref.txt").write_text("resource-" + dirname)
        return sk.SkillSource(dirname, self.skills / dirname, revision=revision)

    def test_two_snapshots_do_not_overwrite_each_other(self) -> None:
        first = sk.project_skills(self.state, [self._source("a_skill")])
        a_file = first.shadow_root / "a-skill" / "SKILL.md"
        self.assertTrue(a_file.is_file())
        second = sk.project_skills(self.state, [self._source("b_skill")])
        self.assertNotEqual(first.snapshot, second.snapshot)
        self.assertNotEqual(first.patch_path, second.patch_path)
        # A's resources are still exactly A's.
        self.assertTrue(a_file.is_file())
        self.assertIn("name: a-skill", a_file.read_text())
        self.assertEqual((first.shadow_root / "a-skill" / "ref.txt").read_text(), "resource-a_skill")
        # A's patch still points at A, B's at B.
        self.assertIn(str(first.shadow_root), Path(first.patch_path).read_text())
        self.assertIn(str(second.shadow_root), Path(second.patch_path).read_text())

    def test_empty_projection_does_not_clear_an_active_snapshot(self) -> None:
        first = sk.project_skills(self.state, [self._source("a_skill")])
        a_file = first.shadow_root / "a-skill" / "SKILL.md"
        empty = sk.project_skills(self.state, [])
        self.assertEqual(empty.snapshot, "")
        self.assertIsNone(empty.patch_path)
        self.assertTrue(a_file.is_file())
        self.assertEqual(sk.load_map(self.state), {})

    def test_same_source_and_revision_reuses_the_snapshot(self) -> None:
        source = self._source("a_skill")
        first = sk.project_skills(self.state, [source])
        second = sk.project_skills(self.state, [source])
        self.assertEqual(first.snapshot, second.snapshot)
        self.assertEqual(second.materialized, ())
        self.assertEqual(second.reused, ("a-skill",))

    def test_moved_source_with_same_revision_rebuilds_links(self) -> None:
        original = self._source("a_skill")
        first = sk.project_skills(self.state, [original])
        moved = self.root / "moved"
        moved.mkdir()
        (moved / "a_skill").mkdir()
        for child in (self.skills / "a_skill").iterdir():
            (moved / "a_skill" / child.name).write_bytes(child.read_bytes())
        relocated = sk.SkillSource("a_skill", moved / "a_skill", revision="r1")
        second = sk.project_skills(self.state, [relocated])
        self.assertNotEqual(first.snapshot, second.snapshot)
        self.assertIn("a-skill", second.materialized)
        self.assertTrue((second.shadow_root / "a-skill" / "ref.txt").exists())
        self.assertEqual(
            (second.shadow_root / "a-skill" / "ref.txt").read_text(),
            "resource-a_skill",
        )

    def test_same_path_and_revision_but_new_resource_rematerializes(self) -> None:
        source = self._source("a_skill")
        first = sk.project_skills(self.state, [source])
        self.assertEqual(first.reused, ())
        (self.skills / "a_skill" / "added.txt").write_text("new-resource")
        second = sk.project_skills(self.state, [source])
        self.assertEqual(second.snapshot, first.snapshot)
        self.assertEqual(second.materialized, ("a-skill",))
        self.assertEqual(
            (second.shadow_root / "a-skill" / "added.txt").read_text(),
            "new-resource",
        )

    def test_pool_token_includes_the_source_location(self) -> None:
        first = self._source("a_skill")
        entries, _ = sk.plan_skills([first])
        token_one = sk.plan_token(entries)
        relocated = sk.SkillSource("a_skill", self.root / "elsewhere" / "a_skill", revision="r1")
        token_two = sk.plan_token(sk.plan_skills([relocated])[0])
        self.assertNotEqual(token_one, token_two)

    def test_symlinked_snapshot_root_is_refused(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.state.mkdir()
        (self.state / "skills_snapshots").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(sk.UnsafeShadowTree):
            sk.project_skills(self.state, [self._source("a_skill")])
        self.assertEqual(list(outside.iterdir()), [])

    def test_content_hash_walk_is_bounded(self) -> None:
        directory = self.skills / "many"
        directory.mkdir()
        (directory / "one.txt").write_text("1")
        (directory / "two.txt").write_text("2")
        with self.assertLogs("qwenpaw.dsh", level="WARNING"):
            self.assertEqual(sk._directory_revision(directory, max_files=1), "")

    def test_invalid_frontmatter_is_skipped_not_projected(self) -> None:
        directory = self.skills / "bad_name"
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            "---\nname: first\nname: second\ndescription: d\n---\nbody\n",
            encoding="utf-8",
        )
        projection = sk.project_skills(
            self.state, [sk.SkillSource("bad_name", directory, revision="r1")]
        )
        self.assertFalse(projection.has_skills)
        self.assertIn(("bad_name", "invalid-frontmatter"), projection.skipped)
        self.assertFalse((self.state / "skills_snapshots").exists())


class _FakeConnection:
    def __init__(self, key: tuple) -> None:
        self.key = key
        self.alive = True

    async def aclose(self) -> None:
        self.alive = False


class ConnectionSnapshotBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dsh-g4-bind-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = self.root / "harnesses"
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.adapter = DshAdapter(
            state_dir=self.state,
            settings={"profile": "qwenpaw"},
            host=HostContext(state_dir=self.state),
        )

    def _settings(self, dirname: str) -> dict:
        write_skill(self.skills, dirname)
        return {
            "_runtime_capabilities": {
                "skills": [
                    {
                        "name": dirname,
                        "directory": str(self.skills / dirname),
                        "revision": "r1",
                    },
                ],
            },
        }

    async def test_connection_records_its_snapshot(self) -> None:
        async def fake_open(**kwargs):
            return _FakeConnection(kwargs["key"])

        with mock.patch.object(DshConnection, "open", side_effect=fake_open):
            await self.adapter._refresh_skills(self._settings("a_skill"))
            connection = await self.adapter._connection()
        self.assertEqual(connection.skills_snapshot, self.adapter._skills_plan.snapshot)
        self.assertEqual(connection.skills_root, self.adapter._skills_plan.shadow_root)
        await self.adapter.stop()


class FrontmatterRewriteTests(unittest.TestCase):
    def _parsed(self, text: str) -> dict:
        match = sk._FRONTMATTER_RE.match(text)
        self.assertIsNotNone(match)
        return yaml.safe_load(match.group("body"))

    def test_folded_name_is_replaced_without_touching_description(self) -> None:
        text = "---\nname: >\n  my skill\n  more\ndescription: d\n---\n\nbody\n"
        out = sk.rewrite_skill_name(text, "my-skill")
        parsed = self._parsed(out)
        self.assertEqual(parsed["name"], "my-skill")
        self.assertEqual(parsed["description"], "d")
        self.assertTrue(out.endswith("\nbody\n"))
        self.assertIn("description: d\n", out)

    def test_literal_name_is_replaced(self) -> None:
        text = "---\nname: |\n  my skill\ndescription: d\n---\nbody\n"
        out = sk.rewrite_skill_name(text, "my-skill")
        self.assertEqual(self._parsed(out)["name"], "my-skill")
        self.assertEqual(self._parsed(out)["description"], "d")

    def test_quoted_keys_are_found(self) -> None:
        for key in ('"name"', "'name'"):
            with self.subTest(key=key):
                text = f"---\n{key}: old\ndescription: d\n---\nbody\n"
                out = sk.rewrite_skill_name(text, "new-name")
                self.assertEqual(self._parsed(out)["name"], "new-name")
                self.assertEqual(self._parsed(out)["description"], "d")

    def test_missing_name_is_inserted(self) -> None:
        text = "---\ndescription: d\nmetadata:\n  qwenpaw:\n    emoji: x\n---\nbody\n"
        out = sk.rewrite_skill_name(text, "new-name")
        parsed = self._parsed(out)
        self.assertEqual(parsed["name"], "new-name")
        self.assertEqual(parsed["metadata"]["qwenpaw"]["emoji"], "x")
        self.assertTrue(out.endswith("\nbody\n"))

    def test_duplicate_name_is_rejected(self) -> None:
        text = "---\nname: one\nname: two\ndescription: d\n---\nbody\n"
        with self.assertRaises(sk.UnsafeFrontmatter):
            sk.rewrite_skill_name(text, "new-name")

    def test_non_scalar_name_and_invalid_yaml_are_rejected(self) -> None:
        with self.assertRaises(sk.UnsafeFrontmatter):
            sk.rewrite_skill_name("---\nname: [a, b]\n---\nbody\n", "new-name")
        with self.assertRaises(sk.UnsafeFrontmatter):
            sk.rewrite_skill_name("---\nname: [unclosed\n---\nbody\n", "new-name")

    def test_crlf_and_document_end_are_preserved(self) -> None:
        text = "---\r\nname: old\r\ndescription: d\r\n...\r\n---\r\nbody\r\n"
        out = sk.rewrite_skill_name(text, "new-name")
        self.assertEqual(self._parsed(out)["name"], "new-name")
        self.assertIn("name: new-name\r\n", out)
        self.assertIn("...\r\n", out)
        self.assertTrue(out.endswith("body\r\n"))

    def test_comments_order_and_duplicate_non_name_keys_survive(self) -> None:
        text = (
            "---\n"
            "# leading comment\n"
            "description: first\n"
            "metadata:\n"
            "  keep: yes\n"
            "name: old  # trailing\n"
            "description: second\n"
            "---\n"
            "body line one\n"
            "body line two\n"
        )
        out = sk.rewrite_skill_name(text, "new-name")
        self.assertIn("# leading comment\n", out)
        self.assertIn("name: new-name  # trailing\n", out)
        self.assertEqual(out.count("description:"), 2)
        self.assertTrue(out.endswith("body line one\nbody line two\n"))

    def test_invalid_target_is_rejected(self) -> None:
        with self.assertRaises(sk.UnsafeFrontmatter):
            sk.rewrite_skill_name("---\nname: old\n---\nbody\n", "not kebab")


if __name__ == "__main__":
    unittest.main(verbosity=2)
