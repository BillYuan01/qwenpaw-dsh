English | [简体中文](README.zh-CN.md)

# qwenpaw-dsh

Use DeepSeek Harness (DSH) in QwenPaw. Create a DSH agent, chat with it in the console, or call it from another agent.

The plugin drives DSH through ACP v1, connecting sessions, model selection, tool events, and approvals. This is a community project, not an official component from either vendor.

Current version: **1.1.1**. Recommended bridge: [dsh-qwenpaw-bridge 1.1.0](https://github.com/BillYuan01/dsh-qwenpaw-bridge). The two components do not need matching version numbers.

## Features

- Stream responses, reasoning, and tool results. Real-time deltas require the DSH bridge.
- Select a model and reasoning effort, with settings confirmed before sending a prompt.
- Resume DSH sessions and route each session's messages separately on a shared connection.
- Project QwenPaw approvals, Skills, stdio / HTTP MCP servers, and image attachments into DSH.

```text
QwenPaw → qwenpaw-dsh (this plugin) → ACP → DSH + dsh-qwenpaw-bridge → model
```

The components install separately: this plugin goes into QwenPaw; the bridge goes into a DSH profile. Installing one does not install the other.

## Requirements

- QwenPaw `>=2.2.0, <2.3.0`, with the official ACP SDK (`acp`) in its Python environment.
- DSH `>=0.2.0-rc.2 <0.3.0-0`, with model routing and credentials already configured.
- Node.js >=22; the DSH bridge also requires pnpm.

Verified on Linux. Dedicated end-to-end tests have not been run on macOS, native Windows, or WSL. Plugin loading is verified for `app`; only backend loading is verified for `desktop`. `qwenpaw task` bypasses plugin loading. See the [runtime forms record](docs/forms-matrix.md) (historical, in Chinese).

## Installation

The steps below assume a fresh installation. If a profile or plugin with the same name already exists, preserve its configuration before updating.

### 1. Set up the DSH bridge

```sh
git clone --branch v1.1.0 https://github.com/BillYuan01/dsh-qwenpaw-bridge.git
dsh --profile qwenpaw --from-default-profile acp --dump-config >/dev/null
dsh plugin --profile qwenpaw add /absolute/path/to/dsh-qwenpaw-bridge
```

Replace the path on the last line with the absolute path to your clone. Create the `qwenpaw` profile from the built-in `acp` template first; running `plugin add` against a new profile name alone is not enough.

The bridge is not published to npm; install it from a local path as shown above. Then follow the [bridge installation guide](https://github.com/BillYuan01/dsh-qwenpaw-bridge#readme) to configure model routing. Merge settings into the existing `cordis.patch.yml`; do not overwrite it.

### 2. Install the QwenPaw plugin

```sh
git clone --branch v1.1.1 https://github.com/BillYuan01/qwenpaw-dsh.git
cd qwenpaw-dsh
qwenpaw plugin install ./qwenpaw-dsh
```

Install the inner `qwenpaw-dsh/` directory, which contains `plugin.json`. Alternatively, download a ZIP from [Releases](https://github.com/BillYuan01/qwenpaw-dsh/releases), extract it, and install that directory. To update an existing plugin, use the host's supported `--force` workflow. Hot reload closes connections owned by the old plugin, so avoid updating during active turns.

### 3. Create an agent

In the console, create an agent with the **DeepSeek Harness** backend and set its profile to `qwenpaw`. This is a configuration fragment, not a complete configuration file:

```json
{
  "backend": "dsh",
  "backend_settings": {
    "binary": "/absolute/path/to/dsh",
    "profile": "qwenpaw"
  }
}
```

`binary` is optional; the plugin looks for DSH on the QwenPaw service's `PATH`. The service must inherit the same `DSH_HOME` used when configuring the profile.

**The default profile is `acp`, not `qwenpaw`.** Even after installing the bridge, explicitly select `qwenpaw` to use its enhancements.

## Permissions

| QwenPaw approval level | DSH mode |
|---|---|
| `STRICT` | `read-only` |
| `SMART` / `AUTO` | `workspace-write` |
| `OFF` | `danger-full-access` |

An explicit `backend_settings.permission_mode` overrides the approval level. Approval-port failures, denials, and timeouts are treated as denial. A single approval does not grant permanent access.

`read-only` restricts changes; it is **not a file-read allowlist**. Projecting MCP connections does not fully propagate QwenPaw Driver policies either. A test directory or a prompt is not a hard isolation boundary.

## Troubleshooting

**The DSH backend is missing from the console.** Check that the plugin is installed in the working directory used by the running QwenPaw instance, that the host is a supported 2.2.x version, and that loading logs show no errors.

**DSH works in a terminal but the service cannot find it.** The service may have a different `PATH`. Set `binary` to an absolute path and check the service's inherited `DSH_HOME`.

**The response appears all at once at the end.** Check that the agent uses the `qwenpaw` profile and that the bridge is actually loaded.

**The model or reasoning effort is wrong.** Check the agent's profile, model routing, and catalog cache. Query the model API in the DSH agent's context; querying from a native QwenPaw agent may use the default `acp` profile. The model count depends on configuration and is not fixed.

**Why isn't chat history rebuilt after resuming a session?** ACP session resumption and QwenPaw history replay are separate features. The plugin's `history()` currently returns an empty list.

**What else is limited?** SSE MCP, context-usage display, and loop-mode selection for third-party backends are not supported. Image capability is detected at initialization, not after a model switch. Skills snapshots are not cleaned up automatically, and source resources remain linked. See the [architecture guide](docs/architecture.md).

## Testing and development

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
```

This allowlisted test entry point runs 272 tests without model calls. Do not run pytest collection over all of `tests/`: it includes scripts that start real DSH processes, read credentials, or call models.

Live Linux tests have covered responses, streaming, two concurrent sessions, reuse after cancellation, and cross-process resumption. Version 1.1.1 also verified that a read tool's completion event directly carries file text. These are results from a specific environment, not a cross-platform guarantee. See the [verification record](docs/verification.md), [development guide](docs/development.md), and [AGENTS.md](AGENTS.md).

## License

[Apache License 2.0](LICENSE). The DSH bridge uses the MIT license in its separate repository.
