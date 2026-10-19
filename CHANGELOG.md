# Changelog

版本按语义化版本编号；仓库发布 = `qwenpaw-dsh`（QwenPaw 插件）+ `dsh-qwenpaw-bridge`（DSH 侧桥）的组合。

## v0.2.0 — 2026-10-19

**补全核心能力（5/5）**：真流式 · 审批对齐 · Skills 投影 · MCP 投影 · 图片附件。

### 新增

- **真流式**：安装桥 profile（`qwenpaw`）后，控制台逐 token 输出（实测 65 chunks，基线为 1）。
- **审批对齐**：DSH 权限请求 → QwenPaw 审批卡（批准/拒绝/超时；最小放行、one-shot）；`approval_level`（STRICT/SMART/AUTO/OFF）自动映射到 DSH 权限模式（read-only / workspace-write / danger-full-access），控制台一处控制、改档自动重建。
- **Skills 投影**：kebab 归一化 + 影子目录（重写 SKILL.md frontmatter `name`）+ `--patch customSkillDirs` 注入；技能快照纳入进程池键。
- **MCP 投影**：`session/new.mcpServers`（stdio 命令转绝对路径 / http；sse 跳过）；坏 server 自动"去 MCP 重试一次"，会话保持可用。
- **图片附件**：内联 base64 图片块（先探测 `promptCapabilities.image`；不支持/未知后缀/超限优雅降级为文本路径）。

### 改进

- 进程池键扩展：权限模式、技能快照（变化自动重建，无需手动重启）。
- 审批上下文按会话路由；环境变量/密钥绝不落日志。

### 测试

- 115 项单元测试 + 5 个 live 集成脚本（流式 / Skills / MCP / 图片 / 冒烟），全部通过。

## v0.1.0 — 2026-10-18

首个公开版本：

- QwenPaw 插件（装载层/适配器核心分离）：插件热装载、`dsh` 一等智能体、文本/思考/工具事件映射、会话跨重启恢复、进程池。
- 桥插件：真流式、权限预设热切换、模型目录 `_meta`（协议 v1）。
- 契约测试 s01–s06 判据在两个 profile 上通过。
