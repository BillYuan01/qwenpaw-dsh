# Project status

> Current overview. See [AGENTS.md](AGENTS.md) for maintenance rules, [architecture](docs/architecture.md) for implementation limits, and [CHANGELOG.md](CHANGELOG.md) for release history.
> Updated **2026-11-13** for the v1.1.3 host-gate widening (QwenPaw 2.3.0 final).

## Current release

**v1.1.3** admits QwenPaw **2.3.0 final**. The official harness-plugin mechanism promised in issue #7396 did not land in 2.3.0 (re-verified 2026-11-13: the registry is still a static `PROVIDER_CATALOG`, `PluginType` has no agent-backend member, and none of the official loader's candidate entries exists), while the v1.1.2 gate `[2.2.0, 2.3.0)` excluded `2.3.0` itself — so both mount strategies failed and the DSH backend dropped out of the catalog after a host restart. Before widening anything, all four monkeypatch target modules and every patched attribute were re-verified present and unchanged on 2.3.0. The supported range is now `[2.2.0, 2.4.0)` in both `MonkeypatchLoader.version_range` and the manifest's `qwenpaw_version.max`; `2.4.0` stays excluded until its internals are re-verified the same way. The change is confined to the loader layer, version constants, the version-gate regression tests, and documentation; the ACP protocol, tool mapping, and bridge are untouched.

**v1.1.2** fixed the host-version gate that made the plugin unmountable on QwenPaw **2.3.0b1**. `MonkeypatchLoader._in_range` scraped digits out of each dot-segment, so `"2.3.0b1"` became `(2, 3, 1)` and sorted *above* the exclusive bound `2.3.0`. The gate now compares with `packaging.version.Version` (PEP 440: a pre-release sorts before its base release) and the digit-scraping path survives only as a dependency-free fallback that fails closed on unparseable input.

**v1.1.1** fixed ACP tool-result text mapping. The nested `TextContentBlock` inside `ToolCallProgress.content` / `ContentToolCallContent(type="content")` was previously dropped, leaving host tool output empty. Tool results have a 32768-character limit with a truncation marker; agent text and reasoning are not truncated. image/audio/resource/diff/terminal content is still not projected.

All **278 offline tests passed** in 14 allowlisted files (the "252" figure in v1.1.2 docs omitted the 26 `test_delivery_tools.py` tests, which carry no fixed count assertion). On this machine (Linux, QwenPaw **2.3.0**, DSH 0.2.1-rc.5, bridge 1.1.0) the whitelisted 1.1.3 ZIP was hot-installed without a restart: the mount report shows `host_version=2.3.0` with all four patch targets mounted and `missing=[]`, `dsh` returned to `/api/harnesses` (available/installed true), `/api/harnesses/dsh/models` returned HTTP 200 with 7 models, and a live `chat_with_agent` round trip returned PONG. **Not retested in this version:** approval cards, Skills/MCP/image projection, streaming chunk counts, macOS/native Windows — those paths are unchanged by this gate-only fix. See the [verification record](docs/verification.md). The bridge remains `dsh-qwenpaw-bridge` **v1.1.0**, compatible with plugin 1.1.3.

**v1.1.0 release-stage context (2026-10-25):** This was the first consolidated release of the G1–G5 safety/lifecycle fixes made after 2026-10-21, paired with bridge v1.1.0. Release-stage verification was offline/static: 252 unit tests in 12 allowlisted files, reproducible packaging, and member-by-member ZIP comparison against final source. That stage did not rerun real DSH, credentials, or billable models, or validate macOS/native Windows. Later Linux live checks for 1.1.0 and 1.1.1 are recorded separately in [verification.md](docs/verification.md). The capability figures below remain historical evidence from 2026-10-21. The 1.1.0 ZIP was built from its final release source; historical `1.0.0` / `1.0.1` ZIPs are unchanged.

## What this is

A **DeepSeek Harness (`dsh`)** integration that makes DSH a **first-class QwenPaw agent**: listed among agents, available for console chat, and callable by other agents through `chat_with_agent`. Communication uses standard **ACP v1** over stdio, without modifying either application's core source.

## Versions

