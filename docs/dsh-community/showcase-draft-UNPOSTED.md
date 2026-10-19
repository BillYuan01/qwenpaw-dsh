# 草案：Show Your Plugins! 帖（qwenpaw-dsh + 桥）

> 目标分类：`Show Your Plugins!`（https://github.com/deepseek-ai/deepseek-harness/discussions）
> **状态：未发布**（待用户批准）

---

**DSH as a first-class agent inside QwenPaw — two small plugins, no core patches**

We use DSH as the agent runtime behind QwenPaw (a personal AI assistant with a console UI, chat channels, skills and MCP). Two half-plugins, MIT/Apache-2.0, repo: https://github.com/BillYuan01/qwenpaw-dsh

**1. `dsh-qwenpaw-bridge` (DSH side, bundle plugin)**

Keeps the wire standard ACP — QwenPaw still talks to `@deepseek-ai/dsh-acp`, so no private protocol — and upgrades the experience:

| Capability | Wire surface |
|---|---|
| Live streaming | `session/update` `agent_message_chunk` / `agent_thought_chunk` projected from `agent/assistant-stream` deltas, duplicate committed text/thought suppressed (65 chunks vs 1 in our measurements) |
| Permission presets, hot-switchable | `_meta['qwenpaw'].permission_presets` + a `permission_mode` select in `configOptions`; switch via `session/set_config_option` or `qwenpaw/permission/set` — no process restart |
| Model catalog | `_meta['qwenpaw'].models` on `initialize` / `session/new`, each model carrying its `reasoning_efforts` |
| Tool kind polish | `tool_call.kind` mapped from tool name |

Fail-soft everywhere: if an internal event or API is missing, the stock behaviour is used unchanged. Pure plugin, `profile/install.sh` into `DSH_HOME`.

**2. `qwenpaw-dsh` (QwenPaw side, backend plugin)**

Registers DSH as a selectable agent backend in QwenPaw: session bridge, event mapping (text/think/tools/approval), process pool, skills projection (kebab-normalised `customSkillDirs`), MCP projection (`mcpServers` incl. stdio/http + retry-without-MCP), image attachments (inline base64 after probing `promptCapabilities.image`).

**Why this shape**: everything the bridge adds is a candidate for `packages/acp` upstream (see #9928 for streaming, #9735 for the posture/session-modes axis) — the plugin exists so users get it today, and the day it lands upstream the bridge shrinks to a config line.

Happy to answer questions or help anyone wiring another ACP client to DSH.
