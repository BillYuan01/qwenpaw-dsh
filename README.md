# qwenpaw-dsh：在 QwenPaw 中使用 DeepSeek Harness

通过 **ACP v1 over stdio**，把 DeepSeek Harness（DSH）注册为 QwenPaw 的第三方智能体后端：在控制台创建 `backend=dsh` 的智能体、进行多轮对话，或由其他智能体调用。

这是社区集成，不是 QwenPaw 或 DeepSeek 的官方组件。插件不改写双方安装目录中的核心源码，但会在 QwenPaw 2.2.x 进程内，对后端注册表及其引用做可还原的运行时 monkeypatch。

**当前插件版本：`1.1.1`**，以 [`qwenpaw-dsh/plugin.json`](qwenpaw-dsh/plugin.json) 为准。本补丁只修复工具结果文本映射：ACP `ContentToolCallContent` 内层文本现在进入工具进度／完成事件；不改权限、模型或其他功能。DSH 侧桥保持 **v1.1.0**，仍兼容，无需升级桥。

> **验证边界**：1.1.1 完成 272 项离线测试和安装包一致性检查；修复后的真实 DSH 工具读取需在安装后另行验证，不能把旧版读取成功当作新版事件内容已通过。macOS／原生 Windows 尚未端到端验证。`dist/qwenpaw-dsh-1.1.1-plugin.zip` 从本版本源码按白名单构建；旧版 ZIP 保持原样。

## 两个仓库各负责什么？

```text
QwenPaw 控制台 / 智能体调用
    ↓
qwenpaw-dsh（本仓库，Python，运行在 QwenPaw 进程内）
    ↓ ACP v1，stdio
DSH 的 acp profile，或安装了桥的 qwenpaw profile
    ↓
模型提供商 / DSH 工具
```

