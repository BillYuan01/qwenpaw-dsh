#!/usr/bin/env python3
"""Standalone live check for the gate-3 skills projection (no QwenPaw host).

Builds a temporary workspace whose ``skills/`` mixes kebab and snake_case
fixtures, then drives the real adapter end to end:

1. deterministic: composed ``--patch`` config carries ``customSkillDirs``;
2. live: the DSH model reports the catalog — kebab names present, the raw
   snake_case name absent;
3. live: loading ``snake-case-skill`` returns its body;
4. no-skills: no ``--patch`` is used and a plain turn still works.

Environment:
    DSH_INTEGRATION_PROFILE  dsh profile (default ``qwenpaw``)
    DSH_INTEGRATION_HOME     DSH_HOME (default ``~/.dsh``)

Run: ~/.qwenpaw/venv/bin/python tests/test_skills_projection.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qwenpaw_dsh.core import skills as sk  # noqa: E402
from qwenpaw_dsh.core.adapter import DshAdapter  # noqa: E402
from qwenpaw_dsh.host import HostContext  # noqa: E402

PROFILE = os.environ.get("DSH_INTEGRATION_PROFILE", "qwenpaw")
DSH_HOME = Path(os.environ.get("DSH_INTEGRATION_HOME", str(Path.home() / ".dsh")))
DSH_BIN = shutil.which("dsh") or str(Path.home() / ".local/bin/dsh")

FIXTURES = {
    "hello-kebab": (
        "---\n"
        "name: hello-kebab\n"
        'description: "Gate-3 fixture (kebab; no normalization needed)."\n'
        "---\n\n"
        "HELLO-KEBAB-OK\n"
    ),
    "snake_case_skill": (
        "---\n"
        "name: snake_case_skill\n"
        'description: "Gate-3 fixture (snake_case; must project as snake-case-skill)."\n'
        "---\n\n"
        "SNAKE-CASE-OK\n"
    ),
}

CATALOG_PROMPT = (
    "List every skill name in your available-skills catalog, one per line, "
    "exactly as written. If the catalog is empty, reply with the single word "
    "NONE. Then reply with the single word DONE."
)
LOAD_PROMPT = (
    "Call the skill tool with name 'snake-case-skill' and then reply with "
    "exactly the token you find in its instructions."
)

_results: list[tuple[str, bool | None, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    _results.append((name, ok, detail))
    marker = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{marker}] {name}" + (f" — {detail}" if detail else ""))


def credentials_present() -> bool:
    if (DSH_HOME / ".credentials.yaml").exists():
        return True
    return any(
        os.environ.get(key)
        for key in ("DEEPSEEK_API_KEY", "OPENCODE_GO_API_KEY", "ANTHROPIC_API_KEY")
    )


def composed_config_has_skills(patch: Path, workspace: Path) -> tuple[bool, bool, str]:
    env = dict(os.environ)
    env["DSH_HOME"] = str(DSH_HOME)
    out = subprocess.run(
        [DSH_BIN, "--profile", PROFILE, "--patch", str(patch), "--dump-config"],
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    index = out.stdout.find("id: skill-filesystem")
    block = out.stdout[index : index + 260] if index >= 0 else ""
    ok = out.returncode == 0 and "customSkillDirs" in block
    row = (
        f"id: {sk.SKILL_FILESYSTEM_ID}\n"
        "  name: '@deepseek-ai/dsh-skill-filesystem'"
    ) in out.stdout
    return ok, row, block.replace("\n", " | ")[:200]


def write_fixtures(root: Path) -> Path:
    skills_dir = root / "skills"
    for dirname, text in FIXTURES.items():
        directory = skills_dir / dirname
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return skills_dir


def fake_capabilities(skills_dir: Path) -> SimpleNamespace:
    skills = [
        SimpleNamespace(
            name=dirname,
            description=FIXTURES[dirname].splitlines()[2],
            directory=skills_dir / dirname,
            revision=sk._directory_revision(skills_dir / dirname),
        )
        for dirname in sorted(FIXTURES)
    ]
    return SimpleNamespace(skills=skills)


async def collect(adapter: DshAdapter, session_id: str, prompt: str, cwd: Path, settings: dict) -> tuple[str, str]:
    chunks: list[str] = []
    terminal = "none"
    async for event in adapter.run_turn(
        session_id=session_id,
        prompt=prompt,
        cwd=cwd,
        settings=settings,
    ):
        terminal = event.kind.value
        if terminal == "text_delta" and event.text:
            chunks.append(event.text)
    return "".join(chunks), terminal


async def main() -> int:
    if not Path(DSH_BIN).exists():
        print("dsh binary not found; cannot run the live integration check")
        return 2
    if not (DSH_HOME / "profiles" / PROFILE).exists():
        print(f"profile {PROFILE!r} missing under {DSH_HOME}; skipping")
        return 2
    if not credentials_present():
        print("no model credentials found; skipping the live check")
        return 2

    work = Path(tempfile.mkdtemp(prefix="gate3-skills-"))
    skills_dir = write_fixtures(work)
    state_dir = work / "harnesses"
    host = HostContext(state_dir=state_dir, binary=DSH_BIN, profile=PROFILE)
    settings = {"binary": DSH_BIN, "profile": PROFILE}
    adapter = DshAdapter(state_dir=state_dir, settings=settings, host=host)
    activation = {
        "_runtime_capabilities": fake_capabilities(skills_dir),
    }

    try:
        await adapter._refresh_skills(activation)
        plan = adapter._skills_plan
        print(f"[projection] {[e.target for e in plan.entries]}")
        ok, row, block = composed_config_has_skills(plan.patch_path, work)
        check("--patch overlay composes customSkillDirs", ok, block)
        check(
            f"dump-config skill row id is {sk.SKILL_FILESYSTEM_ID!r}",
            row,
        )
        check(
            "shadow SKILL.md rewritten before DSH sees it",
            "name: snake-case-skill" in (plan.shadow_root / "snake-case-skill" / "SKILL.md").read_text(),
        )
        check(
            "pool key carries skills snapshot",
            adapter._pool_key()[-1] == plan.snapshot and bool(plan.snapshot),
        )

        catalog, terminal = await collect(
            adapter, "gate3-catalog", CATALOG_PROMPT, work, activation,
        )
        print(f"[catalog] terminal={terminal} answer={catalog.strip()[:400]!r}")
        check("model lists kebab skill", "hello-kebab" in catalog)
        check("model lists projected snake_case skill", "snake-case-skill" in catalog)
        check("model does NOT list raw snake_case name", "snake_case_skill" not in catalog)
        check("catalog turn completed", terminal == "completed", terminal)

        load, terminal = await collect(
            adapter, "gate3-catalog", LOAD_PROMPT, work, activation,
        )
        print(f"[load] terminal={terminal} answer={load.strip()[-200:]!r}")
        check("skill body reached the model", "SNAKE-CASE-OK" in load)
        check("load turn completed", terminal == "completed", terminal)

        # -- no-skills path: plain behavior, no --patch --------------------
        pong, terminal = await collect(
            adapter, "gate3-noskills", "Reply with exactly: PONG", work, {},
        )
        check("no-skills turn works", "PONG" in pong and terminal == "completed", repr(pong[:80]))
        check("no-skills projection has no patch", adapter._skills_plan.patch_path is None)
        check("no patch file written", not (state_dir / "skills.patch.yml").exists())
        check("no-skills pool key is empty token", adapter._pool_key()[-1] == "")
    finally:
        await adapter.stop()

    failures = [name for name, ok, _ in _results if ok is False]
    skips = [name for name, ok, _ in _results if ok is None]
    print(
        f"\n[skills-projection] {len(_results) - len(failures) - len(skips)} passed, "
        f"{len(failures)} failed, {len(skips)} skipped",
    )
    if failures:
        for name in failures:
            print(f"  FAIL: {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
