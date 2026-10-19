# 草案：在 discussion #9735 下留言（权限态势 / presets）

> 目标帖：https://github.com/deepseek-ai/deepseek-harness/discussions/9735
> `Advertise the sandbox and approval posture over ACP session modes`（Ideas, by garfbargle, 2026-10-15）
> **状态：未发布**（待用户批准）

---

+1 on this, with one related data point from the client side.

We drive `dsh-acp` from QwenPaw (an ACP client) and the concrete gap we hit is: **the approval/sandbox posture is fixed when the process is launched and cannot be switched inside a session.** A client that exposes an approval level (read-only / workspace-write / full access) has to tear down and re-spawn the agent process to change it — which drops the session and re-does all boot work.

What our plugin does to work around it (no DSH core changes, in case it informs the design):

- advertises `_meta['qwenpaw'].permission_presets` on `initialize` / `session/new`;
- adds a `permission_mode` entry to the session `configOptions`, so a standard `session/set_config_option` call switches the preset for that session only;
- also exposes `qwenpaw/permission/set|get` for clients that prefer an explicit method;
- if `ctx.permissionPresets` is unavailable, it advertises nothing and intercepts nothing (fail-soft).

So the client-visible surface we would love upstream is roughly:

1. advertise the posture (and, as this thread suggests, ACP session **modes** are the natural carrier for "read-only / workspace-write / full access");
2. allow a **session-scoped** switch (`session/set_config_option` and/or `session/session/set_mode`), not only a launch-time flag;
3. keep the current behaviour as the default when a client does not ask.

Related: #9693 (ACP `agentOptions()` dropping sandbox/permission config) looks like the same axis from the profile side.
