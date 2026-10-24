# Changelog

版本按语义化版本编号。本仓库发布 `qwenpaw-dsh`（QwenPaw 插件）；DSH 侧桥 `dsh-qwenpaw-bridge` 已拆到独立仓库发布。

## v1.1.0 — 2026-10-25

**配套发布版（与 `dsh-qwenpaw-bridge` v1.1.0 同版号）。** 这是 2026-10-21 之后安全与生命周期修复的首次统一发布：把之前只在源码里的收紧行为、安装包工具与安全测试入口，收进一个可追溯的版本。

用人话说：这次没有加新功能，重点是**少猜、少装作成功**——模型/推理档位没确认就不发 prompt；取消、关闭、连接退役失败时如实报错，不把未知当完成；Skills 投影按快照隔离，旧快照不自动清理；图片只读普通文件且有上限；MCP 变化会真的重建会话。`history()` 仍返回空列表（ACP 恢复不等于重建 QwenPaw 历史）。安装包改为显式白名单构建，历史 ZIP 保持原样。

发布边界：以下条目是**离线/静态验证**结果；本版没有重新运行真实 DSH、真实凭据或计费模型，也没有验证 macOS/原生 Windows。历史 live 数字仍属于对应时点，不是本版复验。

### 变更

- **Skills 投影按快照隔离（R12/G4，关闭 G2 记录的共享影子目录缺口）**：每次独特输入写入自己的 `<state_dir>/skills_snapshots/<sha256>/`（`skills_shadow/` + `skills.patch.yml` + `skills_map.json`），快照 id 覆盖 kebab 目标名、源名、revision 与**规范化源目录路径**。不同并发回合不再共享/重写 `skills_shadow` 或 `skills.patch.yml`；A 回合的资源在 B 回合（不同快照或空 Skills）之后仍按 A 的快照可读，空回合不清理任何快照。搬目录即使 revision 不变也会新建快照并重链资源；同源同 revision 仍复用。连接对象记录 `skills_snapshot`/`skills_root` 仅作归属/诊断，进程池键继续包含快照 token（现在含源位置）。**不自动 GC**：旧快照可能积累，需人工删除；不新增后台任务。快照根/快照目录被替换为符号链接时保守拒绝；快照内清单清理仍沿用 G1 的单层 kebab/`lstat`/不跟随链接边界。源资源仍是实时符号链接，用户编辑源文件会立即影响 DSH，不宣称来源不可变。未知 revision 的内容哈希改为有界遍历（文件数/字节预算，超限返回未知并告警）。
- **图片读取有界且拒绝非普通文件（R11）**：`encode_image` 先 `lstat`，只接受普通文件；符号链接/FIFO/设备/socket 返回 `not-regular`，目录保持既有 `unreadable`。以 `O_RDONLY|O_CLOEXEC|O_NONBLOCK|O_NOFOLLOW` 打开并 `fstat` 复核 regular/size/inode；`O_NONBLOCK` 缺失的平台保守返回 `unsupported-platform`；`O_NOFOLLOW` 缺失时用 lstat+inode 一致性检查（残余 TOCTOU 已文档化）。读取改为短读循环，最多 `MAX_IMAGE_BYTES + 1` 字节，增长/撒谎超限返回 `too-large`，fd 在异常/取消路径也关闭。MIME、后缀白名单与既有 reason 不变。
- **frontmatter `name` 结构化重写（R13）**：用已装 PyYAML `compose` 定位**唯一顶层** `name` 的 value 节点并只替换该 span，不再按行正则。支持单/双引号键、折叠 `>`/字面 `|` 块、CRLF、`...` 文档结束与缺 `name` 插入；其他键、注释、顺序与 body 保留。重复顶层 `name`、非标量 name、非法 YAML、非 mapping 根或重写后校验失败一律 `UnsafeFrontmatter`，该技能记为 `invalid-frontmatter` 跳过，不写出坏投影。重复非 `name` 键不解释、原样保留。PyYAML 是宿主已有依赖（6.0.x），未新增包。
- 新增 `tests/test_file_projection_g4.py`（27 项离线回归：FIFO 不阻塞/符号链接/目录/stat 撒谎/短读/fd 关闭、双快照隔离/空回合不清 A/同源复用/搬目录重建/token 含路径/快照根链接拒绝/有界内容哈希/坏 frontmatter 跳过、连接快照绑定、折叠/字面/引号键/缺名/重复名/非法 YAML/CRLF/`...`/注释与 body 保留）。`test_skills_shadow_security.py` 的 12 项改为针对快照根，断言与边界不变。
- **显式模型/推理档位真正生效（R07）**：`_apply_model` 不再在目录缓存缺失时直接返回，也不再忽略 `reasoning_effort`。选择值先按**本次会话** `session/new`/`session/resume` 的 `configOptions` 解析（可避免额外探测），其次使用目录缓存/一次 single-flight 目录发现；绝不按模型名拼 `["provider","model"]`，同名不同 provider 报歧义。写入顺序固定为 model → effort，并按 dsh-acp `session/set_config_option` 响应里的 `configOptions.currentValue` 回读确认（已核对 `dsh-acp/lib/index.js:1332` 与 ACP SDK `SetSessionConfigOptionResponse`）；不匹配、缺字段、抛错或无法解析时输出可见 ERROR 且**不发送 prompt**。推理档位只接受当前实际模型广告的值（`off`/`false` 只是普通字符串）；空值/未指定保持 profile 默认；非字符串值报错。探测失败单独记录（短 TTL），不会被当成“模型不存在”而静默用默认模型继续。目录探测仍会启动 DSH 并新建/关闭探测会话，文档已标注。
- **宿主命令与会话历史契约（R08）**：`DshAdapter` 继承稳定基类 `HarnessAdapter`，宿主调用 `run_command`/`history`/`discover_*` 不再 `AttributeError`。实现 `/status`（`TEXT_DELTA` + `COMPLETED`，只读适配器内存：profile、权限模式、审批策略、binary basename、连接/会话计数、目录缓存状态；不 spawn、不探测模型、不读凭据、不含绝对路径/会话 id/env）；未声明命令返回可见 ERROR。`history()` 明确返回空列表：恢复 ACP 协议会话不等于重建 QwenPaw 历史，不虚构 provider 历史/模型回放，也不清空持久会话。
- 新增 `tests/test_model_contract.py`（22 项纯离线回归：无缓存探测一次并按序下发、会话响应免探测、缓存命中不探测、过期刷新、unknown provider/model、同名多 provider 歧义、缺 `selection_value` 不拼值、effort 合法/非法/`off` 字面/仅 effort/当前模型不可判定、currentValue 不匹配/缺 configOptions/写入抛错、无显式设置保持默认、并发 single-flight、双会话值不串、探测/写入中取消无 prompt 无残留锁）与 `tests/test_host_contract.py`（8 项：基类契约完整、`/status` 形状与隐私、未知命令 ERROR、`history()` 空且零状态变化、`status()` 打桩 `_binary_version`、命令声明与实现一致、`discover_*` 默认继承）。
- **会话生命周期隔离（取消 / 并发 / 连接身份 / MCP）**：
  - 每个 ACP 会话使用独立事件队列，同一 QwenPaw 会话的回合串行、不同会话可并发；删除共享队列清空与无 session 的 `__done__` 全局哨兵，终态只属于本轮。两个并发会话各自收到全部帧与各自终态。
  - `_live` 记录具体连接对象与代次（不再只比连接池键）：DSH 进程重建后同键新连接必须先 `session/resume`，失败才新建，绝不直接 prompt 持久化的旧 ACP id；清理连接时同步清 `_live` 与审批路由 context。
  - MCP 投影改为**本轮局部变量**，不再从共享 `_mcp_plan` 读取。新增会话级指纹，覆盖 name/transport/command/args/url/env/header（含凭据明文参与摘要；摘要只在内存比较，不落日志/状态文件/报告），排序稳定。指纹变化时先 `session/close` 再带新列表 `session/resume`——依据已装 DSH 0.2.1-rc.2 源码 `dsh-acp/lib/index.js:1251-1305`（resume 拒绝 active 会话）、`712-723` 与 `216-219`（resume 的 setup 重新挂载请求中的完整 MCP 列表，即替换而非合并）。close 失败退役连接并报错、不 prompt；resume 失败只新建，不回退旧 id/旧 MCP。
  - 取消：包装标准 ACP `session/cancel`（`acp/client/connection.py:278-284`），每轮最多一次，有限等待清理；超时/断连退役连接，finally 不再无界 `await`，不吞 `CancelledError`。排队等锁时取消不会 cancel 其他会话。
  - Skills 快照变化只退役空闲连接；有进行中回合的连接标记 retiring，回合结束再关闭，避免打断其他会话。
  - 因 MCP 变化无法 resume 而新建会话时，回复开头显示 `[DSH]` 告警说明上下文不可用；不自动补历史、不调用模型。
