# Phase 0 Spikes — DSH↔QwenPaw 接入可行性实测套件

> 目的：在写任何适配器代码之前，用最小可运行脚本证伪/证实 plan.md 的关键假设。
> 结论会直接决定 v1 架构与 v1.1 增强项是否必要。
> 本目录的所有实测结果写进 `results/*.json`；本 README 记录**判据、跑法与已实测结论**。
>
> **已正式化**：本目录的 s01–s06 判据现在同时由 `../contract-tests/` 的 pytest 套件
> 承载（`cd contract-tests && ./pytest -m contract`；`DSH_CONTRACT_PROFILE=qwenpaw`
> 可对 bridge profile 跑同一套判据）。spike 脚本仍是判据的唯一事实来源。

---

## 0. 环境与前置

| 项 | 值 |
|---|---|
| DSH | 0.2.0-rc.2（`dsh --version`） |
| ACP SDK | `agent-client-protocol` 0.10.1（QwenPaw venv 内，import 名 `acp`） |
| 运行解释器 | **`~/.qwenpaw/venv/bin/python`**（唯一已装 `acp` 的环境） |
| 隔离 DSH_HOME | 默认 `/tmp/dsh-spikes-*`；凭据用**符号链接**（不复制明文） |
| 模型路由 | `--seed user` → 用户 `acp` profile 的 `opencode-go/deepseek-v4.1-flash`；`--seed fresh` → shipped 模板的 `deepseek-official/deepseek-v4-flash` |

**关于 `/tmp` 的重要说明（本机 agent 沙箱特有）**：本 agent 的 bash 每次调用是独立挂载命名空间，
`/tmp` 在两次 bash 调用之间**不保留**。因此在本 agent 内必须让整个 `run_all.py` 在**同一次调用**里跑完
（后台 job 也是一次调用，`/tmp` 在该 job 生命周期内保留）。人直接在 shell 里逐条跑没有这个限制。

```bash
# 全量（推荐）
~/.qwenpaw/venv/bin/python spikes/run_all.py --seed user --dsh-home /tmp/dsh-spikes
# 单项
~/.qwenpaw/venv/bin/python spikes/s01_acp_roundtrip.py --seed user
# 只跑个别
~/.qwenpaw/venv/bin/python spikes/run_all.py --only s03,s04 --seed user
# 列出
~/.qwenpaw/venv/bin/python spikes/run_all.py --list
```

`--seed user` 会 `cp -r ~/.dsh/profiles/acp` 到隔离 home（保留用户真实的 `opencode-go` patch）；
`--seed fresh` 让 DSH 从 shipped 模板自动生成 `acp` profile。
两种 seed 都会把 `~/.dsh/.credentials.yaml` **软链**进隔离 home。

**计费降级**：若某路由返回 `Insufficient Balance`，依赖模型的检查会标为 `PENDING`（并写 note），
协议层检查照常运行——套件不会因为欠费而误判为架构失败。

---

## 1. 逐项 Spike

格式：**目的 / 预期 / 判据 / 如何跑**。

### s01 — ACP 全链路 + 流式粒度
- **目的**：跑通 `initialize → session/new → prompt → set_config_option → cancel → close`；量化 `session/update` 的**流式粒度**。
- **预期**：链路可用；正文/思考是**整段提交**（step 末一次性发出），非逐 token。
- **判据**：
  - `protocolVersion==1`、`authMethods==[]`、`sessionCapabilities` 含 list/resume/close；
  - `configOptions` 含 `model`；
  - **关键**：>200 字的长答案若在 `agent_message_chunk` 中只出现 **1 个 chunk** ⇒ whole-step；若几十~几百个小 chunk ⇒ 真流式；
  - `cancel` 后 `stopReason=="cancelled"`。
- **如何跑**：`~/.qwenpaw/venv/bin/python spikes/s01_acp_roundtrip.py --seed user`
- **实测**：链路全通过；长答案 **1672 字符 = 1 个 chunk**（思考 226 字符 = 1 个 chunk）⇒ **确认为整段提交**；
  `cancel` 正常返回 `cancelled`；`set_config_option` 生效。延迟：`initialize 0.63s`、`session/new 0.03s`、短 prompt 2.3s、长 prompt 7.9s。

