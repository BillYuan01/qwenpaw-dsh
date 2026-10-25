# AGENTS.md

本仓库维护 QwenPaw 侧 Python 插件。使用说明见 [README.md](README.md)，实现细节见 [docs/architecture.md](docs/architecture.md)，测试与打包见 [docs/development.md](docs/development.md)。

## 开始修改前

- 读涉及的源码、测试和当前 diff，保留已有修改，不用 reset/clean 清工作区。
- 安装目录是内层 `qwenpaw-dsh/`。当前插件 1.1.1，配套桥 1.1.0；版本以清单为准，不能按两个仓库同版号来推断兼容性。
- 宿主支持 `>=2.2.0, <2.3.0`。future official loader 不是 2.3+ 支持承诺；不要为过测试移除守卫。
- 只改请求范围。文档任务不包含代码修复、部署、模型调用或发布。

## 代码入口

以下路径位于 `qwenpaw-dsh/` 中：

| 路径 | 作用 |
|---|---|
| `plugin.py`、`plugin.json` | 启停钩子与安装清单 |
| `qwenpaw_dsh/host.py`、`approvals.py` | 宿主端口与审批服务适配 |
| `qwenpaw_dsh/provider.py`、`loaders/` | 后端能力、挂载与卸载 |
| `qwenpaw_dsh/core/adapter.py`、`acp_client.py` | 连接、会话、回合和 ACP |
| `qwenpaw_dsh/core/event_mapper.py` | 纯事件映射 |
| `qwenpaw_dsh/core/permissions.py` | 权限映射的唯一实现 |
| `qwenpaw_dsh/core/skills.py`、`mcp.py`、`images.py` | 资源投影与附件 |

`core/` 可依赖宿主稳定事件／适配器契约，不得导入 registry/router 实现；相关耦合放在 loader 和审批端口。

## 修改时必须保留的行为

### 装载与会话

- monkeypatch 覆盖所有目标及按值导入引用；缺目标要明确失败。卸载精确还原，不留下半挂载。DirectLoader 不是生产回退。
- 默认 profile 是 `acp`；使用桥要明确选择 `qwenpaw`，不要暗改默认值。
- 连接身份与池键分开。新进程必须 resume/new，不得直接 prompt 旧进程的 session id。
- 每会话独立队列，同一 QwenPaw 会话的 turn/reset 共用串行锁。不得吞掉其他会话帧或复用全局完成哨兵。
- 缺 response、缺 stop reason、后台任务独立取消不能报 COMPLETED。每轮只产生一个终态，任务异常必须回收。
- 取消最多通知一次，有限等待；放弃等待前同步隔离连接。移出池的连接仍须有关闭任务归属，替换启动失败也要清理旧连接。
- 关闭先唤醒活跃消费者。关闭异常／取消不算成功；超时和尽力 kill 不等于确认进程已停。setup 清理不得覆盖原错误，finally 不得无界等待。
- 恢复失败才新建，并向使用者说明上下文已重建。`history()` 返回空列表，不虚构历史；`/status` 只读内存，不启动 DSH 或读取凭据。

### 模型、权限和事件

- 显式 model→effort 按顺序应用，响应 currentValue 确认后才 prompt；失败报错，不默用默认模型。目录保留 provider 身份，档位只来自当前模型。
- 缺／过期目录采用 single-flight 探测；模型数量不固定，探测可能启动 DSH。空白设置表示不指定，不代表重置模型。
- 审批缺失、拒绝、超时或异常按拒绝处理，优先最小一次性授权，不持久放行。显式 permission_mode 优先于会话／智能体 approval_level；OFF 对应 full-access。
- read-only 不是读取白名单；不为排障默认关闭审批、沙箱或扩大权限。
- 工具文本只提取规范的 content/text 及兼容文本形状，不 repr 未知对象、不展示二进制／rawOutput。保留 32768 字符工具预览上限、截断标记和递归保护；正常回答／思考不截断，failed 状态保留。
- stdout 仅用于 ACP 协议，不混日志或调试 print。

### 资源投影

- Skills 使用独立快照，token 包含源位置／版本。空回合不删其他快照，不自动 GC。清单键按不可信输入处理，只清合法单层名称，不跟随符号链接删除。
- YAML 只更新唯一顶层 name；非法／重复 name 跳过，保留其他正文。源资源是实时链接，不宣称不可变；父路径并发替换仍有残余风险。
- MCP 指纹覆盖连接字段及 env/headers 变更，只在内存使用，不记录凭据摘要。变更先 close 再携完整列表 resume；关闭失败阻止 prompt，恢复失败只新建并告警。保留无 MCP 重试、SSE 跳过，不声称宿主 Driver 策略已完整透传。
- 图片按初始化能力发送，只读普通文件并用有界循环读取；拒绝特殊文件和链接。切换模型不重探图片能力，不随意扩大格式或大小限制。

## 验证

从仓库根运行：

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
python tools/run_safe_tests.py --list
git diff --check
```

runner 当前选择 13 文件／272 项，不调用模型。按修改选择对应测试的表格见 [development.md](docs/development.md)。缺依赖、失败或 skip 单独报告；不要自动安装依赖或为凑绿改断言。

**不要广泛收集 tests。** `smoke_adapter.py`、`stream_check.py`、`test_*_projection.py`、contract/spike/seed helper 可能启动 DSH、链接真实凭据或产生费用。运行前检查其行为，并取得目标、权限、费用范围的授权。临时 DSH_HOME 不自动等于凭据隔离。

## 数据与交付

- 不用真实凭据或私人文件完成单测／文档工作；不用日志输出 key、token、headers、完整环境、profile dump 或会话映射。仅用合成夹具。
- 不提交生产日志、私有报告、状态、缓存、凭据、机器路径和会话编号。服务错误也可能带敏感内容，公开前审阅。
- 文档更新不改版本、不重打已发布 ZIP。当前 main 的文档可新于 release tag；旧包只对照对应 tag 源码校验。
- 发版时同步清单、客户端常量、CHANGELOG，使用 `tools/plugin_package.py` 白名单构建／检查新文件，旧资产和 tag 不覆盖。新增运行文件时同步包白名单和测试。
- commit/push、PR/issue、发布、安装、重启及计费调用按本轮授权执行，不沿用过往许可。提交用认可的 noreply 身份，不改全局 Git 配置。
- 交付说明修改文件、实际检查、未验证范围和发布／部署状态。实际测试证据见 [verification.md](docs/verification.md)，不得把某次 Linux 测试写成全平台支持。
