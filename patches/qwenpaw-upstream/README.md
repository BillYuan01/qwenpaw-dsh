# QwenPaw 上游补丁（v0.3 成果留档）

> 基线：`agentscope-ai/QwenPaw` @ **`2705180`**（main，2026-10-19 clone）
> 本目录是这些改动的**唯一留档处**（按用户 2026-10-19 指令：只往自有仓库提交）。
> 上游曾开过 PR（`#8322` / `#8325` / `#8326`，配套 issue `#8321` / `#8323` / `#8324`）——状态以保持与本文件一致为准；补丁本身与是否开 PR 无关，永远可 rebase 复用。

| 补丁 | 内容 | 改动面 |
|---|---|---|
| `0001-*` | **feat(console): drive third-party provider grid from /harnesses** —— 控制台第三方后端网格改由 `GET /harnesses` 目录驱动；未知 backend 用通用文案（不再回退 Codex）；`coming_soon` 由 API 渲染为禁用卡；请求失败保留 codex/qoder 并显示提示；7 语言 i18n key 齐备 | `console/src/**`（11 文件，+341/−34） |
| `0002-*` | **fix(harnesses): map login/logout failures to readable 409** —— `POST /harnesses/{id}/login` 与 `logout` 不再把"不支持/缺依赖"漏成 500+堆栈，改为 409 + 可读字符串 `detail`（未知 provider 仍 404） | `src/qwenpaw/app/routers/harnesses.py` + 2 个测试文件（+213/−2） |
| `0003-*` | **feat(harnesses): add USAGE event channel for backend token usage** —— 新增 `HarnessEventKind.USAGE` + `HarnessUsage` 模型 + `HarnessEvent.usage` 可选字段；runtime 以"尽力而为"方式把用量送入既有 turn-usage 落地路径（不污染文本/工具流）；`TokenRecordingModelWrapper.record_usage_for_session()` 为第三方后端提供公开入口 | `src/qwenpaw/harnesses/{events,runtime}.py` + `src/qwenpaw/token_usage/model_wrapper.py` + 单测（+348/−30） |

## 应用

```bash
git clone --depth 1 https://github.com/agentscope-ai/QwenPaw.git
cd QwenPaw
git am /path/to/patches/qwenpaw-upstream/*.patch     # 三个补丁彼此独立，可单独 git am
```

依赖：补丁之间**无相互依赖**，可任意顺序单独应用。

## 已验证（2026-10-19，本地实跑）

| 补丁 | 验证 | 结果 |
|---|---|---|
| 0001 | `cd console && npm install && npx tsc -b --noEmit && npx eslint <files> && npx prettier --check <files> && npm run test:run -- src/pages/Settings/Agents/components/AgentBackendFields.test.tsx src/locales/thirdPartyAgentLocales.test.ts` | tsc/eslint/prettier 干净；**2 文件 33 用例全过** |
| 0002 | `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_harnesses_router.py tests/unit/app/routers/test_harnesses_router.py -q` | **19 passed** |
| 0003 | `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/harnesses/ tests/unit/token_usage/ -q` | **186 passed, 3 skipped** |
| 全部 | `pre-commit run --files <改动文件>`（mypy / black / flake8 / pylint / json / whitespace / private-key） | 通过（0003 有一处 black 重排已并入提交） |

## 提交身份

补丁内 author/committer 均为 `BillYuan01 <40728295+BillYuan01@users.noreply.github.com>`。
