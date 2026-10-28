#!/usr/bin/env python3
"""G5 delivery-tool regressions: reproducible packaging and the safe test runner.

All cases use throwaway directories and a synthetic source tree. The real
repository is read only where the test needs the canonical LICENSE or the
historical ``dist/`` ZIP. No plugin code is imported, no DSH process is
started, and no credentials are read.

Run: python -m unittest discover -s tests -p 'test_delivery_tools.py' -v
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INNER = REPO_ROOT / "qwenpaw-dsh"
TOOL = REPO_ROOT / "tools" / "plugin_package.py"
RUNNER = REPO_ROOT / "tools" / "run_safe_tests.py"
INNER_MANIFEST = INNER / "plugin.json"
# Current source version comes from the manifest, so a release bump does not
# require rewriting packaging tests.
CURRENT_VERSION = json.loads(INNER_MANIFEST.read_text(encoding="utf-8"))["version"]
# Historical asset: its bytes and version are frozen evidence, never rewritten.
HISTORICAL = REPO_ROOT / "dist" / "qwenpaw-dsh-1.0.1-plugin.zip"
HISTORICAL_VERSION = "1.0.1"
HISTORICAL_SHA = "2689d89a9cd62d748582185c07ac2654021fe9c1cced57d5c64ace0877bf4a2d"

LICENSE_BYTES = b"Apache License\nVersion 2.0, January 2004\n"
PLUGIN_JSON = {"id": "qwenpaw-dsh", "version": CURRENT_VERSION, "entry": {"backend": "plugin.py"}}
GOOD_PLUGIN = (
    b"class DshHarnessPlugin:\n"
    b"    def register(self, api):\n"
    b"        api.register_uninstall_hook('dsh_unmount', self._shutdown)\n"
    b"    def _shutdown(self, *args, **kwargs):\n"
    b"        pass\n"
    b"plugin = DshHarnessPlugin()\n"
)
OLD_PLUGIN = (
    b"class DshHarnessPlugin:\n"
    b"    def _shutdown(self, plugin_id=None):\n"
    b"        pass\n"
    b"plugin = DshHarnessPlugin()\n"
)

WHITELIST = (
    "LICENSE",
    "README.md",
    "plugin.json",
    "plugin.py",
    "qwenpaw_dsh/__init__.py",
    "qwenpaw_dsh/approvals.py",
    "qwenpaw_dsh/host.py",
    "qwenpaw_dsh/provider.py",
    "qwenpaw_dsh/core/__init__.py",
    "qwenpaw_dsh/core/acp_client.py",
    "qwenpaw_dsh/core/adapter.py",
    "qwenpaw_dsh/core/event_mapper.py",
    "qwenpaw_dsh/core/images.py",
    "qwenpaw_dsh/core/mcp.py",
    "qwenpaw_dsh/core/permissions.py",
    "qwenpaw_dsh/core/skills.py",
    "qwenpaw_dsh/loaders/__init__.py",
    "qwenpaw_dsh/loaders/direct.py",
    "qwenpaw_dsh/loaders/monkeypatch.py",
    "qwenpaw_dsh/loaders/official.py",
    "qwenpaw_dsh/loaders/port.py",
)
LIVE_TEST_NAMES = (
    "test_skills_projection.py",
    "test_mcp_projection.py",
    "test_image_projection.py",
    "smoke_adapter.py",
    "stream_check.py",
)


def safe_env(home: Path) -> dict[str, str]:
    """Minimal environment for tool subprocesses (no inherited secrets)."""
    home.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def run_tool(args: list[str], cwd: Path, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(cwd),
        env=safe_env(home),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )


def run_runner(args: list[str], cwd: Path, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=str(cwd),
        env=safe_env(home),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )


def make_fake_source(root: Path, *, license_bytes: bytes = LICENSE_BYTES) -> Path:
    """Create a complete, valid 22-file source tree under ``root``."""
    pkg = root / "qwenpaw-dsh"
    pkg.mkdir(parents=True)
    (root / "LICENSE").write_bytes(license_bytes)
    for rel in WHITELIST:
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == "LICENSE":
            target.write_bytes(license_bytes)
        elif rel == "plugin.json":
            target.write_text(json.dumps(PLUGIN_JSON) + "\n", encoding="utf-8")
        elif rel == "plugin.py":
            target.write_bytes(GOOD_PLUGIN)
        elif rel == "README.md":
            target.write_text("# fake package\n", encoding="utf-8")
        else:
            target.write_text("# fake module\n", encoding="utf-8")
    return pkg


def read_zip(path: Path) -> list[tuple[str, bytes, int, int]]:
    with zipfile.ZipFile(path) as archive:
        return [
            (
                info.filename,
                archive.read(info),
                (info.external_attr >> 16) & 0xFFFF,
                info.flag_bits,
            )
            for info in archive.infolist()
        ]


def write_zip(path: Path, entries: list[tuple[str, bytes, int]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = mode << 16
            archive.writestr(info, data)


def rewrite_candidate(
    src: Path,
    dst: Path,
    *,
    drop: str | None = None,
    replace: dict[str, bytes] | None = None,
    extra: tuple[str, bytes] | None = None,
    duplicate: str | None = None,
    rename: tuple[str, str] | None = None,
    mode: tuple[str, int] | None = None,
    directory: str | None = None,
) -> None:
    entries: list[tuple[str, bytes, int]] = []
    for name, data, raw_mode, _flag in read_zip(src):
        if name == drop:
            continue
        if rename and name == rename[0]:
            name = rename[1]
        if replace and name in replace:
            data = replace[name]
        current_mode = raw_mode or (stat.S_IFREG | 0o644)
        if mode and name == mode[0]:
            current_mode = mode[1]
        entries.append((name, data, current_mode))
        if duplicate == name:
            entries.append((name, data, current_mode))
    if directory:
        entries.append((directory, b"", stat.S_IFDIR | 0o755))
    if extra:
        entries.append((extra[0], extra[1], stat.S_IFREG | 0o644))
    write_zip(dst, entries)


class BuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g5-build-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_real_source_build_is_reproducible_and_passes_check(self) -> None:
        first = self.tmp / "first.zip"
        second = self.tmp / "second.zip"
        for output in (first, second):
            result = run_tool(
                ["build", "--source", str(REPO_ROOT), "--output", str(output),
                 "--expected-version", CURRENT_VERSION],
                cwd=REPO_ROOT,
                home=self.tmp / "home",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(),
                         hashlib.sha256(second.read_bytes()).hexdigest())
        with zipfile.ZipFile(first) as archive:
            names = archive.namelist()
            self.assertEqual(set(names), set(WHITELIST))
            self.assertEqual(len(names), len(set(names)))
            self.assertFalse(any(name.endswith("/") for name in names))
            for info in archive.infolist():
                self.assertEqual(stat.S_IMODE((info.external_attr >> 16) & 0xFFFF), 0o644)
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
        check = run_tool(
            ["check", "--zip", str(first), "--source", str(REPO_ROOT),
             "--expected-version", CURRENT_VERSION],
            cwd=REPO_ROOT,
            home=self.tmp / "home",
        )
        self.assertEqual(check.returncode, 0, check.stderr + check.stdout)

    def test_candidate_license_matches_root_and_kwargs_are_accepted(self) -> None:
        output = self.tmp / "candidate.zip"
        result = run_tool(
            ["build", "--source", str(REPO_ROOT), "--output", str(output),
             "--expected-version", CURRENT_VERSION],
            cwd=REPO_ROOT,
            home=self.tmp / "home",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("LICENSE"), (REPO_ROOT / "LICENSE").read_bytes())
            tree = ast.parse(archive.read("plugin.py").decode("utf-8"))
        shutdown = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_shutdown":
                shutdown = node
        self.assertIsNotNone(shutdown)
        self.assertIsNotNone(shutdown.args.kwarg)

    def test_historical_zip_is_unchanged_and_checker_fails(self) -> None:
        self.assertEqual(hashlib.sha256(HISTORICAL.read_bytes()).hexdigest(), HISTORICAL_SHA)
        result = run_tool(
            ["check", "--zip", str(HISTORICAL), "--source", str(REPO_ROOT),
             "--expected-version", HISTORICAL_VERSION],
            cwd=REPO_ROOT,
            home=self.tmp / "home",
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("LICENSE", combined)
        self.assertIn("plugin.py", combined)
        self.assertEqual(hashlib.sha256(HISTORICAL.read_bytes()).hexdigest(), HISTORICAL_SHA)

    def test_build_refuses_existing_output(self) -> None:
        source = self.tmp / "source"
        make_fake_source(source)
        output = self.tmp / "candidate.zip"
        first = run_tool(
            ["build", "--source", str(source), "--output", str(output),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        before = output.read_bytes()
        second = run_tool(
            ["build", "--source", str(source), "--output", str(output),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(output.read_bytes(), before)

    def test_build_refuses_output_inside_source(self) -> None:
        source = self.tmp / "source"
        pkg = make_fake_source(source)
        output = pkg / "candidate.zip"
        result = run_tool(
            ["build", "--source", str(source), "--output", str(output),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_build_refuses_symlinked_file(self) -> None:
        source = self.tmp / "source"
        pkg = make_fake_source(source)
        target = pkg / "LICENSE"
        target.unlink()
        target.symlink_to(source / "LICENSE")
        result = run_tool(
            ["build", "--source", str(source), "--output", str(self.tmp / "x.zip"),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_build_refuses_symlinked_directory(self) -> None:
        source = self.tmp / "source"
        pkg = make_fake_source(source)
        real = self.tmp / "real-core"
        shutil.copytree(pkg / "qwenpaw_dsh" / "core", real)
        shutil.rmtree(pkg / "qwenpaw_dsh" / "core")
        (pkg / "qwenpaw_dsh" / "core").symlink_to(real, target_is_directory=True)
        result = run_tool(
            ["build", "--source", str(source), "--output", str(self.tmp / "x.zip"),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFOs")
    def test_build_refuses_fifo(self) -> None:
        source = self.tmp / "source"
        pkg = make_fake_source(source)
        target = pkg / "qwenpaw_dsh" / "host.py"
        target.unlink()
        os.mkfifo(target)
        result = run_tool(
            ["build", "--source", str(source), "--output", str(self.tmp / "x.zip"),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_build_refuses_unexpected_source_file(self) -> None:
        source = self.tmp / "source"
        pkg = make_fake_source(source)
        (pkg / "qwenpaw_dsh" / "unexpected.py").write_text("raise SystemExit\n", encoding="utf-8")
        result = run_tool(
            ["build", "--source", str(source), "--output", str(self.tmp / "x.zip"),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_build_refuses_wrong_source_version(self) -> None:
        source = self.tmp / "source"
        pkg = make_fake_source(source)
        manifest = json.loads((pkg / "plugin.json").read_text(encoding="utf-8"))
        manifest["version"] = "9.9.9"
        (pkg / "plugin.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        result = run_tool(
            ["build", "--source", str(source), "--output", str(self.tmp / "x.zip"),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertNotEqual(result.returncode, 0)


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g5-check-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "source"
        make_fake_source(self.source)
        self.valid = self.tmp / "valid.zip"
        built = run_tool(
            ["build", "--source", str(self.source), "--output", str(self.valid),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertEqual(built.returncode, 0, built.stderr + built.stdout)

    def check(self, path: Path) -> subprocess.CompletedProcess[str]:
        return run_tool(
            ["check", "--zip", str(path), "--source", str(self.source),
             "--expected-version", CURRENT_VERSION],
            cwd=self.tmp,
            home=self.tmp / "home",
        )

    def assert_rejected(self, path: Path, needle: str) -> None:
        result = self.check(path)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(needle, result.stdout + result.stderr)

    def test_valid_candidate_passes(self) -> None:
        result = self.check(self.valid)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_extra_member(self) -> None:
        bad = self.tmp / "extra.zip"
        rewrite_candidate(self.valid, bad, extra=("notes.txt", b"private\n"))
        self.assert_rejected(bad, "notes.txt")

    def test_rejects_missing_license(self) -> None:
        bad = self.tmp / "no-license.zip"
        rewrite_candidate(self.valid, bad, drop="LICENSE")
        self.assert_rejected(bad, "LICENSE")

    def test_rejects_missing_directory(self) -> None:
        bad = self.tmp / "dir.zip"
        rewrite_candidate(self.valid, bad, directory="qwenpaw_dsh/")
        self.assert_rejected(bad, "qwenpaw_dsh/")

    def test_rejects_source_mismatch(self) -> None:
        bad = self.tmp / "mismatch.zip"
        rewrite_candidate(self.valid, bad, replace={"plugin.py": GOOD_PLUGIN + b"# drift\n"})
        self.assert_rejected(bad, "plugin.py")

    def test_rejects_version_mismatch(self) -> None:
        bad = self.tmp / "version.zip"
        manifest = dict(PLUGIN_JSON, version="9.9.9")
        rewrite_candidate(
            self.valid, bad,
            replace={"plugin.json": (json.dumps(manifest) + "\n").encode("utf-8")},
        )
        self.assert_rejected(bad, "version")

    def test_rejects_duplicate_member(self) -> None:
        bad = self.tmp / "duplicate.zip"
        rewrite_candidate(self.valid, bad, duplicate="plugin.json")
        self.assert_rejected(bad, "duplicate")

    def test_rejects_traversal(self) -> None:
        bad = self.tmp / "traversal.zip"
        rewrite_candidate(self.valid, bad, extra=("../evil.txt", b"x"))
        self.assert_rejected(bad, "unsafe")

    def test_rejects_absolute_path(self) -> None:
        bad = self.tmp / "absolute.zip"
        rewrite_candidate(self.valid, bad, extra=("/etc/passwd", b"x"))
        self.assert_rejected(bad, "unsafe")

    def test_rejects_unsafe_permissions(self) -> None:
        bad = self.tmp / "mode.zip"
        rewrite_candidate(
            self.valid, bad,
            mode=("plugin.py", stat.S_IFREG | 0o600),
        )
        self.assert_rejected(bad, "mode")

    def test_rejects_missing_kwargs(self) -> None:
        bad = self.tmp / "old-plugin.zip"
        rewrite_candidate(self.valid, bad, replace={"plugin.py": OLD_PLUGIN})
        self.assert_rejected(bad, "kwargs")


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g5-runner-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.tests = self.repo / "qwenpaw-dsh" / "tests"
        self.tests.mkdir(parents=True)

    def write_test(self, name: str, body: str) -> None:
        (self.tests / name).write_text(body, encoding="utf-8")

    def test_list_is_whitelist_only_and_counts_sum_to_252(self) -> None:
        result = run_runner(["--list"], cwd=REPO_ROOT, home=self.tmp / "home")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        names = [line.split("\t")[0] for line in lines]
        self.assertEqual(len(names), 14)
        for live in LIVE_TEST_NAMES:
            self.assertNotIn(live, names)
        expected = 0
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2 and parts[1].isdigit():
                expected += int(parts[1])
        self.assertEqual(expected, 252)

    def test_runner_does_not_execute_live_sentinel(self) -> None:
        marker = self.tmp / "sentinel-ran"
        self.write_test(
            "test_skills_projection.py",
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('ran')\n"
            "raise SystemExit(99)\n",
        )
        self.write_test(
            "test_permission_mode.py",
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
        )
        result = run_runner(
            ["--repo-root", str(self.repo), "--only", "test_permission_mode.py",
             "--skip-count-check"],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(marker.exists(), "runner executed a non-whitelisted live test")

    def test_runner_uses_throwaway_home_and_env(self) -> None:
        body = (
            "import os, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_env(self):\n"
            "        for key in ('HOME','DSH_HOME','QWENPAW_HOME','QWENPAW_WORKING_DIR','TMPDIR'):\n"
            "            value = os.environ.get(key)\n"
            "            self.assertTrue(value, key)\n"
            "            self.assertNotEqual(value, '/private-test-home-sentinel')\n"
            "        self.assertNotIn('DEEPSEEK_API_KEY', os.environ)\n"
            "        self.assertNotIn('PYTHONPATH', os.environ)\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        )
        self.write_test("test_permission_mode.py", body)
        result = run_runner(
            ["--repo-root", str(self.repo), "--only", "test_permission_mode.py",
             "--skip-count-check"],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_runner_does_not_report_skips_as_green(self) -> None:
        self.write_test(
            "test_permission_mode.py",
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_skip(self):\n"
            "        raise unittest.SkipTest('fake missing dependency')\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
        )
        result = run_runner(
            ["--repo-root", str(self.repo), "--only", "test_permission_mode.py",
             "--skip-count-check"],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertEqual(result.returncode, 3, result.stderr + result.stdout)
        self.assertIn("skipped=1", result.stdout + result.stderr)

    def test_runner_fails_on_missing_file(self) -> None:
        result = run_runner(
            ["--repo-root", str(self.repo), "--only", "test_permission_mode.py"],
            cwd=self.tmp,
            home=self.tmp / "home",
        )
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
