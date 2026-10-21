# qwenpaw-dsh (v1.0.1)

QwenPaw plugin that registers **DeepSeek Harness** as a third-party agent
backend. The provider core (`qwenpaw_dsh/`) is host-agnostic and driven through
a loader seam; the 2.2.x loader monkey-patches the harness registry, the 2.3+
loader feature-detects an official entry point (QwenPaw 2.3+).

The DSH-side bridge lives in its own repository:
[`BillYuan01/dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge)
(MIT; true streaming, hot-switchable permission presets, model catalog `_meta`).

## Status

- ✅ `status` / `models` (bridge `_meta` catalog, per-model reasoning efforts) /
  `run_turn` (text + tool lifecycle + terminal mapping)
- ✅ Process pool keyed by `(binary, profile, permission_mode, approval_policy)`
- ✅ Session mapping with cross-restart `session/resume` (`dsh_sessions.json`)
- ✅ Approval cards: `session/request_permission` → QwenPaw approval service
  (`approvals.py`; approve/deny/timeout; narrowest one-shot grants; fail-closed fallbacks)
- ✅ Permission-mode alignment: `approval_level` (OFF/AUTO/SMART/STRICT) → DSH mode
  (`core/permissions.py`, via `DSH_PERMISSION_MODE`, pool-key aware)
- ✅ True streaming with the `qwenpaw` profile (+ bridge)
- ✅ Skills projection, MCP projection (stdio + HTTP), inline image attachments
- ✅ Tests: `tests/smoke_adapter.py`, `tests/stream_check.py`,
  `tests/test_permission_mode.py`, `tests/test_approval_port.py`,
  `tests/test_loader_wiring.py`
- ✅ 端到端契约套件 s01–s06: `tests/contract/`（真实 `dsh` + ACP v1；两种 profile 均 6 passed）

## Install

```bash
qwenpaw plugin install ./qwenpaw-dsh     # hot-loads; restart QwenPaw if it does not appear
```

For true streaming, install the bridge into a `qwenpaw` DSH profile (see the
bridge repository's README). Uninstall restores the patched registry symbols
exactly (loader `unmount`).
