#!/usr/bin/env python3
"""Reproducible, whitelist-only packager and checker for the qwenpaw-dsh plugin.

The packer never imports the plugin, never runs DSH, and never reads the live
installation. It only reads the 22 explicitly listed files from a checkout.

Build (writes only to the requested new path):
    python tools/plugin_package.py build \
        --source . --output /path/outside/repo/qwenpaw-dsh-1.1.0-plugin.zip \
        --expected-version 1.1.0

Check (read-only):
    python tools/plugin_package.py check \
        --zip dist/qwenpaw-dsh-1.1.0-plugin.zip --source . --expected-version 1.1.0

Pass the version currently declared in qwenpaw-dsh/plugin.json; do not freeze an
old release number in scripts. The checker is expected to FAIL on the
historical 1.0.0/1.0.1 ZIPs: those snapshots predate the LICENSE file and the
uninstall-hook kwargs fix. Historical assets are never rewritten by this tool.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import posixpath
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

PACKAGE_DIR = "qwenpaw-dsh"

# The one and only packaging whitelist (paths relative to PACKAGE_DIR).
PACKAGE_FILES: tuple[str, ...] = (
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
PACKAGE_FILE_SET = frozenset(PACKAGE_FILES)
ALLOWED_DIRS = frozenset(
    {
        "qwenpaw_dsh",
        "qwenpaw_dsh/core",
        "qwenpaw_dsh/loaders",
    }
)
# Source-only directory: never packed, but still checked for links/irregulars.
SOURCE_ONLY_DIRS = frozenset({"tests"})
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644
PERM_MODE = 0o644


class PackageError(Exception):
    """A source/output problem that must stop the operation."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise PackageError(f"cannot stat {path}: {exc.strerror or exc}") from exc


def _check_regular_chain(package_root: Path, relative: str) -> bytes:
    """lstat every component, reject symlinks/irregular files, then read."""
    current = package_root
    parts = relative.split("/")
    for part in parts[:-1]:
        current = current / part
        info = _lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise PackageError(f"refusing symlinked directory: {current}")
        if not stat.S_ISDIR(info.st_mode):
            raise PackageError(f"not a directory: {current}")
    target = package_root / relative
    info = _lstat(target)
    if stat.S_ISLNK(info.st_mode):
        raise PackageError(f"refusing symlinked source file: {target}")
    if not stat.S_ISREG(info.st_mode):
        raise PackageError(f"refusing non-regular source file: {target}")
    try:
        return target.read_bytes()
    except OSError as exc:
        raise PackageError(f"cannot read {target}: {exc.strerror or exc}") from exc


def _scan_link_free_tree(directory: Path) -> None:
    """Allow regular files/dirs, but reject links and irregular files."""
    stack = [directory]
    while stack:
        current = stack.pop()
        with os.scandir(current) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
        for entry in entries:
            path = Path(entry.path)
            info = _lstat(path)
            if stat.S_ISLNK(info.st_mode):
                raise PackageError(f"refusing symlink in source tree: {path}")
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise PackageError(f"irregular file in source tree: {path}")


