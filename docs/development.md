# 开发与打包

## 目录

| 路径 | 用途 |
|---|---|
| `qwenpaw-dsh/plugin.json` / `plugin.py` | 安装清单、生命周期入口 |
| `qwenpaw-dsh/qwenpaw_dsh/host.py` / `approvals.py` | 宿主端口、审批适配 |
| `qwenpaw-dsh/qwenpaw_dsh/provider.py` / `loaders/` | 能力声明、注册与卸载 |
| `qwenpaw-dsh/qwenpaw_dsh/core/` | ACP、回合、事件及资源投影 |
| `qwenpaw-dsh/tests/` | 单元和真实集成测试，注意区分 |
| `tools/` | 本地测试入口和白名单打包器 |
| `dist/` | 历史发布包，不覆盖 |
| `patches/qwenpaw-upstream/` | 归档补丁，不自动应用 |

[架构说明](architecture.md)解释运行行为，[AGENTS.md](../AGENTS.md)规定修改约束。

## 默认检查

在已有 QwenPaw、ACP SDK 和 PyYAML 的 Python 环境，从仓库根运行：

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
python tools/run_safe_tests.py --list
git diff --check
```

runner 显式选择 13 个文件，当前共 272 项：既有安全用例 226 项，打包／runner 用例 26 项，事件映射用例 20 项。它使用临时 HOME、DSH_HOME 和最小环境，不启动 DSH，不读取真实凭据，也不自动安装依赖。依赖缺失、0 tests、失败或默认被跳过的测试都不能记为通过。

按改动选择相应测试，不要为纯文档更新调用模型：

| 修改 | 测试文件（内层 `qwenpaw-dsh/tests/`） |
|---|---|
| 事件映射 | `test_event_mapper.py` |
| 回合、连接、取消、恢复 | `test_lifecycle.py` |
| 模型或宿主接口 | `test_model_contract.py`、`test_host_contract.py` |
| 审批、装载 | `test_permission_mode.py`、`test_approval_port.py`、`test_loader_wiring.py` |
| Skills、图片、MCP | `test_skills.py`、`test_skills_shadow_security.py`、`test_file_projection_g4.py`、`test_images.py`、`test_mcp.py` |
| 打包、测试入口 | `test_delivery_tools.py` |

## 真实集成测试

以下入口会启动真实 DSH，可能使用凭据、调用计费模型或执行工具：

- `tests/smoke_adapter.py`、`tests/stream_check.py`
- `tests/test_*_projection.py`
- `tests/contract/` 及其 spike／seed helper

不要对整个 tests 目录直接执行 pytest 收集。获准运行前，先检查脚本如何创建 home、取凭据及处理权限；旧 helper 可能链接真实 `.env` 或凭据库，临时目录不等于隔离环境。真实调用的目标、权限、费用和输出位置需要单独授权，不能用 full-access 或自动 allow 掩盖故障。

契约套件的说明在 [tests/contract/README.md](../qwenpaw-dsh/tests/contract/README.md)。历史运行形态与 MCP 测试分别见 [forms-matrix.md](forms-matrix.md)、[mcp-http-sse.md](mcp-http-sse.md)。这些记录有各自的日期和版本，不替代当前 checkout 的验证。

## 构建发布包

使用 [tools/plugin_package.py](../tools/plugin_package.py)，不要从现役安装目录复制文件。它按 21 文件白名单构建，拒绝覆盖已有输出、源码树内输出、链接和特殊文件；固定成员顺序、时间戳和权限，包含与根目录一致的 LICENSE。

```sh
python tools/plugin_package.py build --source . \
  --output /path/outside/repo/qwenpaw-dsh-1.1.1-plugin.zip --expected-version 1.1.1
python tools/plugin_package.py check --source . \
  --zip /path/outside/repo/qwenpaw-dsh-1.1.1-plugin.zip --expected-version 1.1.1
```

`check` 比较包与所选 checkout 的成员字节、版本、许可证和卸载回调签名。**只能拿包对照它所属的源码版本**；main 仅更新文档后，不能用它检查旧 tag 的包，再把差异判作损坏。

只改文档不用升版本或重打包。发布前同步清单、客户端版本、CHANGELOG 和安装说明，再构建及核验新文件；旧 ZIP 和 tag 保持原样。文档更新可以先于下一次软件发布存在于 main，已发布 ZIP 中的 README 仍是对应 tag 的快照。

发布、推送和部署分别需要授权。报告执行过的检查、失败和未运行项，不把本地提交当作已发布，也不把发布当作已安装。