- **终态诚实（补强）**：prompt 任务被消费者之外的来源取消（如 SDK shutdown）时不再落到 `finally` 的 `__done__` 而被映射成 COMPLETED；返回 `None` 或缺少 `stop_reason` 同样拒绝猜成功。独立取消输出可见 ERROR 并退役连接（未知状态不复用），缺少 response/stop_reason 输出 ERROR，每轮仍只有一个终态。prompt 任务的结束结果由 done callback 统一回收，避免未取回异常。
- **连接退役显式唤醒**：`aclose` 先向该连接所有已登记回合队列投递 `TURN_CLOSED`、清空回合队列与审批 context，再关闭 transport。`stop()`/退役/close 失败时同连接其他活跃会话的消费者立刻以 ERROR 结束，不再依赖 transport 栈失败，也不会被误报为 COMPLETED。
- **reset 与运行回合串行**：`reset_session` 持有与 `run_turn` 相同的 per-session 锁，prompt 进行中不会 close 同会话；记录在锁内清除，后续回合不会 resume 被 reset 的 ACP id。
- **清理生命周期（补强）**：停止等待 prompt 的清理路径（`session/cancel` 通知中或结算中被二次取消、结算超时）现在**先同步隔离**连接——`retiring`、移出连接池、清 `_live` 与审批 context——再交出有 owner 的关闭任务。此前二次取消会在第一个 `await` 处直接跳出，留下仍可复用的旧连接与旧 ACP 会话映射，下一回合可能复用旧进程/旧活跃 id 并接收迟到帧；正常一次取消且 prompt 已结算的连接仍按原语义复用。
- **替换失败不泄漏旧连接**：Skills 快照变化时 `_recycle_locked` 摘下的空闲连接、替换 spawn 抛异常/被取消时已离开池的连接、以及 `_open_connection_locked` 丢弃的死连接，全部登记为 detached 并由适配器持有其关闭；关闭在独立 task 中执行，一个关闭被取消或卡住不会遗失其余，`stop()` 可回收剩余。
- **关闭有硬等待上限**：连接关闭与 `DshConnection.open` 的失败清理都改为“独立 task + `asyncio.wait` 截止时间”，不再用 `wait_for` 假装有界（`wait_for` 对吞取消的协程会一直等）。超时只做进程树兜底 kill（假 pid 下为 no-op），日志明确写“未确认关闭/未验证终止”，不虚报已停止；二次取消仍保留关闭 task 与进程兜底，不绕过全部回收。
- **关闭结果按实际确认**：关闭 task 正常返回（无异常、未被取消）才算确认关闭。此前只要 task 处于 done 就把异常/被取消当成成功并清除归属：`_close_bounded` 抛错/被取消会静默移出 `_detached`、不标未确认，`_bounded_stack_close` 抛异常只 debug 后返回 True、被取消时 `.exception()` 直接穿透并跳过进程兜底。现在异常/取消一律返回 False、标记未确认、保留 owner（`stop()` 可再次处理并如实告警）、进程树 best-effort kill；已结束的关闭 task 不再被描述为“仍在运行”。`DshConnection.open` 的清理失败不再覆盖原始初始化异常/`CancelledError`。成功路径不变。
- `DshConnection.open` 在 setup 被取消时也关闭已建立的栈/子进程，二次取消不阻止原异常重抛。取消等待仍只约束调用方（`wait_for`），不能强制终止完全吞掉取消的协程，不宣称绝对有界。
- 新增 `tests/test_lifecycle.py`（28 项离线回归：取消一次通知/有限退出/排队取消隔离/同会话串行、双会话全帧与终态、MCP A/B 隔离、同键死连接 resume、A→B 与 A→空撤回、env 假凭据轮换、顺序稳定、close/resume/new 失败、Skills 不打断活跃回合、持久化无凭据与指纹、prompt 独立取消/空 response/缺 stop_reason 终态、双次取消、setup 取消、reset 串行与不复用、退役/停止唤醒全部活跃会话）。既有 `test_mcp.py` 仅机械适配显式 plan 参数与 `OpenSession` 返回（断言不变）。

