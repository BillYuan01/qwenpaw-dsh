# tools

Local delivery tools using the standard library and existing runtime. They do not install dependencies, start DSH, or read real credentials. Their availability does not authorize a release.

## `plugin_package.py`

A package builder/checker with an explicit 21-file allowlist. Packages contain `plugin.json`, `plugin.py`, `README.md`, `LICENSE`, and `qwenpaw_dsh/`, with no tests, caches, state, or symlinks.

Run from the repository root:

```sh
# Build to a new path: no overwrites or output inside the source tree.
# Read the expected version from the checkout's plugin.json.
VERSION=$(python3 -c 'import json;print(json.load(open("qwenpaw-dsh/plugin.json"))["version"])')
python tools/plugin_package.py build \
  --source . --output /path/outside/repo/qwenpaw-dsh-${VERSION}-plugin.zip \
  --expected-version "${VERSION}"

# Read-only verification against the same checkout used to build the package.
python tools/plugin_package.py check \
  --zip /path/outside/repo/qwenpaw-dsh-${VERSION}-plugin.zip \
  --source . --expected-version "${VERSION}"
```

Guarantees:

- Reads the selected checkout only; does not copy an active installation from the QwenPaw plugins directory.
- Uses `lstat` at each source file/directory level. Rejects symlinks, non-regular files such as FIFOs/devices, and source files outside the allowlist.
- Fixes member order, `1980-01-01` timestamps, `0644` permissions, and `ZIP_STORED`; two builds from identical source are byte-identical.
- Rejects existing output paths (including dangling symlinks) and output within the source tree. Writes a temporary file and links it atomically without overwriting.
- Checks duplicate members, path traversal/absolute paths, directory entries, permissions, encryption flags, versions, LICENSE equality, every member's bytes, and `plugin.py`'s `_shutdown(**kwargs)` signature through AST inspection without executing code.

Read-only checks of historical `dist/` ZIPs (`1.0.0` / `1.0.1`) against current source are expected to fail; the tool does not modify them. `--expected-version` is a test/release assertion. Read it from `qwenpaw-dsh/plugin.json`; do not hardcode a historical version. Compare released ZIPs with their matching tagged source: later documentation on main may differ from the packaged README.

## `run_safe_tests.py`

An offline unit-test entry point with an explicit allowlist. The default set is 13 files: the original 12 contain **252** tests (the first 11 have a fixed total of **226**), plus **20** tests in `test_event_mapper.py` added in 1.1.1, for **272** in total.

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
python tools/run_safe_tests.py --list
python tools/run_safe_tests.py --only test_permission_mode.py --skip-count-check
```

- Does not perform broad `discover test_*.py` collection. `tests/test_*_projection.py`, `smoke_adapter.py`, `stream_check.py`, and `tests/contract/` are not allowlisted.
- Runs each file separately with `unittest discover -p <file>`, using the inner `qwenpaw-dsh/` as the working directory.
- Uses disposable `HOME/DSH_HOME/QWENPAW_HOME/QWENPAW_WORKING_DIR/TMPDIR` directories and a minimal environment allowlist; keys, tokens, and PYTHONPATH are not inherited.
- Reports tests/failures/errors/skipped per file and in total. Skips cause exit 3 by default (`--allow-skips` accepts them). Missing dependencies/files fail explicitly; no automatic pip installation.
- Enforces test counts for the first 11 files and the new `test_event_mapper.py` (226 / 20 respectively), preventing false success from zero tests. `test_delivery_tools.py` retains its existing policy of no fixed count.
