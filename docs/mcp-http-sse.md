# MCP 投影的 HTTP / SSE 实测（门7 验证报告）

> 验证日期 **2026-10-19** ｜ QwenPaw **2.2.2** + `qwenpaw-dsh` **0.2.0** + DSH 0.2.0-rc.2
> 范围：`core/mcp.py` 的三条分支里，**stdio 已在门4 验过**；本报告补 **HTTP（streamable_http）** 的端到端实测与 **SSE** 的跳过行为。

## 1. 结论

| 传输 | 投影行为 | 本门结果 |
|---|---|---|
| `stdio` | → `McpServerStdio`（命令转绝对路径） | 门4 已验（`ping` → `PONG`） |
| `http` / `streamable_http` | → `HttpMcpServer` | ✅ **本门端到端通过**：DSH 连接夹具并调用工具，模型答 `PONG-HTTP` |
| `sse` | **跳过**（记日志） | ✅ **破坏性为零**：同会话里 HTTP 工具照常可用；跳过行为有明确日志行 |

## 2. HTTP 端到端：怎么验的

**夹具**（已入仓库）：`qwenpaw-dsh/tests/fixtures/http_mcp_server.py` —— FastMCP，`streamable-http` 传输，暴露一个工具：

```python
@mcp.tool()
def http_ping() -> str:
    return "PONG-HTTP"
```

**卡片**（`<workspace>/drivers/mcp/spike-mcp-http.yaml`）：

```yaml
name: spike-mcp-http
protocol: mcp
endpoint: {transport: streamable_http, url: http://127.0.0.1:8931/mcp, headers: {}}
enabled: true
```

**证据链（三层独立佐证）**

1. 夹具协议层自检：`POST /mcp` 的 `initialize` 正常返回（SSE 帧包裹），`serverInfo.name = gate7-http`
2. **夹具服务端日志**（决定性）：收到来自 `127.0.0.1` 的 `POST /mcp 200`，并打印
   `Processing request of type ListToolsRequest` → `Processing request of type CallToolRequest`
   （即 DSH 确实连上并调用了工具）
3. **模型侧**：新会话 `gate7-http-mcp` 一轮对话后，回答中出现 `PONG-HTTP`；请求里带 `http_ping` 工具名

```
curl -sN -X POST http://127.0.0.1:8088/api/console/chat \
  -H "X-Agent-Id: dsh" -H "Content-Type: application/json" \
  -d '{"channel":"console","user_id":"default","session_id":"gate7-http-mcp",
       "input":[{"content":[{"type":"text",
       "text":"Call the MCP tool named http_ping with no arguments, then reply with exactly its return value."}]}]}'
→ 回答含 PONG-HTTP；夹具日志含 ListToolsRequest + CallToolRequest
```

## 3. SSE：跳过是**有意的**，且已实测无副作用

在**同一轮**里同时挂了 stdio + http + sse 三张卡：

```
WARNING .../qwenpaw_dsh/core/mcp.py:195 | dsh: MCP server 'spike-mcp-sse' skipped: sse-unsupported
```

- 该行来自本插件的投影层，**跳过语义明确**（原因串 `sse-unsupported`），其余服务器不受影响
- **同会话的 HTTP 工具调用照常成功**（§2 证据），即"一张卡不可用不会拖垮会话"

### 一个容易踩的坑（宿主侧，不是本插件）

SSE 卡**enabled** 时，QwenPaw **自身的** MCP 层也会去连它（`drivers/handlers/mcp_stateful_client.py` → `mcp.client.sse`）：

```
ERROR .../mcp_stateful_client.py:303 | Error in MCP client lifecycle for spike-mcp-sse (failure 5/5)
ERROR .../mcp_stateful_client.py:320 | MCP client 'spike-mcp-sse': circuit breaker OPEN after 5 consecutive failures. ... Will probe after 300s
```

即：**指向不可达 SSE 服务时，宿主会做 5 次重连后熔断 300s**，产生日志噪音。这属于宿主的原生 MCP 通道，与 DSH 投影无关；本门结束后两张夹具卡已置 `enabled: false`，恢复门7 前状态。

## 4. 对使用者的实际建议

- 需要给 **DSH（第三方 backend）** 用的 MCP 服务：用 **stdio 或 HTTP(streamable_http)** —— 两者均已实测端到端
- **SSE 类型**：QwenPaw 原生智能体可用，但 **DSH 用不了**（DSH 不支持 SSE MCP）；配了 SSE 卡不会报错，只会被跳过 + 宿主侧可能重连
- 坏 server 的安全网依旧有效：`session/new` 失败时适配器会**去 MCP 重试一次**（门4 实测），会话保持可用

## 5. 复现

```bash
# 1) 起 HTTP 夹具
~/.qwenpaw/venv/bin/python qwenpaw-dsh/tests/fixtures/http_mcp_server.py --port 8931 &

# 2) 在 <workspace>/drivers/mcp/ 放上面那张 spike-mcp-http.yaml（enabled: true）

# 3) 发一轮对话（见 §2 的 curl）→ 期望回答含 PONG-HTTP

# 4) 清理：停夹具进程 + 把卡片 enabled 改回 false
```