### s02 — session resume（跨进程重启）
- **目的**：进程 A 建会话并植入暗号后退出；进程 B 全新启动后 `session/list` + `session/resume` 并回忆暗号。
- **预期**：可跨进程恢复上下文；resume 不重放历史 update；cwd 不一致/未知 id 报错。
- **判据**：`list` 含该 session；`resume` 后 prompt 能答出 `BANANA-7734`；resume 期间收到 0 条 update；
  wrong-cwd 与 unknown-id 各抛 `RequestError`。
- **如何跑**：`~/.qwenpaw/venv/bin/python spikes/s02_session_resume.py --seed user`
- **实测**：**全部通过**。wrong-cwd 报 `session cwd does not match: <path>`；unknown 报 `session is not resumable`；
  resume 不重放历史（0 条）；resume 延迟 0.019s。
  ⚠️ 副作用：同进程内对同一 id **二次 resume** 会报 `session is already active`——所以负例必须先于成功 resume 执行（脚本已按此排序）。

### s03 — `session/request_permission` 捕获 + toolCallId 合并
- **目的**：验证 DSH 审批请求的形状，以及 adapter 能否用 `toolCallId` 合并出"要执行什么"。
- **预期**：`DSH_PERMISSION_MODE=read-only` 下触发审批；`options=[allow-once,reject-once]`；`toolCall` **只带 toolCallId**；可从前序 `tool_call` 合出 title/rawInput。
- **判据**：捕获到 permission；option id 集合正确；`tool_call` keys ⊆ `{toolCallId}`；合并结果含 `title`/`rawInput`。
- **如何跑**：`~/.qwenpaw/venv/bin/python spikes/s03_permission.py --seed user`
- **实测**：**审批捕获通过**：
  - `options` = `allow-once` / `reject-once`（kind=`allow_once`/`reject_once`）；
  - `tool_call` = **`{toolCallId}`**（确证 plan §3.3 的前提）；
  - 合并成功：`title='bash'`、`rawInput.command="printf ... > .../perm_allow_marker.txt"`。
  - 拒绝路径：捕获到第 2 次审批，选 `reject-once`，文件未创建。
  - ⚠️ 待解释：**allow-once 后文件仍未创建**（69.6s，模型还尝试了 `workspace-write` 升级并被拒）。
    说明 read-only 沙箱下"批准一次"不等于"获得写权限"，模型会走 escalation 且需要**第二次审批**——
    这是 QwenPaw 审批预设映射必须处理的语义（见 README §3 与 addendum）。

### s04 — skills 投影（`customSkillDirs` + SKILL.md 兼容性）
- **目的**：验证 DSH `skill-filesystem` 能否直接扫描 QwenPaw 风格的 `<workspace>/skills/<name>/SKILL.md`；
  重点验证**技能名语法**差异。
- **预期**：overlay 生效；DSH 要求 kebab-case，QwenPaw 的 `snake_case` 技能会被丢弃。
- **判据**：
  - `--dump-config --patch` 合成结果含 `customSkillDirs`；
  - 用 Node 直接调用已安装的 `@deepseek-ai/dsh-skill.isSkillName`（**不依赖模型**）判定语法；
  - 模型 catalog 中：kebab 技能出现、snake_case 技能消失。
- **如何跑**：`~/.qwenpaw/venv/bin/python spikes/s04_skills.py --seed user`
- **实测**：**全部通过**。DSH 语法 = `/^[a-z0-9]+(?:-[a-z0-9]+)*$/`（`spike_underscore=False`、`browser_visible=False`）；
  模型 catalog 实际列表 = `company-background-check, spike-kebab-case, spike-qwenpaw-metadata`，
  **真实 QwenPaw 的 `browser_visible`（snake_case）确实被丢弃**；带 `metadata.qwenpaw` 的 kebab 技能正常加载并召回暗号。
  → 这是 plan.md 不确定项 #4 的确定答案：**frontmatter 兼容（name/description/metadata 可共存），但技能名必须转 kebab-case**。

### s05 — MCP `session/new`（好/坏 server + 回退）
- **目的**：验证 ACP `mcpServers` 挂载；验证 `failOnStartupError` 会让坏 server 拖垮整个 `session/new`，以及"去 MCP 重试"回退是否可行。
- **预期**：好 server 成功且工具可调用；坏 server 使 `session/new` 失败；去掉 MCP 后成功。
- **判据**：好 server → `sessionId` 非空 + 工具生命周期出现 `mcp__spike__ping` + 模型拿到 `PONG`；坏 server → `RequestError`；重试 → 成功。
- **如何跑**：`~/.qwenpaw/venv/bin/python spikes/s05_mcp.py --seed user`（依赖 QwenPaw venv 的 `mcp` 包，附最小 FastMCP server）
- **实测**：**全部通过**。好 server 工具名形如 `mcp__spike__ping`，模型正确取得 `PONG`；
  坏 server（不存在的二进制）令 `session/new` 抛 `RequestError: Internal error`；同连接去掉 MCP 重试成功。

