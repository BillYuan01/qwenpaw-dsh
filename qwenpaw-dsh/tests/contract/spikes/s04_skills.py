# -*- coding: utf-8 -*-
"""Spike 04 — skills projection via ``customSkillDirs`` + SKILL.md compatibility.

Writes a patch overlay that adds a QwenPaw-style ``skills/`` root to DSH's
``skill-filesystem`` provider, then checks (a) the overlay reaches the composed
config and (b) which SKILL.md files the model actually sees in its catalog.

The interesting question is the *name grammar*: DSH requires kebab-case
``[a-z0-9]+(-[a-z0-9]+)*`` (``@deepseek-ai/dsh-skill`` SKILL_NAME), while
QwenPaw's skill pool mixes ``snake_case`` and kebab-case.  This spike encodes
one skill of each shape so the drop is observable, and additionally runs the
*actual* ``isSkillName`` from the installed ``@deepseek-ai/dsh-skill`` through
Node — so the grammar verdict does not depend on a live model call.

Run:
    ~/.qwenpaw/venv/bin/python spikes/s04_skills.py
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from spike_lib import (
    DSH_BIN,
    Result,
    SpikeClient,
    dsh_env,
    dsh_package_entry,
    initialize,
    open_acp,
    run_spike,
    safe_prompt,
    text_block,
)

KEBAB_TOKEN = "KEBAB-TOKEN-1"
UNDERSCORE_TOKEN = "UNDERSCORE-TOKEN-2"

SKILLS = {
    "spike-kebab-case": (
        "---\n"
        "name: spike-kebab-case\n"
        'description: "DSH skills-projection probe. Load it when asked about spike-kebab-case."\n'
        "---\n\n"
        "# Kebab skill\n\n"
        f"The secret token is {KEBAB_TOKEN}.\n"
    ),
    "spike_underscore": (
        "---\n"
        "name: spike_underscore\n"
        'description: "QwenPaw-style snake_case skill name, expected to be rejected by DSH."\n'
        "---\n\n"
        "# Underscore skill\n\n"
        f"The secret token is {UNDERSCORE_TOKEN}.\n"
    ),
    "spike-qwenpaw-metadata": (
        "---\n"
        "name: spike-qwenpaw-metadata\n"
        'description: "QwenPaw-shaped frontmatter with a metadata.qwenpaw block (kebab name)."\n'
        "metadata:\n"
        '  builtin_skill_version: "1.2"\n'
        "  qwenpaw:\n"
        '    emoji: "\U0001F9EA"\n'
        "    requires: {}\n"
        "---\n\n"
        "# QwenPaw-shaped skill\n\n"
        "The secret token is QWENPAW-META-3.\n"
    ),
}


def write_skill(root: Path, dirname: str, text: str) -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def copy_real_skills(root: Path, result: Result) -> list[str]:
    """Copy two real QwenPaw skills to measure the real-world drop."""
    pool = Path.home() / ".qwenpaw" / "skill_pool"
    copied: list[str] = []
    for name in ("company-background-check", "browser_visible"):
        src = pool / name
        if (src / "SKILL.md").exists():
            dst = root / name
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "SKILL.md").write_text((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"),
                                          encoding="utf-8")
            copied.append(name)
    if not copied:
        result.note("~/.qwenpaw/skill_pool not available; real-skill copy skipped")
    return copied


def check_skill_name_grammar(result: Result) -> None:
    """Run the installed dsh-skill ``isSkillName`` through Node (no model needed)."""
    names = ["spike-kebab-case", "spike-qwenpaw-metadata", "spike_underscore",
             "browser_visible", "company-background-check", "Spike-Uppercase"]
    entry = dsh_package_entry("dsh-skill")
    if entry is None:
        result.check("deterministic skill-name grammar probe (node)", None,
                     "dsh-skill entry not found next to the dsh binary", required=False)
        return
    script = (
        f"import('{entry.as_uri()}').then(m => "
        f"console.log(JSON.stringify({json.dumps(names)}.map(n => [n, m.isSkillName(n)]))));"
    )
    try:
        out = subprocess.run(["node", "--input-type=module", "-e", script],
                             capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        result.check("deterministic skill-name grammar probe (node)", None, repr(exc), required=False)
        return
    verdict = {name: ok for name, ok in data}
    result.observe("dsh_isSkillName", verdict)
    expected = {
        "spike-kebab-case": True,
        "spike-qwenpaw-metadata": True,
        "spike_underscore": False,
        "browser_visible": False,
        "company-background-check": True,
        "Spike-Uppercase": False,
    }
    result.check(
        "skill-name grammar is kebab-case only: /^[a-z0-9]+(?:-[a-z0-9]+)*$/",
        verdict == expected, f"isSkillName={verdict}",
    )


def check_dump_config(home: Path, ws: Path, overlay: Path, profile: str, result: Result) -> None:
    out = subprocess.run(
        [DSH_BIN, "--profile", profile, "--patch", str(overlay), "--dump-config"],
        cwd=str(ws), env=dsh_env(home), capture_output=True, text=True, timeout=120,
    )
    text = out.stdout
    result.raw["dump_config_exit"] = out.returncode
    result.check("--dump-config with skills overlay composes successfully", out.returncode == 0,
                 (out.stderr or "")[:200])
    idx = text.find("skill-filesystem")
    block = text[idx: idx + 400] if idx >= 0 else ""
    result.observe("skill_filesystem_config_block", block)
    result.check("composed config carries customSkillDirs", "customSkillDirs" in block, block[:160])


async def runner(args, result: Result, home: Path, work: Path) -> None:
    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    skills_root = work / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    for dirname, text in SKILLS.items():
        write_skill(skills_root, dirname, text)
    copied = copy_real_skills(skills_root, result)
    result.observe("skills_root", str(skills_root))
    result.observe("skills_written", sorted(SKILLS) + copied)

    overlay = work / "qwenpaw-skills.yml"
    overlay.write_text(
        "- id: skill-filesystem\n"
        "  config:\n"
        "    customSkillDirs:\n"
        f"      - '{skills_root}'\n",
        encoding="utf-8",
    )
    result.observe("overlay", overlay.read_text(encoding="utf-8"))

    check_dump_config(home, ws, overlay, args.profile, result)
    check_skill_name_grammar(result)

    client = SpikeClient()
    async with open_acp(
        home=home, workdir=ws, client=client, profile=args.profile, patches=[overlay],
    ) as (conn, process):
        await initialize(conn)
        new = await conn.new_session(cwd=str(ws))
        sid = new.session_id

        response = await safe_prompt(conn, session_id=sid, result=result, label="catalog prompt", prompt=[text_block(
            "List every skill name in your available-skills catalog, one per line, "
            "exactly as written. If the catalog is empty, reply with the single word NONE. "
            "Then reply with the single word DONE.",
        )])
        catalog_answer = client.assistant_text()
        result.observe("catalog_answer", catalog_answer.strip()[:1200])
        model_ok = response is not None

        def present_check(name: str, skill: str, expect: bool, detail: str) -> None:
            if not model_ok:
                result.check(name, None, "model unavailable (billing/quota)", required=False)
                return
            is_present = skill in catalog_answer
            result.check(name, is_present == expect, f"present={is_present}; {detail}")

        present_check("catalog lists kebab-case skill", "spike-kebab-case", True,
                      "frontmatter minimal, valid name")
        present_check("catalog lists QwenPaw-metadata kebab skill", "spike-qwenpaw-metadata", True,
                      "metadata.qwenpaw block tolerated")
        present_check("catalog DROPS snake_case name", "spike_underscore", False,
                      "DSH grammar rejects '_'")
        if "browser_visible" in copied:
            present_check("catalog DROPS real QwenPaw snake_case skill", "browser_visible", False,
                          "real-world impact sample")
        if "company-background-check" in copied:
            present_check("catalog lists real QwenPaw kebab skill", "company-background-check", True,
                          "real-world positive sample")

        # load the valid skill and read its body through the skill tool
        before_tools = len(client.tool_calls)
        await safe_prompt(conn, session_id=sid, result=result, label="skill-load prompt", prompt=[text_block(
            "Call the skill tool with name 'spike-kebab-case' and then reply with exactly the "
            "secret token found in its instructions.",
        )])
        load_answer = client.assistant_text()
        result.observe("load_answer", load_answer.strip()[-600:])
        result.observe("tool_calls", client.tool_call_summary())
        if model_ok:
            result.check("skill tool was invoked", any(
                (t.get("title") or "").lower().startswith("skill") for t in client.tool_call_summary()
            ) or len(client.tool_calls) > before_tools, f"tools={[t['title'] for t in client.tool_call_summary()]}")
            result.check("loaded skill body reached the model (token recalled)",
                         KEBAB_TOKEN in load_answer, repr(load_answer[-160:].strip()))
        else:
            result.check("skill tool was invoked", None, "model unavailable", required=False)
            result.check("loaded skill body reached the model", None, "model unavailable", required=False)

        await conn.close_session(session_id=sid)


if __name__ == "__main__":
    raise SystemExit(run_spike(
        "s04_skills",
        "skills projection (customSkillDirs) + SKILL.md format/name-grammar compatibility",
        runner,
    ))
