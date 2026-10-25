# Verification record

The checks below have been completed; they are not a compatibility guarantee for every environment. Public records describe synthetic test scope and results without machine identifiers, private paths, session logs, or credentials.

## 2026-10-25: 1.1.1 tool-result mapping

Environment: Linux, QwenPaw 2.2.2.post1, DSH 0.2.1-rc.2, plugin 1.1.1, bridge 1.1.0.

- All 272 offline tests passed, with 0 skipped. Package members matched the release source byte for byte.
- The plugin was hot-loaded by the host, and installed files matched the official ZIP.
- A live model request read a synthetic temporary file. The prompt did not include the file's random marker.
- The run received start and completion events for a single read tool. The completion event directly carried 182 characters of text, including the random marker; the final response matched the file contents.
- The test file was unchanged. No other tools or privilege-escalation requests were observed. The probe used read-only mode and an approval port that denied escalation.

This verifies tool-text projection through a live integration, without reconstructing output from session logs afterward. It does not mean read-only enforces a file-read allowlist: that mode restricts modifications, not the full range of reads.

## 2026-10-25: 1.1.0 baseline integration

The same Linux host and DSH versions, with both plugin and bridge at 1.1.0.

| Check | Path | Result |
|---|---|---|
| Short response | Active QwenPaw HTTP API | Returned PONG and completed normally; no tool calls observed |
| Real-time text | Installed adapter → ACP | Response arrived in multiple deltas, not as a single delivery at the end of the turn |
| Concurrent-session isolation | One adapter, two sessions on one connection | Each response contained only its own marker, with no cross-session content; both ended normally |
| Cancellation and reuse | Installed adapter → ACP | Cancellation notification sent, consumer exited, and a subsequent request in the same session completed normally |
| Cross-process resumption | New adapter process, existing session mapping | After resumption, the model could answer about a synthetic topic from the earlier conversation |
| File read | Installed adapter → ACP | read returned synthetic file contents; the host completion event lost the text at that time, later fixed in 1.1.1 |

The HTTP API PONG used the agent's existing permission settings; standalone adapter tests used read-only. Different test paths must not be treated as equivalent permission checks. The cancellation test confirmed notification, consumer exit, and subsequent usability; it did not measure when provider billing stopped.

The original 1.1.0 release-stage offline checks covered 252 Python tests, 39 bridge Node tests, 32 helper tests, 6 atomic-write tests, and 5 runner self-tests. Version 1.1.1 added 20 mapping tests. Historical counts are not rewritten for newer versions.

## Not yet covered

- Dedicated end-to-end tests on macOS, native Windows, and WSL.
- Every MCP, image, sandboxed Skills-resource, and approval-interaction scenario with the current release pair.
- Resumption across DSH versions, every model provider, and every failure condition.
- Strong read isolation for all private paths.

Earlier runtime-form and MCP evidence remains in [forms-matrix.md](forms-matrix.md) and [mcp-http-sse.md](mcp-http-sse.md) (historical, in Chinese). Before reproducing live tests, inspect the entry points and confirm the target, model costs, permissions, and output scope. See the [development guide](development.md).
