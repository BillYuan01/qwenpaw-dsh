#!/usr/bin/env python3
"""Regression tests for the host-version gate in the monkeypatch loader.

Regression (1.1.1 -> 1.1.2): ``_in_range`` scraped the digits out of every
dot-segment, so the pre-release suffix "0b1" became patch ``1`` and the string
"2.3.0b1" resolved to ``(2, 3, 1)`` — sorted *above* the exclusive bound
``2.3.0``. On QwenPaw 2.3.0b1 every patch target still resolved, yet the gate
reported "outside supported range", the official loader found no registration
entry, the plugin was marked FAILED, and the DSH backend disappeared from the
provider catalog with no diagnostic beyond a version string.

``packaging.version.Version`` gives the correct PEP 440 ordering, where a
pre-release sorts *before* its base release. The digit-scraping path is kept
only as a dependency-free fallback and must fail closed.

Run: ~/.qwenpaw/venv/bin/python tests/test_version_gate.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.loaders.monkeypatch import MonkeypatchLoader  # noqa: E402

LO, HI = "2.2.0", "2.3.0"


class VersionGateTests(unittest.TestCase):
    def test_prerelease_of_upper_bound_is_admitted(self) -> None:
        for version in ("2.3.0a1", "2.3.0b1", "2.3.0b2", "2.3.0rc1"):
            with self.subTest(version=version):
                self.assertTrue(
                    MonkeypatchLoader._in_range(version, LO, HI),
                    f"{version} should mount under [{LO}, {HI})",
                )

    def test_upper_bound_stays_exclusive(self) -> None:
        self.assertFalse(MonkeypatchLoader._in_range("2.3.0", LO, HI))
        self.assertFalse(MonkeypatchLoader._in_range("2.3.1", LO, HI))
        self.assertFalse(MonkeypatchLoader._in_range("2.4.0", LO, HI))

    def test_supported_hosts(self) -> None:
        for version in ("2.2.0", "2.2.1", "2.2.2", "2.2.2.post1"):
            with self.subTest(version=version):
                self.assertTrue(MonkeypatchLoader._in_range(version, LO, HI))

    def test_older_hosts_are_rejected(self) -> None:
        self.assertFalse(MonkeypatchLoader._in_range("1.9.9", LO, HI))
        self.assertFalse(MonkeypatchLoader._in_range("2.1.9", LO, HI))

    def test_missing_version_is_never_mounted(self) -> None:
        self.assertFalse(MonkeypatchLoader._in_range(None, LO, HI))
        self.assertFalse(MonkeypatchLoader._in_range("", LO, HI))

    def test_unparseable_version_fails_closed(self) -> None:
        self.assertFalse(MonkeypatchLoader._in_range("not-a-version", LO, HI))
        self.assertFalse(MonkeypatchLoader._in_range("x.y.z", LO, HI))


if __name__ == "__main__":
    unittest.main(verbosity=2)
