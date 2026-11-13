# v1.1.4 — AgentScope Platform 中文展示适配

> 发布状态：**准备就绪的源码，尚未创建 GitHub Release 或上传 ZIP 附件**。本文件是待发布的中文说明和操作清单，不应被视为已发布凭据。

## GitHub Release 正文（发布时可直接使用）

### 更新内容

- **中文优先展示**：`qwenpaw-dsh/plugin.json` 的插件名称和简介改为中文，准确介绍模型选择、会话恢复、工具事件、权限审批，以及独立安装 `dsh-qwenpaw-bridge` 的要求。
- **安装包中文 README**：插件包内的 `README.md` 改为简体中文，在顶部提供 [English README](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/README.en.md) 跳转。GitHub 仓库首页也默认显示中文。
- **版本同步**：插件清单与 ACP 客户端版本号同步至 `1.1.4`。桥 `dsh-qwenpaw-bridge` 继续使用 `1.1.0`。
- **运行逻辑不变**：未修复或修改 ACP 通信、模型路由、权限审批、流式输出、工具映射、会话、Skills 或 MCP 等业务逻辑。

### 兼容范围

- QwenPaw：`>=2.2.0, <2.4.0`（2.2.x、2.3.x）。
- DSH：`>=0.2.0-rc.2 <0.3.0-0`。
- 桥：推荐 [dsh-qwenpaw-bridge v1.1.0](https://github.com/BillYuan01/dsh-qwenpaw-bridge)；实时流式增量需另行安装桥并选用 `qwenpaw` profile。

### 已知边界

本版本是插件市场中文化及元数据更新，不代表安全问题已修复。详见[已知实现限制](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/docs/known-limitations.md)。Linux 的既有集成测试属于历史验证记录，不等同于本版新执行的端到端测试。

**文档：** [简体中文](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/README.md) | [English](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/README.en.md)

---

## 发布前：从当前源码重新生成 ZIP

在仓库根目录操作。运行这两条命令只在指定输出路径创建新包、校验该包；不会调用付费模型，也不会安装插件。

```sh
# 输出路径必须尚不存在，且不能位于仓库源码目录内部
mkdir -p /tmp/qwenpaw-dsh-release-1.1.4
python tools/plugin_package.py build \
  --source . \
  --output /tmp/qwenpaw-dsh-release-1.1.4/qwenpaw-dsh-1.1.4-plugin.zip \
  --expected-version 1.1.4

python tools/plugin_package.py check \
  --source . \
  --zip /tmp/qwenpaw-dsh-release-1.1.4/qwenpaw-dsh-1.1.4-plugin.zip \
  --expected-version 1.1.4

sha256sum /tmp/qwenpaw-dsh-release-1.1.4/qwenpaw-dsh-1.1.4-plugin.zip
```

校验时检查：

- ZIP 包含 **21 个白名单成员**，其中 `README.md` 开头为 `简体中文 | [English](...)`；
- `plugin.json` 的 `version` 为 `1.1.4`，`name` / `description` 为中文；
- 代码中的 `_CLIENT_VERSION` 为 `1.1.4`；
- 若使用现有 Python 环境，按仓库 [开发说明](../docs/development.md) 运行白名单离线测试并如实记录结果，不能把旧版的 278 项通过记录当成这次实测。

## 发布至 GitHub

在 GitHub 仓库的 **Releases → Draft a new release** 中：

1. 在确认版本、文件和测试结果后，于本次最终源码提交上创建新标签 `v1.1.4`；不要移动旧版标签。
2. 标题使用 `v1.1.4 — 中文插件介绍与 README / Chinese marketplace metadata`。
3. 粘贴上方的 Release 正文，上传刚构建并校验的 `qwenpaw-dsh-1.1.4-plugin.zip`。
4. 将 SHA256 添加到正文，确认附件已上传成功，再发布 Release。

随后在 AgentScope Platform 重新提交/更新插件的 GitHub 来源，以确保它重新抓取 **v1.1.4 新 ZIP**，而不是缓存的 v1.1.3 安装包。平台是否重新抓取应以预览中的版本、中文 `description` 和中文 README 为准。

**不要重新打包或覆盖 v1.1.3 的文件，也不要将当前源代码直接改名冒充 v1.1.4 ZIP。**
