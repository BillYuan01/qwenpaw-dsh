# DSH × QwenPaw 社区集成

把 **DeepSeek Harness（`dsh`）** 接入 **QwenPaw**，成为一等智能体：出现在智能体列表、可在控制台直接对话、可被其他智能体通过 `chat_with_agent` 调用。全程通过标准 **ACP v1**（stdio）驱动，**不改任何一方的核心代码**。

> **状态**：**v1.0.1（2026-10-21）** —— 能力齐备且经 live 实测：✅ 真流式 · ✅ 审批对齐 · ✅ Skills 投影 · ✅ MCP 投影（stdio + HTTP）· ✅ 图片附件 · ✅ 端到端契约套件（s01–s06）；1.0.1 修复控制台模型目录 500 / 档位为空。
> 一页纸现状见 [`STATUS.md`](./STATUS.md)；路线图、未完成事项与开发约定见 [`AGENTS.md`](./AGENTS.md)；逐版变更见 [`CHANGELOG.md`](./CHANGELOG.md)。

## 组件

| 组件 | 目录 | 作用 |
|---|---|---|
| **qwenpaw-dsh** | `qwenpaw-dsh/` | QwenPaw 侧插件：把 `dsh` 注册为第三方智能体后端。装载层与适配器核心分离（进程池、会话恢复、模型目录、事件映射、审批卡片、权限档位对齐） |
| **dsh-qwenpaw-bridge** | `dsh-qwenpaw-bridge/` | DSH 侧桥插件（可选、推荐）：在标准 ACP 面上补上真·逐 token 流式、权限预设热切换、模型目录 `_meta`。不改 DSH core，fail-soft |

## 快速开始

```bash
# 0) 前置：Node.js；QwenPaw 2.2.x；DSH 0.2.x
npm i -g @deepseek-ai/dsh

# 1) （可选，推荐）安装桥插件 → 控制台里获得真流式
DSH_HOME=~/.dsh ./dsh-qwenpaw-bridge/profile/install.sh
#    然后编辑 ~/.dsh/profiles/qwenpaw/cordis.patch.yml，把你的模型 provider 配好

# 2) 安装 QwenPaw 插件（热装载；未生效就重启一次 QwenPaw）
qwenpaw plugin install ./qwenpaw-dsh

# 3) 控制台 → 设置 → 智能体管理 → 创建智能体 → 后端选「DeepSeek Harness」→ 开聊
#    想让它干活：在智能体配置里把 approval_level 调到需要的档位（见下文「权限档位对齐」）
```

---

## 实现原理

### 总体架构

```
QwenPaw 控制台 / 其他智能体（chat_with_agent）
        │  消息 · 附件 · backend_controls（模型、权限档位）
        ▼
QwenPaw harness runtime ── 按 backend 取适配器（运行时把智能体工作目录作为 cwd）
        │
        ▼
┌─ qwenpaw-dsh 插件（运行在 QwenPaw 进程内）───────────────────────────┐
│  装载层   loaders/monkeypatch.py  ← 把 dsh 注册进 harness registry  │
│  适配器   core/adapter.py         ← DshAdapter：进程池/会话/事件流   │
│  ACP 客户端 core/acp_client.py    ← 标准 ACP v1 over stdio          │
│  审批端口 approvals.py            ← 权限请求 → 控制台审批卡          │
│  档位映射 core/permissions.py     ← approval_level → 沙箱权限模式    │
└─────────────────────────────────────────────────────────────────────┘
        │  spawn：dsh --profile qwenpaw（或 acp；stdio 上跑 ACP v1）
        ▼
DSH = dsh-base + dsh-acp-app（+ dsh-qwenpaw-bridge 桥，若安装）
        ▼
模型（如 opencode-go/deepseek-v4.1-flash）
```

### 1. 装载层：把 dsh 变成"内置后端"

QwenPaw 2.2.x 的第三方后端没有插件入口（provider 目录写死在 `harnesses/registry.py`）。插件在**启动钩子**里对 harness registry 做最小 monkeypatch：

