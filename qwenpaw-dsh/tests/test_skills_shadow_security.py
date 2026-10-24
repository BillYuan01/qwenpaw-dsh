#!/usr/bin/env python3
"""Security regression tests for the skills snapshot cleanup boundary (R01/G4).

Each projection now writes an isolated
``<state_dir>/skills_snapshots/<id>/skills_shadow``. That snapshot's
``.qwenpaw_projection.json`` is bookkeeping on disk, so it must be treated as
untrusted input: a poisoned key must never turn stale cleanup into a delete
primitive outside the shadow root, and a symlinked snapshot/shadow root or
shadow entry must never make the code act on the link target.

Every test uses throw-away directories under the system temp dir. No real
home, state dir, credential or DSH process is touched.

Run: python -m unittest discover -s tests -p 'test_skills_shadow_security.py'
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core import skills as sk  # noqa: E402

SENTINEL_BODY = "keep-me"


def write_skill(root: Path, dirname: str, *, name: str | None = None) -> Path:
    directory = root / dirname
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name if name is not None else dirname}\n"
        f'description: "test skill {dirname}"\n'
        "---\n\n"
        f"body of {dirname}\n",
        encoding="utf-8",
    )
    return directory


def write_manifest(root: Path, payload: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / sk.MANIFEST_FILENAME
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


class ShadowCleanupBoundaryTests(unittest.TestCase):
    """A poisoned manifest must not reach outside the snapshot shadow root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dsh-g1-skills-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.skills = self.tmp / "skills"

    def _sentinel(self, directory: Path, body: str = SENTINEL_BODY) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "sentinel.txt"
        target.write_text(body, encoding="utf-8")
        return target

    def _assert_outside_intact(self, sentinel: Path) -> None:
        self.assertTrue(sentinel.is_file(), f"external sentinel removed: {sentinel}")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), SENTINEL_BODY)

    def _source(self, dirname: str = "legit_skill") -> sk.SkillSource:
        return sk.SkillSource(dirname, write_skill(self.skills, dirname))

    def _shadow_path(self, source: sk.SkillSource, *, create: bool = True) -> Path:
        entries, _ = sk.plan_skills([source])
        snapshot = sk.snapshot_id(entries)
        shadow = sk.snapshot_dir(self.state, snapshot) / sk.SHADOW_DIRNAME
        if create:
            shadow.mkdir(parents=True, exist_ok=True)
        return shadow

    # ------------------------------------------------------------------ R01
    def test_relative_traversal_key_does_not_delete_outside(self) -> None:
        victim = self.outside / "victim-relative"
        sentinel = self._sentinel(victim)
        source = self._source()
        write_manifest(self._shadow_path(source), {"../../outside/victim-relative": {"revision": "x"}})

        projection = sk.project_skills(self.state, [source])

        self._assert_outside_intact(sentinel)
        self.assertEqual(projection.removed, ())
        self.assertEqual([e.target for e in projection.entries], ["legit-skill"])

    def test_absolute_path_key_does_not_delete_outside(self) -> None:
        victim = self.outside / "victim-absolute"
        sentinel = self._sentinel(victim)
        source = self._source()
        write_manifest(self._shadow_path(source), {str(victim): {"revision": "x"}})

        projection = sk.project_skills(self.state, [source])

        self._assert_outside_intact(sentinel)
        self.assertEqual(projection.removed, ())

    def test_separator_and_pseudo_component_keys_are_rejected(self) -> None:
        victim = self.outside / "victim-nested"
        sentinel = self._sentinel(victim)
        keys = [
            "a/b",
            "..",
            ".",
            "",
            "../",
            "..\\..\\outside\\victim-nested",
            "C:\\Windows\\victim",
            "with space",
            "UPPER_CASE",
            "trailing-slash/",
        ]
        source = self._source()
        write_manifest(
            self._shadow_path(source),
            {key: {"revision": "x"} for key in keys},
        )

        projection = sk.project_skills(self.state, [source])

        self._assert_outside_intact(sentinel)
        self.assertEqual(projection.removed, ())
        self.assertEqual(sorted(item.name for item in self.outside.iterdir()), ["victim-nested"])

    def test_legit_stale_single_layer_kebab_is_still_removed(self) -> None:
        source = self._source()
        shadow = self._shadow_path(source)
        (shadow / "stale-ok").mkdir()
        write_manifest(shadow, {"stale-ok": {"revision": "x"}})

        projection = sk.project_skills(self.state, [source])

        self.assertEqual(projection.removed, ("stale-ok",))
        self.assertFalse((shadow / "stale-ok").exists())
        self.assertTrue((shadow / "legit-skill" / "SKILL.md").is_file())

    def test_mixed_manifest_removes_legit_and_skips_unsafe(self) -> None:
        victim = self.outside / "victim-mixed"
        sentinel = self._sentinel(victim)
        source = self._source()
        shadow = self._shadow_path(source)
        (shadow / "stale-ok").mkdir(parents=True)
        write_manifest(
            shadow,
            {
                "stale-ok": {"revision": "x"},
                "../../outside/victim-mixed": {"revision": "x"},
            },
        )

        projection = sk.project_skills(self.state, [source])

        self.assertEqual(projection.removed, ("stale-ok",))
        self.assertFalse((shadow / "stale-ok").exists())
        self._assert_outside_intact(sentinel)

    def test_symlinked_shadow_entry_is_unlinked_not_followed(self) -> None:
        target = self.outside / "linked-dir"
        sentinel = self._sentinel(target)
        source = self._source()
        shadow = self._shadow_path(source)
        link = shadow / "legacy-link"
        link.symlink_to(target, target_is_directory=True)
        write_manifest(shadow, {"legacy-link": {"revision": "x"}})

        projection = sk.project_skills(self.state, [source])

        self.assertEqual(projection.removed, ("legacy-link",))
        self.assertFalse(link.is_symlink(), "shadow symlink itself was not removed")
        self.assertTrue(target.is_dir(), "symlink target directory was deleted")
        self._assert_outside_intact(sentinel)

    def test_symlinked_shadow_root_is_refused(self) -> None:
        target = self.outside / "shadow-target"
        sentinel = self._sentinel(target / "named-dir")
        (target / sk.MANIFEST_FILENAME).write_text(
            json.dumps({"named-dir": {"revision": "x"}}), encoding="utf-8"
        )
        source = self._source()
        shadow = self._shadow_path(source, create=False)
        shadow.parent.mkdir(parents=True, exist_ok=True)
        shadow.symlink_to(target, target_is_directory=True)

        with self.assertRaises(sk.UnsafeShadowTree):
            sk.project_skills(self.state, [source])

        self._assert_outside_intact(sentinel)
        self.assertTrue((target / "named-dir").is_dir())
        self.assertTrue((target / sk.MANIFEST_FILENAME).is_file())

    def test_symlinked_shadow_root_is_not_written_through(self) -> None:
        target = self.outside / "shadow-target-write"
        target.mkdir()
        source = self._source("write_through")
        shadow = self._shadow_path(source, create=False)
        shadow.parent.mkdir(parents=True, exist_ok=True)
        shadow.symlink_to(target, target_is_directory=True)

        try:
            sk.project_skills(self.state, [source])
        except Exception:  # noqa: BLE001 — conservative refusal is an accepted outcome
            pass

        self.assertEqual(sorted(item.name for item in target.iterdir()), [])

    def test_source_name_traversal_is_contained(self) -> None:
        source = write_skill(self.skills, "evil")
        projection = sk.project_skills(
            self.state, [sk.SkillSource("../../evil", source)]
        )

        self.assertEqual([entry.target for entry in projection.entries], ["evil"])
        self.assertTrue((projection.shadow_root / "evil" / "SKILL.md").is_file())
        self.assertFalse((self.tmp / "evil").exists())
        self.assertFalse((self.state / "evil").exists())

    def test_destination_symlink_replacement_keeps_external_tree(self) -> None:
        target = self.outside / "dest-target"
        sentinel = self._sentinel(target)
        source = self._source("swap")
        shadow = self._shadow_path(source)
        (shadow / "swap").symlink_to(target, target_is_directory=True)

        projection = sk.project_skills(self.state, [source])

        self.assertIn("swap", projection.materialized)
        self.assertFalse((projection.shadow_root / "swap").is_symlink())
        self.assertTrue((projection.shadow_root / "swap" / "SKILL.md").is_file())
        self._assert_outside_intact(sentinel)

    def test_rejection_warning_does_not_echo_the_manifest_path(self) -> None:
        victim = self.outside / "victim-logged"
        self._sentinel(victim)
        source = self._source()
        write_manifest(self._shadow_path(source), {str(victim): {"revision": "x"}})

        with self.assertLogs("qwenpaw.dsh", level=logging.WARNING) as captured:
            sk.project_skills(self.state, [source])

        text = "\n".join(captured.output)
        self.assertNotIn(str(victim), text)
        self.assertNotIn(str(self.tmp), text)

    def test_corrupt_manifest_still_rebuilds(self) -> None:
        source = self._source("rebuild_me")
        shadow = self._shadow_path(source)
        (shadow / sk.MANIFEST_FILENAME).write_text("[not, a, mapping]", encoding="utf-8")

        projection = sk.project_skills(self.state, [source])

        self.assertIn("rebuild-me", projection.materialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
