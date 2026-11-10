# 开发指南

## 开始开发

项目使用 Windows x64、Python 3.14 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录执行：

```powershell
uv sync
uv run main.py
```

Python 版本由 `.python-version` 指定，依赖声明和锁定版本分别见 `pyproject.toml`、`uv.lock`。

## 代码结构

| 目录或文件 | 内容 |
| --- | --- |
| `main.py`、`app/window.py` | 应用启动、页面装配与信号连接 |
| `app/project` | 讲稿、配音、PPT 关联及历史记录 |
| `app/documents` | 文稿解析、工程包与文档导出 |
| `app/audio`、`app/video` | 音频处理与视频导出 |
| `app/presentation` | PowerPoint / WPS 连接与放映控制 |
| `engines` | 语音服务适配器 |
| `tasks` | 后台任务 |
| `ui` | 页面、弹窗与控件 |
| `native`、`third_party` | 原生组件、第三方源码与许可证 |
| `tests` | 自动化测试 |

`AppContext.project` 保存当前 `ProjectSession`，各页面共享同一份工程状态。
修改工程逻辑时，注意以下约定：

- 业务数据保存在工程对象中，不从其他页面的控件读取。
- 功能可用条件集中在 `ProjectAvailability`，按钮状态与执行入口使用相同条件。
- 加载历史记录时，先解析和校验，再更新现有工程对象。
- 修改持久化字段时，保留对已有历史记录和工程包的兼容。

## 修改界面

Designer 文件和 `resource.qrc` 是生成代码的来源。修改后执行：

```powershell
uv run scripts/generate_ui.py
```

不要直接编辑 `Ui_mainwindow.py`、`settingInterface.py`、`toolsInterface.py` 或
`resource_rc.py`。动态布局和交互代码位于 `ui`。打包时会自动生成上述文件。

## 代码检查与测试

```powershell
uv run ruff check .
$env:PYTHONPATH = '.;tests'
uv run python -m unittest discover -s tests
```

涉及音频播放或系统语音的测试需要可用的 Windows 桌面和音频设备。
测试应使用临时配置与数据，不读写本机的真实工程。

代码检查配置见 `pyproject.toml`。格式化时只处理本次修改的文件：

```powershell
uv run ruff format path/to/file.py
```

普通导入放在文件顶部。语音 SDK、文档解析和视频窗口按需加载，COM 模块的导入还需
考虑线程初始化顺序。异常应在任务或界面边界记录，重新抛出时保留原因。
注释用于解释约束与设计取舍，不记录调试过程。

## 配音生成

批量生成使用引擎配置快照，按引擎能力限制在途请求数。短句配音共用同一工作队列，
完成后按讲稿顺序组装，并保留实际音频边界。缓存锁覆盖生成、读取和复制，避免相同
讲稿重复请求或同时替换文件。失败或取消后停止派发，已完成的缓存可供下次重试复用。

云端请求分别控制并发数与请求间隔。HTTP 会话由批次持有，每个线程独立复用，任务
结束后关闭。调整合成策略时应同时检查取消、限流重试、重复文本和成品顺序。

倒计时使用 Qt 计时器与随程序分发的短提示音，不调用语音合成服务。

## 临时文件

应用临时目录使用 `app.workspaces.TemporaryWorkspace`，退出工作区时清理，异常退出的
残留由下次启动时的后台任务处理。目录带有归属标记和进程锁，其他实例仍在使用时会跳过。
历史音频、工程文件和用于恢复的视频不放入这类目录。

新增临时目录类型时，在 `_PREFIXES` 中登记用途，并放在应用临时区或音频缓存区。
清理范围不扩展到用户导出目录；清理失败应保留归属标记，供后续重试。

## 更新依赖

修改 `pyproject.toml` 后执行 `uv lock`、`uv sync`，一并提交声明和锁文件。
版本限制的原因见依赖声明旁的注释。

升级 PySide6 时，同时检查 Fluent Widgets 兼容性和原生媒体组件使用的 FFmpeg ABI。
调整依赖、资源或打包规则后，执行发布包检查：

```powershell
uv run --group build scripts/check_bundle.py
```

此检查会临时构建程序并测试资源、语音接口、媒体处理和界面。云端请求使用本地测试服务，
实际账号、PowerPoint / WPS 和 OBS 仍需按改动范围手动验证。

构建和发布步骤见 [发布指南](releasing.md)。
