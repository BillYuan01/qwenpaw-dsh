# 运行形态 × 插件加载矩阵（门6 验证报告）

> 验证日期 **2026-10-19** ｜ 环境：QwenPaw **2.2.2**（`~/.qwenpaw/venv`）、`qwenpaw-dsh` **0.2.0**、DSH 0.2.0-rc.2 ｜ 方法：**代码判定 + 隔离 `QWENPAW_WORKING_DIR` 真实启动**
> 结论只覆盖本机可实测的形态；未跑到的形态明确标注为「本门未验证」，不做推测。

## 0. 解析规则（形态无关）

- `WORKING_DIR` 解析优先级：`QWENPAW_WORKING_DIR` → 旧目录 `~/.copaw` → `~/.qwenpaw`（`qwenpaw/constant.py`）
- `PLUGINS_DIR = WORKING_DIR / "plugins"` —— **与形态无关**
- **插件装载的唯一入口是构建 FastAPI app 时**：`qwenpaw/app/_app.py` 中 `PluginLoader(get_plugins_dir())`
  → 因此「是否装载插件」= 「该形态是否构建 app」

## 1. 矩阵

| 形态（CLI） | 构建 app？ | 插件装载 | 本门证据 |
|---|---|---|---|
| `qwenpaw app` | ✅ | ✅ | 隔离 home 冷启动，见 §2 |
| `qwenpaw desktop` | ✅（子进程跑 app） | ✅ | 隔离 home 真启动，见 §3（窗口本体被 GUI 依赖挡住） |
| `qwenpaw task`（无头单任务） | ❌（`AgentBuilder` 直建） | ❌ | 隔离 home 实跑见 §4：任务成功但日志零插件痕迹 |
| `qwenpaw hub`（多用户控制面） | 按用户空间拉起实例 | 未验证 | 本门未跑 |
| `qwenpaw tui` | 未判定 | 未验证 | 本门未跑 |
| `qwenpaw acp`（把 QwenPaw 当 ACP agent） | 未判定 | 未验证 | 本门未跑 |

## 2. `app` 形态：隔离 home 冷启动 ✅

隔离目录 `/tmp/qp-forms/app-home`（全新，无历史状态），本地目录安装插件后启动：

```
Location: /tmp/qp-forms/app-home/plugins/qwenpaw-dsh
INFO plugins/loader.py:806 | ✓ Loaded plugin 'qwenpaw-dsh' successfully
INFO plugins/registry.py:501 | Registered startup hook 'dsh_mount' from plugin 'qwenpaw-dsh' (priority=10)
INFO app-home/plugins/qwenpaw-dsh/plugin.py:46 | dsh harness mounted via monkeypatch-2.2:
     {'mounted': ['qwenpaw.app.routers.agents', 'qwenpaw.app.routers.harnesses',
                  'qwenpaw.harnesses.registry', 'qwenpaw.harnesses.runtime'],
      'missing': [], 'ok': True, 'detail': 'version 2.2.2 in [2.2.0, 2.3.0)'}
```

同实例 `GET /api/harnesses`（**dsh 在目录里，且探测为已安装/已认证**）：

```
id=codex    name=Codex            installed=True  coming_soon=False  auth=False
id=claude   name=Claude Code      installed=False coming_soon=True   auth=False
id=qoder    name=Qoder            installed=False coming_soon=False  auth=False
id=dsh      name=DeepSeek Harness installed=True  coming_soon=False  auth=True
```

`qwenpaw plugin list`（隔离 home）：`DeepSeek Harness (DSH) backend (v0.2.0)`。

> 附带验证：`claude` 在目录中为 `coming_soon=True` —— 正是控制台网格动态化补丁（`patches/qwenpaw-upstream/0001`）所依赖的字段形状。

## 3. `desktop` 形态：后端 ✅ / 窗口 ⛔（缺 GUI 后端）

隔离目录 `/tmp/qp-forms/desk-home`，`DISPLAY=:0` 启动：

```
INFO:     Uvicorn running on http://127.0.0.1:52629 (Press CTRL+C to quit)   # 端口写入 <home>/desktop_port
INFO plugins/loader.py:806 | ✓ Loaded plugin 'qwenpaw-dsh' successfully
INFO desk-home/plugins/qwenpaw-dsh/plugin.py:46 | dsh harness mounted via monkeypatch-2.2: {... 'ok': True}
INFO app/_app.py:170 | ✓ Completed shutdown hook 'dsh_unmount' from plugin 'qwenpaw-dsh'
ERROR cli/desktop_cmd.py:382 | WebViewException('You must have either QT or GTK with
      Python extensions installed in order to use pywebview.')
```

**判定**：desktop 形态下**插件装载链路与 `app` 形态完全相同**（desktop 只是「子进程 app + pywebview 窗口」，端口写在 `<home>/desktop_port`），已实测挂载成功；**唯独原生窗口起不来**，原因是本机 pywebview 既没有 GTK 绑定（`import gi` 失败）也没有 Qt。

**补窗口的两种方式（未执行，涉及安装系统包/额外依赖，需用户决定）**
- 系统包：`sudo apt install python3-gi gir1.2-webkit2-4.1`（GTK + WebKit2）
- 或 Python 侧：`pip install pyqt5 pyqtwebengine`（第三方轮子更重）

## 4. `task` 形态（无头，无 web server）：**不装载插件**

隔离 home 实跑：

```
QWENPAW_WORKING_DIR=/tmp/qp-forms/app-home qwenpaw task -i "reply with the single word PONG" --agent-id default
→ EXIT=0，结果 {"response": "\n\nPONG"}
→ 日志中 "plugin" 出现次数：0
```

代码印证：`cli/task_cmd.py` 直接使用 `runtime.builder.AgentBuilder`，**不构建 FastAPI app**，因此不经过 `PluginLoader`。

**影响（重要）**：无头 `task` 形态**用不到 `dsh` 后端**（该形态里第三方 agent backend 不存在）。要用 DSH 的无头能力，应走 `qwenpaw app` + API，或直接跑 `dsh` CLI。

## 5. 对本项目的结论

1. **一次安装覆盖所有 app 系形态**（`app` / `desktop` / 控制台 / 任何拉起 app 的路径），因为插件目录由 `WORKING_DIR` 唯一决定、装载点唯一（`app/_app.py`）。
2. **桌面窗口不是插件问题**：插件已挂载；缺的是宿主机的 GTK/Qt 绑定。补上依赖即可，无需改本插件。
3. **无头 `task` 形态天然不含第三方 backend**（宿主设计如此，不是本插件的缺陷）——已在文档中标注，避免误判为 bug。
4. 本门**未验证**：`hub` / `tui` / `acp` 三种形态（未跑；判定它们是否构建 app 即可推断，留给后续门或按需补测）。

## 6. 复现命令

```bash
# 隔离 home + 安装插件
export QWENPAW_WORKING_DIR=/tmp/qp-forms/app-home
qwenpaw plugin install <repo>/qwenpaw-dsh --force

# app 形态：冷启动后查目录
qwenpaw app --host 127.0.0.1 --port 8099 &   # 等待启动
curl -s http://127.0.0.1:8099/api/harnesses | python3 -m json.tool | grep -A2 '"id": "dsh"'

# desktop 形态：端口写在 <home>/desktop_port
DISPLAY=:0 qwenpaw desktop --log-level info &  # 需 GTK/Qt 才能开出窗口
cat $QWENPAW_WORKING_DIR/desktop_port

# task 形态（不装载插件，仅作对照）
qwenpaw task -i "reply with the single word PONG" --agent-id default
```
