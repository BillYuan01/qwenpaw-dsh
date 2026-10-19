# Changelog

版本按语义化版本编号；仓库发布 = `qwenpaw-dsh`（QwenPaw 插件）+ `dsh-qwenpaw-bridge`（DSH 侧桥）的组合。

## v1.0.0 — 2026-10-19

**首个稳定版**：v0.2 的五项能力之上，补齐**质量与可验证性**（v0.3 线），并明确"只进本仓库"的产出边界。

### 新增

- **端到端契约套件入库**：`qwenpaw-dsh/tests/contract/`（s01–s06，驱动真实 `dsh` 进程走 ACP v1；自包含，含 `spikes/` 与 MCP 夹具）。实测：`acp` profile **6 passed（126s）**、`qwenpaw`(桥) profile **6 passed（108s）**。
- **运行形态矩阵**（`docs/forms-matrix.md`）：`app` 与 `desktop` 形态的插件装载已实测；`task`（无头）经证实**不装载插件**；插件目录由 `WORKING_DIR` 唯一决定、装载点唯一 → 一次安装覆盖所有 app 系形态。
- **MCP 传输实测**（`docs/mcp-http-sse.md` + 夹具 `tests/fixtures/http_mcp_server.py`）：**HTTP(streamable_http) 端到端通过**（`http_ping` → `PONG-HTTP`，夹具侧 `ListToolsRequest`+`CallToolRequest` 为证）；SSE 为**设计性跳过**且不影响同轮其他服务器。
- **`STATUS.md`**：一页纸项目现状（版本、能力、形态、限制、安装、仓库布局）。
- **上游改进补丁归档**：`patches/qwenpaw-upstream/`（控制台 provider 网格动态化 / harness login-logout 可读 409 / `HarnessEventKind.USAGE` 通道），含应用方式与本地验证结果；**不对外提交**。

### 变更

- README 状态行与 `AGENTS.md` 的"未完成 / 已知限制"清单按实测结果刷新（契约套件、形态、MCP 三项已从待办移出）。
- 明确产出边界：本项目一切产出只提交至本仓库。

### 已知限制（摘要，详见 `AGENTS.md`）

审批卡标题兜底名、MCP 策略不能经 ACP 传递、Skills 影子资源未做沙箱 live 验证、`context_usage` 通道未落地、图片能力探测不随会话内切模型重探、跨 DSH 版本 resume 未验证、SSE MCP 上游不支持。

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
