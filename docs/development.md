# Development and packaging

## Repository layout

| Path | Purpose |
|---|---|
| `qwenpaw-dsh/plugin.json` / `plugin.py` | Installation manifest and lifecycle entry point |
| `qwenpaw-dsh/qwenpaw_dsh/host.py` / `approvals.py` | Host ports and approval adapters |
| `qwenpaw-dsh/qwenpaw_dsh/provider.py` / `loaders/` | Capability declarations, registration, and unloading |
| `qwenpaw-dsh/qwenpaw_dsh/core/` | ACP, turns, events, and resource projection |
| `qwenpaw-dsh/tests/` | Both unit and live integration tests; distinguish them before running |
| `tools/` | Local test entry point and allowlisted packager |
| `dist/` | Historical release packages; do not overwrite |
| `patches/qwenpaw-upstream/` | Archived patches; not applied automatically |

The [architecture guide](architecture.md) describes runtime behavior; [AGENTS.md](../AGENTS.md) defines contribution constraints. For installation and usage, start with the [root README](../README.md).

## Default checks

From the repository root, using an existing Python environment with QwenPaw, the ACP SDK, and PyYAML:

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
python tools/run_safe_tests.py --list
git diff --check
```

The runner explicitly selects 13 files, currently totaling 272 tests: 226 existing safety tests, 26 packaging/runner tests, and 20 event-mapping tests. It uses temporary HOME and DSH_HOME directories and a minimal environment. It does not start DSH, read real credentials, or install dependencies automatically. Missing dependencies, zero tests, failures, and tests skipped under the default policy must not be reported as passes.

Choose tests that match the change. Do not call models for documentation-only updates.

| Change | Test files (inside `qwenpaw-dsh/tests/`) |
|---|---|
| Event mapping | `test_event_mapper.py` |
| Turns, connections, cancellation, resumption | `test_lifecycle.py` |
| Models or host interfaces | `test_model_contract.py`, `test_host_contract.py` |
| Approvals, loading | `test_permission_mode.py`, `test_approval_port.py`, `test_loader_wiring.py` |
| Skills, images, MCP | `test_skills.py`, `test_skills_shadow_security.py`, `test_file_projection_g4.py`, `test_images.py`, `test_mcp.py` |
| Packaging, test entry point | `test_delivery_tools.py` |

## Live integration tests

These entry points start real DSH processes and may use credentials, call billable models, or execute tools:

- `tests/smoke_adapter.py`, `tests/stream_check.py`
- `tests/test_*_projection.py`
- `tests/contract/` and its spike/seed helpers

Do not run pytest collection over the entire tests directory. Before an authorized run, inspect how the script creates its home, obtains credentials, and handles permissions. Older helpers may link real `.env` files or credential stores; a temporary directory is not an isolated environment. The target, permissions, costs, and output location of live calls require separate authorization. Do not conceal failures by using full access or automatic approval.

See [tests/contract/README.md](../qwenpaw-dsh/tests/contract/README.md) for the contract suite. Historical runtime-form and MCP tests are in [forms-matrix.md](forms-matrix.md) and [mcp-http-sse.md](mcp-http-sse.md). Each record has its own date and version; it does not replace verification of the current checkout.

Historical material retains its original language, primarily Chinese: [CHANGELOG.md](../CHANGELOG.md), the runtime-form and MCP reports above, `docs/dsh-community/`, `patches/`, and contract/spike reports. These archives are not translated as part of the English-default documentation update.

## Building release packages

Use [tools/plugin_package.py](../tools/plugin_package.py), not files copied from an active installation. The builder uses a 21-file allowlist and rejects existing output paths, output inside the source tree, symlinks, and special files. Member order, timestamps, and permissions are fixed; the package includes a LICENSE identical to the root copy.

```sh
python tools/plugin_package.py build --source . \
  --output /path/outside/repo/qwenpaw-dsh-1.1.1-plugin.zip --expected-version 1.1.1
python tools/plugin_package.py check --source . \
  --zip /path/outside/repo/qwenpaw-dsh-1.1.1-plugin.zip --expected-version 1.1.1
```

`check` compares member bytes, versions, the license, and the shutdown callback signature against the selected checkout. **Compare a package only with the source version it was built from.** After documentation changes on main, checking an older tagged package against main will show differences; that does not mean the package is corrupt.

Documentation-only changes do not require a version bump or repackaging. Before a release, update the manifest, client version, CHANGELOG, and installation instructions, then build and verify a new file. Leave old ZIPs and tags unchanged. Documentation may reach main ahead of the next software release; READMEs in released ZIPs remain snapshots of their tags.

Release, push, and deployment each require authorization. Report checks actually run, failures, and checks not run. A local commit is not a release, and a release is not an installation.