def _scan_package_tree(package_root: Path) -> None:
    """Reject unexpected/symlinked/irregular entries in the packaged subtree."""
    stack = [package_root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise PackageError(f"cannot list {directory}: {exc.strerror or exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(package_root).as_posix()
            info = _lstat(path)
            if stat.S_ISLNK(info.st_mode):
                raise PackageError(f"refusing symlink in package tree: {path}")
            if stat.S_ISDIR(info.st_mode):
                if entry.name == "__pycache__":
                    continue
                if directory == package_root and relative in SOURCE_ONLY_DIRS:
                    _scan_link_free_tree(path)
                    continue
                if relative not in ALLOWED_DIRS:
                    raise PackageError(f"unexpected directory in package tree: {relative}")
                stack.append(path)
                continue
            if stat.S_ISREG(info.st_mode):
                if relative in PACKAGE_FILE_SET:
                    continue
                if entry.name.endswith(".pyc"):
                    continue
                raise PackageError(f"unexpected file in package tree: {relative}")
            raise PackageError(f"irregular file in package tree: {relative}")


def validate_source(source: str | Path) -> tuple[Path, dict[str, bytes], dict]:
    """Validate the checkout and return (package_root, files, manifest)."""
    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    root_info = _lstat(source_path)
    if stat.S_ISLNK(root_info.st_mode):
        raise PackageError(f"refusing symlinked source root: {source_path}")
    if not stat.S_ISDIR(root_info.st_mode):
        raise PackageError(f"source root is not a directory: {source_path}")
    source_root = source_path.resolve()

    root_license = source_root / "LICENSE"
    license_info = _lstat(root_license)
    if stat.S_ISLNK(license_info.st_mode) or not stat.S_ISREG(license_info.st_mode):
        raise PackageError("repository root LICENSE must be a regular file")
    root_license_bytes = root_license.read_bytes()

    package_root = source_root / PACKAGE_DIR
    package_info = _lstat(package_root)
    if stat.S_ISLNK(package_info.st_mode):
        raise PackageError(f"refusing symlinked package root: {package_root}")
    if not stat.S_ISDIR(package_info.st_mode):
        raise PackageError(f"missing package directory: {package_root}")

    _scan_package_tree(package_root)

    files: dict[str, bytes] = {}
    for relative in PACKAGE_FILES:
        files[relative] = _check_regular_chain(package_root, relative)

    if files["LICENSE"] != root_license_bytes:
        raise PackageError("package LICENSE is not byte-identical to repository LICENSE")

    try:
        manifest = json.loads(files["plugin.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"plugin.json is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("version"), str):
        raise PackageError("plugin.json is missing a string version")
    return package_root, files, manifest


def _check_output_path(output: str | Path, source_root: Path) -> Path:
    output_path = Path(output).expanduser()
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    if output_path.suffix.lower() != ".zip":
        raise PackageError("output must end in .zip")
    parent = output_path.parent
    parent_info = _lstat(parent)
    if stat.S_ISLNK(parent_info.st_mode):
        raise PackageError(f"refusing symlinked output directory: {parent}")
    if not stat.S_ISDIR(parent_info.st_mode):
        raise PackageError(f"output directory does not exist: {parent}")
    if os.path.lexists(output_path):
        raise PackageError(f"refusing to overwrite existing output: {output_path}")
    resolved = output_path.resolve(strict=False)
    source_resolved = source_root.resolve()
    try:
        common = Path(os.path.commonpath([str(resolved), str(source_resolved)]))
    except ValueError:
        common = Path("/")
    if common == source_resolved:
        raise PackageError("output must not be inside the source checkout")
    return output_path


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED,
                         allowZip64=False) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = FILE_MODE << 16
            info.internal_attr = 0
            info.flag_bits = 0
            archive.writestr(info, files[name])
    buffer.seek(0)
    data = buffer.read()
    buffer.close()
    return data


def build(source: str | Path, output: str | Path, expected_version: str) -> dict:
    source_root = Path(source).expanduser()
    if not source_root.is_absolute():
        source_root = Path.cwd() / source_root
    source_resolved = source_root.resolve()
    _package_root, files, manifest = validate_source(source_root)
    version = manifest["version"]
    if version != expected_version:
        raise PackageError(
            f"plugin.json version {version!r} does not match expected {expected_version!r}"
        )
    output_path = _check_output_path(output, source_resolved)
    payload = _zip_bytes(files)

    fd, temporary = tempfile.mkstemp(prefix=".plugin-package-", suffix=".zip",
                                     dir=str(output_path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, output_path)
        except FileExistsError as exc:
            raise PackageError(f"refusing to overwrite existing output: {output_path}") from exc
        except OSError:
            # Fallback for filesystems without hard links: exclusive create.
            try:
                exclusive = os.open(str(output_path),
                                    os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError as exc:
                raise PackageError(
                    f"refusing to overwrite existing output: {output_path}"
                ) from exc
            try:
                with os.fdopen(exclusive, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.unlink(output_path)
                except OSError:
                    pass
                raise
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass

    return {
        "ok": True,
        "version": version,
        "members": len(files),
        "bytes": len(payload),
        "sha256": _sha256(payload),
        "output": str(output_path),
        "source": str(source_resolved),
    }


def _unsafe_member_reason(name: str) -> str | None:
    if not name:
        return "empty member name"
    if "\\" in name:
        return "backslash separator"
    if "\x00" in name:
        return "NUL in member name"
    if name.startswith("/"):
        return "absolute path"
    if len(name) >= 2 and name[1] == ":":
        return "drive-letter path"
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "unsafe path segment"
    if posixpath.normpath(name) != name:
        return "non-normalized path"
    return None


def _ast_has_shutdown_kwargs(source: bytes) -> tuple[bool, str]:
    try:
        tree = ast.parse(source.decode("utf-8"), filename="plugin.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        return False, f"plugin.py cannot be parsed: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DshHarnessPlugin":
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        child.name == "_shutdown":
                    if child.args.kwarg is not None:
                        return True, "_shutdown accepts **kwargs"
                    return False, "_shutdown does not accept **kwargs"
    return False, "DshHarnessPlugin._shutdown not found"


def check(zip_path: str | Path, source: str | Path,
          expected_version: str) -> dict:
    reasons: list[str] = []
    source_root = Path(source).expanduser()
    if not source_root.is_absolute():
        source_root = Path.cwd() / source_root
    package_root, source_files, manifest = validate_source(source_root)
    source_version = manifest["version"]

    archive_path = Path(zip_path).expanduser()
    if not archive_path.is_absolute():
        archive_path = Path.cwd() / archive_path
    if not archive_path.is_file():
        return {"ok": False, "reasons": [f"ZIP not found: {archive_path}"],
                "sha256": None, "members": 0}
    payload = archive_path.read_bytes()
    digest = _sha256(payload)

    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        return {"ok": False, "reasons": [f"not a readable ZIP: {exc}"],
                "sha256": digest, "members": 0}

    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        counts: dict[str, int] = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        for name, count in sorted(counts.items()):
            if count > 1:
                reasons.append(f"duplicate member: {name} ({count} entries)")

        for info in infos:
            name = info.filename
            unsafe = _unsafe_member_reason(name)
            if unsafe:
                reasons.append(f"unsafe member {name!r}: {unsafe}")
            if info.is_dir() or name.endswith("/"):
                reasons.append(f"directory member not allowed: {name}")
            if info.flag_bits & 0x1:
                reasons.append(f"encrypted member not allowed: {name}")
            raw_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(raw_mode)
            permissions = raw_mode & 0o777
            if file_type not in (0, stat.S_IFREG):
                reasons.append(f"member is not a regular file: {name}")
            elif permissions != PERM_MODE:
                reasons.append(
                    f"unsafe member mode {oct(permissions)} (expected 0644): {name}"
                )

        present = set(names)
        missing = sorted(PACKAGE_FILE_SET - present)
        extra = sorted(present - PACKAGE_FILE_SET)
        for name in missing:
            reasons.append(f"missing member: {name}")
        for name in extra:
            if _unsafe_member_reason(name) is None:
                reasons.append(f"unexpected member: {name}")

        for name in PACKAGE_FILES:
            if name not in present:
                continue
            try:
                data = archive.read(name)
            except (KeyError, OSError, zipfile.BadZipFile) as exc:
                reasons.append(f"cannot read member {name}: {exc}")
                continue
            expected = source_files[name]
            if data != expected:
                reasons.append(
                    f"member differs from source: {name} "
                    f"(zip {_sha256(data)[:12]}, source {_sha256(expected)[:12]})"
                )

        if "plugin.json" in present:
            try:
                embedded = json.loads(archive.read("plugin.json").decode("utf-8"))
                embedded_version = embedded.get("version")
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                embedded_version = None
            if embedded_version != expected_version:
                reasons.append(
                    f"version mismatch: zip {embedded_version!r}, "
                    f"expected {expected_version!r}"
                )
            if source_version != expected_version:
                reasons.append(
                    f"source version mismatch: {source_version!r}, "
                    f"expected {expected_version!r}"
                )

        if "LICENSE" in present:
            root_license = (source_root / "LICENSE").read_bytes()
            if archive.read("LICENSE") != root_license:
                reasons.append("LICENSE differs from repository root LICENSE")

        if "plugin.py" in present:
            ok, message = _ast_has_shutdown_kwargs(archive.read("plugin.py"))
            if not ok:
                reasons.append(f"uninstall callback incompatible: {message}")

    return {
        "ok": not reasons,
        "reasons": reasons,
        "sha256": digest,
        "bytes": len(payload),
        "members": len(infos),
        "version": expected_version,
    }


def _print_result(result: dict, as_json: bool, label: str) -> int:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        if result.get("ok"):
            print(
                f"OK {label}: {result.get('members')} members, "
                f"{result.get('bytes')} bytes, sha256 {result.get('sha256')}"
            )
        else:
            print(f"FAIL {label}: {len(result.get('reasons', []))} problem(s)")
            for reason in result.get("reasons", []):
                print(f"  - {reason}")
            if result.get("sha256"):
                print(f"  sha256 {result['sha256']}")
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Whitelisted, reproducible qwenpaw-dsh plugin packager/checker"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="build a new candidate ZIP")
    build_parser.add_argument("--source", required=True, help="repository checkout root")
    build_parser.add_argument("--output", required=True, help="new ZIP path (never overwritten)")
    build_parser.add_argument("--expected-version", required=True)
    build_parser.add_argument("--json", action="store_true")

    check_parser = subparsers.add_parser("check", help="read-only ZIP verification")
    check_parser.add_argument("--zip", required=True)
    check_parser.add_argument("--source", required=True, help="repository checkout root")
    check_parser.add_argument("--expected-version", required=True)
    check_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build(args.source, args.output, args.expected_version)
            return _print_result(result, args.json, "build")
        result = check(args.zip, args.source, args.expected_version)
        return _print_result(result, args.json, "check")
    except PackageError as exc:
        result = {"ok": False, "reasons": [str(exc)]}
        return _print_result(result, args.json, args.command)
    except OSError as exc:
        result = {"ok": False, "reasons": [f"filesystem error: {exc}"]}
        return _print_result(result, args.json, args.command)


if __name__ == "__main__":
    raise SystemExit(main())
