# -*- coding: utf-8 -*-
"""Project QwenPaw workspace skills into a DSH-compatible shadow tree.

Why this exists
---------------
DSH's ``skill-filesystem`` provider only accepts a skill when its name matches
``^[a-z0-9]+(?:-[a-z0-9]+)*$`` and it takes that name from the **SKILL.md
frontmatter**, not the directory (gate-3 probe, DSH 0.2.0-rc.2). QwenPaw's
skill pool mixes ``snake_case`` and kebab-case, so a snake_case skill is
silently dropped. This module fixes that:

* :func:`to_kebab` normalizes every source name (pure);
* collisions resolve deterministically in source-name order with ``-2``/``-3``;
* :func:`project_skills` materializes ``<state_dir>/skills_shadow/<kebab>/``
  where ``SKILL.md`` carries the rewritten kebab name while every other
  resource is symlinked (copy fallback) so relative references keep working;
* it writes ``<state_dir>/skills_map.json`` (kebab -> source, the mapping) and
  ``<state_dir>/skills.patch.yml`` (``customSkillDirs``) for the adapter to
  pass to ``dsh --patch``.

Only :func:`project_skills` touches the filesystem and it is idempotent: a
skill whose host ``revision`` (content hash) is unchanged is not rebuilt.

This module imports nothing from QwenPaw — the host skill definitions are
consumed duck-typed (pydantic model or plain ``dict``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("qwenpaw.dsh")

# DSH ``@deepseek-ai/dsh-skill`` SKILL_NAME: /^[a-z0-9]+(?:-[a-z0-9]+)*$/
KEBAB_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# The ``skill-filesystem`` row id in ``dsh --profile qwenpaw --dump-config``
# (verified against DSH 0.2.0-rc.2; the patch overlay merges by id).
SKILL_FILESYSTEM_ID = "skill-filesystem"

SHADOW_DIRNAME = "skills_shadow"
MAP_FILENAME = "skills_map.json"
PATCH_FILENAME = "skills.patch.yml"
SKILL_FILENAME = "SKILL.md"
MANIFEST_FILENAME = ".qwenpaw_projection.json"

_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)


# --------------------------------------------------------------------------- names
def to_kebab(name: str) -> str:
    """Normalize ``name`` to DSH's kebab grammar.

    Lower-case, every char outside ``[a-z0-9]`` becomes ``-``, runs of ``-``
    collapse, leading/trailing ``-`` are stripped. An all-symbol name (for
    example all-CJK) yields ``""`` and must be skipped by the caller.
    """
    lowered = str(name or "").lower()
    dashed = "".join(
        char if ("a" <= char <= "z" or "0" <= char <= "9") else "-"
        for char in lowered
    )
    return re.sub(r"-+", "-", dashed).strip("-")


def is_kebab(name: str) -> bool:
    return bool(KEBAB_NAME_RE.match(name or ""))


# --------------------------------------------------------------------------- host input
@dataclass(frozen=True)
class SkillSource:
    """One QwenPaw skill as reported through ``_runtime_capabilities``."""

    name: str
    directory: Path
    revision: str = ""
    description: str = ""


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def skill_sources(capabilities: Any) -> list[SkillSource]:
    """Extract skill definitions from a runtime-capabilities object or dict."""
    if capabilities is None:
        return []
    raw_skills = _field(capabilities, "skills")
    if not isinstance(raw_skills, (list, tuple)):
        return []
    sources: list[SkillSource] = []
    for raw in raw_skills:
        name = str(_field(raw, "name") or "").strip()
        directory = _field(raw, "directory")
        if not name or directory is None:
            continue
        sources.append(
            SkillSource(
                name=name,
                directory=Path(str(directory)),
                revision=str(_field(raw, "revision") or ""),
                description=str(_field(raw, "description") or ""),
            ),
        )
    return sources


def runtime_skills(settings: dict[str, Any] | None) -> list[SkillSource]:
    """Skills carried by ``settings["_runtime_capabilities"]`` for one turn."""
    return skill_sources((settings or {}).get("_runtime_capabilities"))


# --------------------------------------------------------------------------- plan
@dataclass(frozen=True)
class SkillEntry:
    """One source skill resolved to its unique kebab projection."""

    source: SkillSource
    target: str          # kebab (unique)
    suffix: int = 0      # 0 = base name, n = "-n" appended

    @property
    def shadow_name(self) -> str:
        return self.target


def plan_skills(
    sources: Iterable[SkillSource],
) -> tuple[list[SkillEntry], list[tuple[str, str]]]:
    """Resolve kebab targets + collisions deterministically.

    Returns ``(entries, skipped)`` where ``skipped`` holds
    ``(source_name, reason)``. Sorting by source name makes the outcome stable
    regardless of host ordering.
    """
    entries: list[SkillEntry] = []
    skipped: list[tuple[str, str]] = []
    taken: set[str] = set()
    for source in sorted(sources, key=lambda item: item.name):
        base = to_kebab(source.name)
        if not base:
            skipped.append((source.name, "empty-kebab"))
            continue
        if base not in taken:
            target, suffix = base, 0
        else:
            suffix = 2
            while f"{base}-{suffix}" in taken:
                suffix += 1
            target = f"{base}-{suffix}"
        taken.add(target)
        entries.append(SkillEntry(source=source, target=target, suffix=suffix))
    return entries, skipped


def plan_token(entries: Iterable[SkillEntry]) -> str:
    """Stable pool snapshot hash: kebab name + source revision (``""`` if none)."""
    ordered = sorted(entries, key=lambda item: item.target)
    if not ordered:
        return ""
    digest = hashlib.sha256()
    for entry in ordered:
        revision = entry.source.revision or _directory_revision(entry.source.directory)
        digest.update(entry.target.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.source.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(revision.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# --------------------------------------------------------------------------- result
@dataclass(frozen=True)
class SkillProjection:
    """Outcome of one :func:`project_skills` call."""

    entries: tuple[SkillEntry, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    snapshot: str = ""
    shadow_root: Path | None = None
    map_path: Path | None = None
    patch_path: Path | None = None
    materialized: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def has_skills(self) -> bool:
        return bool(self.entries)

    def command_args(self) -> list[str]:
        """Extra ``dsh`` args for this projection (empty when no skills)."""
        if self.patch_path is None:
            return []
        return ["--patch", str(self.patch_path)]


# --------------------------------------------------------------------------- projection
def shadow_root(state_dir: Path) -> Path:
    return Path(state_dir) / SHADOW_DIRNAME


def map_path(state_dir: Path) -> Path:
    return Path(state_dir) / MAP_FILENAME


def patch_path(state_dir: Path) -> Path:
    return Path(state_dir) / PATCH_FILENAME


def project_skills(state_dir: Path, sources: Iterable[SkillSource]) -> SkillProjection:
    """Materialize the shadow tree + mapping + patch for ``sources``.

    Idempotent by revision. Never raises for per-skill issues (a missing
    ``SKILL.md`` is skipped); filesystem errors propagate so the adapter can
    fail soft at its own seam.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    entries, skipped = plan_skills(sources)
    map_file = map_path(state_dir)
    patch_file = patch_path(state_dir)
    root = shadow_root(state_dir)

    usable: list[SkillEntry] = []
    for entry in entries:
        source_md = entry.source.directory / SKILL_FILENAME
        if not source_md.is_file():
            log.warning(
                "dsh: skill %r has no SKILL.md at %s; skipped",
                entry.source.name,
                entry.source.directory,
            )
            skipped.append((entry.source.name, "missing-skill-md"))
            continue
        usable.append(entry)

    snapshot = plan_token(usable)
    if not usable:
        _remove_path(patch_file)
        _write_json(map_file, {})
        removed = _reconcile(root, {}, keep_manifest=False)
        return SkillProjection(
            entries=(),
            skipped=tuple(skipped),
            snapshot=snapshot,
            shadow_root=root,
            map_path=map_file,
            patch_path=None,
            removed=tuple(removed),
        )

    root.mkdir(parents=True, exist_ok=True)
    manifest = _load_json(root / MANIFEST_FILENAME)
    if not isinstance(manifest, dict):
        manifest = {}

    materialized: list[str] = []
    reused: list[str] = []
    active: dict[str, dict[str, Any]] = {}
    for entry in usable:
        revision = entry.source.revision or _directory_revision(entry.source.directory)
        dest = root / entry.target
        record = {
            "source_name": entry.source.name,
            "source_dir": str(entry.source.directory),
            "revision": revision,
        }
        active[entry.target] = record
        previous = manifest.get(entry.target)
        if (
            isinstance(previous, dict)
            and previous.get("revision") == revision
            and (dest / SKILL_FILENAME).is_file()
        ):
            reused.append(entry.target)
            continue
        _materialize(entry, dest)
        materialized.append(entry.target)
        log.info("dsh: projected skill %s -> %s", entry.source.name, entry.target)

    removed = _reconcile(root, active, manifest=manifest)
    _write_json(root / MANIFEST_FILENAME, active)
    _write_json(
        map_file,
        {
            entry.target: {
                "source_name": entry.source.name,
                "source_dir": str(entry.source.directory),
                "suffix": entry.suffix,
            }
            for entry in usable
        },
    )
    _write_patch(patch_file, root)

    return SkillProjection(
        entries=tuple(usable),
        skipped=tuple(skipped),
        snapshot=snapshot,
        shadow_root=root,
        map_path=map_file,
        patch_path=patch_file,
        materialized=tuple(materialized),
        reused=tuple(reused),
        removed=tuple(removed),
    )


