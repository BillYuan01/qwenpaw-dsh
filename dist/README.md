# dist

发布快照（构建产物，**不得手工修改、不得重打同名版本**）。源码更新不会自动改写这里。

| 文件 | 大小 | SHA256 | 说明 |
|---|---:|---|---|
| `qwenpaw-dsh-1.1.1-plugin.zip` | 207452 | `6a7bffb1f58adcdd8b41cdbe83d4d4254454e29570fe9638cafbaa1b7a0af5af` | 2026-10-25 工具映射补丁，与本版源码逐成员一致 |
| `qwenpaw-dsh-1.1.0-plugin.zip` | 203101 | `527ea24b2219f8018a3da80e10af6f56c7933f4e428eea761a50ae624628971d` | 2026-10-25 的 v1.1.0 历史快照 |
| `qwenpaw-dsh-1.0.1-plugin.zip` | 38873 | `2689d89a9cd62d748582185c07ac2654021fe9c1cced57d5c64ace0877bf4a2d` | 2026-10-21 发布的 v1.0.1 快照 |
| `qwenpaw-dsh-1.0.0-plugin.zip` | 37965 | `8ef56414fa53930cedec76ee890c1c95182f767651797111145013bb8da38c92` | v1.0.0 历史资产 |

## 为什么不能重打旧包

每个 ZIP 是发布时点的快照。`qwenpaw-dsh-1.0.1-plugin.zip` **不含 `LICENSE`**，`plugin.py` 也不接受宿主传入的 `plugin_id=`（G1 之前）；它不代表当前源码。重打同名 ZIP 会让同一个版本号出现两个不同字节内容，破坏已发布资产的可追溯性，因此历史 ZIP 保持原样。

用新校验器只看不写地检查旧包，应当失败（缺 LICENSE、旧 `plugin.py`、旧源码字节和权限）：

```sh
python ../tools/plugin_package.py check \
  --zip qwenpaw-dsh-1.0.1-plugin.zip --source .. --expected-version 1.0.1
```

这不是旧包“损坏”，而是它本来就不代表当前源码；**不要把失败后的包改名或重打后当作正式发布**。

## 校验当前发布包

```sh
python ../tools/plugin_package.py check \
  --zip qwenpaw-dsh-1.1.1-plugin.zip --source .. --expected-version 1.1.1
```

包内 21 个成员与源码逐字节一致，固定 `1980-01-01` 时间戳、`0644` 权限、无目录条目。同一源码重复构建应得到完全相同的 SHA256。

## 生成下一个版本（需维护者批准）

1. 先更新文档与 `CHANGELOG.md`，再按发布流程提升 `../qwenpaw-dsh/plugin.json` 版本。**版本以清单为准，不要在脚本里写死旧版本。**
2. 用白名单打包器从明确的 checkout 构建到**新文件名**（工具拒绝覆盖已有文件，也拒绝把输出写进源码树）：

   ```sh
   VERSION=$(python3 -c 'import json;print(json.load(open("../qwenpaw-dsh/plugin.json"))["version"])')
   python ../tools/plugin_package.py build \
     --source .. \
     --output /path/outside/repo/qwenpaw-dsh-${VERSION}-plugin.zip \
     --expected-version "${VERSION}"
   ```

3. 用同一工具校验成员、版本、LICENSE、字节一致性与卸载回调兼容性：

   ```sh
   python ../tools/plugin_package.py check \
     --zip /path/outside/repo/qwenpaw-dsh-${VERSION}-plugin.zip \
     --source .. --expected-version "${VERSION}"
   ```

4. 只有经过维护者批准的发布动作，才把新 ZIP 提交/上传；历史 ZIP 不覆盖。发布产物的文件名、tag 与清单版本必须一致。

## 打包白名单

`LICENSE`、`README.md`、`plugin.json`、`plugin.py`、`qwenpaw_dsh/` 下的 17 个模块文件（共 21 个）。`tests/`、缓存、状态、日志、凭据、报告与符号链接都不进包。`LICENSE` 必须与仓库根 `../LICENSE` 逐字节一致。

临时验证包可以构建到仓库外的目录，但它**不是发布资产**，不提交、不放进交付包、不命名为正式 Release。
