# dist

可直接被平台/脚本抓取的插件包（构建产物，勿手改）：

| 文件 | 说明 |
|---|---|
| `qwenpaw-dsh-1.0.0-plugin.zip` | v1.0.0 插件包（`plugin.json` 在根；与 GitHub Release 资产同源） |

```bash
# 重新生成（在仓库根）
(cd qwenpaw-dsh && zip -qr ../dist/qwenpaw-dsh-1.0.0-plugin.zip    plugin.json plugin.py qwenpaw_dsh README.md -x '*__pycache__*' '*.pyc')
```