- **安全修复（Skills 影子清理越界）**：`skills_shadow/.qwenpaw_projection.json` 现在按不可信输入处理——只接受单层 kebab 名（`^[a-z0-9]+(?:-[a-z0-9]+)*$`），拒绝 `..`、`.`、空键、`/` 与 `\` 分隔符、绝对路径、Windows 盘符/UNC 形式；异常键记录哈希摘要告警（不回显路径），不再进入删除流程。清理与替换前用 `lstat` 确认影子根是真实目录，符号链接根保守拒绝（`UnsafeShadowTree`，由适配器按 fail-soft 继续无 Skills）；条目本身是符号链接时只删链接、不跟随目标，非普通文件拒绝删除。正常 stale 清理行为不变。**未防御**检查与删除之间父路径被并发替换的场景（需能写状态树的攻击者），不构成绝对安全承诺。
- 新增 `tests/test_skills_shadow_security.py`（12 项离线回归：越界键、绝对路径、分隔符、合法 stale、混合清单、符号链接条目/根、源名越界、告警不回显路径）。未运行任何真实 DSH/profile 集成。
- **DSH 侧桥拆到独立仓库** [`BillYuan01/dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge)（由 DSH 维护；MIT）。本仓库删除 `dsh-qwenpaw-bridge/`；安装改为两段式：先 `dsh --profile qwenpaw --from-default-profile acp --dump-config`，再 `dsh plugin --profile qwenpaw add <包或本地目录>`（需要 pnpm）。
- 桥包名从 `@local/dsh-qwenpaw-bridge` 改为无 scope 的 `dsh-qwenpaw-bridge`；`_meta.bridge` 保持 `dsh-qwenpaw-bridge` 不变。
- 契约套件支持外部桥：`DSH_CONTRACT_BRIDGE` 或同级 checkout；桥缺失时 `pytest.skip` 并给出明确提示。
- 修复 shutdown/uninstall hook 不接受 `plugin_id=` 的问题（重载/卸载时抛 `TypeError`）。

