# 草案：在 discussion #9928 下留言（streaming）

> 目标帖：https://github.com/deepseek-ai/deepseek-harness/discussions/9928
> `ACP: opt-in incremental agent_message_chunk / agent_thought_chunk while a response streams`（Ideas, by foxj77, 2026-10-17）
> **状态：未发布**（待用户批准）

---

Strong +1 — and we have a working implementation of exactly this shape, plus numbers from a real client, in case they help settle the design.

**What we built (plugin half, no DSH core changes)**

`dsh-qwenpaw-bridge` (a DSH bundle plugin, https://github.com/BillYuan01/qwenpaw-dsh) subscribes to `agent/assistant-stream` `text-delta` / `reasoning-delta` and re-projects them as standard `session/update` `agent_message_chunk` / `agent_thought_chunk`, while **suppressing the duplicate committed text/thought** so the client never sees the same content twice. It is fail-soft: if the event is missing, the subscription fails, or the client opts out, the stock committed projection is used unchanged.

**Measured difference on a real ACP client (QwenPaw)**

| | stock `dsh-acp` | with opt-in deltas |
|---|---|---|
| `session/update` text chunks for one long answer | **1** (whole message committed at the end) | **65** |
| reasoning chunks | 0 until commit | **484** |
| time-to-first-visible-token | end of turn | immediate |

**Design notes that may be useful for the upstream version**

1. **Opt-in matters.** Committed-only is a deliberate, correct default for many controllers; make it a config value (e.g. `streaming: false`) rather than a behaviour change.
2. **Suppression is the hard part.** The committed projection still has to arrive (or not) in a way that does not double the text. Our rule: mark deltas as provisional per (session, message) and drop the committed body when its deltas were already emitted; on any mismatch, fall back to committed and drop nothing.
3. **Order and settlement.** Deltas must be serialized per session (same as today's updates) and a delta stream that ends without settlement must not be treated as success.
4. **Capability advertisement.** It is worth advertising the support on `initialize` so clients can choose without guessing.

Happy to port our implementation into `packages/acp` if the team wants it — otherwise the plugin stays available for anyone who needs this today (profile-install, no core patch).
