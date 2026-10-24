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
* :func:`project_skills` materializes one **content-addressed snapshot** per
  distinct skills input under
  ``<state_dir>/skills_snapshots/<snapshot>/skills_shadow/<kebab>/``. ``SKILL.md``
  carries the rewritten kebab name while every other resource is symlinked
  (copy fallback) so relative references keep working. A snapshot id covers the
  kebab target, source name, revision and normalized source directory, so a
  moved source or changed content never reuses a stale link;
* it writes ``<snapshot>/skills_map.json`` and ``<snapshot>/skills.patch.yml``
  (``customSkillDirs`` pointing at that snapshot) for the adapter to pass to
  ``dsh --patch``; ``<state_dir>/skills_map.json`` keeps the most recent mapping
  for compatibility;
* it never deletes another snapshot. Old snapshots are intentionally not
  garbage-collected: there is no reference count or background job, so the
  directory may grow until the operator removes old ids. Source resources are
  still symlinks into the live workspace, so a user editing a source file is
  visible to DSH immediately — this module does not claim immutable sources.

Only :func:`project_skills` touches the filesystem and it is idempotent for the
same snapshot: an unchanged source is not rebuilt.

This module imports nothing from QwenPaw — the host skill definitions are
consumed duck-typed (pydantic model or plain ``dict``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import ntpath
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

log = logging.getLogger("qwenpaw.dsh")

# DSH ``@deepseek-ai/dsh-skill`` SKILL_NAME: /^[a-z0-9]+(?:-[a-z0-9]+)*$/
KEBAB_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# The ``skill-filesystem`` row id in ``dsh --profile qwenpaw --dump-config``
# (verified against DSH 0.2.0-rc.2; the patch overlay merges by id).
SKILL_FILESYSTEM_ID = "skill-filesystem"

SHADOW_DIRNAME = "skills_shadow"
SNAPSHOTS_DIRNAME = "skills_snapshots"
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


class UnsafeShadowTree(RuntimeError):
    """The shadow root is not a plain directory, so projection is refused.

    Raised instead of writing through a symlink or special file. The adapter
    treats a projection failure as "continue without skills", so this is a
    visible, fail-closed refusal rather than a silent partial projection.
    """


class UnsafeFrontmatter(ValueError):
    """The SKILL.md frontmatter cannot be rewritten without guessing.

    Raised for invalid YAML, a non-mapping root, a non-scalar ``name`` or more
    than one top-level ``name``. The caller skips that skill and reports the
    reason instead of publishing a projection whose name is ambiguous.
    """


# Characters that can never appear in a single shadow entry name. The kebab
# grammar already excludes them; the explicit list keeps the intent readable
# and survives a future grammar change.
_FORBIDDEN_NAME_CHARS = ("/", "\\", "\x00")


def is_plain_shadow_name(name: Any) -> bool:
    """Whether ``name`` is one single-layer kebab component under the root.

    This is the whitelist for ``.qwenpaw_projection.json`` keys. Rejecting
    everything else (``..``/``.``/empty strings, path separators, absolute
    paths, Windows drive/UNC forms) is what keeps stale cleanup from becoming
    a delete primitive outside the shadow tree.
    """
    if not isinstance(name, str) or not name:
        return False
    if name in (".", ".."):
        return False
    if any(char in name for char in _FORBIDDEN_NAME_CHARS):
        return False
    if os.path.isabs(name) or ntpath.splitdrive(name)[0]:
        return False
    return is_kebab(name)


def _shadow_root_state(root: Path) -> str:
    """Classify the shadow root without following symlinks.

    ``lstat`` (not ``stat``) is deliberate: a symlinked or replaced root must be
    detected as such, never resolved into its target.
    """
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "other"
    if stat.S_ISLNK(info.st_mode):
        return "link"
    return "real" if stat.S_ISDIR(info.st_mode) else "other"


def _warn_rejected_entry(name: Any) -> None:
    """Warn about an unusable manifest key without echoing attacker paths."""
    digest = hashlib.sha256(str(name).encode("utf-8", "replace")).hexdigest()[:8]
    log.warning(
        "dsh: refusing unusable skills manifest entry (sha256:%s, type=%s); "
        "only single-layer kebab names are cleaned up",
        digest,
        type(name).__name__,
    )


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


def _normalized_source_dir(directory: Path) -> str:
    """Absolute, lexically normalized source path (symlinks are not resolved).

    The path is part of the snapshot id and the pool token, so moving a skill
    directory — even with an unchanged host revision — produces a new snapshot
    and new resource links. ``abspath``/``normpath`` deliberately do not call
    ``realpath``: a source symlink must not be resolved into an outside tree
    that cleanup could then be pointed at.
    """
    return os.path.normpath(os.path.abspath(str(directory)))


def _entry_revision(entry: SkillEntry) -> str:
    return entry.source.revision or _directory_revision(entry.source.directory)


def snapshot_id(entries: Iterable[SkillEntry]) -> str:
    """Deterministic id for one projection input.

    Covers the target name, source name, revision (host value or bounded
    content hash) and normalized source directory. ``""`` means "no skills"
    and is the empty pool token.
    """
    ordered = sorted(entries, key=lambda item: item.target)
    if not ordered:
        return ""
    digest = hashlib.sha256()
    digest.update(b"qwenpaw-dsh/skills-snapshot/v2\0")
    for entry in ordered:
        for value in (
            entry.target,
            entry.source.name,
            _entry_revision(entry),
            _normalized_source_dir(entry.source.directory),
        ):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        digest.update(b"\n")
    return digest.hexdigest()


def plan_token(entries: Iterable[SkillEntry]) -> str:
    """Pool snapshot token; same inputs as :func:`snapshot_id`."""
    return snapshot_id(entries)


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
def snapshots_root(state_dir: Path) -> Path:
    return Path(state_dir) / SNAPSHOTS_DIRNAME


def snapshot_dir(state_dir: Path, snapshot: str) -> Path:
    return snapshots_root(state_dir) / snapshot


def map_path(state_dir: Path) -> Path:
    """Most recent mapping (compatibility view, not a run-time input)."""
    return Path(state_dir) / MAP_FILENAME


def _ensure_snapshot_dir(state_dir: Path, snapshot: str) -> Path:
    """Create/verify ``skills_snapshots/<snapshot>`` without following links."""
    if not snapshot or not re.fullmatch(r"[0-9a-f]{64}", snapshot):
        raise UnsafeShadowTree("skills snapshot id is not a sha256 hex digest")
    base = snapshots_root(state_dir)
    base_state = _shadow_root_state(base)
    if base_state not in ("absent", "real"):
        raise UnsafeShadowTree(f"skills snapshot root is not a plain directory: {base}")
    base.mkdir(parents=True, exist_ok=True)
    target = snapshot_dir(state_dir, snapshot)
    target_state = _shadow_root_state(target)
    if target_state not in ("absent", "real"):
        raise UnsafeShadowTree(f"skills snapshot is not a plain directory: {snapshot}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def project_skills(state_dir: Path, sources: Iterable[SkillSource]) -> SkillProjection:
    """Materialize one isolated snapshot for ``sources``.

    Each distinct input (target/revision/normalized source path) gets its own
    ``skills_snapshots/<id>/`` directory, so a concurrent turn with different
    skills can never rewrite or remove this turn's resources. A repeated call
    with the same input reuses that snapshot.

    Never raises for per-skill issues (missing/invalid ``SKILL.md`` is skipped
    with a reason); filesystem errors propagate so the adapter can fail soft at
    its own seam. No snapshot is ever deleted here, including for an empty
    input: the caller may still have a running process reading an older one.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    entries, skipped = plan_skills(sources)
    map_file = map_path(state_dir)

    prepared: list[tuple[SkillEntry, str]] = []
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
        try:
            original = source_md.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            log.warning("dsh: skill %r is unreadable; skipped", entry.source.name)
            skipped.append((entry.source.name, "unreadable-skill-md"))
            continue
        try:
            rewritten = rewrite_skill_name(original, entry.target)
        except UnsafeFrontmatter as error:
            # Never project a name that YAML would interpret differently.
            log.warning(
                "dsh: skill %r has unusable frontmatter (%s); skipped",
                entry.source.name,
                error,
            )
            skipped.append((entry.source.name, "invalid-frontmatter"))
            continue
        prepared.append((entry, rewritten))

    usable = [entry for entry, _ in prepared]
    snapshot = snapshot_id(usable)
    if not usable:
        # Compatibility view only. Do not touch any snapshot directory: an
        # empty turn must not clear skills another active turn is using.
        _write_json(map_file, {})
        return SkillProjection(
            entries=(),
            skipped=tuple(skipped),
            snapshot="",
            shadow_root=None,
            map_path=map_file,
            patch_path=None,
            removed=(),
        )

    snapshot_path = _ensure_snapshot_dir(state_dir, snapshot)
    root = snapshot_path / SHADOW_DIRNAME
    snap_map_file = snapshot_path / MAP_FILENAME
    patch_file = snapshot_path / PATCH_FILENAME

    # Never write through a symlinked or otherwise replaced shadow root. The
    # check is a plain lstat, so a link is refused instead of resolved.
    root_state = _shadow_root_state(root)
    if root_state not in ("absent", "real"):
        raise UnsafeShadowTree(f"skills shadow root is not a plain directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    manifest = _load_json(root / MANIFEST_FILENAME)
    if not isinstance(manifest, dict):
        manifest = {}

    materialized: list[str] = []
    reused: list[str] = []
    active: dict[str, dict[str, Any]] = {}
    mapping: dict[str, dict[str, Any]] = {}
    for entry, rewritten in prepared:
        revision = _entry_revision(entry)
        source_dir = _normalized_source_dir(entry.source.directory)
        dest = root / entry.target
        record = {
            "source_name": entry.source.name,
            "source_dir": source_dir,
            "revision": revision,
            "snapshot": snapshot,
        }
        active[entry.target] = record
        mapping[entry.target] = {
            "source_name": entry.source.name,
            "source_dir": source_dir,
            "suffix": entry.suffix,
        }
        previous = manifest.get(entry.target)
        if (
            isinstance(previous, dict)
            and previous.get("revision") == revision
            and previous.get("source_dir") == source_dir
            and (dest / SKILL_FILENAME).is_file()
            and _resources_current(entry, dest)
        ):
            reused.append(entry.target)
            continue
        _materialize(entry, dest, rewritten)
        materialized.append(entry.target)
        log.info("dsh: projected skill %s -> %s", entry.source.name, entry.target)

    removed = _reconcile(root, active, manifest=manifest)
    _write_json(root / MANIFEST_FILENAME, active)
    _write_json(snap_map_file, mapping)
    _write_json(map_file, mapping)
    _write_patch(patch_file, root)

    return SkillProjection(
        entries=tuple(usable),
        skipped=tuple(skipped),
        snapshot=snapshot,
        shadow_root=root,
        map_path=snap_map_file,
        patch_path=patch_file,
        materialized=tuple(materialized),
        reused=tuple(reused),
        removed=tuple(removed),
    )


def _resources_current(entry: SkillEntry, dest: Path) -> bool:
    """Whether the projected sibling resources still mirror the source tree.

    Reuse is keyed by revision + source path, but the host revision can be
    stale or coarse. Comparing the sibling name sets and requiring every
    projected link to resolve catches added/removed resources (and a moved or
    deleted source target) without hashing file contents on every turn.
    """
    try:
        source_names = {
            child.name
            for child in entry.source.directory.iterdir()
            if child.name != SKILL_FILENAME
        }
        projected = {child.name for child in dest.iterdir() if child.name != SKILL_FILENAME}
    except OSError:
        return False
    if source_names != projected:
        return False
    for name in projected:
        target = dest / name
        if target.is_symlink() and not target.exists():
            return False
    return True


def _materialize(entry: SkillEntry, dest: Path, rewritten: str) -> None:
    """Write the pre-verified SKILL.md and link every sibling resource."""
    # A leftover entry (including a symlink pointing outside the tree) is
    # unlinked/replaced here, never traversed.
    _remove_shadow_child(dest)
    dest.mkdir(parents=True)
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


def _top_level_name_nodes(root: yaml.Node | None) -> list[tuple[yaml.Node, yaml.Node]]:
    if root is None:
        return []
    if not isinstance(root, yaml.MappingNode):
        raise UnsafeFrontmatter("frontmatter-root-not-mapping")
    found: list[tuple[yaml.Node, yaml.Node]] = []
    for key_node, value_node in root.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "name":
            found.append((key_node, value_node))
    return found


def _verify_name(text: str, expected: str) -> None:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise UnsafeFrontmatter("rewrite-verification-failed")
    try:
        root = yaml.compose(match.group("body"))
    except yaml.YAMLError as error:
        raise UnsafeFrontmatter("rewrite-verification-failed") from error
    nodes = _top_level_name_nodes(root)
    if len(nodes) != 1:
        raise UnsafeFrontmatter("rewrite-verification-failed")
    value_node = nodes[0][1]
    if not isinstance(value_node, yaml.ScalarNode) or value_node.value != expected:
        raise UnsafeFrontmatter("rewrite-verification-failed")


def rewrite_skill_name(text: str, new_name: str) -> str:
    """Replace the unique top-level frontmatter ``name`` with ``new_name``.

    Uses PyYAML's composed node spans instead of line regexes, so quoted keys,
    folded/literal block values and complex sibling keys are handled without
    dumping the whole frontmatter (comments, key order and the body stay
    byte-identical). Raises :class:`UnsafeFrontmatter` for invalid YAML, a
    non-mapping root, a non-scalar name, more than one top-level ``name``, or a
    failed post-rewrite verification; the caller skips that skill.
    """
    if not is_kebab(new_name):
        raise UnsafeFrontmatter("target-name-not-kebab")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        eol = "\r\n" if "\r\n" in text else "\n"
        result = f"---{eol}name: {new_name}{eol}---{eol}{eol}{text}"
        _verify_name(result, new_name)
        return result

    body = match.group("body")
    eol = "\r\n" if "\r\n" in text[: match.end()] else "\n"
    try:
        root = yaml.compose(body)
    except yaml.YAMLError as error:
        raise UnsafeFrontmatter("invalid-yaml") from error
    nodes = _top_level_name_nodes(root)
    if len(nodes) > 1:
        raise UnsafeFrontmatter("duplicate-name")
    if not nodes:
        new_body = f"name: {new_name}{eol}" + body
    else:
        value_node = nodes[0][1]
        if not isinstance(value_node, yaml.ScalarNode):
            raise UnsafeFrontmatter("name-not-scalar")
        start = value_node.start_mark.index
        end = value_node.end_mark.index
        # A block scalar's node span includes its own trailing line break;
        # keep that break (and the next key) by replacing only the content.
        while end > start and body[end - 1] in "\r\n":
            end -= 1
        new_body = body[:start] + new_name + body[end:]

    result = text[: match.start("body")] + new_body + text[match.end("body") :]
    _verify_name(result, new_name)
    return result


def _reconcile(
    root: Path,
    active: dict[str, dict[str, Any]],
    *,
    manifest: dict[str, Any] | None = None,
    keep_manifest: bool = True,
) -> list[str]:
    """Drop shadow dirs we previously created that are no longer active.

    ``.qwenpaw_projection.json`` lives next to the projection and can be
    edited, restored from a backup or written by another tool, so its keys are
    treated as untrusted input:

    * only single-layer kebab names are eligible (see
      :func:`is_plain_shadow_name`); anything else is reported and skipped;
    * nothing is touched unless ``root`` is a real directory — a symlinked or
      replaced root is refused rather than resolved;
    * a shadow entry that is itself a symlink is unlinked, never followed.

    Residual boundary: checks happen before the removal, so a concurrent
    replacement of a parent path between ``lstat`` and ``rmtree`` is not
    defended against. That requires an attacker who can write the state tree
    while projection runs.
    """
    root = Path(root)
    if manifest is None:
        manifest = _load_json(root / MANIFEST_FILENAME)
    if not isinstance(manifest, dict):
        manifest = {}
    candidates = sorted(set(manifest) - set(active))

    root_state = _shadow_root_state(root)
    if root_state == "absent":
        return []
    if root_state != "real":
        log.warning(
            "dsh: skills shadow root is not a plain directory (%s); refusing cleanup",
            root_state,
        )
        return []

    removed: list[str] = []
    for name in candidates:
        if not is_plain_shadow_name(name):
            _warn_rejected_entry(name)
            continue
        if _remove_shadow_child(root / name):
            removed.append(name)
    if not keep_manifest:
        _remove_shadow_child(root / MANIFEST_FILENAME)
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


def _remove_shadow_child(path: Path) -> bool:
    """Remove one entry *inside* the shadow root without following links.

    ``lstat`` classifies the entry itself: a symlink is unlinked (its target is
    untouched), a real directory is removed recursively (``rmtree`` does not
    follow symlinks found inside), a regular file is unlinked. Special files
    are refused. Returns whether something was removed.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        log.warning("dsh: cannot inspect shadow entry; refusing to remove it")
        return False
    try:
        if stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode):
            os.unlink(path)
            return True
        if stat.S_ISDIR(info.st_mode):
            # rmtree refuses a path that became a symlink after the lstat, and
            # never follows symlinked children.
            shutil.rmtree(path)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        log.warning("dsh: cannot remove shadow entry; leaving it in place")
        return False
    log.warning("dsh: refusing to remove a non-regular shadow entry")
    return False


# --------------------------------------------------------------------------- revision
def _directory_revision(
    directory: Path,
    *,
    max_files: int = 4096,
    max_bytes: int = 64 * 1024 * 1024,
) -> str:
    """Bounded content hash fallback when the host did not provide a revision.

    Only regular files below *directory* are hashed; symlinks are skipped and
    directory links are never followed. The walk stops (returns ``""`` with a
    warning) once the file/byte budget is exceeded, so an unexpected or
    oversized source path cannot turn revision probing into an unbounded scan
    of a sensitive directory. ``""`` means "unknown", never "unchanged".
    """
    digest = hashlib.sha256()
    paths: list[Path] = []
    total_bytes = 0
    try:
        for current, dirs, files in os.walk(directory, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(
                name
                for name in dirs
                if not os.path.islink(os.path.join(current, name))
            )
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink():
                    continue
                try:
                    info = path.stat()
                except OSError:
                    return ""
                if not stat.S_ISREG(info.st_mode):
                    continue
                if len(paths) >= max_files or total_bytes + info.st_size > max_bytes:
                    log.warning(
                        "dsh: skill source is too large to hash within the budget; "
                        "revision is treated as unknown",
                    )
                    return ""
                total_bytes += info.st_size
                paths.append(path)
    except OSError:
        return ""
    paths.sort(key=lambda item: item.relative_to(directory).as_posix())
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
