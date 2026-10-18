# `@local/dsh-qwenpaw-bridge` v0.1

A **DSH bundle plugin** that upgrades the stock `acp` profile for QwenPaw
without touching DSH core. It keeps the wire **standard ACP**: QwenPaw still
talks to `@deepseek-ai/dsh-acp`, so the adapter needs no private protocol.

> The upstream half of this idea (opt-in streaming inside `dsh-acp` itself)
> remains open; this package is the plugin half and needs no DSH core changes.

## What it adds

| # | Capability | Wire surface | Fail-soft behavior |
|---|---|---|---|
| 1 | **Live streaming** | standard `session/update` with `agent_message_chunk` / `agent_thought_chunk` from `agent/assistant-stream` `text-delta` / `reasoning-delta`; duplicate committed text/thought suppressed | event missing / subscription fails / client opts out → stock committed projection, unchanged |
| 2 | **Permission presets, hot-switchable** | `_meta['qwenpaw'].permission_presets` + a `permission_mode` select in `configOptions`; switch through `session/set_config_option` or `qwenpaw/permission/set` | `ctx.permissionPresets` missing → no option, no interception |
| 3 | **Model catalog** | `_meta['qwenpaw'].models` on `initialize` and `session/new`; every model carries its `reasoning_efforts` | `ctx.llm` lookup failure → field omitted, no crash |
| 4 | Tool kind polish (optional) | `tool_call.kind` mapped from the tool name (`read`/`edit`/`execute`/`search`/`fetch`) | unknown names stay `other` |

Everything else (images, tool updates, usage, config updates, MCP, resume,
cancel) is forwarded verbatim.

## Install

```sh
# into the real DSH home
DSH_HOME=~/.dsh dsh-qwenpaw-bridge/profile/install.sh
DSH_HOME=~/.dsh dsh --profile qwenpaw
```

The profile is `dsh-base` + `dsh-acp-app` + this bundle. The bundle patch
disables the stock `acp` row and inserts `qwenpaw-bridge`; `acp-app-startup`
still owns stdin EOF and `--help`.

For a throwaway home (used by `TESTING.md` and `test/probe.py`):

```sh
mkdir -p /tmp/qp-home/profiles/qwenpaw/node_modules/@local
ln -s "$PWD/dsh-qwenpaw-bridge" /tmp/qp-home/profiles/qwenpaw/node_modules/@local/dsh-qwenpaw-bridge
cp dsh-qwenpaw-bridge/profile/package.json /tmp/qp-home/profiles/qwenpaw/
# edit the file: dependency to the absolute bridge path
DSH_HOME=/tmp/qp-home dsh --profile qwenpaw
```

## `_meta['qwenpaw']` contract (protocol = 1)

`initialize` result:

```jsonc
{
  "_meta": {
    "qwenpaw": {
      "protocol": 1,
      "bridge": "dsh-qwenpaw-bridge",
      "version": "0.1.0",
      "streaming": true,
      "models": [
        {
          "id": "deepseek-v4.1-flash",
          "name": "DeepSeek V4.1 Flash",
          "provider": "opencode-go",
          "provider_name": "opencode-go",
          "selection_value": "[\"opencode-go\",\"deepseek-v4.1-flash\"]",
          "description": "…",              // optional
          "context_window": 1000000,       // optional
          "max_tokens": 384000,            // optional
          "input_modalities": ["text", "image"], // optional
          "reasoning_efforts": [{"id": "low", "name": "Low"}, …],
          "default_reasoning_effort": "high",    // optional
          "default": true                  // current route
        }
      ],
      "current_model": {"provider": "opencode-go", "model": "deepseek-v4.1-flash"},
      "permission_presets": [{"id": "workspace-write", "name": "workspace-write"}, …],
      "default_permission_preset": "workspace-write",
      "permission_preset": "workspace-write"
    }
  }
}
```

`session/new` / `session/resume` results repeat the same object (with
`current_model`/`permission_preset` resolved for that session), and
`configOptions` gains:

```jsonc
{
  "id": "permission_mode",
  "name": "Permission mode",
  "category": "permission",
  "type": "select",
  "currentValue": "workspace-write",
  "options": [{"value": "read-only", "name": "read-only"}, …]
}
```

### Client-side switches

```jsonc
// standard ACP: hot switch, no process restart
{"method": "session/set_config_option",
 "params": {"sessionId": "…", "configId": "permission_mode", "value": "read-only"}}

// optional private helpers (ACP extension methods; the SDK sends the leading
// underscore automatically: `_qwenpaw/permission/set`)
{"method": "qwenpaw/permission/set", "params": {"sessionId": "…", "preset": "read-only"}}
{"method": "qwenpaw/permission/get", "params": {"sessionId": "…"}}
{"method": "qwenpaw/streaming/set", "params": {"enabled": false}}
```

An initial preset can be requested in `session/new`:
`params._meta.qwenpaw.permission_preset = "read-only"`.

To opt out of live deltas for a connection, send
`initialize._meta.qwenpaw.streaming = false`; the response then advertises
`"streaming": false` and the stock committed projection is used.

## Package layout

```
lib/index.js      plugin entry: inject acp services + acpAppStartup, build the ACP stream, call acp.apply
lib/inbound.js    bridge-owned requests; permission_mode interception
lib/outbound.js   _meta decoration, committed-duplicate suppression, config options
lib/stream.js     agent/assistant-stream -> session/update; duplicate flags
lib/catalog.js    provider/model/efforts discovery for _meta
lib/presets.js    ctx.permissionPresets projection + switch
lib/toolkind.js   tool name -> ACP kind
lib/protocol.js   _meta key, config ids, response helpers
cordis.patch.yml  disables `acp`, inserts `qwenpaw-bridge`
profile/          `qwenpaw` profile template + install.sh
test/probe.py     live acceptance probe (streaming / meta / presets / committed fallback)
```

## Compatibility / failure policy

- **No DSH core changes**; the stock `@deepseek-ai/dsh-acp` `apply()` is reused
  directly with a custom `config.stream` (the same runtime-only field the
  community `dsh-acp-door` uses).
- **Non-public dependencies are optional**: `agent/assistant-stream`,
  `ctx.permissionPresets`, `ctx.llm` catalog calls, and `ctx.agentDefaultModel`
  are all read defensively. Missing anything degrades to plain committed ACP.
- Peer versions are pinned to the tested runtime
  (`@deepseek-ai/dsh-acp 0.2.0-rc.2`, `@agentclientprotocol/sdk ^1.4.0`).

See `TESTING.md` for the measured evidence.
