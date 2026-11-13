简体中文 | [English](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/README.en.md)

# DeepSeek Harness（DSH）智能体后端

将 **DeepSeek Harness（DSH）** 接入 **QwenPaw**，通过 ACP v1 创建 DSH 智能体，在 QwenPaw 控制台对话，也可以供其他智能体调用。本插件是社区集成项目，不属于厂商官方组件。

**当前版本：v1.1.4**。这是面向插件市场的**中文说明和元数据更新版**；相较 v1.1.3，未修复业务逻辑或改变 ACP 协议。

## 主要功能

- **模型选择**：选择模型和推理强度，并在发送提示词前确认选项。
- **会话管理**：创建、恢复会话，隔离同一连接上的不同会话消息。
- **工具与资源**：转发工具事件，将 QwenPaw 的 Skills、stdio / HTTP MCP 和图片附件投影到 DSH。
- **权限审批**：对接 QwenPaw 的审批接口，并将审批级别映射到 DSH 权限模式。
- **实时流式输出**：回答与思考的实时增量需要**另行安装** [dsh-qwenpaw-bridge v1.1.0](https://github.com/BillYuan01/dsh-qwenpaw-bridge)。

```text
QwenPaw → qwenpaw-dsh（本插件）→ ACP → DSH + dsh-qwenpaw-bridge → 模型
```

**注意：本插件与 DSH 桥是两个独立组件。** 安装本插件不等于安装桥，也不自动配置 DSH 模型、账号或凭据。

## 兼容要求

| 组件 | 要求 |
|---|---|
| QwenPaw | `>=2.2.0, <2.4.0`，即 2.2.x、2.3.x |
| DSH | `>=0.2.0-rc.2 <0.3.0-0`，需先配置模型路由及凭据 |
| ACP SDK | QwenPaw 的 Python 环境中须安装官方 `acp` |
| DSH 桥（可选增强） | `dsh-qwenpaw-bridge v1.1.0`，需要 Node.js >=22 与 pnpm |

目前主要在 Linux 验证；macOS、原生 Windows、WSL 未进行专项端到端验收。QwenPaw 2.3.0 正式版的加载兼容性已验证，但这不代表全部功能在所有环境中均已验证。

## 安装与配置

### 1. 准备 DSH 和可选增强桥

如果需要实时流式输出、扩展模型目录及桥接权限预设，先创建独立的 `qwenpaw` profile 并安装桥：

```sh
git clone --branch v1.1.0 https://github.com/BillYuan01/dsh-qwenpaw-bridge.git
dsh --profile qwenpaw --from-default-profile acp --dump-config >/dev/null
dsh plugin --profile qwenpaw add /absolute/path/to/dsh-qwenpaw-bridge
```

把最后一行改为实际绝对路径。桥须从本地目录安装，尚未发布到 npm；配置模型路由时合并已有的 `cordis.patch.yml`，不要直接覆盖。详见[桥的安装说明](https://github.com/BillYuan01/dsh-qwenpaw-bridge#readme)。

### 2. 安装本插件

在源码仓库根目录运行：

```sh
qwenpaw plugin install ./qwenpaw-dsh
```

或者下载对应版本的 [Release ZIP](https://github.com/BillYuan01/qwenpaw-dsh/releases)，按 QwenPaw 当前版本支持的方式安装。已有同名插件时，应先保存配置，并避免在活跃对话期间更新；热重载可能关闭旧连接。

### 3. 创建 DSH 智能体

在 QwenPaw 控制台新建 **DeepSeek Harness（DSH）** 后端智能体。若安装了桥，明确将 profile 设为 `qwenpaw`：

```json
{
  "backend": "dsh",
  "backend_settings": {
    "binary": "/absolute/path/to/dsh",
    "profile": "qwenpaw"
  }
}
```

以上只是配置片段。若 DSH 在服务的 `PATH` 中，可省略 `binary`。**默认 profile 是 `acp`，不是 `qwenpaw`**；没有配置桥时可以使用原生 ACP，但不会获得桥的扩展能力。

## 权限与已知限制

| QwenPaw 审批级别 | 对应 DSH 模式 |
|---|---|
| `STRICT` | `read-only` |
| `SMART` / `AUTO` | `workspace-write` |
| `OFF` | `danger-full-access` |

显式设置 `backend_settings.permission_mode` 时会优先使用该设置。审批拒绝、超时或审批接口故障按拒绝处理；单次授权不代表永久放行。

**`read-only` 不是文件读取白名单，插件也不是独立的安全沙箱。** `workspace` 预设中的 `approval_policy: never` 尚未由审批回调作为独立的强制拒绝规则实现；仍需依赖 DSH 本身的限制。流式输出异常、插件加载异常和版本检查也有已知边界。详见[已知实现限制](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/docs/known-limitations.md)，以及[桥的已知限制](https://github.com/BillYuan01/dsh-qwenpaw-bridge/blob/main/docs/known-limitations.md)。

当前不支持 SSE MCP、上下文用量显示、第三方后端 loop 模式设置或 QwenPaw 历史消息的自动重建。图片能力在会话初始化时检测，不会在切换模型后重新探测。

## 更多资料

- [中文完整 README](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/README.md) · [English README](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/README.en.md)
- [架构说明（英文）](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/docs/architecture.md)
- [验证记录（英文）](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/docs/verification.md)
- [版本更新记录](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/CHANGELOG.md)

许可证为 [Apache-2.0](LICENSE)。桥为独立仓库，采用 MIT 许可证。