- `PROVIDER_CATALOG` 追加 `dsh` 目录项 → 控制台与运行时立刻可见；
- `create_adapter` / `adapter_config_key` / `get_provider` 按 provider id 分流到本插件。

三条硬契约：

- **fail loud** —— 目标模块/属性缺失直接报错，绝不半生效；
- **精确还原** —— 卸载/热重载时 `unmount` 把 registry 恢复原样（已用"挂载→卸载→再挂载"自测）；
- **版本守卫** —— 仅允许 `qwenpaw_version ∈ [2.2.0, 2.3.0)`。2.3 官方第三方智能体机制落地后，只替换 `loaders/official.py`（feature-detect，不猜 API）。

### 2. 适配器核心：进程池、会话、事件流

`DshAdapter` 实现 QwenPaw 的 harness 适配器接口（`status` / `models` / `run_turn` / `reset_session` 等）：

- **进程池**：按 `(binary, profile, 权限模式, 审批策略)` 分组复用 `dsh` 子进程；任一维变化自动重建（例如控制台改权限档位）；
- **会话映射**：QwenPaw 会话 ↔ ACP `sessionId`，持久化在状态文件（`dsh_sessions.json`），支持**跨 QwenPaw 重启恢复**（`session/load`）；
- **模型切换**：每回合按请求中的模型设置走 `session/set_config_option`；
- **事件映射**（`core/event_mapper.py`，纯函数）：ACP `session/update` → QwenPaw `HarnessEvent`——文本/思考增量、工具开始/进度/完成；`stopReason` → 完成/取消/错误。`usage_update` 暂无宿主通道（见 AGENTS.md 未完成 #4）；
- **附件**：v0.1 按文本路径转发；图片内联（base64）在 v0.2 计划中。

### 3. ACP 通道

标准 ACP v1 over stdio：`spawn(dsh --profile <profile>)` → `initialize` → `session/new` → `session/prompt`。

- `session/update` 推送进队列，由适配器逐条转成 QwenPaw 事件（这就是流式的管道）；
- `session/request_permission` 回调进审批端口（见下节）；
- 关闭时清理**整个进程组**，不留孤儿。

### 4. 审批卡片：权限请求接控制台

DSH 在需要提权时会发 `session/request_permission`。插件把它接到 QwenPaw 的审批服务，控制台出现审批卡：

- **缝**：`HostContext.approval`（`ApprovalPort` 协议，`host.py`）——核心不 import QwenPaw，可脱离宿主单测；
- **实现**：`QwenPawApprovalPort` 调 `get_approval_service().create_pending_summary(...)` → `wait_for_approval(...)`（与 QwenPaw 内置 codex 适配器同款先例）；所有 QwenPaw import 均为惰性；
- **上下文路由**：每个 ACP 会话绑定 `session_id / agent_id / user_id / channel`，随请求传递（一个共享端口服务所有池化会话，互不串扰）；
- **决定映射**：批准 → 选**最小放行**选项（优先 `allow_once`，绝不选 always/persist 语义）；拒绝/超时 → reject；端口缺失/异常 → 保守拒绝（绝不炸回合）；
- **语义**：每次提权请求 = 一张新卡；**批准一次 ≠ 会话级放宽**（one-shot）。

### 5. 权限档位对齐：一处控制

QwenPaw 侧的四档审批级别，自动映射到 DSH 的沙箱权限模式（`core/permissions.py`）：

| QwenPaw `approval_level` | DSH 权限模式 | 效果 |
|---|---|---|
| `OFF` | `danger-full-access` | 无沙箱限制、不询问 |
| `AUTO` / `SMART` | `workspace-write` | 工作区内自由；越界操作触发提权审批卡 |
| `STRICT` | `read-only` | 写操作被硬拦截；提权请求走审批卡 |

