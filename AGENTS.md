# AGENTS.md — qwenpaw-dsh 维护约定

本文件面向在**本仓库**工作的 AI 编码助手和贡献者。用户安装与使用说明见 [README.md](README.md)。这里不是部署授权，不要求自动执行任何命令。

## 1. 先确认边界和事实来源

- 本仓库维护 **QwenPaw 侧 Python 插件**；DSH 侧 JavaScript 桥在独立仓库 [`BillYuan01/dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge)。两个仓库独立版本、独立许可，不互相代发版。
- 插件版本读取 `qwenpaw-dsh/plugin.json`，当前源码为 **1.1.1（未发布）**；不要根据目录名、客户端标识、历史报告或 ZIP 文件名推断当前源码版本。1.1.1 只修复工具结果文本映射，未打 tag/未构建 ZIP/未部署；已发布版本仍是 **1.1.0**，与桥 `dsh-qwenpaw-bridge` v1.1.0 配套。桥本次不改（保持 1.1.0），1.1.1 仍与其兼容；版号相同不表示 DSH 核心变成 1.1.0（兼容范围仍是 `>=0.2.0-rc.2 <0.3.0-0`），也不承诺以后同步。
- 当前清单与 monkeypatch 装载器支持 QwenPaw **`>=2.2.0, <2.3.0`**。`loaders/official.py` 是未来接口的探测/预留，不等于已经支持 2.3+。
- 运行、权限和配置行为以源码为准；`STATUS.md`、`CHANGELOG.md`、`docs/` 中的实测数字是历史快照，不是本轮复验结果。
- 本项目是社区集成，不得暗示 QwenPaw 或 DeepSeek 官方背书。内部 DSH 服务与宿主 monkeypatch 不是稳定公开 API。
- 用户只要求解释/诊断时，不擅自改代码；只要求文档时，不改运行代码、清单、部署或版本号。

## 2. 开工检查

1. 读本文件、根 README，以及本次涉及的源码和测试。
2. 查 `git status --short` 与 diff，保留用户已有修改；禁止盲目 reset、覆盖或清理。
3. 分清仓库根与内层安装目录 `qwenpaw-dsh/`；只在用户授权范围内修改。
4. 先列出要验证的事实和安全测试范围。陌生测试先读源码，不能从文件名判断它是否安全。
5. 缺少会改变目标或风险的信息时集中询问；禁止自选真实凭据、服务、账号或发布目标。

## 3. 实现地图与依赖方向

以下路径均相对仓库根：

| 路径 | 职责 / 改动约束 |
|---|---|
| `qwenpaw-dsh/plugin.json` | 插件身份、版本、宿主版本区间 |
| `qwenpaw-dsh/plugin.py` | 生命周期钩子、HostContext 组装、挂载/卸载；不塞入协议业务 |
| `qwenpaw-dsh/qwenpaw_dsh/host.py` | ApprovalPort、请求/决定、HostContext 边界 |
| `qwenpaw-dsh/qwenpaw_dsh/approvals.py` | 宿主审批服务适配，惰性导入；失败保守拒绝 |
| `qwenpaw-dsh/qwenpaw_dsh/provider.py` | 后端描述、能力声明、配置键、适配器工厂 |
| `qwenpaw-dsh/qwenpaw_dsh/loaders/` | 宿主装载策略；2.2 monkeypatch、未来 official、测试 direct |
| `qwenpaw-dsh/qwenpaw_dsh/core/adapter.py` | 连接池、会话持久化/恢复、模型目录、投影和回合编排 |
| `qwenpaw-dsh/qwenpaw_dsh/core/acp_client.py` | ACP 握手、会话调用、进程/连接生命周期、权限回调 |
| `qwenpaw-dsh/qwenpaw_dsh/core/event_mapper.py` | ACP 更新 → HarnessEvent 的映射，尽量保持纯函数 |
| `qwenpaw-dsh/qwenpaw_dsh/core/permissions.py` | 审批级别 → DSH 模式的单一事实来源 |
| `qwenpaw-dsh/qwenpaw_dsh/core/skills.py` | kebab 命名、frontmatter 重写、影子资源、注入 patch |
| `qwenpaw-dsh/qwenpaw_dsh/core/mcp.py` | stdio/HTTP 投影与不支持类型的跳过 |
| `qwenpaw-dsh/qwenpaw_dsh/core/images.py` | MIME、尺寸/读取限制、编码与降级 |
| `qwenpaw-dsh/tests/` | 混合单元与真实集成测试，必须区分 |
| `patches/qwenpaw-upstream/` | 历史补丁归档；不默认应用，也不默认向上游提交 |
| `dist/` | 已发布 ZIP 快照；不得手工修改或偷偷重打同名版本 |
| `tools/` | 白名单打包/校验器与安全测试 runner；不安装依赖、不启动 DSH、不读凭据 |

`core/**` 不得依赖宿主 registry/router 实现；宿主注册表耦合留在装载层，审批服务耦合留在端口实现。核心仍使用 `qwenpaw.harnesses.events` 契约类型及 ACP SDK，不能宣传为“无 QwenPaw 依赖”。

## 4. 不可破坏的运行契约

### 装载与生命周期

- monkeypatch 必须检查所有目标及按值导入引用；缺模块、缺属性或版本不匹配要明确失败，不得静默半挂载。
- 保留精确还原和重复挂载/卸载语义。不要为了让检查通过而移除版本守卫。
- `DirectLoader` 只用于测试/嵌入，不是生产宿主加载失败后的隐式回退。
- 不猜未来官方 API；确认真实接口后只改相应 seam，并复验挂载/卸载与审批。

### ACP、池和模型

- 保留标准 ACP v1/stdio；协议 stdout 不得混入普通日志。
- 默认 profile 是 `acp`。桥只在用户明确选择已配置的 `qwenpaw` profile 时生效；不得暗改默认值。
- 实际连接池键含 binary、profile、permission mode、approval policy、Skills snapshot；更改任何键语义需验证连接重建、会话映射和清理。
- **连接身份不等于池键**：`_live` 必须记录具体 connection 对象/代次；同键新进程只能 resume 或 new，绝不直接 prompt 旧 ACP id。清理连接时必须同时清 `_live` 与审批路由 context。
- **同一 QwenPaw 会话的回合串行**（per-session lock），不同会话可在同一连接并发。每个 ACP 会话有独立事件队列；禁止共享全局队列或 `__done__` 全局哨兵，禁止把他会话帧静默丢弃后仍报完成。
- **取消**：用户取消/生成器关闭时最多发送一次 `session/cancel`，有限等待 prompt 结束；超时或断连必须退役连接（防迟到帧），finally 不得无界 `await`，不得吞 `CancelledError`。对完全不配合的协程不能宣称已强制停止：`wait_for` 只给等待方一个上界，不是对协程的硬上界。
- **清理不可再被取消打断**：任何停止等待 prompt 的清理路径（通知中/结算中被二次取消、结算超时）必须**先同步**把连接标记为不可复用（`retiring`、移出池、清 `_live` 与审批 context），再交出一个有明确 owner 的关闭任务；关闭用独立 task + `asyncio.wait` 硬等待上限，超时只做进程树兜底 kill，并如实报告“未确认关闭”，不得宣称已终止。**关闭 task 正常返回才算确认关闭**：抛异常或被取消都必须标记未确认、保留归属（`stop()` 仍可再次处理）并进程树兜底 kill；已结束的关闭 task 不得被描述成“仍在运行/可能仍在跑”。`DshConnection.open` 的失败清理同理，且清理结果不得覆盖原始 setup 异常/`CancelledError`。正常一次取消且 prompt 已结算的连接仍可复用。
- **移出池 = 仍有 owner**：`_recycle_locked` 摘下的空闲连接、替换 spawn 失败/被取消时已离开池的连接、以及 `_open_connection_locked` 丢弃的死连接，都必须登记为 detached 并由适配器持有其关闭（同步起 task，禁止在锁内无界关闭）；一个关闭被取消/卡住不得遗失其余，`stop()` 必须能回收剩余并如实报告未确认项。
- **终态诚实**：prompt 任务被消费者之外的来源取消、返回 `None`、或 response 缺少 `stop_reason` 时，一律输出可见 ERROR，绝不映射成 COMPLETED（`map_stop_reason(None)` 会返回 COMPLETED，必须在调用前拦截）；独立取消还要退役连接（未知状态不复用）。每个回合最多一个终态，prompt 任务结果必须由 done callback 回收。
- **连接退役/停止必须显式唤醒消费者**：`aclose` 先向该连接所有已登记回合队列投递 `TURN_CLOSED` 并清空回合队列与审批 context，再关 transport；不得依赖 transport 失败来通知在 `queue.get()` 上等待的回合，退役不得让其他回合成 COMPLETED。
- **reset 同步**：`reset_session` 必须持有与 `run_turn` 相同的 per-session 锁，顺序执行 close 与清理记录；不得在 prompt 进行中 close 同一会话，后续回合不得 resume 被 reset 的 id。
- 会话恢复失败可回退新会话，但必须告警，不能谎称上下文已恢复；因 MCP 变化新建会话时还要在回复中给出可见 `[DSH]` 告警。
- 模型目录优先本次会话 `session/new`/`resume` 的 `configOptions`（可避免探测），其次桥元数据/目录缓存；选择值必须保持原义，不能只按显示名/裸 model id 拼接。同名不同 provider 必须报歧义，不得合并。
- **显式模型/档位必须应用并回读确认**：`model` 先于 `reasoning_effort`；`set_config_option` 后必须从响应 `configOptions.currentValue` 核对（dsh-acp 的写入响应返回完整选项），不匹配/缺字段/抛错一律可见 ERROR 且**不 prompt**。缺/过期缓存只做一次 single-flight 探测；探测失败要报原因，不得静默回落默认模型。未指定/空白保持 profile 默认；非字符串值直接报错。
- 推理档位只来自**当前模型**广告的档位（`reasoning_effort` 选项或该模型 `reasoning_efforts`），不得用所有模型的合集；`off`/`false` 不是布尔语义，只有被广告才是合法值。
- 模型目录依路由变化，不硬编码模型数量、上下文上限或推理档位。当前目录缓存 600 秒，排障须考虑缓存。
- 目录探测也会启动 DSH/创建会话，不得称为完全无副作用检查。
- **宿主契约必须可调用**：`DshAdapter` 继承 `HarnessAdapter`，`commands` 声明里的命令必须真的有实现。`/status` 只读适配器内存（不 spawn、不探测、不读凭据、不含绝对路径/会话 id/env），未知命令返回可见 ERROR。`history()` 返回空列表并说明“ACP resume ≠ 宿主历史重建”，不得虚构 provider 历史、也不得清空持久会话。

### 权限与审批

- **审批 fail-closed**：无端口、端口故障、拒绝、超时不能转为同意；优先最小 one-shot grant，不偷偷记住为 always/persist。
- 显式 `permission_mode` > 会话 `approval_level` > 智能体级别 > `AUTO`；`STRICT→read-only`、`SMART/AUTO→workspace-write`、`OFF→danger-full-access`。映射与 provider 配置键必须一致。
- 显式模式是覆盖项，不可在文档中承诺它仍受较低审批档位控制。未知级别当前回落 workspace-write，不得写成自动 read-only。
- DSH 沙箱可能先硬拒绝再由模型发起提权；批准一次不等于绕过所有后续沙箱层。
- 不为排障默认关闭审批/沙箱，或用 full-access 代替解释失败。

### 投影与降级

- Skills 保留名称映射、frontmatter 重写、资源链接/复制和快照变更重建逻辑。
- **每次投影一个独立快照**：`<state>/skills_snapshots/<id>/`，id 覆盖 target、源名、revision 与规范化源目录；patch/shadow 都在快照内。不同回合不得共享或重写同一 `skills_shadow`/`skills.patch.yml`；空 Skills 回合不得删除/清理其他快照。**不自动 GC**，旧快照积累是可接受成本，不做后台 cron 或猜测式清理。
- **快照清单同样是不可信输入**：`.qwenpaw_projection.json` 的键只允许单层 kebab 名，异常键必须拒绝并告警（不打印攻击者路径），不得当成删除/替换路径。清理前要求快照 shadow 根是真实目录（`lstat`，不解析符号链接）；条目本身是符号链接时只删链接、不跟随目标；根/快照目录被替换/链接时保守拒绝，不写入、不删除。尚未防御的是检查与删除之间的父路径并发替换（需要能写状态树的攻击者），文档不得宣称绝对安全。
- **frontmatter 只改唯一顶层 `name` 的 value span**（PyYAML compose），保留其他键/注释/顺序/body；重复 `name`、非法 YAML、非标量或重写校验失败必须跳过该技能并记 `invalid-frontmatter`，不得写坏投影。重复非 `name` 键不解释、原样保留。
- 源资源仍是符号链接，用户实时编辑源文件会立即反映；不得宣称来源内容不可变。
- 图片先看初始化能力，再看 MIME/尺寸/读取；只接受普通文件（符号链接/FIFO/设备拒绝），打开后 `fstat` 复核，读取循环封顶 `MAX+1`，不可用时降级文本路径。不要凭扩展名以外猜任意格式。
- MCP 为会话级投影，不是进程池键；保留 SSE 明确跳过、坏服务去 MCP 重试一次及告警。工具投影不代表宿主全部 Driver 策略已透传。
- **MCP 指纹**必须覆盖所有影响连接的字段（name/transport/command/args/url/env/header，含凭据明文参与摘要），排序稳定使纯顺序变化不触发重建；摘要只在内存比较，绝不写入日志、状态文件或报告。指纹变化时先 close 再 resume 携带新列表（DSH 源码：resume 在 setup 中重新挂载请求列表，且拒绝 active 会话）；close 失败必须退役连接并阻止 prompt，不得宣称撤回成功；resume 失败只能新建，不得回退旧 id/旧 MCP。无变化时保持复用。Skills 快照变化只能退役空闲连接，不得打断进行中的回合。
- 图片先看初始化能力，再看 MIME/尺寸/读取；不可用时降级文本路径。不要凭扩展名以外猜任意格式。
- 便利能力可降级，**权限与授权不能以“fail-soft”为理由放宽**。

## 5. 平台与能力承诺

- 现有完整集成证据来自 Linux。macOS、原生 Windows、WSL 无本项目专项端到端认证，不能写“全平台已支持”，也不能把未验证写成技术上绝对不能运行。
- 桥仓库的凭据同步工具有独立限制：Linux 验证、macOS 未验证、原生 Windows 暂不支持；不能拿它代表整个 Python 插件或桥的限制。
- 已验证 `app` 加载；`desktop` 后端加载不等于 GUI 全验；`task` 不走插件加载；`hub/tui/acp` 未专项验。
- context usage、第三方 loop 选择、SSE、跨 DSH 版本 resume、Skills 沙箱资源读取、切模型后的图片能力重探仍有实现或验证边界。
- 适配器 `_version_supported()` 只作宽松状态检查，当前接受范围比桥的 semver 更宽，不能据此宣传所有 DSH 0.x 都兼容。

## 6. 测试分级：默认只跑明确安全的范围

### A. 文档/静态检查

- `git diff --check`；核对相对链接、命令工作目录、代码块、版本与清单、公开示例隐私。
- 只改文档不需要安装插件、启动 DSH、读真实凭据或发起模型回合。

### B. 精选单元与装载测试（不调用模型）

在已有 QwenPaw 与 ACP SDK 的 Python 环境，从**仓库根**运行显式白名单 runner（不要对 `tests/` 做宽泛 discovery）：

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
python tools/run_safe_tests.py --list
```

白名单固定为 12 个文件：`test_permission_mode.py`、`test_approval_port.py`、
`test_loader_wiring.py`、`test_skills.py`、`test_skills_shadow_security.py`、
`test_mcp.py`、`test_images.py`、`test_lifecycle.py`、`test_model_contract.py`、
`test_host_contract.py`、`test_file_projection_g4.py`（以上合计 **226** 项）与
`test_delivery_tools.py`。runner 逐文件执行 `unittest discover -p <file>`，工作目录为内层
`qwenpaw-dsh/`，并为每次运行创建一次性 `HOME/DSH_HOME/QWENPAW_HOME/QWENPAW_WORKING_DIR/TMPDIR`
与最小环境 allowlist。

`test_lifecycle.py` / `test_model_contract.py` / `test_host_contract.py` /
`test_file_projection_g4.py` / `test_delivery_tools.py` 全部使用假 ACP 连接/临时目录：
不启动 DSH、不读凭据、不调用模型。模型契约用例的“目录探测”也走假连接，禁止把 live
目录探测混进这些入口。runner 报告逐文件与总计 tests/failures/errors/skipped；**skip 不算
通过**（默认退出 3），缺依赖/缺文件明确失败，不自动安装。

| 改动 | 最少对应检查 |
|---|---|
| 审批/权限/provider 配置键 | permission mode + approval port + loader wiring |
| 装载/卸载 | loader wiring，确认精确还原 |
| Skills / MCP / 图片编码 | 对应精选单元测试 |
| 打包/校验/测试入口 | `test_delivery_tools.py` + `tools/plugin_package.py check` |
| ACP/事件/回合/恢复/流式 | 单元与静态检查后，说明尚需授权的真实集成验证，不伪称完成 |

装载测试只改测试子进程内注册表；用临时目录。依赖缺失、守卫不匹配导致 skip，要单列，不能报全通过。新增依赖下载也需遵守用户授权。

### C. 真实集成：必须先取得针对目标的明确授权

以下**不是普通单元测试**：

- `tests/smoke_adapter.py`、`tests/stream_check.py`
- `tests/test_skills_projection.py`、`tests/test_mcp_projection.py`、`tests/test_image_projection.py`
- `tests/contract/` 的 s01–s06、夹具与 spike runners

它们可能启动真实 DSH、读取/链接真实凭据、调用计费模型、执行工具、开启夹具端口、写状态。**禁止对整个 tests 目录盲跑 pytest 或通配所有 `test_*.py`。**

获准前先读 runner、conftest 和 seed/prepare helper；特别检查 `.credentials.yaml`/`.env` 的链接。测试 home 独立不等于凭据独立。授权真实凭据测试也不等于授权发布输出。

契约套件可用 `DSH_CONTRACT_PYTHON` 指定解释器，桥位置用 `DSH_CONTRACT_BRIDGE`，profile 用 `DSH_CONTRACT_PROFILE`；详见 `qwenpaw-dsh/tests/contract/README.md`。默认 seed 与旧夹具行为可能用真实 home，不可因设置一个 TMPDIR 就宣称安全。

## 7. 隐私和外部操作

- 不读、复制、链接或修改用户真实凭据来完成普通文档/单元测试任务。
- 不输出 key、token、Authorization、MCP env/headers、完整环境变量、原始 profile dump 或会话映射。
- 假夹具只用显然虚构值；报告也可能含个人路径、IP、会话/机器标识和服务错误，发布前需逐项审阅。
- 不提交 `.env`、`.credentials.yaml`、私有诊断报告、模型原始输出、测试状态、缓存、打包证据或临时备份。
- 日志不能打印密钥；也不能通过 hash、前缀、长度等输出绕过用户的数据边界。
- GitHub commit/push、PR/issue、发版、依赖安装、生产插件安装、服务重启均不是改文档的隐含授权。
- 对外提交仅限明确授权的自有仓库；不得顺便向上游发帖、提 issue/PR 或应用归档补丁。
- 经授权提交时使用维护者认可的 GitHub noreply 身份，避免真实邮箱公开；不得擅自修改全局 Git 配置。

## 8. 文档、版本与交付

- README 面向使用者；本文件面向执行者。两者区分“已实现”“历史验证”“本轮验证”“未验证”，不沿用过期路线图作为当前事实。
- 文档变动通常无需 bump `plugin.json`、打 tag、重打 ZIP，更不需要发布桥的 npm 包。
- 已发布源码是 v1.1.0，包含 2026-10-21 之后的 G1–G5 修复；`dist/qwenpaw-dsh-1.1.0-plugin.zip` 由该份源码构建。**未发布的 1.1.1** 只在其上修复工具结果文本映射（`core/event_mapper.py`），没有对应的 `dist/` ZIP，也不得复用 1.1.0 的文件名。历史 `dist/qwenpaw-dsh-1.0.1-plugin.zip` 是旧发布快照（没有 `LICENSE`、旧 `plugin.py`），必须保持字节不变。
- 打包只使用 `tools/plugin_package.py`：显式 21 文件白名单、从明确 checkout 读取、固定顺序/时间戳/权限、拒绝覆盖与源码内输出、拒绝符号链接/非常规文件。新增或删除包内文件时同步更新白名单、`qwenpaw-dsh/LICENSE` 与 `test_delivery_tools.py`。
- 历史 `dist/` ZIP 用新 `check` 应失败，这是预期；不要为了“让检查变绿”而修改旧资产。未来补丁版必须使用**新文件名**并先经维护者批准版本与发布动作。
- **发布顺序**：先更新文档与 CHANGELOG，再提升 `plugin.json` 版本，最后从最终源码构建产物；发布产物、tag 名与清单版本必须一致（已发布 `v1.1.0` / `1.1.0`）。**1.1.1 尚未发布**：本轮只有源码与本地 commit，没有 tag/ZIP/部署；任何发布动作仍需单独授权。构建后必须用 `check` 对照最终源码，并记录产物 SHA256；不要把构建日志、私有验收报告或本机绝对路径提交进仓库。
- **授权不是常设的**：某次发布获得批准，不等于以后任何 push/tag/Release/npm 发版都被自动授权；每次公开动作仍需针对目标与版本单独确认。
- 重写根文档不会自动更新内层 README、STATUS、历史报告或 dist 资产；交付时说明实际修改范围，若发现矛盾则指出或在授权内修正，不能静默扩大范围。
- 完成时报告修改文件、实际执行检查及结果、未跑的真实测试和原因、是否提交/推送/发版。没有工具证据不能说“已通过”“已发布”。