### s06 — 图片能力探测
- **目的**：确认 `initialize.promptCapabilities.image` 是否可用，并真实发一次内联 base64 PNG。
- **预期**：**路由相关**——`opencode-go` 声明 `input:[text,image]` 时为 true；`deepseek-official` 默认路由为 false。
- **判据**：读 capability；true 时发 64×64 纯红 PNG，模型答 "red"；false 时显式拒绝并给出可读错误。
- **如何跑**：`~/.qwenpaw/venv/bin/python spikes/s06_image_probe.py --seed user`（对比 `--seed fresh`）
- **实测**：
  - `--seed user`（opencode-go）：`image=true`，PNG 被接受，模型答 `Red` → **PASS**；
  - `--seed fresh`（deepseek-official）：`image=false`，发图被拒：`inline image prompts were not advertised by this connection`
    → 证实 adapter 必须**先 feature-detect 再发图**。
  - 两种结果分别存于 `results/s06_image_probe.user-seed.json` 与 `results/s06_image_probe.fresh-seed.json`。

---

## 2. 实测结果汇总（`--seed user`，2026-10-18）

| spike | status | 关键实测 |
|---|---|---|
| s01 | partial (12/13) | 全链路通；**整段流式确认**；cancel 正常；set_config_option 生效 |
| s02 | pass (9/9) | 跨进程 resume + 上下文回忆 + cwd/id 负例全部符合预期 |
| s03 | partial (7/8) | 审批捕获/结构/合并全部符合；allow-once 后未落盘（read-only 语义，见上） |
| s04 | pass (10/10) | `customSkillDirs` 可用；**技能名必须 kebab-case**，真实 snake_case 技能被丢弃 |
| s05 | pass (6/6) | 好/坏 MCP + failOnStartupError + 回退全部符合 |
| s06 | pass (3/3) | opencode-go 路由 image=true 且真实可用；deepseek-official 为 false |

`results/summary.json` 为聚合结果；每个 `results/sNN_*.json` 含完整 observations 与 raw（可复现）。

---

## 3. 对 plan.md 的必要修正（spike 直接证据）

1. **架构正确**：整段流式、`toolCallId`-only 审批、`failOnStartupError`、kebab 名字语法——全部与 plan.md 推断一致，§4 的 v1.1 bridge（补真流式）**仍有必要**。
2. **skills 投影不是"格式大概率兼容"这么简单**：frontmatter 兼容，但 **name 必须是 kebab-case**。QwenPaw `skill_pool` 中大量 `snake_case` 技能会被 DSH **静默丢弃**（仅 host log warn）。v1 的 `projection.py` 必须做 **name 归一化 + 映射表**（`browser_visible` → `browser-visible`），否则投影会大面积失效。
3. **审批预设语义要细化**：`read-only` 下"批准一次"并不直接授予写权限，DSH 会走 `workspace-write` escalation 并再次征询；简单"allow-once → 期望执行"的假设不成立。QwenPaw 预设映射需明确"批准 = 允许一次越权尝试"，或改用 `workspace-write` + 目标越界的触发方式（后者本机因 `/tmp`/`$HOME` 属可写区而不可靠）。
4. **`context_usage` 的素材确实存在**：s01 在长答案后收到 `usage_update`（ACP 有，`HarnessEvent` 无通道）——支持 plan.md §5 的诚实标注与"给 QwenPaw 加 USAGE 通道"的小 PR 建议。
5. **`--seed fresh` 不可用于端到端验收**：`deepseek-official` 路由既无图片能力、又可能欠费；`opencode-go`（用户真实路由）才代表目标环境。

---

## 4. 待验证 / 未决

- s03 的 approve→执行语义只做了行为观察，未给出"最自然的预设映射"最终结论（需在真实审批卡片 UI 上再验）。
- 未测：跨 DSH 版本升级后的 resume 兼容性；SSE/HTTP MCP；`session/list` 分页游标；daemon/desktop 形态的插件加载。
- 所有实测在**本机用户凭据 + 隔离 DSH_HOME**下完成；未在干净机器上验证凭据缺失时的整体表现（不过脚本已把这种情况降级为 PENDING）。
