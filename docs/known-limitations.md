# Known implementation limitations (documentation-only record)

This page records **potential or confirmed code-path mismatches** identified by source review on 2026-11-13. It does not claim that an exploit, data loss, or failure was reproduced against a running DSH/QwenPaw deployment. No source code, tests, package versions, or release artifacts were changed to address these points.

## 1. `approval_policy: never` is not an independently enforced refusal rule

The `workspace` approval preset in `qwenpaw_dsh/provider.py` sets `permission_mode: workspace-write` plus `approval_policy: never`. The adapter stores the approval-policy setting and includes it in its connection identity/status, but its `request_permission` callback in `core/acp_client.py` still consults the host approval port and may return a one-time allow. It does not branch on `approval_policy=never` to reject a request automatically.

**Implication:** do not treat `approval_policy: never` as a security guarantee supplied by this plugin. DSH's own sandbox/preset and host-side policies may still independently block an operation; this finding alone does not demonstrate out-of-workspace access.

## 2. Version checks may report unsupported or malformed values as compatible

- `loaders/monkeypatch.py::_in_range` uses PEP 440 parsing first, then falls back to digit extraction for invalid input. A malformed version such as `2.3.0garbage` can therefore be accepted numerically instead of being rejected. The fallback also depends on `packaging` being importable; a missing package is not handled by the same exception branch.
- `core/adapter.py::_version_supported` compares simplified major/minor components for runtime status. This is broader than the documented DSH interval `>=0.2.0-rc.2 <0.3.0-0`, so the status indicator should not be treated as an authoritative compatibility check.

Use the manifest's declared bounds and a properly parsed actual version as the reference; avoid bypassing host or DSH version checks.

## 3. An exceptional monkeypatch mount can leave partial changes

`loaders/monkeypatch.py` checks that target attributes exist, then replaces multiple imported bindings. If an attribute write raises after earlier writes succeeded, the mount path does not roll those writes back in the same exception handler. The normal unmount path also depends on the mount-complete flag. Normal startup was checked on QwenPaw 2.3.0, but this exceptional failure path has not been demonstrated safe by that check.

The no-partial-installation statement in [AGENTS.md](../AGENTS.md) is a **maintenance invariant**, not an unconditional claim about current failure handling.

## 4. Status guidance versus profile creation

A missing-profile diagnostic can mention a `dsh PROFILE --help` style command. To create the actual bridge profile, follow [the installation instructions](../README.md):

```sh
dsh --profile qwenpaw --from-default-profile acp --dump-config >/dev/null
```

Avoid confusing the bridge profile `qwenpaw` with the default `acp` profile.

## 5. Legacy source comments and verification limits

Some shipped Python module docstrings still call the installable plugin a "skeleton" or "draft". These are stale **source comments**, not a sign that the plugin is unimplemented. They are intentionally unchanged in a docs-only update.

The 14-file/278-test offline suite and dated Linux integration reports do not establish full macOS, native Windows, WSL, or every runtime-feature acceptance. A green test count is historical evidence for the environment in its associated report, not a fresh result from this documentation update.
