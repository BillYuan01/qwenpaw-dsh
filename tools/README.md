# tools

本地交付工具。均为 stdlib/现有运行时，不安装依赖、不启动 DSH、不读真实凭据。它们不是发布授权。

## `plugin_package.py`

显式白名单（21 个文件）打包/校验器。包内布局是 `plugin.json`、`plugin.py`、`README.md`、`LICENSE`、`qwenpaw_dsh/`，没有 `tests/`、缓存、状态或链接。

```sh
# 构建到新路径（拒绝覆盖；输出不得在源码树内）。期望版本取自当前 plugin.json。
python tools/plugin_package.py build \
  --source . --output /path/outside/repo/qwenpaw-dsh-1.1.0-plugin.zip \
  --expected-version 1.1.0

# 只读校验（当前发布包应通过；历史 dist/1.0.1 应失败）
python tools/plugin_package.py check \
  --zip dist/qwenpaw-dsh-1.1.0-plugin.zip \
  --source . --expected-version 1.1.0
```

保证：

- 只读明确 checkout；不复制 `~/.qwenpaw/plugins/` 的现役安装。
- 源文件/目录逐级 `lstat`；符号链接、FIFO/设备等非常规文件、白名单外源码文件一律拒绝。
- 固定成员顺序、`1980-01-01` 时间戳、`0644` 权限、`ZIP_STORED`；同一源码两次构建字节一致。
- 输出已存在（含悬空链接）或位于源码树内 → 拒绝；先写临时文件再原子链接，不覆写。
- 校验重复成员、路径穿越/绝对路径、目录条目、权限、加密位、版本、LICENSE 一致性、每个成员字节，以及 `plugin.py` 的 `_shutdown(**kwargs)`（AST，不执行代码）。

历史 `dist/` ZIP（`1.0.0` / `1.0.1`）只读校验失败是预期结果；工具不会修改它们。`--expected-version` 属于测试/发布断言，调用方应从 `qwenpaw-dsh/plugin.json` 读取当前值，不要写死历史版本。

## `run_safe_tests.py`

显式白名单的离线单元测试入口。默认 12 个文件：现有 11 个共 **226** 项 + 本门的 `test_delivery_tools.py`。

```sh
python tools/run_safe_tests.py --python /path/to/qwenpaw-venv/bin/python
python tools/run_safe_tests.py --list
python tools/run_safe_tests.py --only test_permission_mode.py --skip-count-check
```

- 不做 `discover test_*.py` 宽泛收集；`tests/test_*_projection.py`、`smoke_adapter.py`、`stream_check.py`、`tests/contract/` 不在白名单。
- 每个文件单独跑 `unittest discover -p <file>`；工作目录内层 `qwenpaw-dsh/`。
- 一次性临时 `HOME/DSH_HOME/QWENPAW_HOME/QWENPAW_WORKING_DIR/TMPDIR`，最小环境 allowlist；不继承 key/token/PYTHONPATH。
- 逐文件与总计打印 tests/failures/errors/skipped；skip 默认退出 3（`--allow-skips` 才接受），缺依赖/缺文件明确失败，不自动 pip。
- 现有 11 个文件的条数硬校验（合计 226），防止 0 tests 假绿。