- **优先级**：显式 `permission_mode` 设置 > 会话级 `approval_level` > 智能体级 `approval_level` > `AUTO`；
- **生效方式**：解析结果经环境变量 `DSH_PERMISSION_MODE` 注入 `dsh` 子进程（dsh-base 据此设置沙箱模式与询问策略），并纳入进程池配置键——**改档即自动重建，无需手动重启**；
- **行为说明**：在 `workspace-write` 下，越界写不是弹卡而是**先被沙箱硬拒绝**（EROFS），模型随后可发起"提权重试"——那一刻才产生审批卡；读操作不受沙箱限制。

### 6. 桥插件：真流式从哪来

`dsh-acp` 把文本攒到回合末才提交（实测长答案 = 1 个 chunk），标准 ACP 面上拿不到逐 token 增量。`dsh-qwenpaw-bridge` 在 DSH 内部做**最小拦截**（不改 core，全部是公开扩展点）：

- 把已提交输出改为**真·逐 token 增量**推送（实测 93–172 chunks vs 基线 1）；
- 权限预设**热切换**（`session/set_config_option`，进程 PID 不变）；
- 模型目录 `_meta['qwenpaw']`（协议 v1，详见桥的 README）；
- **fail-soft**：所需服务缺失时静默退回标准行为，绝不弄坏 DSH。

安装方式：`profile/install.sh` 把桥作为依赖挂进 `~/.dsh/profiles/qwenpaw`（符号链接 + 绝对路径依赖），与 dsh 插件系统同构、可整体卸载。

### 7. Skills 投影

QwenPaw 工作区技能会投影进 DSH 会话（DSH 只接受 `[a-z0-9-]` 技能名，且以 **SKILL.md frontmatter 的 `name`** 为准）：

- **归一化**：snake_case 等 → kebab（`core/skills.py`；确定性冲突消解 `-2`/`-3`）；
- **影子目录**：`<state_dir>/skills_shadow/<kebab>/`，SKILL.md 重写 name 为真实文件、其余资源符号链接（复制兜底）；
- **注入**：生成 `skills.patch.yml`（`customSkillDirs`），spawn 时以 `--patch` 叠加（无技能则不加）；
- **变更检测**：技能快照（kebab 名 + 宿主 revision）纳入进程池键，变化即自动重建；
- **映射**：`skills_map.json` 保留 kebab ↔ 源技能双向对应。

### 8. MCP 投影

QwenPaw 配置的 MCP servers（工作区驱动卡）会投影进 DSH 会话（`session/new.mcpServers`）：

- **映射**（`core/mcp.py`）：stdio → `McpServerStdio`（命令转绝对路径）、http/streamable_http → `HttpMcpServer`；sse 跳过（DSH 不支持）；env/headers 经宿主 `revealed_*` 取明文、绝不落日志；
- **坏 server 容错**：DSH 以 `failOnStartupError` 挂载 MCP，任一坏 server 会让 `session/new` 整体失败 → 适配器自动**去掉 MCP 重试一次**，会话保持可用并告警；
- **会话级**：随 `session/new` / `session/resume` 携带（resume 就地刷新），不参与进程池键。

### 9. 图片附件

图片附件在路由支持时以**内联 base64 图片块**发送（`core/images.py`）：

- **能力探测**：取 `initialize` 的 `promptCapabilities.image`——不支持时绝不发块（DSH 会直接报错），降级为文本路径；
- **编码**：后缀 allow-list → MIME（png/jpeg/gif/webp/bmp），15 MiB 读盘上限；未知后缀/不可读/超限 → 文本路径 + 日志；
- **发送**：`[text_block, image_block…]`（与 spike s06 一致），多图按附件顺序。

### 10. 安全模型

- 三档沙箱由 DSH 强制执行（`read-only` / `workspace-write` / `danger-full-access`）；
- 审批默认 **fail-closed**：无人应答 → 超时自动拒绝；拒绝后模型被要求停止而非绕行；
- 批准为 one-shot，不构成会话级权限放宽；
- `delegate_external_agent` 路径的 `trusted: false` 保持权限请求交用户决定；
- 社区方案、非官方；请自行评估风险。