| Component | Version | Notes |
|---|---|---|
| Repository release | **v1.1.3** | Host-gate widening for QwenPaw 2.3.0 final (2026-11-13) |
| `qwenpaw-dsh` (QwenPaw plugin) | **1.1.3** | `qwenpaw-dsh/plugin.json`; bridge remains 1.1.0 |
| `dsh-qwenpaw-bridge` (DSH bridge) | **1.1.0** | Unchanged by this patch; [separate repository](https://github.com/BillYuan01/dsh-qwenpaw-bridge) |

**Requirements:** QwenPaw `2.2.x`–`2.3.x` (guard range `[2.2.0, 2.4.0)`), DSH `>=0.2.0-rc.2 <0.3.0-0`, the QwenPaw Python environment, and a working model route. Historical end-to-end tests used DSH `0.2.0-rc.2`; the 2026-10-25 live checks used `0.2.1-rc.2`; the 2026-11-13 live checks used `0.2.1-rc.5`.

## Capabilities: historical live evidence from 2026-10-21

This table is not a 1.1.0 / 1.1.1 retest report. See the current [verification record](docs/verification.md) for later checks.

| Capability | Status | Evidence |
|---|---|---|
| First-class agent and event mapping | Verified historically | `chat_with_agent` → ACP → `dsh`; text/reasoning/tool events mapped; sessions resumed across restarts. Empty tool-result text was reproduced on 2026-10-25, then fixed and verified offline and live in 1.1.1 |
| **Real streaming** | Verified | `qwenpaw` profile + bridge: **65 chunks** in one response (baseline 1), **484 chunks** of reasoning |
| **Approval alignment** | Verified | Approval-card approve/deny/timeout paths passed; all three `approval_level` ↔ DSH permission presets passed live tests |
| **Skills projection** | Verified | `snake_case_skill` → `snake-case-skill`, including frontmatter name rewrite; returned `SNAKE-CASE-OK` |
| **MCP projection** | Verified | stdio: `ping` → `PONG`; **HTTP(streamable_http): `http_ping` → `PONG-HTTP`**, with `ListToolsRequest` + `CallToolRequest` in fixture logs; a bad server triggered one retry without MCP |
| **SSE MCP** | Skipped by design | `core/mcp.py` records `sse-unsupported`; other MCP servers in the turn are unaffected. Upstream DSH does not support SSE |
| **Image attachments** | Verified | Inline base64 after probing `promptCapabilities.image`; a pure-blue PNG produced the answer "All pixels (0,0,255) = pure blue" |
| **End-to-end contract suite** | Verified | `tests/contract/`: `acp` profile **6 passed (126s)**; `qwenpaw` with bridge **6 passed (108s)** |
| **Unit tests** | Verified | 115 unit tests, smoke testing, and 5 live integration scripts (streaming/Skills/MCP/images/smoke) |

## Runtime forms

| Form | Plugin loading | Notes |
|---|---|---|
| `qwenpaw app` | Verified | Cold start with isolated `QWENPAW_WORKING_DIR`: `/api/harnesses` included `dsh` (installed/auth=True) |
| `qwenpaw desktop` | Backend verified; window not verified | Backend mounted successfully; the window required host GTK/Qt bindings absent in the test environment, not a plugin failure |
| `qwenpaw task` (headless) | Does not load plugins | By host design, it uses `AgentBuilder` directly and bypasses plugin loading, so third-party backends are unavailable in this form |
| `hub` / `tui` / `acp` | Unverified | See [forms-matrix.md](docs/forms-matrix.md) (historical, in Chinese) |

`WORKING_DIR` alone determines the plugin directory (`QWENPAW_WORKING_DIR` → `~/.copaw` → `~/.qwenpaw`). There is one loading point, during FastAPI app construction, so **one installation covers app-based forms**.

## Installation in three steps

```bash
qwenpaw plugin install /path/to/qwenpaw-dsh/qwenpaw-dsh --force    # 1) Install the QwenPaw plugin
# 2) Install the DSH bridge (separate repository; requires pnpm)
dsh --profile qwenpaw --from-default-profile acp --dump-config >/dev/null
dsh plugin --profile qwenpaw add /path/to/dsh-qwenpaw-bridge
# 3) Create a backend=dsh agent in the console or API; set backend_settings.profile=qwenpaw
```

See the [README](README.md) for the full installation instructions and update precautions.

## Known limits

| # | Limit | Impact |
|---|---|---|
| 1 | Approval cards without a title use `external-agent` | Presentation only |
| 2 | MCP policies/allowlists cannot be fully passed through ACP; tool sets are projected | Coarser policy control on the DSH side |
| 3 | Live reads of Skills shadow resources (symlinks) under sandboxing have not been verified | Coverage gap |
| 4 | No `context_usage` channel; depends on QwenPaw `HarnessEventKind.USAGE`, see `patches/qwenpaw-upstream/0003` | Token usage is not visible for third-party backends |
| 5 | Image capability comes from the default route at `initialize`; model switches do not re-probe | Edge-case limitation |
| 6 | Cross-DSH-version resume is unverified; the historical baseline was 0.2.0-rc.2 | DSH upgrades require retesting |
| 7 | SSE MCP is unsupported upstream | Use stdio or HTTP |
| 8 | Third-party backends have no agent loop-mode entry point (`usesQwenPawBackend` host gate; DSH has no runtime loop mode) | Only the default loop is available |

See [architecture.md](docs/architecture.md) for runtime boundaries and [AGENTS.md](AGENTS.md) for required safeguards. read-only restricts modifications; it is not a file-read allowlist.

## Repository layout

```text
qwenpaw-dsh/                 QwenPaw plugin: loader, adapter core, unit/contract tests, fixtures
                             The DSH bridge lives in its separate repository
docs/forms-matrix.md         Runtime forms × plugin-loading matrix (gate 6)
docs/mcp-http-sse.md         MCP transport tests (gate 7)
docs/dsh-community/          Archived DSH community proposals, including unpublished drafts
patches/qwenpaw-upstream/    Three archived QwenPaw improvement patches; not submitted upstream
tools/                       Allowlisted package builder/checker and safe test runner
CHANGELOG.md / AGENTS.md     Release history / maintenance rules
```

## Upstream relationship

Project deliverables stay **in this repository only**, following the project owner's 2026-10-19 direction. Earlier upstream submissions were withdrawn. Three QwenPaw improvements remain archived in `patches/qwenpaw-upstream/` as `git am`-compatible patches with verification results; they are not being submitted upstream again.
