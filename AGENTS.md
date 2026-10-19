# AGENTS.md — 项目说明（面向 AI agents 与贡献者）

> 面向在本仓库工作的 AI coding agents（与人类贡献者）：项目现状、**路线图**、
> **实现地图**、**未完成事项**与开发约定。使用者文档（含实现原理详解）见 `README.md`。

## 项目一句话

把 **DeepSeek Harness（`dsh`）** 接入 **QwenPaw** 成为一等智能体后端，走标准 **ACP v1**（stdio），不改双方核心：

- `qwenpaw-dsh/` — QwenPaw 插件（装载层/适配器核心分离；进程池、会话恢复、事件映射、模型目录、审批卡片、权限档位对齐）
- `dsh-qwenpaw-bridge/` — DSH 侧桥插件（真流式、权限预设热切换、模型目录 `_meta`；fail-soft）

## 现状（2026-10-18）

**v0.1（已发布）**

- 插件热装载；`dsh` 出现在 `/api/harnesses`；控制台可创建 `dsh` 智能体并对话（多轮连续 ✓）
- `run_turn`：文本/思考、工具生命周期、终态映射；进程池按 `(binary, profile, 权限模式, 审批策略)` 复用
- 会话跨重启 `session/resume`（`dsh_sessions.json`）
- 桥插件：真流式（长答案 93–172 chunks，基线为 1）、权限预设热切换（PID 不变）、模型目录 `_meta`（协议 v1）
- 契约测试：s01–s06 判据已在两个 profile 上通过（套件暂未收录进本仓库，见"未完成 #6"）

**v0.2（进行中，2/5）**

- ✅ 真流式：`qwenpaw` profile + 桥 → 控制台逐 token；standalone 实测 text_chunks=65（基线 1）
- ✅ 审批对齐：权限请求 → 控制台审批卡（批准/拒绝/超时三径 live 实测）；`approval_level` → DSH 权限模式（OFF/AUTO/STRICT 三档 live 实测）
- ⏳ 剩余：MCP 投影（#2）/ Skills 投影（#3）/ 图片附件（#4）

## 实现地图（代码结构）

```
qwenpaw-dsh/
├─ plugin.py                 插件入口：启动钩子、HostContext 组装、卸载
├─ qwenpaw_dsh/
│  ├─ host.py                宿主端口与数据类（ApprovalPort / ApprovalRequest / ApprovalDecision / HostContext）
│  ├─ approvals.py           审批卡端口实现（惰性 import 宿主审批服务；fail-closed）
│  ├─ provider.py            ProviderSpec / 适配器配置键（含解析后的权限模式）
│  ├─ core/
│  │  ├─ adapter.py          DshAdapter：进程池、会话映射与恢复、回合、审批上下文路由
│  │  ├─ acp_client.py       ACP v1 客户端：spawn / initialize / session/* / request_permission
│  │  ├─ event_mapper.py     session/update → HarnessEvent（纯函数）
│  │  └─ permissions.py      approval_level → DSH 权限模式映射（单一来源）
│  └─ loaders/               装载层：monkeypatch（2.2.x）/ official（2.3+ 预留）/ direct（独立运行）/ port（接口）
└─ tests/                    冒烟 + 流式验收 + 单元测试（见「开发约定 · 验证」）
```

## 路线图（Roadmap）

### v0.2 — 补全核心能力

1. **审批卡片** ✅（2026-10-18）：权限请求 → 宿主审批卡（批准/拒绝/超时三径 live 实测通过）；`approval_level`（STRICT/SMART/AUTO/OFF）→ DSH 权限模式（read-only/workspace-write/danger-full-access）对齐（实现：`qwenpaw_dsh/approvals.py` + `qwenpaw_dsh/core/permissions.py`；缝：`host.py` 的 `ApprovalPort`）。行为备注：workspace-write 下越界操作先被沙箱硬拒绝、由模型提权重试触发卡片；批准为 one-shot（不构成会话级放宽）
2. **MCP 投影**：`session/new.mcpServers`（stdio 需绝对路径；不支持 sse；坏 server 需"去 MCP 重试一次"）
3. **Skills 投影**：`--patch customSkillDirs` + **kebab 归一化与双向映射**（DSH 强制 `[a-z0-9-]`，snake_case 技能会被静默丢弃）
4. **图片附件**：内联 base64 图片块（需目标路由声明 image 能力，先探测再发）
5. **切 `qwenpaw` profile** ✅（2026-10-18）：安装桥 profile 后控制台获得真流式；standalone 实测 text_chunks=65（基线 1）

### v0.3 — 上游化（Upstream）

6. 对 QwenPaw 的 PR 计划：
   - **PR-1** 控制台 provider 网格动态化（消除"未知 backend 回退 Codex 文案"）— 普惠、低风险、可先提
   - **PR-2** `harnesses/dsh/` 适配器 + registry 目录项（对齐 codex/qoder 结构）
   - **PR-3** login 路由容错 + `HarnessEventKind.USAGE` 通道（context 用量可见）
7. 对 DSH 的 PR：**PR-4** `dsh-acp` opt-in streaming + `_meta` + 会话级权限预设（上游接受后可删桥插件的流式补偿）
8. **QwenPaw 2.3 迁移**：官方"第三方 agent 插件"机制落地后，只替换 `loaders/official.py`（只做 feature-detect，不猜 API；官方动态见 QwenPaw issue #7396）

## 未完成 / 已知限制（"还没做到的"）

1. **审批卡标题可读性**：权限请求缺 title 时卡片显示兜底名（"external-agent"），可补传工具标题/命令提升可读性
2. **MCP**：策略/allowlist 不可经 ACP 传递；sse 不支持
3. **Skills**：归一化层未实现（当前 snake_case 技能进不去）
4. **`context_usage`**：ACP 有 `usage_update`，宿主事件模型暂无通道（依赖 PR-3）
5. **图片**：v0.1 附件按文本路径转发；base64 未实现
6. **契约测试套件**（s01–s06）暂未收录进本仓库
7. **未验证**：desktop/daemon 形态的插件加载；跨 DSH 版本 resume；SSE/HTTP MCP
8. **模型目录差异**：QwenPaw 控制台对"不在后端目录中的模型"有归一化行为（选择模型时注意）
9. **DSH 为 developer preview**（0.2.0-rc.2）：仅依赖标准 ACP v1 + feature-detect；`SUPPORTED_DSH=<0.3.0` 守卫

## 开发约定

- **依赖方向**：`core/**` 禁止 import QwenPaw registry/routers 内部符号；仅 `loaders/monkeypatch.py` 允许（2.3 只换 `loaders/official.py`）
- **fail loud**：装载缺模块/属性直接报错，绝不半生效；`unmount` 精确还原
- **版本守卫**：monkeypatch 变体 `qwenpaw_version ∈ [2.2.0, 2.3.0)`
- **验证**：改核心后跑 `qwenpaw-dsh/tests/smoke_adapter.py`；涉审批/档位/装载加跑 `test_approval_port.py`、`test_permission_mode.py`、`test_loader_wiring.py`；流式相关跑 `stream_check.py --profile qwenpaw`（需装好 `acp` SDK 的 Python 环境 + 本机 `dsh` + 模型 key）
- **提交信息写清楚**；许可：Apache-2.0（插件）/ MIT（桥）
