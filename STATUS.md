# 项目状态（STATUS.md）

> 一页纸现状。详细开发约定/待办见 [`AGENTS.md`](./AGENTS.md)；逐版变更见 [`CHANGELOG.md`](./CHANGELOG.md)。
> 更新于 **2026-10-19**（v1.0.0 收官）。

## 这是什么

把 **DeepSeek Harness（`dsh`）** 接入 **QwenPaw**，成为**一等智能体**：出现在智能体列表、可在控制台对话、可被其他智能体通过 `chat_with_agent` 调用。全程走标准 **ACP v1**（stdio），两端核心代码零改动。

## 版本

| 项 | 版本 | 说明 |
|---|---|---|
| 仓库发布 | **v1.0.0** | 本文件的时点 |
| `qwenpaw-dsh`（QwenPaw 侧插件） | **1.0.0** | `qwenpaw-dsh/plugin.json` |
| `dsh-qwenpaw-bridge`（DSH 侧桥） | 0.1.0 | 独立版本（DSH bundle 插件），随仓库发布一起分发 |

**运行要求**：QwenPaw `2.2.x`（守卫区间 `[2.2.0, 2.3.0)`）· DSH `0.2.0-rc.2`（守卫 `<0.3.0`）· Python `~/.qwenpaw/venv` · 一个可用的模型路由。

## 能力（全部经 live 实测）

| 能力 | 状态 | 关键证据 |
|---|---|---|
| 一等智能体 + 事件映射 | ✅ | `chat_with_agent` → ACP → `dsh`；文本/思考/工具事件全映射；会话跨重启恢复 |
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
# 2) 装 DSH 侧桥 profile（真流式/权限预设/模型目录）
DSH_HOME=~/.dsh <本仓库>/dsh-qwenpaw-bridge/profile/install.sh
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

完整清单见 `AGENTS.md · 未完成 / 已知限制`。

## 仓库布局

```
qwenpaw-dsh/                 QwenPaw 侧插件（装载层 + 适配器核心 + 单测 + 契约套件 + 夹具）
dsh-qwenpaw-bridge/          DSH 侧桥（bundle 插件 + profile 安装脚本）
docs/forms-matrix.md         运行形态 × 插件加载矩阵（门6）
docs/mcp-http-sse.md         MCP 传输实测（门7）
docs/dsh-community/          DSH 社区提案存档（含未发布草案）
patches/qwenpaw-upstream/    三条 QwenPaw 侧改进补丁（归档；不对外提交）
CHANGELOG.md / AGENTS.md     版本记录 / 开发约定与待办
```

## 与上游的关系

本项目的产出**只进本仓库**（用户 2026-10-19 指令）。此前对上游客群的提交已全部撤下；对 QwenPaw 的三条改进以补丁形式归档在 `patches/qwenpaw-upstream/`（`git am` 可用，含验证结果），不再对外提交。
