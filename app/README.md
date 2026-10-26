# 模块组织

`app` 保存应用服务与启动装配，界面组件位于 `ui`，语音服务实现位于 `engines`，后台任务位于 `tasks`。

| 位置 | 职责 |
| --- | --- |
| `project/` | 当前工程状态、历史记录、配音更新流程与历史列表摘要 |
| `documents/` | 讲稿读写、正文处理、统计、文档导出与工程包 |
| `audio/` | 音频缓存、解码、效果处理、片段调整与播放协调 |
| `presentation/` | PPT 关联、备注监听、动画校验、放映控制和静态页面导出 |
| `video/` | PowerPoint 视频导出、OBS 录制、配音时间轴与字幕封装 |
| `ai/` | AI 服务请求与讲稿改写 |
| `updates/` | 更新检查、镜像下载和更新安装 |

根目录中的 `app_context.py`、`window.py` 负责应用装配；`paths.py`、`settings_store.py`、`logging_setup.py` 和 `hotkeys.py` 提供应用级基础服务。

## 放映公共能力

- `presentation/com.py` 集中管理 COM 初始化、运行实例枚举和进程号查询。
- `presentation/control.py` 的 `PresentationControl` 供普通播放和录制共用。
- `presentation/session.py` 管理放映会话及本次打开的临时文稿。
- `presentation/playback.py` 协调普通朗读与放映之间的联动。

## 资源与界面

原生库通过 `app.paths.resource_root()` 定位，兼容源码运行和 PyInstaller 的内置资源目录；用户配置、历史记录与缓存仍使用 `app.paths` 中的数据目录定义。

主题、图标、文件拖入和控件状态辅助模块位于 `ui/`。新增代码按职责直接导入对应模块。
