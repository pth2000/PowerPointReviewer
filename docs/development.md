# 代码结构与界面维护

当前工程使用 PySide6 和 Fluent Widgets。Designer 源文件负责稳定的页面布局，
动态卡片、事件处理和任务接线位于 `ui` 中。

| 位置 | 职责 |
| --- | --- |
| `app/project/model.py` | 当前讲稿、配音、PPT 关联与功能可用条件 |
| `app/project/history.py` | 历史记录解析、缓存校验与原子保存 |
| `app/project/history_catalog.py` | 历史列表摘要缓存，由后台任务分批读取 |
| `app/project/generation.py` | 更新讲稿时的分段、片段调整匹配与播放位置恢复 |
| `app/documents`、`app/audio`、`app/presentation`、`app/video` | 各领域的处理逻辑 |
| `tasks` | 后台任务与线程结果 |
| `ui/pages`、`ui/dialogs` | 显示、交互及页面内的任务协调 |
| `app/window.py` | 页面装配、导航与跨页面信号接线 |

## 工程状态

正式应用的 `AppContext.project` 持有唯一的 `ProjectSession`。首页、工具页和
审听窗口读取同一个对象。独立页面测试可以注入单独的工程对象。

- 工程数据放入 `ProjectSession`，不从其他页面的控件读取业务数据。
- 播放器、线程、定时器和弹窗等界面生命周期对象仍由页面负责。
- 工具按钮与执行入口使用 `ProjectAvailability` 的同一套条件。执行前重新检查，
  避免按钮显示后文件被移动、删除或其他任务开始运行。
- 历史记录先完整解析并检查音频，再将结果装载到现有工程对象。
  保留对象身份，避免其他页面仍引用旧工程。
- 持久化字段维持现有格式，内部字段调整不应直接改变历史记录或工程包格式。

## UI 源文件

修改 `.ui` 或 `resource.qrc` 后运行：

```powershell
python scripts/generate_ui.py
```

只检查生成文件是否同步：

```powershell
python scripts/generate_ui.py --check
```

`Ui_mainwindow.py`、`settingInterface.py`、`toolsInterface.py` 和 `resource_rc.py`
均为生成文件，修改应写在对应源文件或 `ui` 的运行时代码中。正常打包会先同步生成文件。

## 回归测试

```powershell
$env:PYTHONPATH = '.;tests'
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s tests
```

测试使用独立的临时工程、配置与媒体，不应读取或修改本机真实配置。
