# 实现说明

本仓库维护 QwenPaw 侧 Python 插件。DSH 侧桥在 [dsh-qwenpaw-bridge](https://github.com/BillYuan01/dsh-qwenpaw-bridge)，两者通过 ACP v1 over stdio 通信。

## 装载

`plugin.py` 组装 `HostContext` 并注册启停钩子。QwenPaw 2.2.x 装载器在进程内替换 harness registry 及各模块按值导入的引用；不改写宿主安装文件。挂载前检查目标与版本，卸载时还原原符号。`OfficialLoader` 是未来接口的预留，不代表已支持 2.3+；`DirectLoader` 用于测试和嵌入，不是加载失败的生产回退。

`core/` 依赖宿主的事件契约和 ACP SDK，但不导入 registry 或 router。审批服务通过 `ApprovalPort` 接入，具体宿主导入留在端口实现。

## 回合、连接与取消

连接池键包含 binary、profile、权限模式、审批策略和 Skills 快照。同一个池键不代表同一个进程：live 映射还记录连接对象及代次。进程重建后先恢复会话，失败才新建，并说明上下文已重建。

每个 ACP 会话有独立队列。同一 QwenPaw 会话的回合和 reset 使用同一把锁，不同会话可以并发。连接关闭时显式通知等待中的消费者，不能依赖 transport 自行报错。终态只属于本轮；缺 response、缺 stop reason 或后台 prompt 独立取消都不能报正常完成。

用户取消时最多发送一次 `session/cancel`，有限等待 prompt 结算。无法确认结算的连接先同步移出可复用池，再交由关闭任务处理。被移出池的连接仍有清理归属，替换连接启动失败也不能遗失旧进程。关闭任务异常或取消不算关闭成功。

等待时限只限制调用方等待；不配合取消的协程或子进程不保证立即停止。进程树终止是尽力执行，日志会区分“尝试终止”和“确认关闭”。

## 模型与工具事件

模型选择值优先取本会话 `configOptions`，其次目录缓存；目录刷新采用 single-flight。目录通常缓存 600 秒，失败快照短期保留。探测会启动 DSH 并创建会话，不是纯文件检查。

显式 model 先于 reasoning effort 下发，响应的 `currentValue` 必须确认匹配，否则本轮报错、不发送 prompt。档位只采用当前模型的可选值，不从所有模型合并。同名模型保留 provider 身份；缺失、空白设置表示不改当前默认，不能用它显式重置到 ACP 的空字符串档位。

`event_mapper.py` 将回答、思考、工具开始／进度／完成转为宿主事件。1.1.1 支持 `type=content` 包装中的文本，工具展示上限 32768 字符，超出带截断标记；递归深度和循环检测限制异常载荷。正常回答与思考不截断。图片、音频、blob、diff、terminal 引用和未知内容不字符串化，也不从 rawInput/rawOutput 抽取文本。工具 failed 状态仍使用 TOOL_COMPLETED 事件，并保留 `data.status`。

## Skills、MCP、图片

- **Skills**：名称归一化为 kebab，使用 YAML 节点位置只改唯一顶层 name。每组源路径、版本与目标名生成独立快照，patch 和影子树都位于快照中；不同回合不会覆盖对方资源。旧快照不自动删除，源资源仍是实时链接。清单键不可信，清理仅接受单层合法名称，不跟随链接删除目标。父目录被并发替换的竞争仍是残余风险。
- **MCP**：按回合投影配置，会话指纹覆盖命令、参数、URL、env 和 headers。含凭据的摘要只在内存比较，不落盘。变化时先 close 再携新列表 resume；关闭失败退役连接且不 prompt。恢复失败才创建新会话并告警。新会话挂载 MCP 失败可去 MCP 重试一次；stdio 与 HTTP 支持，SSE 跳过。宿主 Driver 策略不会被完整透传。
- **图片**：初始化能力允许时内联 PNG/JPEG/GIF/WebP/BMP。只读普通文件，打开后复核并有界读取，最多 15 MiB；拒绝链接和特殊文件。失败降级为文本路径。会话内切模型不重新探测图片能力。

## 当前接口限制

`/status` 只读内存计数和配置摘要，不启动 DSH。`history()` 返回空列表：可以恢复 ACP 会话，不代表可以从 DSH 重建宿主聊天记录。当前不提供 `context_usage` 和第三方 loop 模式选择。

审批映射的优先级是显式 permission_mode、会话 approval_level、智能体 approval_level、默认 AUTO。STRICT 对应 read-only，SMART/AUTO 对应 workspace-write，OFF 对应 danger-full-access。拒绝、超时或审批端口故障不能变成授权；一次同意不构成永久权限。
