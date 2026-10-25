[English](README.md) | 简体中文

# qwenpaw-dsh

在 QwenPaw 里使用 DeepSeek Harness（DSH）。安装后，可以创建 DSH 智能体，在控制台对话，或让其他智能体调用它。

插件通过 ACP v1 驱动 DSH，负责会话、模型选择、工具事件和审批的衔接。这是社区项目，不是两家厂商的官方组件。

当前版本：**1.1.1**。推荐搭配 [dsh-qwenpaw-bridge 1.1.0](https://github.com/BillYuan01/dsh-qwenpaw-bridge)，不要求两个组件同版号。

## 能做什么

- 流式显示回答、思考和工具执行结果；实时增量需要安装 DSH 桥。
- 选择模型和推理强度，并在发送消息前确认设置生效。
- 恢复 DSH 会话，让同一连接上的不同会话分别接收自己的消息。
- 将 QwenPaw 的审批请求、Skills、stdio / HTTP MCP 和图片附件交给 DSH 处理。

```text
QwenPaw → qwenpaw-dsh（本插件）→ ACP → DSH + dsh-qwenpaw-bridge → 模型
```

两个组件安装在不同位置：本插件装进 QwenPaw；桥装进 DSH 的 profile。只安装其中一个，不会自动装好另一端。

## 环境要求

- QwenPaw `>=2.2.0, <2.3.0`，以及其 Python 环境中的官方 ACP SDK（`acp`）。
- DSH `>=0.2.0-rc.2 <0.3.0-0`，已经配置好模型和凭据。
- Node.js >=22；安装 DSH 桥还需要 pnpm。

目前在 Linux 上验证。macOS、原生 Windows 和 WSL 尚未做专项端到端测试。`app` 的插件加载已验证；`desktop` 只验证过后端加载，`qwenpaw task` 不经过插件加载流程。详见[运行形态记录](docs/forms-matrix.md)。

## 安装

以下以一套新安装为例。已有同名 profile 或插件时，先保留现有配置，再进行更新。

### 1. 准备 DSH 桥

```sh
git clone --branch v1.1.0 https://github.com/BillYuan01/dsh-qwenpaw-bridge.git
dsh --profile qwenpaw --from-default-profile acp --dump-config >/dev/null
dsh plugin --profile qwenpaw add /absolute/path/to/dsh-qwenpaw-bridge
```

把最后一行换成刚克隆目录的绝对路径。`qwenpaw` profile 必须先从内置 `acp` 模板创建，不能只对新名字执行 `plugin add`。

桥尚未发布到 npm，请使用上面的本地路径安装。然后按[桥的安装说明](https://github.com/BillYuan01/dsh-qwenpaw-bridge#readme)配置模型路由；合并配置，不要覆盖已有的 `cordis.patch.yml`。

### 2. 安装 QwenPaw 插件

```sh
git clone --branch v1.1.1 https://github.com/BillYuan01/qwenpaw-dsh.git
cd qwenpaw-dsh
qwenpaw plugin install ./qwenpaw-dsh
```

安装目标是仓库内层的 `qwenpaw-dsh/`，那里才有 `plugin.json`。也可以从 [Releases](https://github.com/BillYuan01/qwenpaw-dsh/releases) 下载 ZIP，解压后安装该目录。更新现有插件可使用宿主支持的 `--force` 流程；热加载会关闭旧插件持有的连接，应避开正在运行的回合。

### 3. 创建智能体

在控制台创建后端为 **DeepSeek Harness** 的智能体，设置 profile 为 `qwenpaw`。相关配置片段如下，不是完整配置文件：

```json
{
  "backend": "dsh",
  "backend_settings": {
    "binary": "/absolute/path/to/dsh",
    "profile": "qwenpaw"
  }
}
```

`binary` 可省略，插件会从 QwenPaw 服务的 `PATH` 查找。服务继承的 `DSH_HOME` 要与配置 profile 时一致。

**默认 profile 是 `acp`，不是 `qwenpaw`。** 装好桥后也需要明确选择 `qwenpaw`，否则不会使用桥的增强功能。

## 权限

| QwenPaw 审批级别 | DSH 模式 |
|---|---|
| `STRICT` | `read-only` |
| `SMART` / `AUTO` | `workspace-write` |
| `OFF` | `danger-full-access` |

显式设置的 `backend_settings.permission_mode` 优先于审批级别。审批端口故障、拒绝和超时都按拒绝处理，一次批准不代表永久放行。

`read-only` 限制修改，**不是文件读取白名单**。MCP 连接投影也不等于完整传递 QwenPaw 的 Driver 策略。不要把测试目录或一条提示词当作硬隔离。

## 常见问题

**控制台没有 DSH 后端。** 检查插件是否装到实际使用的 QwenPaw 工作目录、宿主是否为支持的 2.2.x，以及加载日志是否报错。

**终端能运行 DSH，服务却找不到。** 服务的 `PATH` 可能不同，填写 `binary` 的绝对路径，并检查服务继承的 `DSH_HOME`。

**回答只在最后整段出现。** 检查智能体是否选择 `qwenpaw` profile，以及桥是否真正加载。

**模型或推理档位不对。** 核对当前智能体使用的 profile、模型路由和目录缓存。查询模型 API 时应选择 DSH 智能体的上下文；从原生 QwenPaw 智能体查询，可能走默认 `acp` profile。模型数量取决于配置，不是固定值。

**恢复会话后，为什么没有重建聊天记录？** ACP 会话恢复与 QwenPaw 历史回放是两回事。插件的 `history()` 目前返回空列表。

**还有哪些限制？** SSE MCP、上下文用量显示和第三方后端 loop 模式选择尚不支持；图片能力在初始化时探测，切换模型后不重探。Skills 快照不会自动清理，源资源仍通过链接读取。更多实现说明见[架构文档（英文）](docs/architecture.md)。

## 测试与开发

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
```

这是不调用模型的白名单测试入口，目前共 272 项。不要直接对整个 `tests/` 目录做 pytest 收集，其中包含会启动真实 DSH、读取凭据或调用模型的脚本。

Linux 上已实测真实回答、流式、双会话并发、取消后继续使用和跨进程恢复；1.1.1 还验证了读取工具的完成事件直接带回文件文本。这些是特定环境下的测试，不是全平台保证。详见[验证记录（英文）](docs/verification.md)、[开发说明（英文）](docs/development.md)和 [AGENTS.md（英文）](AGENTS.md)。

## 许可证

[Apache License 2.0](LICENSE)。DSH 桥使用其独立仓库中的 MIT 许可证。