### 交付与测试（G5）

- 新增 `qwenpaw-dsh/LICENSE`（与仓库根 Apache-2.0 逐字节一致），并纳入显式打包白名单。
- 新增 `tools/plugin_package.py`：21 文件白名单打包/校验器。只读明确 checkout，拒绝符号链接/非常规文件/白名单外源码；固定顺序、`1980-01-01` 时间戳、`0644` 权限、`ZIP_STORED`；拒绝覆盖已有输出和源码内输出；校验重复成员、路径安全、权限、版本、LICENSE、逐成员字节与 `_shutdown(**kwargs)`（AST，不执行插件）。
- 新增 `tools/run_safe_tests.py`：12 文件显式白名单（现有 226 项 + `test_delivery_tools.py`），一次性 fake HOME/DSH_HOME/QWENPAW_*，逐文件/总计报告 counts，skip 默认不算绿，不自动安装依赖。
- 新增 `tests/test_delivery_tools.py`（26 项）：构建可复现、候选校验、历史 ZIP 只读失败、污染/版本/权限/路径/覆盖/链接/FIFO 拒绝、runner 白名单与 sentinel 不执行。
- 文档对齐：`dist/README.md` 明确历史 ZIP 不可重打、下个版本如何用工具构建/校验；根 README/AGENTS/STATUS 与内层 README 记录当时仍是未发版源码。G5 当时未 bump 版本；本次 v1.1.0 统一提升版本，并用同一工具从最终源码生成 `dist/qwenpaw-dsh-1.1.0-plugin.zip`。**未 push、未打 tag、未创建 GitHub Release。**

## v1.0.1 — 2026-10-21

**修复控制台模型选择不全 / 推理档位为空**（1.0.0 回归）。

### 修复

- **`GET /api/harnesses/dsh/models` 500**：`DshAdapter` 缺 `capability_unavailable_message`，宿主取模型目录时抛 `AttributeError`，控制台退回"仅显示当前模型"。现补齐该属性（`dsh` 不在 PATH 时返回可读原因，否则 `None`），接口返回 200。
- **每模型 `reasoning_efforts` 为空**：`models()` 原先只读 ACP `model` config option（只有 provider 分组，没有档位）。现优先读桥在 `session/new` 的 `field_meta.qwenpaw.models` 中广告的权威目录（含 `reasoning_efforts` / `default_reasoning_effort` / `selection_value`），失败再回落到 config option。
- **同名模型不可区分**：模型显示名带上 provider 名（`<provider> · <model>`）；控制台只渲染 `name`，没有 provider 列。

### 实测

- `GET /api/harnesses/dsh/models` → HTTP 200，5 个模型，档位齐全：`deepseek-official/deepseek-flash`、`deepseek-v4-pro`（off/low/high/max）；`opencode-go/` 三个（3–5 档）。
- 115 个单测全过。

### 未包含

- **agent loop 模式选择**：QwenPaw 控制台只对原生 backend 渲染 `LoopModeSelector`（`usesQwenPawBackend` 硬门控），且 DSH 无运行时 loop 模式（`maxParallelToolCalls` 等是 profile 级启动配置）。四条路线（宿主补丁 / 前端插件 / 桥映射 `maxParallelToolCalls` / 不做）待定，留待后续版本。

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
