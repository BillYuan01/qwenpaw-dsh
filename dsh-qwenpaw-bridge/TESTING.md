# `dsh-qwenpaw-bridge` v0.1 — test evidence

All numbers below are from a single Linux workstation (2026-10-18) with an
**isolated `DSH_HOME`** and a real `opencode-go/deepseek-v4.1-flash` route.

| Item | Value |
|---|---|
| DSH | `0.2.0-rc.2` |
| Node | `v22.23.2` |
| Interpreter | `~/.qwenpaw/venv/bin/python` (Python 3.12.14, `acp` SDK) |
| Route | `opencode-go/deepseek-v4.1-flash` (`--seed user`; credentials symlinked, never copied) |
| Profile under test | `qwenpaw` = `dsh-base` + `dsh-acp-app` + `@local/dsh-qwenpaw-bridge` |

## Commands

```sh
# live probe (streaming + meta + presets + no duplicate)
~/.qwenpaw/venv/bin/python dsh-qwenpaw-bridge/test/probe.py \
    --dsh-home "$PWD/.bridge-test/home" --work "$PWD/.bridge-test/work" \
    --profile qwenpaw --seed user

# fail-soft: ask for committed mode; committed projection must still work
~/.qwenpaw/venv/bin/python dsh-qwenpaw-bridge/test/probe.py ... --committed

# formalized Phase-0 suite (stock acp profile)
cd contract-tests && DSH_CONTRACT_HOME=$PWD/.contract-test/home ./pytest -m contract

# same suite against the bridge profile
DSH_CONTRACT_HOME=$PWD/.contract-test/qwenpaw-home DSH_CONTRACT_PROFILE=qwenpaw \
    ./pytest -m contract
```

## 1. Live streaming — many chunks, not one committed block

Long prompt: *"Write a single plain-prose paragraph of about 300 words
describing the ocean. No headings, no lists, no tools."*

| Measurement | Stock `acp` baseline (s01) | Bridge v0.1 |
|---|---:|---:|
| answer chars | 1672 (README) / 1826 (re-run) | **1573–1655** (model varies) |
| `agent_message_chunk` count | **1** | **93–172** |
| live chunks (`messageId` = `qwenpaw-live-…`) | 0 | all text chunks |
| committed text chunks | 1 | **0** |
| first chunk at | end of step | ~2–11 s, then dense |

The bridge also projected reasoning: `agent_thought_chunk` follows the same
`reasoning-delta` path. `probe.py` verifies `live_message_ids == True`.

Raw artifact: `.bridge-test/probe-live.json`
(`observations.long_prompt`).

## 2. No duplicate text

`committed_text_chunk_count = 0` while `live_chunk_count = 93–172`: the outbound
relay suppressed every committed text chunk for the attempt, so
`assistant_text()` contains the answer exactly once (1573–1655 chars, not ~2×).

The duplicate suppression is per attempt (`start` frame resets the flags) and
only fires when a live delta for that session was actually sent. The
`--committed` run proves the other half:

| Committed-mode run | Value |
|---|---:|
| `agent_message_chunk` count | 1 |
| committed text chunks | 1 |
| answer chars | 1682 |
| meta `streaming` | `false` |

Raw artifact: `.bridge-test/probe-committed.json`.

## 3. `_meta['qwenpaw']` model catalog + reasoning efforts

`initialize` and `session/new` both returned the complete catalog in one shot
(no per-model probing by the adapter). Observed 4 models:

| provider | model | reasoning efforts | default |
|---|---|---:|---|
| `opencode-go` | `deepseek-v4.1-flash` | `low, high, max` | ✅ |
| `opencode-go` | `glm-5.3-flash` | `low, high, max` | |
| `deepseek-official` | `deepseek-flash` | `off, low, high, max` | |
| `deepseek-official` | `deepseek-v4-pro` | `off, low, high, max` | |

Each entry also carried `selection_value` (the opaque value accepted by
`session/set_config_option`), `context_window`, `max_tokens`, and
`input_modalities`. `current_model` resolved to
`{provider: opencode-go, model: deepseek-v4.1-flash, reasoning_effort: max}`.
The probe checks `every_model_has_efforts`,
`every_model_has_provider_and_selection`, and
`session_meta_repeats_models`.

## 4. Permission presets — advertised and hot-switchable

`_meta['qwenpaw'].permission_presets` = `read-only`, `workspace-write`,
`danger-full-access`; `configOptions` gained a `permission_mode` select whose
`currentValue` matched the meta.

| Step | Result |
|---|---|
| `session/set_config_option(configId=permission_mode, value=read-only)` | `currentValue = read-only` |
| `qwenpaw/permission/set` (wire `_qwenpaw/permission/set`) back to `workspace-write` | `permission_preset = workspace-write` |
| new session with `_meta.qwenpaw.permission_preset = read-only` | meta + `configOptions.currentValue = read-only` |
| process PID before / after | `11` / `11` (**no restart**) |

Raw artifact: `.bridge-test/probe-live.json`
(`observations.permission_switch`).

## 5. Formalized contract suite

`contract-tests/` wraps the s01–s06 spike runners in pytest. Both runs below
used the shared interpreter (pytest is vendored under `contract-tests/.vendor`
because the QwenPaw venv is read-only on this machine).

| Run | Result |
|---|---|
| `acp` profile (stock DSH) | **6 passed**, 80.57 s (`.contract-test-full.log`) |
| `qwenpaw` profile (bridge) | **6 passed**, 66.04 s (`.contract-test-qwenpaw.log`) |

The bridge run exercises the exact same s01–s06 criteria through the plugin.
One model-behavior flake is handled explicitly: s03's reject path depends on
the model attempting a write in a fresh session; the test retries once when
only that check misses (no bridge code path is involved in that check).

## Fail-soft matrix (verified or by construction)

| Non-public dependency | If unavailable |
|---|---|
| `agent/assistant-stream` event | subscription never fires → committed chunks pass through (verified with `--committed`) |
| `ctx.permissionPresets` | no `permission_mode` option, no `_meta` presets, plain ACP |
| `ctx.llm` catalog calls | `models` omitted; session still works |
| `ctx.agentDefaultModel` | bridge uses explicit `provider`/`model` config; if neither, no model option |
| decoration exception | original frame is forwarded unchanged |

## Known limitations

- Live deltas are not buffered/replayed: if a client disconnects mid-attempt,
  the partial live text is what it received (standard ACP has no retraction).
- A failed model attempt's deltas are not retracted before a retry; the
  transcript may show the abandoned prefix followed by the retry.
- `messageId` for live chunks is `qwenpaw-live-<attemptId>`; committed
  `messageId` values are DSH's own UUIDs. Clients that key on `messageId`
  should treat the two namespaces separately.
- `--seed fresh` (`deepseek-official`) is not an acceptance environment: it
  may lack image input or quota. Use `--seed user` with `opencode-go`.
