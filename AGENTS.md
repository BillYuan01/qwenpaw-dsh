# AGENTS.md

This repository maintains the QwenPaw-side Python plugin. See [README.md](README.md) for usage, [docs/architecture.md](docs/architecture.md) for implementation details, and [docs/development.md](docs/development.md) for testing and packaging.

## Before making changes

- Read the relevant source, tests, and current diff. Preserve existing work; do not use reset/clean to clear the working tree.
- The installable directory is the inner `qwenpaw-dsh/`. The current plugin is 1.1.1 and its companion bridge is 1.1.0. Manifests are authoritative; do not infer compatibility from matching repository versions.
- Supported hosts are `>=2.2.0, <2.3.0`. The future official loader is not a promise of 2.3+ support; do not remove guards to make tests pass.
- Stay within the request. Documentation work does not authorize code fixes, deployment, model calls, or releases.

## Code entry points

These paths are inside `qwenpaw-dsh/`:

| Path | Purpose |
|---|---|
| `plugin.py`, `plugin.json` | Lifecycle hooks and installation manifest |
| `qwenpaw_dsh/host.py`, `approvals.py` | Host ports and approval-service adapters |
| `qwenpaw_dsh/provider.py`, `loaders/` | Backend capabilities, registration, and unloading |
| `qwenpaw_dsh/core/adapter.py`, `acp_client.py` | Connections, sessions, turns, and ACP |
| `qwenpaw_dsh/core/event_mapper.py` | Pure event mapping |
| `qwenpaw_dsh/core/permissions.py` | The single implementation of permission mapping |
| `qwenpaw_dsh/core/skills.py`, `mcp.py`, `images.py` | Resource projection and attachments |

`core/` may depend on stable host event/adapter contracts, but must not import registry/router implementations. Keep that coupling in loaders and approval ports.

## Behaviors to preserve

### Loading and sessions

- Monkeypatch every target, including references imported by value; fail explicitly if a target is missing. Unloading must restore the exact originals and leave no partial installation. DirectLoader is not a production fallback.
- The default profile is `acp`. Using the bridge requires explicitly selecting `qwenpaw`; do not silently change the default.
- Keep connection identity separate from pool keys. A new process must resume/create a session, not prompt a session ID from the old process directly.
- Use a separate queue per session. Turns and resets for the same QwenPaw session share a serialization lock. Do not consume other sessions' frames or reuse a global completion sentinel.
- Missing responses, missing stop reasons, and independently cancelled background tasks must not report COMPLETED. Emit one terminal state per turn and retrieve task exceptions.
- Send at most one cancellation notification and wait for a bounded time. Synchronously isolate the connection before abandoning the wait. Connections removed from the pool still need an owner for their close task; clean up the old connection even if its replacement fails to start.
- Wake active consumers before closing. A close exception/cancellation is not success; a timeout or best-effort kill is not confirmation that the process stopped. Setup cleanup must not mask the original error, and finally blocks must not wait indefinitely.
- Create a new session only after resumption fails, and tell the user that context was rebuilt. `history()` returns an empty list, not fabricated history. `/status` reads memory only; it must not start DSH or read credentials.

### Models, permissions, and events

- Apply an explicit model before effort, in order. Confirm the response's currentValue before prompting; fail rather than silently using a default model. Preserve provider identity in catalogs and use effort choices only from the current model.
- Use single-flight probing for missing/expired catalogs. Model counts are not fixed, and probing may start DSH. Blank settings mean unspecified, not a model reset.
- Missing approvals, denials, timeouts, and approval errors mean denial. Prefer the least-privileged one-time grant; do not persist access. An explicit permission_mode takes precedence over session/agent approval_level; OFF maps to full access.
- read-only is not a read allowlist. Do not disable approvals or sandboxing, or broaden permissions, as a default troubleshooting step.
- Extract tool text only from standard content/text and compatible text shapes. Do not repr unknown objects or expose binary/rawOutput data. Preserve the 32768-character tool preview limit, truncation marker, and recursion guards. Do not truncate normal responses/reasoning; retain failed status.
- Reserve stdout for ACP protocol traffic, not logs or debug prints.

### Resource projection

- Use separate Skills snapshots, with source location/version in their tokens. Empty turns must not delete other snapshots; do not add automatic GC. Treat manifest keys as untrusted input: clean only valid single-level names and never follow symlinks when deleting.
- Update only a unique top-level YAML name. Skip invalid/duplicate names and preserve the rest of the document. Source resources are live links, not immutable copies; concurrent replacement of parent paths remains a residual risk.
- MCP fingerprints cover connection fields and env/headers changes. Use them only in memory; do not log credential digests. On changes, close first, then resume with the full list. Close failures block prompts; resume failures create a new session with a warning. Preserve the retry without MCP and the SSE skip. Do not claim full propagation of host Driver policies.
- Send images according to initialization capabilities. Read only regular files using bounded loops; reject special files and symlinks. Do not re-probe image capability after model changes or casually widen format/size limits.

## Verification

Run from the repository root:

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
python tools/run_safe_tests.py --list
git diff --check
```

The runner currently selects 13 files / 272 tests, without model calls. See the change-to-test table in [development.md](docs/development.md). Report missing dependencies, failures, and skips separately. Do not install dependencies automatically or weaken assertions to get a passing result.

**Do not collect all tests indiscriminately.** `smoke_adapter.py`, `stream_check.py`, `test_*_projection.py`, and contract/spike/seed helpers may start DSH, link real credentials, or incur charges. Inspect their behavior before running them and obtain authorization for the target, permissions, and costs. A temporary DSH_HOME does not automatically isolate credentials.

## Data and delivery

- Use synthetic fixtures, not real credentials or private files, for unit tests/documentation. Do not log keys, tokens, headers, full environments, profile dumps, or session mappings.
- Do not commit production logs, private reports, state, caches, credentials, machine paths, or session IDs. Service errors may also contain sensitive data; review them before publication.
- Do not bump versions or rebuild released ZIPs for documentation updates. Documentation on main may be newer than a release tag. Validate an old package only against its corresponding tagged source.
- For releases, update the manifest, client constants, and CHANGELOG together. Use the allowlisted `tools/plugin_package.py` builder/checker to create a new file; do not overwrite old assets or tags. Update the package allowlist and tests when adding runtime files.
- Commit/push, PRs/issues, releases, installation, restarts, and billable calls require authorization for the current request; do not reuse earlier permission. Use an approved noreply identity for commits and do not change global Git configuration.
- Report changed files, checks actually run, unverified scope, and release/deployment status. See [verification.md](docs/verification.md) for actual evidence; do not turn a Linux test result into a claim of all-platform support.