---

## 测试与开发

```bash
cd qwenpaw-dsh
# 冒烟（真实 spawn dsh + 模型回合）
~/.qwenpaw/venv/bin/python tests/smoke_adapter.py
# 流式验收（--profile qwenpaw 应多 chunk；acp 基线为 1）
~/.qwenpaw/venv/bin/python tests/stream_check.py --profile qwenpaw
# 单元测试（不需要真实宿主）
~/.qwenpaw/venv/bin/python tests/test_permission_mode.py
~/.qwenpaw/venv/bin/python tests/test_approval_port.py
~/.qwenpaw/venv/bin/python tests/test_loader_wiring.py
```

桥侧的契约测试（s01–s06）见 `dsh-qwenpaw-bridge/TESTING.md`。
**端到端契约套件已随仓库提供**：`qwenpaw-dsh/tests/contract/`（真实 `dsh` 进程跑 ACP v1）

```sh
cd qwenpaw-dsh/tests/contract
./pytest -m contract                                # stock acp profile → 6 passed
DSH_CONTRACT_PROFILE=qwenpaw ./pytest -m contract    # 桥 profile  → 6 passed
```

## 兼容性

**运行形态**：插件装载与形态无关——由 `WORKING_DIR`（`QWENPAW_WORKING_DIR` → `~/.copaw` → `~/.qwenpaw`）唯一决定插件目录，装载点唯一（构建 FastAPI app 时）。
已实测：`qwenpaw app` ✅、`qwenpaw desktop` ✅（后端装载；窗口需宿主机的 GTK/Qt 绑定）；**`qwenpaw task`（无头）不装载插件**（宿主设计）。
完整矩阵与证据：**[`docs/forms-matrix.md`](./docs/forms-matrix.md)**。

- QwenPaw **2.2.x**（装载层隔离；2.3 官方机制落地后仅替换装载层）
- DSH **≥ 0.2.0-rc.2**（developer preview，接口可能变化；已做 `SUPPORTED_DSH` 守卫）
- Node.js（`dsh` 运行时）

## 许可

- `qwenpaw-dsh`：Apache-2.0（与 QwenPaw 一致，见根目录 `LICENSE`）
- `dsh-qwenpaw-bridge`：MIT（见其 `package.json`）

---

## About (English)

A community integration that plugs **DeepSeek Harness** into **QwenPaw** as a
first-class agent backend over standard **ACP v1** (stdio) — no core changes on
either side.

**How it works.** A QwenPaw plugin (`qwenpaw-dsh`) hot-registers the `dsh`
backend via a minimal, exactly-reversible monkeypatch of the harness registry
(loader seam; the core adapter is host-agnostic). The adapter (`DshAdapter`)
drives `dsh --profile <p>` over ACP: process pool keyed by
`(binary, profile, permission-mode, approval-policy)`, persistent session
mapping with cross-restart `session/load`, model switching via
`session/set_config_option`, and pure-function event mapping of
`session/update` → QwenPaw `HarnessEvent`s. Permission requests are routed to
QwenPaw's approval service through an `ApprovalPort` seam (same precedent as the
built-in Codex adapter): approve → narrowest one-shot grant, deny/timeout →
reject, fail-closed. QwenPaw's `approval_level` (OFF/AUTO/SMART/STRICT) is
translated to DSH's sandbox modes (`danger-full-access` / `workspace-write` /
`read-only`) via `DSH_PERMISSION_MODE`, and participates in the pool config key
so a level change rebuilds the backend automatically. An optional DSH-side
bridge plugin (`dsh-qwenpaw-bridge`, fail-soft) adds true token streaming,
hot-switchable permission presets and a one-shot model catalog `_meta`.

Apache-2.0 (plugin) / MIT (bridge). Community project, not affiliated with the
vendors; use at your own risk.
