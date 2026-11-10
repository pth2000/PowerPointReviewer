# 发布指南

以下命令在仓库根目录的 PowerShell 中执行。开发环境和测试命令见 [开发指南](development.md)。

## 首次配置

安装 .NET 10 SDK 和 Velopack CLI：

```powershell
dotnet tool install -g vpk
```

发布前，在当前终端设置 `GITEE_TOKEN` 和 `GITHUB_TOKEN`。
两个令牌均需具备发行版及附件的写入权限。

Git 推送地址需覆盖 Gitee 和 GitHub。可用 `git remote -v` 检查配置，
两个平台的发布标签应指向同一提交。

## 发布新版本

1. 修改 `main.py` 的 `VERSION` 和 `pyproject.toml` 的 `project.version`，执行 `uv lock`。
2. 更新 `CHANGELOG.md`，只保留本次版本日志。
3. 完成测试，提交修改，创建并推送对应的 `v<版本号>` 标签。
4. 执行发布：

```powershell
uv run --group build release.py --notes CHANGELOG.md
```

脚本会检查工作区、版本号和远程标签，生成界面代码、构建程序，再上传两个平台的发行版及附件。
不需要先运行 `build.py`、PyInstaller 或 UI 生成命令。

如需提前检查发布条件，可在命令末尾加 `--dry-run`。预演访问远程仓库，但不构建或上传。
依赖、原生组件或打包规则有变化时，还应完成 [发布包检查](development.md#更新依赖)。

## 仅构建

```powershell
uv run --group build build.py --notes CHANGELOG.md
```

产物保存在 `releases`：

| 文件 | 用途 |
| --- | --- |
| `PowerPointReviewer-win-Setup.exe` | 安装版 |
| `PowerPointReviewer-win-Portable.zip` | 便携版 |
| `PowerPointReviewer-<版本>-full.nupkg` | 完整更新包 |
| `PowerPointReviewer-<版本>-delta.nupkg` | 增量更新包 |
| `releases.win.json` | 更新索引 |

生成增量包需要上一版完整包。保留本地 `releases` 目录中的历史包和索引。

若本次已构建且代码、版本号、更新日志均未变化，可在发布命令末尾加 `--skip-build` 复用产物。

## 清理 Gitee 附件

按需执行，不属于每次发布的固定步骤：

```powershell
uv run --group build release.py --prune --dry-run
uv run --group build release.py --prune
```

第一条显示待删除附件，第二条执行清理。保留规则如下：

| 版本 | 保留内容 |
| --- | --- |
| 最新版本 | 全部附件 |
| v1.6.0 | 安装版与便携版 |
| 中间自动更新版本 | 该版本的增量包 |
| v1.5.1 及更早版本 | 原有下载附件 |

## 上传失败

先检查发行版的附件列表。当前脚本不支持自动续传，直接重跑可能遇到同名发行版已存在的提示。
若使用原产物重试，添加 `--skip-build`，并通过 `--skip-gitee` 或 `--skip-github` 跳过已完成的平台。
若删除该次不完整发行版后重试，应只处理上传失败的平台，保留已发布成功的版本。

已完整发布的版本不再替换附件，后续修改使用新版本号。
其他维护参数可通过 `uv run --group build release.py --help` 查看。
