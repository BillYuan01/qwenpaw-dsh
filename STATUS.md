# 项目状态（STATUS.md）

> 一页纸现状。详细开发约定/待办见 [`AGENTS.md`](./AGENTS.md)；逐版变更见 [`CHANGELOG.md`](./CHANGELOG.md)。
> 更新于 **2026-10-25**（v1.1.1 工具文本映射补丁）。

> **v1.1.1 补丁**：只修复 ACP 工具结果文本映射——`ToolCallProgress.content` 的 `ContentToolCallContent(type="content")` 内层 `TextContentBlock` 此前被丢弃，宿主看到的工具输出为空。补丁只改 `core/event_mapper.py`（另含版本常量与新测试/文档），工具结果上限 32768 字符并带截断标记，Agent 文本/思考不截断；image/audio/resource/diff/terminal 仍不投影。**272 项离线测试及新 ZIP 校验通过；现役安装与 live 复验另行记录**。桥保持 `dsh-qwenpaw-bridge` v1.1.0，1.1.1 仍与其兼容。
>
> **2026-10-25 发布说明**：v1.1.0 是 2026-10-21 之后 G1–G5 安全/生命周期修复的首次统一发布，与桥 `dsh-qwenpaw-bridge` v1.1.0 配套。本版验证为**离线/静态**：12 个白名单文件 252 项单测、打包可重现、ZIP 对最终源码逐成员校验；**没有**重新运行真实 DSH、真实凭据、计费模型，也没有验证 macOS/原生 Windows。下方能力表与 live 数字仍是 2026-10-21 的历史证据，不是 v1.1.0 的复验结果。`dist/qwenpaw-dsh-1.1.0-plugin.zip` 由最终源码生成；历史 `1.0.0`/`1.0.1` ZIP 未改动。

## 这是什么

把 **DeepSeek Harness（`dsh`）** 接入 **QwenPaw**，成为**一等智能体**：出现在智能体列表、可在控制台对话、可被其他智能体通过 `chat_with_agent` 调用。全程走标准 **ACP v1**（stdio），两端核心代码零改动。

## 版本