| 组件 | 安装到哪里 | 职责 |
|---|---|---|
| **本仓库 `qwenpaw-dsh`** | QwenPaw | 注册后端；进程与会话管理；事件、审批、Skills、MCP、图片投影 |
| **[`dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge)** | DSH 的 `qwenpaw` profile | 补充实时文本/思考流、模型目录和权限预设；独立版本、MIT 许可 |
| 桥仓库的 `status-plugin/` | DSH 的 `web` profile | 可选的连接状态卡片，不是桥本体 |
| 桥仓库的 `tools/`、`diagnostics/` | 仅源码 checkout 中的手动工具 | 可选凭据同步与诊断，**不是安装本插件的必需步骤**，也不随桥 npm 包分发 |

安装 QwenPaw 侧插件不会自动安装桥；安装桥也不会自动把 DSH 注册到 QwenPaw。

## 兼容性与已验证范围

| 项目 | 要求 / 验证边界 |
|---|---|
| QwenPaw | 清单与 2.2.x 装载器支持区间为 `>=2.2.0, <2.3.0`。2.3+ 装载器只是预留和特征探测，**不是已验证的支持承诺** |
| Python | 使用 QwenPaw 的 Python 环境；该环境需可导入 `qwenpaw` 与官方 ACP Python SDK（模块名 `acp`） |
| DSH | 历史端到端验证包含 `0.2.0-rc.2`；桥的兼容范围为 `>=0.2.0-rc.2 <0.3.0-0`。不要把状态探测中的宽松版本检查当成兼容认证 |
| Node / pnpm | DSH/桥需要 Node >=22；`dsh plugin add` 需要 pnpm 在服务环境的 `PATH` 中 |
| 模型 | DSH profile 中已有可用模型路由与对应凭据；本插件不提供模型账号登录或免费额度 |
| Linux | 现有集成验证环境；测试通过的具体版本与时间见历史验证文档 |
| macOS / 原生 Windows | 本项目未完成端到端验证，暂不作为已支持平台推荐；未验证不等于已证明不能运行 |
| WSL | 应按独立 Linux 环境检查，尚无专项端到端验证承诺 |

**不要把辅助工具的限制套到整个桥或本插件上。** 桥仓库的环境同步工具目前在 Linux 验证，macOS 未验证，原生 Windows 暂不支持；它不是本项目必装依赖。

### 验证边界（历史 v1.1.0 / v1.1.1 补丁）

| 类别 | 本版实际做了什么 |
|---|---|
| 已执行 | 12 个白名单文件的 **252** 项离线单测（0 skip）；打包器对同一源码两次构建字节一致；生成的 ZIP 与最终源码逐成员比对；成员名/权限/版本/`LICENSE`/卸载回调校验；Python 语法编译、shell 语法、Markdown 本地链接与 `git diff --check`；待发布文本与新增 diff 的有限隐私扫描 |
| 未执行 | 真实 `dsh` 进程 / ACP live 回合、真实凭据、计费模型、真实插件安装或升级、macOS、原生 Windows、WSL 专项 |
| 明确行为 | `history()` 返回空列表（ACP 会话可 resume，但不会重建 QwenPaw 历史）；Skills 快照只增不自动 GC；图片只读普通文件且有 15 MiB 上限；MCP 变化会 close + resume，失败只新建并告警 |

> **v1.1.1 的增量**：新增 `qwenpaw-dsh/tests/test_event_mapper.py`（20 项，先红后绿），安全 runner 白名单变为 13 个文件、**272** 项（252 + 20）。这是离线/静态结果，不表示现役安装已更新或 live 已复验。

历史 live 证据（115 单测、契约 s01–s06、流式/审批/Skills/MCP/图片）记录在对应版本的 CHANGELOG 与 `docs/` 中，代表当时环境，不是 v1.1.0 的复验结果。

### QwenPaw 运行形态

以下是已有证据，不是本次文档更新重新执行的结果：

| 形态 | 已有验证结论 |
|---|---|
| `qwenpaw app` / 基于它的控制台 | 已验证插件加载 |
| `qwenpaw desktop` | 后端加载已验证；原生窗口曾受宿主 GTK/Qt 依赖限制，不能据此声称桌面 GUI 已完整验收 |
| `qwenpaw task` | 已验证该形态不经过插件装载点，不能直接用此第三方后端；无头使用可采用 app + API |
| `hub` / `tui` / `qwenpaw acp` | 未完成本项目的专项验证 |

详见 [`docs/forms-matrix.md`](docs/forms-matrix.md)。

## 安装与开始使用

下列命令面向 Linux/POSIX shell，是**用户手动操作说明**。安装会下载或执行第三方代码、修改 profile 或插件目录；不是无副作用的检查。先确认目标环境，不要覆盖正在使用的配置。

### 1. 检查运行环境

在**启动 QwenPaw 服务的同一用户和环境**中检查：

```sh
qwenpaw --version
dsh --version
node --version
pnpm --version
```

终端能找到 `dsh` 不代表 systemd/桌面应用也能找到。服务使用不同 `PATH` 时，后面的 `backend_settings.binary` 应填实际可执行文件的绝对路径。

如果尚未安装 DSH，请按其官方说明选择兼容版本；不要在已有环境中盲目安装最新预览版。

### 2. 选择 DSH profile

**方案 A：基础 ACP。** 使用已有、已配置模型路由的 `acp` profile，不安装桥也可进行基础对话；实时增量、模型目录和权限预设能力取决于 DSH 实际提供的接口。

**方案 B：推荐的桥 profile。** 在全新目标 profile 上，先由内置 `acp` 模板创建 `qwenpaw`，再安装桥：

```sh
dsh --profile qwenpaw --from-default-profile acp --dump-config >/dev/null

# 获取与本插件同版（v1.1.0）的桥源码，再从本地目录安装
git clone --branch v1.1.0 https://github.com/BillYuan01/dsh-qwenpaw-bridge.git
dsh plugin --profile qwenpaw add /absolute/path/to/dsh-qwenpaw-bridge
```

> **不要依赖 `dsh plugin add dsh-qwenpaw-bridge`。** 该包尚未发布到 npm registry（截至 v1.1.0 查询为 404），用裸包名安装会失败。请使用上面的固定 tag + 本地路径方式；若你自行从源码打包，也按桥仓库的 `files` 清单生成，不要把 `tools/`、`diagnostics/`、`status-plugin/` 或测试目录塞进根包。

`qwenpaw` 不是 DSH 内置模板。不要只对一个全新名字执行 `plugin add`：那样可能没有完整 ACP app。已有同名 profile 时，先核对内容，不能用示例覆盖它。

然后在 DSH 中配置模型路由和凭据。不要把密钥贴入 issue、截图或提交；不要覆盖已有 `cordis.patch.yml`，也不要重复插入 `qwenpaw-bridge` 行。完整安装与组成检查见[桥仓库 README](https://github.com/BillYuan01/dsh-qwenpaw-bridge#readme)。

### 3. 安装 QwenPaw 侧插件

获取本仓库后，从仓库根执行：

```sh
qwenpaw plugin install ./qwenpaw-dsh
```

**安装目标是内层 `qwenpaw-dsh/`，不是仓库根目录。** 内层才有 `plugin.json` 和 `plugin.py`。遵守所安装 QwenPaw 版本的插件生命周期要求；需要离线安装或重启时，在合适的维护窗口操作，不要假设所有版本都支持热装载。

[`dist/`](dist/README.md) 中的 ZIP 是已有版本的快照，不会随源码文档自动更新。要使用当前源码，请使用上述本地目录安装方式。需要重新打包时使用 [`tools/plugin_package.py`](tools/README.md)：它按显式白名单构建到新路径、拒绝覆盖，并可用 `check` 校验版本、LICENSE、成员字节与卸载回调；历史 ZIP 不应被重打。

### 4. 创建并配置智能体

在控制台创建后端为 **DeepSeek Harness** 的智能体。使用桥时，要**明确指定 profile 为 `qwenpaw`**；代码默认值是 `acp`，不会因为装了桥就自动改用它。

以下为智能体配置的相关字段片段，不是完整配置文件；按实际界面/API 合并，勿整份覆盖：

```json
{
  "backend": "dsh",
  "backend_settings": {
    "binary": "/absolute/path/to/dsh",
    "profile": "qwenpaw"
  }
}
```

`binary` 可省略而从服务的 `PATH` 查找。`DSH_HOME` 由运行 QwenPaw 的环境传给子进程，必须指向你配置该 profile 的同一 DSH home。

审批档位优先从 QwenPaw 的智能体/会话设置控制；先用低风险任务确认会话、模型与权限行为，再授予更大权限。

### 5. 验证是否接通

- `qwenpaw plugin list`：插件已安装；服务日志有后端挂载成功信息。
- 控制台或已授权访问的 `GET /api/harnesses`：存在 `dsh`，且没有路径/profile/版本错误。
- `GET /api/harnesses/dsh/models`：能返回实际路由的模型目录；数量不是固定值。
- 经你明确同意后，用一轮简单对话检查文本输出与连续上下文。**真实对话会调用模型，可能产生费用。**

状态中的 `authenticated=True` 是本插件无账号登录界面的标志，**不代表模型密钥已验证、余额充足或模型目录一定完整**。模型列表探测虽不发送 prompt，也会启动 DSH 和创建会话，可能加载插件、读取凭据或访问服务。

## 已实现能力与边界

| 能力 | 实现与边界 |
|---|---|
| 会话管理 | 持久化 QwenPaw ↔ ACP 会话映射，并按**连接实例**（不只连接池键）校验：DSH 进程重建后先 `session/resume`，失败才新建并给出可见告警。同一 QwenPaw 会话的回合串行，不同会话可在同一连接上并发，各自独立接收事件。 |
| 流式事件 | 映射文本、思考、工具生命周期与终态；桥从 DSH 内部事件补实时增量，缺能力时回落，不能保证每个字符对应一个 token |
| 模型选择 | 用户在控制台显式选择的模型/推理档位会**先应用并回读确认**，确认不了就报可见错误、不发送 prompt（不再静默用默认模型继续）。模型选择值来自真实目录（本次会话 `session/new`/`resume` 的 `configOptions` 优先，其次桥 `_meta.qwenpaw.models` / 目录缓存；缺/过期缓存只做一次探测），不会按名字拼 `["provider","model"]`，同名不同 provider 视为歧义而非合并。目录缓存 600 秒；探测失败单独记录，不会当成“模型不存在”。 |
| 命令 / 会话历史 | 继承宿主 `HarnessAdapter` 契约：`/status` 已实现（`TEXT_DELTA` + `COMPLETED`，只读适配器内存，不启动 DSH、不探测模型、不读凭据）；未声明的命令返回可见 ERROR。`history()` 明确返回空列表——恢复 ACP 会话不等于重建宿主历史，不虚构历史、也不清空持久会话。 |
| 审批 | `session/request_permission` → QwenPaw 审批卡；优先最小一次性授权，拒绝/超时/端口故障时保守拒绝 |
| Skills | 名称归一化为 kebab-case；每个独特输入写入独立快照目录（`skills_snapshots/<sha256>/`），frontmatter 用 PyYAML 节点 span 只改唯一顶层 `name`，资源链接或复制；不同快照/空 Skills 回合不会清理其他回合的资源。快照变化只退役**空闲**连接，正在跑回合的连接等回合结束再换。旧快照不自动 GC，可能积累 |
| MCP | 投影 stdio 与 HTTP/streamable HTTP；SSE 跳过。会话级指纹覆盖 name/transport/command/args/url/env/header（含凭据，仅内存摘要、不落盘）：变化时先 `session/close` 再带新列表 `session/resume`；禁用/撤回/轮换即生效。新建因 MCP 失败可去 MCP 重试一次并告警；close 失败则退役连接并报错，不向可能仍带旧 MCP 的会话发 prompt。 |
| 图片 | 按初始化能力发送内联 base64，支持的后缀含 PNG/JPEG/GIF/WebP/BMP；15 MiB 读盘上限；只读普通文件（FIFO/设备/符号链接拒绝），有界短读循环，失败回退文本路径 |
| 用量 / loop | 当前未提供宿主 `context_usage` 通道，也未实现第三方后端的 loop 模式选择 |

MCP 投影传递工具连接信息，不等于同步宿主全部 allowlist、审批与 Driver 策略。Skills 资源在沙箱中的可读性、会话内切换模型后的图片能力重探，仍有验证或实现缺口。

模型与推理档位的语义：`model` 或 `reasoning_effort` 缺失/`null`/空白表示“未指定”，保持 profile 默认；非空值则必须来自目录并成功回读确认。推理档位只接受**当前实际模型**广告的档位（`off`/`false` 只是普通字符串，只有被广告才合法）。空值不能被用来显式选择 DSH 的“provider 默认档”（`""` 在 ACP 里就是未指定）。写入顺序固定为 model → effort；任一步失败都输出可见 ERROR 且**不发送 prompt**，已成功的 model 切换可能留在该 DSH 会话上，下一轮会重新按显式值应用。目录探测本身会启动 DSH 并新建/关闭一个探测会话（可能加载插件、读取凭据），不是无副作用检查；本次会话响应已带可用目录时不会额外探测。

宿主 `history()` 返回空列表是明确的边界声明：DSH 的 ACP 会话可以 resume，但 QwenPaw 侧的对话记录不会由 DSH 重建。`/status` 只展示适配器内存中的 profile、权限模式、审批策略、binary basename、连接/会话计数与目录缓存状态，不含绝对路径、会话 id、环境变量或凭据摘要。

会话生命周期还有几条使用者可见的边界：MCP 配置变化时若旧会话无法 resume，会新建会话（旧上下文不可用），并在回复开头显示 `[DSH]` 告警；取消回合会发送标准 `session/cancel` 并有限等待，若 DSH 完全不响应，适配器只能退役该连接（停止等待并丢弃迟到帧）——等待超时只约束调用方，**不是对协程的强制终止**，完全吞掉取消的协程仍可能滞留；此时日志会明确写“未确认关闭/未验证终止”，不会声称进程已停。关闭 task 自己抛异常或被取消也不算确认关闭：连接会保持未确认归属、进程树兜底 kill，`stop()` 仍可再次处理，且已结束的关闭 task 不会被说成“仍在运行”。`DshConnection.open` 的失败清理同样有界，且清理失败不会覆盖原始初始化异常。清理本身再被取消时，连接会先同步标记为不可复用并交出关闭任务，不会留下可复用的旧连接或旧会话映射。prompt 被调用方之外的来源取消、或没有 response/`stop_reason` 时，适配器输出可见 ERROR 而不是 COMPLETED；连接被退役/停止时，同连接上所有活跃回合会被显式唤醒并以 ERROR 结束。`reset` 与同一会话的运行回合共用串行锁，不会在 prompt 进行中关闭会话，后续回合也不会复用被 reset 的 ACP 会话 id。


### 权限映射

| QwenPaw `approval_level` | DSH 模式 | 风险说明 |
|---|---|---|
| `STRICT` | `read-only` | 写入受限，提权请求交审批 |
| `SMART` / `AUTO` | `workspace-write` | 允许工作区写入；越界可能先被沙箱拒绝，模型显式提权重试才会弹卡 |
| `OFF` | `danger-full-access` | 高风险、不受同等沙箱约束，不作为排障默认做法 |

优先级为：显式 `backend_settings.permission_mode` > 会话 `approval_level` > 智能体 `approval_level` > 默认 `AUTO`。显式模式会覆盖审批档位映射。解析结果参与进程配置键；变化可能重建 DSH 进程，**不同于桥在同一进程中热切换权限预设**。

一次批准不代表后续命令都获准，也不保证 DSH 的第二层沙箱提权不再询问。安全判断必须以实际请求和 DSH 行为为准。

## 常见问题

| 现象 | 先查什么 |
|---|---|
| 后端列表没有 DSH | 插件安装目录、实际 QwenPaw 工作目录、加载日志、版本是否在 2.2.x；不要自行绕过守卫 |
| `dsh` 找不到 / profile 不存在 | 服务用户的 `PATH`、绝对 `binary`、服务继承的 `DSH_HOME`、profile 名是否一致 |
| 只有最终整段输出 | 是否误用默认 `acp`；桥是否真正加载；内部流事件是否可用。不是只看 npm 安装成功 |
| 模型目录为空或档位不全 | 实际模型路由、桥元数据、目录缓存、DSH 服务注册。不能据此直接断言缺 key 或立刻重写凭据 |
| 模型目录 HTTP 500 | 插件 1.0.1 起已修复缺少 `capability_unavailable_message` 的已知问题（1.1.0 保留）；确认服务加载的是新代码，不只是下载了新源码 |
| MCP 工具缺失 | SSE 被跳过、服务启动错误、是否发生去 MCP 重试；详见 [MCP 记录](docs/mcp-http-sse.md) |
| 想用凭据同步脚本解决所有问题 | 它只补特定 headless 启动环境，不是模型发现根因修复；使用前阅读桥仓库安全说明 |

不要在排障时默认关闭审批、启用 full-access、打印环境变量、上传原始 profile dump，或重启用户生产服务。

## 开发与验证

### 不调用模型的检查

使用已具备 QwenPaw 与 ACP SDK 的 Python 环境，从仓库根运行显式白名单 runner：

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
```

默认只跑 12 个白名单文件：现有 11 个安全单元文件共 **226** 项，加上打包/runner 的 `test_delivery_tools.py`。每个文件单独执行 `unittest discover -p <file>`，使用一次性临时 `HOME/DSH_HOME/QWENPAW_HOME/QWENPAW_WORKING_DIR/TMPDIR` 和最小环境 allowlist；逐文件/总计报告 tests、failures、errors、skipped。**skip 不算绿**（默认退出 3），缺依赖或文件明确失败，不会自动 pip。

不要绕过 runner 对 `tests/` 做宽泛 discovery：`tests/test_*_projection.py`、`smoke_adapter.py`、`stream_check.py` 与 `tests/contract/` 会驱动真实 DSH，部分会发送 prompt、执行工具或使用真实凭据。`--list` 可查看固定白名单与计数。

装载/审批/生命周期等白名单用例都在测试进程内使用假连接与临时目录，不会安装插件、启动 DSH 或修改正在运行的服务。依赖缺失与 skip 应如实报告，不能算作通过。

### 必须单独授权的真实集成测试

`tests/smoke_adapter.py`、`tests/stream_check.py`、`tests/test_*_projection.py` 以及 [`tests/contract/`](qwenpaw-dsh/tests/contract/README.md) 会驱动真实 DSH，部分会发送 prompt、执行工具或使用真实凭据。

**不要直接对 `tests/` 做宽泛的 pytest 收集，不能因为文件名带 `test_` 就把它当作无副作用单测。** 契约套件的所谓隔离 home 可能链接真实凭据；隔离目录不等于凭据隔离。获准前不要执行，也不要把生产 `.env` 或凭据文件链接到夹具目录。

历史 s01–s06 两种 profile 的验收、形态与 MCP 记录见以下文档；历史通过不代表当前 checkout 或新系统已经复验：

- [契约测试说明](qwenpaw-dsh/tests/contract/README.md)
- [运行形态证据](docs/forms-matrix.md)
- [MCP HTTP / SSE 证据](docs/mcp-http-sse.md)
- [历史状态快照](STATUS.md)与[变更记录](CHANGELOG.md)

## 仓库地图与维护

```text
qwenpaw-dsh/plugin.json          安装清单与插件版本
qwenpaw-dsh/plugin.py            启停钩子、宿主上下文
qwenpaw-dsh/qwenpaw_dsh/         provider、审批端口、core 与 loaders
qwenpaw-dsh/tests/               单元与真实集成测试（注意区分）
tools/                           白名单打包/校验器与安全测试 runner
docs/                           历史验证与社区资料
patches/qwenpaw-upstream/        可选宿主补丁归档，不自动应用
dist/                           已发布 ZIP 快照，不手工修改
AGENTS.md                       AI 编码助手与贡献者操作约束
```

`STATUS.md` 和历史报告保留其记录时点；当前实现以源码、清单及本 README 的边界说明为准。归档上游补丁不代表已经合并，也不是默认安装要求。对外 issue/PR、GitHub 推送、发布新 ZIP、npm 发版与部署都是独立动作，需维护者授权。

两个仓库**本次**使用同一个 `v1.1.0` 版号，表示这是一套配套发布；版号相同不表示 DSH 核心也变成 1.1.0（DSH 兼容范围仍是 `>=0.2.0-rc.2 <0.3.0-0`），也不承诺以后永远同步。之后两仓库可以独立发版；只改文档不必提高运行版本。

## English overview

A community QwenPaw 2.2.x plugin that registers DeepSeek Harness as an agent backend over ACP v1/stdio. This repository owns the Python adapter; the separately versioned [DSH bridge](https://github.com/BillYuan01/dsh-qwenpaw-bridge) adds incremental output, permission presets and model metadata on the DSH side. The adapter defaults to the `acp` profile: select `qwenpaw` explicitly to use the bridge. Linux is the verified integration environment; macOS and native Windows are not end-to-end certified. Future loader scaffolding does not imply QwenPaw 2.3 support. Live probes can read credentials, run tools and incur model charges; they are not ordinary unit tests.

## 许可

本仓库：[Apache License 2.0](LICENSE)。独立桥仓库：MIT。两者均为社区项目。
