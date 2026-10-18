# DSH × QwenPaw 社区集成（v0.1）

把 **DeepSeek Harness（`dsh`）** 接入 **QwenPaw**，成为一等智能体：出现在智能体列表、可在控制台直接对话。通过标准 **ACP v1**（stdio）驱动，**不改任何一方的核心代码**。

## 组件

| 组件 | 目录 | 作用 |
|---|---|---|
| **qwenpaw-dsh** | `qwenpaw-dsh/` | QwenPaw 插件：注册 `dsh` 第三方智能体后端（装载层/适配器分离：进程池、会话恢复、模型目录、事件映射） |
| **dsh-qwenpaw-bridge** | `dsh-qwenpaw-bridge/` | DSH 侧桥插件（可选、推荐）：为 ACP 通道补上真·逐 token 流式、权限预设热切换、模型目录 `_meta`。不改 DSH core，fail-soft |

## 快速开始

```bash
# 0) 前置：Node.js；QwenPaw 2.2.x；DSH 0.2.x
npm i -g @deepseek-ai/dsh

# 1) （可选，推荐）安装桥插件 → 控制台里获得真流式
DSH_HOME=~/.dsh ./dsh-qwenpaw-bridge/profile/install.sh

# 2) 安装 QwenPaw 插件（热装载；未生效就重启一次 QwenPaw）
qwenpaw plugin install ./qwenpaw-dsh

# 3) 控制台 → 设置 → 智能体管理 → 创建智能体 → 后端选「DeepSeek Harness」→ 开聊
```

## 工作方式（一句话）

QwenPaw 的第三方后端系统（harness）经插件装载一个 `DshAdapter`；它用标准 ACP v1 拉起 `dsh --profile acp` 并驱动多轮会话。桥插件在同一个标准协议面上补充流式/热切换能力——双方都只依赖公开协议。

## 状态与兼容

- **v0.1 已实测**：文本/思考、工具生命周期、模型选择、跨重启会话恢复、进程池（证据见 `dsh-qwenpaw-bridge/TESTING.md`）
- 路线图：审批卡片、MCP/Skills 投影、图片附件、`context_usage`
- 兼容：QwenPaw **2.2.x**（装载层隔离；2.3 官方插件机制落地后仅替换装载层）；DSH **≥ 0.2.0-rc.2**（developer preview，接口可能变化）
- ⚠️ 社区方案、非官方；请自行评估风险后使用

## 许可

- `qwenpaw-dsh`：Apache-2.0（与 QwenPaw 一致，见根目录 `LICENSE`）
- `dsh-qwenpaw-bridge`：MIT（见其 `package.json`）
- © 2026 BillYuan01

---

## About (English)

A community integration that plugs **DeepSeek Harness** into **QwenPaw** as a
first-class agent backend over standard ACP v1 — no core changes on either
side. Two parts: a QwenPaw plugin (`qwenpaw-dsh`) and an optional DSH-side
bridge plugin (`dsh-qwenpaw-bridge`) adding true token streaming, hot-switchable
permission presets and a one-shot model catalog. Apache-2.0 / MIT. Community
project, not affiliated with the vendors; use at your own risk.
