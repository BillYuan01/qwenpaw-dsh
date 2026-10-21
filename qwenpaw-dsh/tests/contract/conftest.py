# -*- coding: utf-8 -*-
"""Shared fixtures for the formalized Phase-0 contract suite.

The pytest layer deliberately wraps the already-validated ``spikes/sNN_*.py``
runners instead of re-implementing their assertions: the spike files remain the
single source of truth for the acceptance criteria, and this suite adds
isolation, markers, and pytest-friendly failure reporting.

Run (pytest comes from the interpreter; an optional ``.vendor`` is used when
QwenPaw venv is read-only on this machine)::

    cd contract-tests
    ./run-contract.sh                 # profile=acp, seed=user
    DSH_CONTRACT_PROFILE=qwenpaw ./run-contract.sh
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import traceback
from argparse import Namespace
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Repo layout is plugin/ <- repo root <- bridge sibling: …/qwenpaw-dsh/tests/contract/
_REPO_ROOT = HERE.parents[2] if len(HERE.parents) > 2 else ROOT


def _default_bridge() -> Path:
    """Locate the bridge checkout without assuming a monorepo.

    Order: env (handled by the caller) → sibling of this repo → the old
    in-repo path. The sibling layout is what the standalone bridge repo uses
    once it is checked out next to this one.
    """
    for candidate in (
        _REPO_ROOT.parent / "dsh-qwenpaw-bridge",
        _REPO_ROOT / "dsh-qwenpaw-bridge",
    ):
        if (candidate / "package.json").is_file():
            return candidate
    return _REPO_ROOT / "dsh-qwenpaw-bridge"


BRIDGE = Path(os.environ.get("DSH_CONTRACT_BRIDGE", str(_default_bridge()))).expanduser().resolve()


def _bridge_name() -> str:
    """Read the package name from the bridge checkout (scope may change)."""
    try:
        return str(json.loads((BRIDGE / "package.json").read_text(encoding="utf-8"))["name"])
    except Exception:  # noqa: BLE001
        return "@local/dsh-qwenpaw-bridge"


BRIDGE_NAME = _bridge_name()
# The spike runners ship next to this file (ported into the plugin repo), so the
# contract suite is self-contained: `tests/contract/spikes/`.
SPIKES = HERE / "spikes"
VENDOR = HERE / ".vendor"  # optional: only if pytest is vendored here
for candidate in (str(VENDOR), str(SPIKES), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from spike_lib import Result, dsh_version, seed_dsh_home  # noqa: E402

PROFILE = os.environ.get("DSH_CONTRACT_PROFILE", "acp")
SEED = os.environ.get("DSH_CONTRACT_SEED", "user")
HOME = Path(os.environ.get("DSH_CONTRACT_HOME", str(HERE / ".contract-test" / "home"))).expanduser().resolve()
WORK = Path(os.environ.get("DSH_CONTRACT_WORK", str(HERE / ".contract-test" / "work"))).expanduser().resolve()


def pytest_configure(config):
    config.addinivalue_line("markers", "contract: end-to-end ACP contract test (spawns a real dsh process)")


def _ensure_qwenpaw_profile(home: Path) -> None:
    """Create the bridge-backed profile when the suite asks for it explicitly."""
    profile_dir = home / "profiles" / "qwenpaw"
    manifest_path = profile_dir / "package.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if BRIDGE_NAME in (manifest.get("dependencies") or {}):
                return
        except Exception:  # noqa: BLE001
            pass
    profile_dir.mkdir(parents=True, exist_ok=True)
    package_parts = BRIDGE_NAME.split("/")
    vendor = profile_dir / "node_modules"
    if len(package_parts) > 1:
        vendor = vendor / package_parts[0]
    vendor.mkdir(parents=True, exist_ok=True)
    link = profile_dir / "node_modules" / Path(*package_parts)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(BRIDGE)
    manifest = {
        "name": "dsh-profile-qwenpaw",
        "private": True,
        "dependencies": {BRIDGE_NAME: f"file:{BRIDGE}"},
        "dsh": {
            "profile": {
                "bundles": [
                    "@deepseek-ai/dsh-base",
                    "@deepseek-ai/dsh-acp-app",
                    BRIDGE_NAME,
                ]
            }
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    source_patch = Path.home() / ".dsh" / "profiles" / "acp" / "cordis.patch.yml"
    if source_patch.exists():
        (profile_dir / "cordis.patch.yml").write_text(source_patch.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        (profile_dir / "cordis.patch.yml").write_text("[]\n", encoding="utf-8")
    (profile_dir / "cordis.yml").write_text("[]\n", encoding="utf-8")


@pytest.fixture(scope="session")
def dsh_home() -> Path:
    if PROFILE == "qwenpaw":
        _ensure_qwenpaw_profile(HOME)
    descriptor = seed_dsh_home(HOME, SEED)
    if not (HOME / "profiles" / PROFILE).exists():
        pytest.skip(
            f"profile {PROFILE!r} is not available under {HOME}; "
            "set DSH_CONTRACT_HOME or DSH_CONTRACT_PROFILE",
        )
    return descriptor["home"]


@pytest.fixture
def spike_args(dsh_home) -> Namespace:
    return Namespace(
        profile=PROFILE,
        seed=SEED,
        dsh_home=str(dsh_home),
        keep=False,
        dry_run=False,
    )


@pytest.fixture
def work_root(request) -> Path:
    path = WORK / f"{request.node.name}-{os.getpid()}"
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_spike(module_name: str, args: Namespace, work: Path) -> Result:
    """Execute one spike runner in-process and return its structured Result."""
    module = importlib.import_module(module_name)
    result = Result(module_name, getattr(module, "__doc__", module_name))
    result.env = {
        "dsh_version": dsh_version(),
        "profile": args.profile,
        "seed": args.seed,
        "dsh_home": args.dsh_home,
        "python": sys.executable,
    }
    try:
        asyncio.run(module.runner(args, result, Path(args.dsh_home), work))
    except Exception:  # noqa: BLE001 - surface the original traceback to pytest
        result.raw["traceback"] = traceback.format_exc()
        result.check("spike completed without exception", False, "see traceback")
    return result


def assert_contract(result: Result, *, ignored: tuple[str, ...] = ()) -> None:
    """Fail on required regressions; pass with a warning when only model calls are pending."""
    failed = [c for c in result.checks if c.required and c.ok is False and c.name not in ignored]
    pending = [c for c in result.checks if c.required and c.ok is None and c.name not in ignored]
    passed = [c for c in result.checks if c.ok is True and c.name not in ignored]
    if failed:
        detail = "\n".join(f"  - {c.name}: {c.detail}" for c in failed)
        if "traceback" in result.raw:
            detail += "\n" + result.raw["traceback"]
        pytest.fail(f"{result.spike} required checks failed:\n{detail}")
    if pending and not passed:
        pytest.skip(f"{result.spike}: model-dependent checks pending ({len(pending)}): {[c.name for c in pending]}")
    if pending:
        import warnings
        warnings.warn(f"{result.spike}: {len(pending)} model-dependent check(s) pending: {[c.name for c in pending]}")


def require_check(result: Result, name: str):
    """Return one named check, failing loudly when the spike renamed/removed it."""
    for check in result.checks:
        if check.name == name:
            return check
    pytest.fail(f"{result.spike}: expected check {name!r}; got {[c.name for c in result.checks]}")
