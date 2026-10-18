# qwenpaw-dsh (v0.1)

QwenPaw plugin that registers **DeepSeek Harness** as a third-party agent
backend. The provider core (`qwenpaw_dsh/`) is host-agnostic and driven through
a loader seam; the 2.2.x loader monkey-patches the harness registry, the 2.3+
loader feature-detects an official entry point (QwenPaw 2.3+).

## Status (v0.1 slice)

- ✅ `status` / `models` (catalog probe) / `run_turn` (text + tool lifecycle + terminal mapping)
- ✅ Process pool keyed by `(binary, profile, permission_mode, approval_policy)`
- ✅ Session mapping with cross-restart `session/resume` (`dsh_sessions.json`)
- ✅ Smoke test: `~/.qwenpaw/venv/bin/python tests/smoke_adapter.py`
- ⏳ Next: approval cards (currently conservative auto-reject), MCP/skills
  projection, image attachments, `context_usage` channel (needs PR-3)

## Install

```bash
qwenpaw plugin install ./qwenpaw-dsh     # hot-loads; restart QwenPaw if it does not appear
```

Uninstall restores the patched registry symbols exactly (loader `unmount`).
