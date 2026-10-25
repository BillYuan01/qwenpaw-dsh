# Architecture

This repository maintains the QwenPaw-side Python plugin. The DSH-side bridge lives in [dsh-qwenpaw-bridge](https://github.com/BillYuan01/dsh-qwenpaw-bridge). They communicate through ACP v1 over stdio.

## Loading

`plugin.py` assembles `HostContext` and registers lifecycle hooks. The QwenPaw 2.2.x loader replaces the harness registry and references imported by value within the running process; it does not modify installed host files. It checks targets and versions before mounting and restores the original symbols on unloading. `OfficialLoader` reserves a future interface, not 2.3+ support. `DirectLoader` is for tests and embedding, not a production fallback after a loading failure.

`core/` depends on host event contracts and the ACP SDK, but does not import the registry or router. Approval services connect through `ApprovalPort`; concrete host imports stay in the port implementation.

## Turns, connections, and cancellation

The connection-pool key includes the binary, profile, permission mode, approval policy, and Skills snapshot. A shared pool key does not imply the same process: the live mapping also records the connection object and generation. After a process is recreated, the plugin attempts session resumption first. Only if that fails does it create a new session and explain that context was rebuilt.

Each ACP session has its own queue. Turns and resets for the same QwenPaw session share a lock; different sessions can run concurrently. Closing a connection explicitly wakes waiting consumers rather than relying on transport errors. Terminal states belong to the current turn only. A missing response, missing stop reason, or independently cancelled background prompt must not count as normal completion.

On user cancellation, the plugin sends `session/cancel` at most once and waits for a bounded time for the prompt to settle. A connection whose prompt cannot be confirmed settled is synchronously removed from the reusable pool before being handed to a close task. Removed connections still have a cleanup owner; a failed replacement startup must not orphan the old process. A failed or cancelled close task does not count as a successful close.

Wait deadlines limit how long the caller waits; they do not guarantee an uncooperative coroutine or subprocess stops immediately. Process-tree termination is best effort, and logs distinguish an attempted termination from a confirmed close.

## Models and tool events

Model choices come first from the session's `configOptions`, then from the catalog cache. Catalog refresh uses single-flight probing. Catalogs are normally cached for 600 seconds; failure snapshots are retained briefly. Probing starts DSH and creates a session; it is not a file-only check.

An explicit model is applied before reasoning effort. The returned `currentValue` must match; otherwise the turn fails without sending a prompt. Effort choices come only from the current model, not a union of all models. Models with the same name retain provider identity. Missing or blank settings leave the current default unchanged; they cannot explicitly reset an ACP effort setting to an empty string.

`event_mapper.py` converts responses, reasoning, and tool start/progress/completion into host events. Version 1.1.1 supports text inside `type=content` wrappers. Tool previews are limited to 32768 characters with a truncation marker; depth limits and cycle detection constrain malformed payloads. Normal responses and reasoning are not truncated. Images, audio, blobs, diffs, terminal references, and unknown content are not stringified, and text is not extracted from rawInput/rawOutput. A failed tool still emits TOOL_COMPLETED, preserving `data.status`.

## Skills, MCP, and images

- **Skills:** Names are normalized to kebab case. YAML node positions allow changing only a unique top-level name. Each combination of source paths, versions, and target names produces a separate snapshot containing its patch and shadow tree, so turns do not overwrite each other's resources. Old snapshots are not automatically deleted, and source resources remain live links. Manifest keys are untrusted: cleanup accepts only valid single-level names and never follows links to delete their targets. Concurrent replacement of a parent directory remains a residual race risk.
- **MCP:** Configuration is projected per turn. Session fingerprints cover commands, arguments, URLs, env, and headers. Credential-bearing digests are compared only in memory, never written to disk. On a change, the plugin closes the session before resuming it with the new list. Close failure retires the connection and blocks the prompt. Resume failure creates a new session with a warning. If attaching MCP to a new session fails, it may retry once without MCP. stdio and HTTP are supported; SSE is skipped. Host Driver policies are not fully propagated.
- **Images:** If initialization capabilities permit, PNG/JPEG/GIF/WebP/BMP images are inlined. Only regular files are read, with a post-open check and bounded reads, up to 15 MiB. Symlinks and special files are rejected. Failures fall back to a text path. Switching models within a session does not re-probe image capability.

## Current interface limits

`/status` reads in-memory counters and configuration summaries only; it does not start DSH. `history()` returns an empty list: ACP session resumption does not reconstruct host chat history from DSH. `context_usage` and loop-mode selection for third-party backends are not currently available.

Permission mapping follows this precedence: explicit permission_mode, session approval_level, agent approval_level, then AUTO by default. STRICT maps to read-only, SMART/AUTO to workspace-write, and OFF to danger-full-access. Denials, timeouts, and approval-port failures must not become grants; a one-time approval does not confer permanent permissions. read-only restricts modifications, not the full range of readable files.
