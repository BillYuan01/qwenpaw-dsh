# qwenpaw-dsh 插件安装目录

这是 QwenPaw 侧插件的安装根目录，包含 `plugin.json`、`plugin.py`、`LICENSE` 与 `qwenpaw_dsh/` Python 包。当前版本以 `plugin.json` 为准：**1.1.0**。

> 本目录是 v1.1.0 发布源码；`../dist/qwenpaw-dsh-1.1.0-plugin.zip` 由同一份源码按白名单可重现构建。历史 `1.0.0` / `1.0.1` ZIP 是各自时点的旧快照，保持原字节，不含本目录的修复。
>
> 配套的 DSH 侧桥 [`dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge) 本次同为 **v1.1.0**；两个仓库的版号相同表示配套发布，不表示 DSH 核心版本也变成 1.1.0（兼容范围仍是 `>=0.2.0-rc.2 <0.3.0-0`）。

完整安装、架构、兼容性与排障文档见[仓库 README](https://github.com/BillYuan01/qwenpaw-dsh#readme)，维护约定见[根目录 AGENTS.md](https://github.com/BillYuan01/qwenpaw-dsh/blob/main/AGENTS.md)。这里使用公开链接，便于此目录单独安装后阅读。

## 安装

从仓库根目录执行：

```sh
qwenpaw plugin install ./qwenpaw-dsh
```

从其他目录安装时，使用本目录的实际路径。安装执行插件代码并修改 QwenPaw 插件目录，应遵守所用宿主版本的生命周期要求；先确认目标环境，必要时在维护窗口重启。

## 使用前必须确认

- 当前清单和 monkeypatch 装载器支持 QwenPaw `>=2.2.0, <2.3.0`。未来 official loader 的预留代码不代表已支持 2.3+。
- 服务环境能找到兼容的 DSH CLI、ACP SDK、模型路由与凭据；QwenPaw 已安装不等于 DSH 模型已配置。
- 默认 DSH profile 是 `acp`。要获得桥提供的实时增量、模型目录和权限预设，先在 DSH 安装独立的 [`dsh-qwenpaw-bridge`](https://github.com/BillYuan01/dsh-qwenpaw-bridge)，再明确设置 `backend_settings.profile` 为 `qwenpaw`。
- Linux 有集成验证；macOS 和原生 Windows 尚未完成本项目端到端认证。不要把未验证说成已支持或绝对不可运行。
- 审批端口异常、拒绝和超时按拒绝处理；一次批准不构成永久授权。显式 `permission_mode` 会覆盖 `approval_level` 映射。
- MCP 支持 stdio 与 HTTP 投影，SSE 跳过；图片能力、Skills 资源和恢复行为仍有边界，见完整文档。
- `tests/` 混合单元与真实集成脚本。后者可能读取凭据、调用计费模型或执行工具，不要直接盲跑整个目录。

## 分发与许可

`dist/qwenpaw-dsh-1.1.0-plugin.zip` 与本目录源码对应，可用 `tools/plugin_package.py check` 逐成员核对。历史 `1.0.0` / `1.0.1` ZIP 是各自时点的快照，**不得重打同名版本**；其中 `1.0.1` 不含本目录的 `LICENSE` 与之后的安全修复。需要重新打包时从仓库根使用 [`tools/plugin_package.py`](../tools/README.md)：显式白名单包含本目录 `LICENSE`（与仓库根逐字节一致），输出到新文件名并运行 `check`。

DSH 桥、桥的 web 状态卡与源码辅助工具都不是此插件的内置组件，不会自动安装或启动。

Apache License 2.0，见本目录 [`LICENSE`](LICENSE)（与[仓库根 LICENSE](../LICENSE) 相同）。本项目为社区集成，非厂商官方组件。
