# qwenpaw-dsh plugin directory

This is the installable root of the QwenPaw-side plugin. It contains `plugin.json`, `plugin.py`, `LICENSE`, and `qwenpaw_dsh/`. Current version: **1.1.1**. This patch fixes nested tool-result text mapping without changing permissions or other runtime behavior.

> Version 1.1.1 passed a live file-read test on Linux: the tool completion event directly carried file text. See the repository's [verification record](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/docs/verification.md) for scope; other environments are not thereby verified. A release ZIP preserves its tag's files. Later documentation updates on main do not rebuild old packages.
>
> The companion DSH-side bridge, [`dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge), remains at **v1.1.0** and is compatible with plugin 1.1.1. The bridge version does not mean DSH core is version 1.1.0; the supported DSH range remains `>=0.2.0-rc.2 <0.3.0-0`.

For installation, architecture, compatibility, and troubleshooting, see the [repository README](https://github.com/BillYuan01/qwenpaw-dsh#readme). Maintenance rules are in the [root AGENTS.md](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/AGENTS.md). Public links remain usable when this directory is installed separately.

## Installation

From the repository root:

```sh
qwenpaw plugin install ./qwenpaw-dsh
```

From another directory, use the actual path to this directory. Installation executes plugin code and modifies the QwenPaw plugin directory. Follow the host version's lifecycle requirements, confirm the target environment first, and restart in a maintenance window if needed.

## Before use

- The current manifest and monkeypatch loader support QwenPaw `>=2.2.0, <2.3.0`. Reserved code for a future official loader does not mean 2.3+ is supported.
- The service environment must have a compatible DSH CLI, ACP SDK, model routing, and credentials. Installing QwenPaw does not configure DSH models.
- The default DSH profile is `acp`. For the bridge's real-time deltas, model catalog, and permission presets, install the separate [`dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge) into DSH, then explicitly set `backend_settings.profile` to `qwenpaw`.
- Linux has integration-test coverage. This project has not completed end-to-end validation on macOS or native Windows. Unverified does not mean either supported or unable to run.
- Approval-port errors, denials, and timeouts mean denial; one approval is not permanent authorization. An explicit `permission_mode` overrides the `approval_level` mapping. read-only is not a file-read allowlist.
- MCP projection supports stdio and HTTP; SSE is skipped. See the full documentation for image, Skills-resource, and session-resumption limits.
- `tests/` mixes unit tests with live integration scripts. The latter may read credentials, call billable models, or execute tools. Do not blindly run the whole directory.

## Distribution and license

`dist/qwenpaw-dsh-1.1.1-plugin.zip` is the 1.1.1 release snapshot; `dist/qwenpaw-dsh-1.1.0-plugin.zip` is the earlier 1.1.0 snapshot. Use `tools/plugin_package.py check` to compare each package with its matching tagged source, not later documentation on main. Historical `1.0.0` / `1.0.1` ZIPs are snapshots of their own releases: **never rebuild an existing version under the same filename**. Version `1.0.1` lacks this directory's `LICENSE` and later safety fixes. To package a new release, use [`tools/plugin_package.py`](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/tools/README.md) from the repository root. Its explicit allowlist includes this directory's `LICENSE`, byte-identical to the root license. Write a new output file and run `check`.

The DSH bridge, its web status card, and source helper tools are not bundled with this plugin and are not installed or started automatically.

Apache License 2.0; see [`LICENSE`](LICENSE) in this directory, identical to the [repository root LICENSE](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/LICENSE). This is a community integration, not an official vendor component.
