#!/usr/bin/env python3
"""Explicit-whitelist runner for the safe (offline) qwenpaw-dsh unit tests.

It never discovers ``test_*.py`` broadly. Projection/contract/smoke scripts are
deliberately absent: they can start DSH, read credentials, execute tools, or
call billed models.

Usage:
    python tools/run_safe_tests.py --python /path/to/venv/bin/python
    python tools/run_safe_tests.py --list
    python tools/run_safe_tests.py --only test_permission_mode.py --skip-count-check

Every run gets a throwaway HOME / DSH_HOME / QWENPAW_HOME /
QWENPAW_WORKING_DIR / TMPDIR and a minimal environment allowlist. Nothing is
installed automatically; a missing dependency is a failure, never a green run.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "qwenpaw-dsh"
TESTS_DIR = PACKAGE_DIR / "tests"

# Explicit whitelist. The first eleven files are the 226 documented safe tests;
# ``test_delivery_tools.py`` and ``test_event_mapper.py`` are checked below.
SAFE_TEST_FILES: tuple[str, ...] = (
    "test_permission_mode.py",
    "test_approval_port.py",
    "test_loader_wiring.py",
    "test_skills.py",
    "test_skills_shadow_security.py",
    "test_mcp.py",
    "test_images.py",
    "test_lifecycle.py",
    "test_model_contract.py",
    "test_host_contract.py",
    "test_file_projection_g4.py",
    "test_delivery_tools.py",
    "test_event_mapper.py",
    "test_version_gate.py",
)

EXPECTED_EXISTING = {
    "test_permission_mode.py": 9,
    "test_approval_port.py": 18,
    "test_loader_wiring.py": 2,
    "test_skills.py": 29,
    "test_skills_shadow_security.py": 12,
    "test_mcp.py": 31,
    "test_images.py": 26,
    "test_lifecycle.py": 42,
    "test_model_contract.py": 22,
    "test_host_contract.py": 8,
    "test_file_projection_g4.py": 27,
}
EXISTING_TOTAL = 226

# Files added after the original 226-test baseline. Kept separate so the
# documented existing-total guard stays exactly at 226.
EXPECTED_ADDED = {
    "test_event_mapper.py": 20,
    "test_version_gate.py": 6,
}

RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
FAILED_RE = re.compile(r"^FAILED\b.*$", re.MULTILINE)
OK_RE = re.compile(r"^OK\b.*$", re.MULTILINE)


def _temp_environment(python: str) -> tuple[dict[str, str], Path]:
    root = Path(tempfile.mkdtemp(prefix="qwenpaw-dsh-safe-tests-"))
    home = root / "home"
    for path in (
        home,
        root / "dsh",
        root / "qwenpaw-home",
        root / "qwenpaw-work",
        root / "tmp",
    ):
        path.mkdir(parents=True, exist_ok=True)
    python_dir = str(Path(python).resolve().parent)
    env = {
        "PATH": f"{python_dir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "DSH_HOME": str(root / "dsh"),
        "QWENPAW_HOME": str(root / "qwenpaw-home"),
        "QWENPAW_WORKING_DIR": str(root / "qwenpaw-work"),
        "TMPDIR": str(root / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
    }
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env, root


def _parse_counts(output: str) -> dict[str, int | str]:
    ran_match = RAN_RE.search(output)
    if not ran_match:
        return {"ran": 0, "failures": 0, "errors": 0, "skipped": 0,
                "status": "no-summary"}
    ran = int(ran_match.group(1))
    failed = FAILED_RE.search(output)
    ok = OK_RE.search(output)
    tail = failed.group(0) if failed else (ok.group(0) if ok else "")
    counts = {
        "ran": ran,
        "failures": int(re.search(r"failures=(\d+)", tail).group(1))
        if re.search(r"failures=(\d+)", tail) else 0,
        "errors": int(re.search(r"errors=(\d+)", tail).group(1))
        if re.search(r"errors=(\d+)", tail) else 0,
        "skipped": int(re.search(r"skipped=(\d+)", tail).group(1))
        if re.search(r"skipped=(\d+)", tail) else 0,
        "status": "failed" if failed else ("ok" if ok else "unknown"),
    }
    return counts


def _resolve_python(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(value)
    return found


def _expected_count(name: str) -> int | None:
    if name in EXPECTED_EXISTING:
        return EXPECTED_EXISTING[name]
    return EXPECTED_ADDED.get(name)


def _list_files() -> int:
    for name in SAFE_TEST_FILES:
        count = _expected_count(name)
        print(f"{name}\t{count if count is not None else '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run only the explicitly whitelisted offline qwenpaw-dsh tests"
    )
    parser.add_argument("--python", default=sys.executable,
                        help="existing interpreter to use (no installs)")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--only", action="append", default=[],
                        help="run one whitelisted file (repeatable, diagnostics only)")
    parser.add_argument("--list", action="store_true", help="list whitelist and counts")
    parser.add_argument("--allow-skips", action="store_true",
                        help="treat skips as green (default: exit 3)")
    parser.add_argument("--skip-count-check", action="store_true",
                        help="do not require the documented per-file counts")
    parser.add_argument("--timeout", type=int, default=600,
                        help="per-file timeout in seconds (default 600)")
    args = parser.parse_args(argv)

    if args.list:
        return _list_files()

    python = _resolve_python(args.python)
    if not python:
        print(f"ERROR: interpreter not found: {args.python}", file=sys.stderr)
        return 2

    selected = tuple(args.only) if args.only else SAFE_TEST_FILES
    unknown = [name for name in selected if name not in SAFE_TEST_FILES]
    if unknown:
        print(f"ERROR: refusing non-whitelisted test(s): {', '.join(unknown)}",
              file=sys.stderr)
        return 2

    repo_root = Path(args.repo_root).expanduser().resolve()
    tests_dir = repo_root / PACKAGE_DIR.name / "tests"
    if not tests_dir.is_dir():
        print(f"ERROR: tests directory not found: {tests_dir}", file=sys.stderr)
        return 2

    missing = [name for name in selected if not (tests_dir / name).is_file()]
    if missing:
        print(f"ERROR: whitelisted test file(s) missing: {', '.join(missing)}",
              file=sys.stderr)
        return 2

    env, temp_root = _temp_environment(python)
    totals = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0, "files": 0}
    ran_by_name: dict[str, int] = {}
    setup_errors = 0
    count_errors: list[str] = []
    skipped_files: list[str] = []
    failed = False

    print(f"interpreter: {python}")
    print(f"repo root:   {repo_root}")
    print(f"temp root:   {temp_root}")
    print(f"files:       {len(selected)}")
    print()

    try:
        for name in selected:
            print(f"=== {name} ===", flush=True)
            command = [
                python, "-m", "unittest", "discover",
                "-s", "tests", "-p", name, "-v",
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(repo_root / PACKAGE_DIR.name),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=args.timeout,
                )
            except subprocess.TimeoutExpired:
                print(f"ERROR: {name} timed out after {args.timeout}s", file=sys.stderr)
                setup_errors += 1
                failed = True
                continue
            if completed.stdout:
                print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            if completed.stderr:
                print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")

            combined = completed.stdout + "\n" + completed.stderr
            counts = _parse_counts(combined)
            totals["files"] += 1
            ran_by_name[name] = int(counts["ran"])
            for key in ("ran", "failures", "errors", "skipped"):
                totals[key] += int(counts[key])  # type: ignore[arg-type]

            status = counts["status"]
            expected = _expected_count(name)
            if (not args.skip_count_check and expected is not None
                    and int(counts["ran"]) != expected):
                count_errors.append(
                    f"{name}: ran {counts['ran']}, expected {expected}"
                )
                failed = True
            if completed.returncode != 0 or status != "ok":
                failed = True
            if int(counts["skipped"]) > 0:
                skipped_files.append(name)
            print(
                f"--- {name}: ran={counts['ran']} failures={counts['failures']} "
                f"errors={counts['errors']} skipped={counts['skipped']} "
                f"status={status} exit={completed.returncode}",
                flush=True,
            )
            print()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    print("=== summary ===")
    print(
        f"files={totals['files']} ran={totals['ran']} failures={totals['failures']} "
        f"errors={totals['errors']} skipped={totals['skipped']}"
    )
    if skipped_files:
        print(f"skipped files: {', '.join(skipped_files)}")
    if count_errors:
        for message in count_errors:
            print(f"count error: {message}", file=sys.stderr)
    if not args.only and not args.skip_count_check:
        existing_ran = sum(ran_by_name.get(name, 0) for name in EXPECTED_EXISTING)
        if existing_ran != EXISTING_TOTAL:
            count_errors.append(
                f"documented existing total: ran {existing_ran}, expected {EXISTING_TOTAL}"
            )
            print(
                f"count error: documented existing total ran {existing_ran}, "
                f"expected {EXISTING_TOTAL}",
                file=sys.stderr,
            )
            failed = True
        else:
            print(f"documented existing total: {EXISTING_TOTAL} OK")

    if setup_errors:
        print(f"RESULT: FAIL ({setup_errors} setup error(s))")
        return 2
    if failed:
        print("RESULT: FAIL")
        return 1
    if skipped_files and not args.allow_skips:
        print("RESULT: FAIL (skips are not green; pass --allow-skips to accept)")
        return 3
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