def _materialize(entry: SkillEntry, dest: Path) -> None:
    """Write a real (rewritten) SKILL.md and link every sibling resource."""
    if dest.exists() or dest.is_symlink():
        _remove_path(dest)
    dest.mkdir(parents=True)
    source_md = entry.source.directory / SKILL_FILENAME
    text = source_md.read_text(encoding="utf-8", errors="replace")
    rewritten = rewrite_skill_name(text, entry.target)
    (dest / SKILL_FILENAME).write_text(rewritten, encoding="utf-8")
    for child in sorted(entry.source.directory.iterdir(), key=lambda item: item.name):
        if child.name == SKILL_FILENAME:
            continue
        target = dest / child.name
        try:
            target.symlink_to(child, target_is_directory=child.is_dir())
        except OSError:  # noqa: BLE001 — filesystems without symlinks: copy
            if child.is_dir():
                shutil.copytree(child, target, symlinks=True)
            else:
                shutil.copy2(child, target)


def rewrite_skill_name(text: str, new_name: str) -> str:
    """Replace the frontmatter ``name`` with ``new_name`` (add if absent)."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return f"---\nname: {new_name}\n---\n\n{text}"
    block = match.group("body")
    lines = block.split("\n")
    replaced = False
    for index, line in enumerate(lines):
        if re.match(r"^name\s*:", line):
            lines[index] = f"name: {new_name}"
            replaced = True
            break
    if not replaced:
        lines.insert(0, f"name: {new_name}")
    return text[: match.start("body")] + "\n".join(lines) + text[match.end("body") :]


def _reconcile(
    root: Path,
    active: dict[str, dict[str, Any]],
    *,
    manifest: dict[str, Any] | None = None,
    keep_manifest: bool = True,
) -> list[str]:
    """Drop shadow dirs we previously created that are no longer active."""
    if manifest is None:
        manifest = _load_json(root / MANIFEST_FILENAME)
    if not isinstance(manifest, dict):
        manifest = {}
    removed: list[str] = []
    for name in sorted(set(manifest) - set(active)):
        stale = root / name
        if stale.is_dir() or stale.is_symlink():
            _remove_path(stale)
            removed.append(name)
    if not keep_manifest:
        _remove_path(root / MANIFEST_FILENAME)
    return removed


# --------------------------------------------------------------------------- files
def _write_patch(path: Path, root: Path) -> None:
    quoted = str(root).replace("'", "''")
    text = (
        "# Generated by qwenpaw-dsh: project QwenPaw workspace skills into DSH.\n"
        f"- id: {SKILL_FILESYSTEM_ID}\n"
        "  config:\n"
        "    customSkillDirs:\n"
        f"      - '{quoted}'\n"
    )
    _write_text(path, text)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 — corrupt bookkeeping must not break projection
        log.warning("dsh: unreadable %s; rebuilding", path, exc_info=True)
        return {}


def _remove_path(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- revision
def _directory_revision(directory: Path) -> str:
    """Content hash fallback when the host did not provide a revision."""
    digest = hashlib.sha256()
    try:
        paths = sorted(
            (item for item in directory.rglob("*") if item.is_file() and not item.is_symlink()),
            key=lambda item: item.relative_to(directory).as_posix(),
        )
    except OSError:
        return ""
    for path in paths:
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except OSError:
            return ""
    return digest.hexdigest()


# --------------------------------------------------------------------------- mapping helpers
def load_map(state_dir: Path) -> dict[str, dict[str, Any]]:
    """Read ``skills_map.json`` (kebab -> source metadata)."""
    payload = _load_json(map_path(state_dir))
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }


def target_for(mapping: dict[str, dict[str, Any]], source_name: str) -> str | None:
    """Reverse lookup: source skill name -> projected kebab name."""
    for target, record in mapping.items():
        if str(record.get("source_name")) == source_name:
            return target
    return None
