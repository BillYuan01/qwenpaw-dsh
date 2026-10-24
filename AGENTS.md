# AGENTS.md — qwenpaw-dsh 维护约定

本文件面向在**本仓库**工作的 AI 编码助手和贡献者。用户安装与使用说明见 [README.md](README.md)。这里不是部署授权，不要求自动执行任何命令。

## 1. 先确认边界和事实来源

- 本仓库维护 **QwenPaw 侧 Python 插件**；DSH 侧 JavaScript 桥在独立仓库 [`BillYuan01/dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge)。两个仓库独立版本、独立许可，不互相代发版。
- 插件版本读取 `qwenpaw-dsh/plugin.json`，当前为 **1.0.1**；不要根据目录名、客户端标识、历史报告或 ZIP 文件名推断当前源码版本。
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
- 会话恢复失败可回退新会话，但必须告警，不能谎称上下文已恢复。
- 模型目录优先桥元数据，回退 config options；选择值必须保持原义，不能只按显示名/裸 model id 拼接。
- 模型目录依路由变化，不硬编码模型数量、上下文上限或推理档位。当前目录缓存 600 秒，排障须考虑缓存。
- 目录探测也会启动 DSH/创建会话，不得称为完全无副作用检查。

### 权限与审批

- **审批 fail-closed**：无端口、端口故障、拒绝、超时不能转为同意；优先最小 one-shot grant，不偷偷记住为 always/persist。
- 显式 `permission_mode` > 会话 `approval_level` > 智能体级别 > `AUTO`；`STRICT→read-only`、`SMART/AUTO→workspace-write`、`OFF→danger-full-access`。映射与 provider 配置键必须一致。
- 显式模式是覆盖项，不可在文档中承诺它仍受较低审批档位控制。未知级别当前回落 workspace-write，不得写成自动 read-only。
- DSH 沙箱可能先硬拒绝再由模型发起提权；批准一次不等于绕过所有后续沙箱层。
- 不为排障默认关闭审批/沙箱，或用 full-access 代替解释失败。

### 投影与降级

- Skills 保留名称映射、frontmatter 重写、资源链接/复制和快照变更重建逻辑。
- MCP 为会话级投影，不是进程池键；保留 SSE 明确跳过、坏服务去 MCP 重试一次及告警。工具投影不代表宿主全部 Driver 策略已透传。
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

在已有 QwenPaw 与 ACP SDK 的 Python 环境，工作目录为内层 `qwenpaw-dsh/`：

```sh
python -m unittest discover -s tests -p 'test_permission_mode.py'
python -m unittest discover -s tests -p 'test_approval_port.py'
python -m unittest discover -s tests -p 'test_loader_wiring.py'
python -m unittest discover -s tests -p 'test_skills.py'
python -m unittest discover -s tests -p 'test_mcp.py'
python -m unittest discover -s tests -p 'test_images.py'
```

| 改动 | 最少对应检查 |
|---|---|
| 审批/权限/provider 配置键 | permission mode + approval port + loader wiring |
| 装载/卸载 | loader wiring，确认精确还原 |
| Skills / MCP / 图片编码 | 对应精选单元测试 |
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
- 运行修复需要发版时，先核对清单、CHANGELOG、源码包与安装产物一致，再由维护者批准发布。不要修改历史同名 ZIP。
- 重写根文档不会自动更新内层 README、STATUS、历史报告或 dist 资产；交付时说明实际修改范围，若发现矛盾则指出或在授权内修正，不能静默扩大范围。
- 完成时报告修改文件、实际执行检查及结果、未跑的真实测试和原因、是否提交/推送/发版。没有工具证据不能说“已通过”“已发布”。