| 项 | 版本 | 说明 |
|---|---|---|
| 仓库发布 | **v1.1.1** | 工具结果文本补丁（2026-10-25） |
| `qwenpaw-dsh`（QwenPaw 侧插件） | **1.1.1** | `qwenpaw-dsh/plugin.json`；桥保持 1.1.0 |
| `dsh-qwenpaw-bridge`（DSH 侧桥） | **1.1.0** | 本轮未改；独立仓库：[`BillYuan01/dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge) |

**运行要求**：QwenPaw `2.2.x`（守卫区间 `[2.2.0, 2.3.0)`）· DSH `>=0.2.0-rc.2 <0.3.0-0`（历史端到端验证基于 `0.2.0-rc.2`）· Python `~/.qwenpaw/venv` · 一个可用的模型路由。

## 能力（下表为 2026-10-21 历史 live 证据，不是 v1.1.0 / 1.1.1 复验）

| 能力 | 状态 | 关键证据 |
|---|---|---|
| 一等智能体 + 事件映射 | ✅（历史） | `chat_with_agent` → ACP → `dsh`；文本/思考/工具事件全映射；会话跨重启恢复。**2026-10-25 复现工具结果文本为空**，1.1.1 修复且离线回归通过；真实事件复测另行记录 |
| **真流式** | ✅ | `qwenpaw` profile + 桥：一轮 **65 chunks**（基线 1），思考 **484 chunks** |
| **审批对齐** | ✅ | 审批卡三径（批准/拒绝/超时）全过；`approval_level`↔DSH 权限档位三档 live 全过 |
| **Skills 投影** | ✅ | `snake_case_skill` → `snake-case-skill` 可用（含 frontmatter 名字重写），取回 `SNAKE-CASE-OK` |
| **MCP 投影** | ✅ | stdio：`ping` → `PONG`；**HTTP(streamable_http)：`http_ping` → `PONG-HTTP`**（夹具日志见 `ListToolsRequest`+`CallToolRequest`）；坏 server 自动"去 MCP 重试一次" |
| **SSE MCP** | ⛔ 设计性跳过 | `core/mcp.py` 记 `sse-unsupported`；同轮其他 MCP 服务器不受影响（DSH 上游不支持 SSE） |
| **图片附件** | ✅ | 内联 base64（先探测 `promptCapabilities.image`）；发纯蓝 PNG → 模型答 "All pixels (0,0,255) = pure blue" |
| **端到端契约套件** | ✅ | `tests/contract/`：`acp` profile **6 passed (126s)**、`qwenpaw`(桥) **6 passed (108s)** |
| **单元测试** | ✅ | 115 单测 + 冒烟 + 5 个 live 集成脚本（流式/Skills/MCP/图片/冒烟） |

## 运行形态支持

| 形态 | 插件装载 | 备注 |
|---|---|---|
| `qwenpaw app` | ✅ | 隔离 `QWENPAW_WORKING_DIR` 冷启动实测：`/api/harnesses` 含 `dsh`（installed/auth=True） |
| `qwenpaw desktop` | ✅ 后端 / ⛔ 窗口 | 后端实测挂载成功；窗口需宿主机的 GTK/Qt 绑定（本机缺，非插件问题） |
| `qwenpaw task`（无头） | ⛔ 不装载 | 宿主设计：直用 `AgentBuilder`，不经插件装载点 → 该形态无第三方 backend |
| `hub` / `tui` / `acp` | 未验证 | 详见 `docs/forms-matrix.md` |

插件目录由 `WORKING_DIR`（`QWENPAW_WORKING_DIR` → `~/.copaw` → `~/.qwenpaw`）唯一决定，装载点唯一（构建 FastAPI app 时）→ **一次安装覆盖所有 app 系形态**。

## 安装（三步）

```bash
qwenpaw plugin install <本仓库>/qwenpaw-dsh --force    # 1) 装 QwenPaw 侧插件
# 2) 装 DSH 侧桥（独立仓库；需要 pnpm）
dsh --profile qwenpaw --from-default-profile acp --dump-config >/dev/null
dsh plugin --profile qwenpaw add /path/to/dsh-qwenpaw-bridge
# 3) 建一个 backend=dsh 的智能体（控制台或 API），backend_settings 指定 profile=qwenpaw
```

## 已知限制（简表）

| # | 限制 | 影响 |
|---|---|---|
| 1 | 审批卡标题缺省时显示兜底名 `external-agent` | 观感问题 |
| 2 | MCP 策略/allowlist 不能经 ACP 传递（仅工具集） | DSH 侧策略粒度较粗 |
| 3 | Skills 影子资源（符号链接）未做沙箱下 live 资源读取验证 | 覆盖缺口 |
| 4 | `context_usage` 无通道（依赖 QwenPaw 侧 `HarnessEventKind.USAGE`，见 `patches/qwenpaw-upstream/0003`） | 第三方 backend 的 token 用量暂不可见 |
| 5 | 图片能力探测取自 `initialize` 默认路由；会话内切模型不重探 | 边界情形 |
| 6 | 跨 DSH 版本 resume 未验证（当前锁 0.2.0-rc.2） | 升级 DSH 需重测 |
| 7 | SSE MCP 不支持（DSH 上游能力缺口） | 只能用 stdio / HTTP |
| 8 | 第三方 backend 无 agent loop 模式入口（宿主 `usesQwenPawBackend` 门控；DSH 无运行时 loop 模式） | 只能用默认 loop |

完整清单见 `AGENTS.md · 未完成 / 已知限制`。

## 仓库布局

```
qwenpaw-dsh/                 QwenPaw 侧插件（装载层 + 适配器核心 + 单测 + 契约套件 + 夹具）
                             DSH 侧桥已拆到独立仓库 BillYuan01/dsh-qwenpaw-bridge
docs/forms-matrix.md         运行形态 × 插件加载矩阵（门6）
docs/mcp-http-sse.md         MCP 传输实测（门7）
docs/dsh-community/          DSH 社区提案存档（含未发布草案）
patches/qwenpaw-upstream/    三条 QwenPaw 侧改进补丁（归档；不对外提交）
tools/                       白名单打包/校验器与安全测试 runner
CHANGELOG.md / AGENTS.md     版本记录 / 开发约定与待办
```

## 与上游的关系

本项目的产出**只进本仓库**（用户 2026-10-19 指令）。此前对上游客群的提交已全部撤下；对 QwenPaw 的三条改进以补丁形式归档在 `patches/qwenpaw-upstream/`（`git am` 可用，含验证结果），不再对外提交。
