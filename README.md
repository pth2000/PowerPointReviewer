<div align="center">
  <img src="image/readme-banner.png" alt="演示文稿、播放按钮与语音波形组成的项目封面" width="100%">
  <h1>PowerPointReviewer</h1>
  <p>PowerPoint 讲稿朗读与审阅工具</p>

  <p>
    <a href="https://gitee.com/pth2000/PowerPointReviewer/releases">下载（Gitee）</a>
    &nbsp; · &nbsp;
    <a href="https://github.com/pth2000/PowerPointReviewer/releases/latest">下载（GitHub）</a>
    &nbsp; · &nbsp;
    <a href="#使用方法">使用方法</a>
    &nbsp; · &nbsp;
    <a href="CHANGELOG.md">更新日志</a>
    &nbsp; · &nbsp;
    <a href="https://github.com/pth2000/PowerPointReviewer/issues">反馈问题</a>
  </p>
</div>

![截图](./image/screenshots.png)

## 项目简介

一个基于PySide6实现的演讲稿朗读审阅工具，使用TTS引擎朗读PPT中的备注部分，从而辅助您进一步完善演讲的内容与措辞，助您顺利完成精彩的PPT演讲与展示。

## 基本特性

- 基于Python、PySide6、PySide6-Fluent-Widgets实现
- 支持从 PPT 备注导入演讲稿，也可从 Word / JSON 讲稿文件导入
- 支持编辑页内分隔符，用于在一页PPT中执行点击效果
- 支持朗读前插入并设置倒计时
- 支持PPT同步翻页功能
- 支持停止朗读后重新从当前语句开始朗读
- 支持页码跳转
- 支持统计演讲稿信息
- 支持文件拖入导入
- 支持讲稿往返编辑：导出为 Word 表格或 JSON，修改后可直接导回
- 支持导出 PPT备注、Markdown、SRT字幕、音频文件（逐条或合并）及工程包格式
- 支持导出PPT放映视频，包括放映录制和静态合成两种方式
- 支持多TTS引擎切换与参数独立保存
- 支持在线TTS引擎（Edge / 阿里百炼 / 千问复刻）
- 支持连接本机或局域网的 qwentts.cpp 服务，使用本地模型合成语音
- 支持本地 qwentts.cpp 语速调节，保持音高
- 支持所有TTS引擎设置音频头尾最低留白，改善短句连播衔接
- 支持AI改写讲稿，兼容 OpenAI 格式
- 支持历史记录列表与会话恢复，可重新关联移动或改名后的 PPT，保留已有讲稿和配音
- PPT 备注更新后可查看变化并更新讲稿；再次启动时可恢复上次讲稿及播放段落
- 支持音频缓存复用
- 支持切换界面字体
- 支持检查更新

## TTS引擎说明

当前内置5种引擎：

- 本地 TTSx3：离线可用，稳定，适合无网场景
- 在线 Edge-TTS：配置简单，音色自然，支持切换语言地区
- 在线 阿里百炼 CosyVoice：可配置模型/语速/音量/音调
- 在线 千问音色复刻：支持云端复刻音色，支持 Qwen-Audio-TTS 3.1 Flash、3.0 系列模型
- 本地 qwentts.cpp：连接已启动的本地服务，支持模型与音色读取、试听和批量合成

## 使用方法

1. 启动软件，根据您的讲稿文本，编辑分隔符。
2. 在设置页选择TTS引擎并调整参数，可随时试听。点击“保存设置”生效。
3. 在主页点击导入按钮，选择您的文件路径，也可将 PPTX、Word、JSON 讲稿或工程包文件直接拖入软件窗口。之后，软件会将讲稿文本导入，并转换为语音文件，这可能需要一点时间。
4. 软件导入完毕后，即可使用播放控制功能。您可以选择播放、停止、重置音频，跳转播放页码，查看统计信息。
5. 如果启用倒计时播放功能，点击播放后，软件将先播放倒计时，再播放正文讲稿。
6. 可在主页或设置页的“放映控制”中启用同步翻页，两处开关保持同步。默认使用“演示软件接口（COM）”，点击播放会自动准备对应 PPT 放映，再开始倒计时和配音；已有对应放映时直接连接，文稿已打开时从该文稿开始放映。在“放映联动”中可分别设置自动开始放映、朗读跟随 PPT 翻页、讲稿定位时同步放映，以及退出放映时停止朗读，默认均开启。关闭自动开始放映后，需先手动开始对应 PPT 的放映；关闭退出时停止朗读后，退出放映会继续播放剩余配音。其他翻页方式需先手动开始放映。
7. 可在“历史记录列表”中快速加载过往会话。支持 Ctrl / Shift 多选与全选，可统一管理记录。
8. 可在实用工具页对已导入讲稿进行进一步处理。

Word / JSON 讲稿可在导出编辑后再次导入。当前已关联 PPT 时，按页码更新对应讲稿，保留其他页、PPT 关联和语音设置；未关联 PPT 时，作为独立讲稿导入并进入“受限模式”。已有独立讲稿时再导入 PPT，可选择“关联 PPT，保留当前讲稿”或“读取 PPT 备注作为新讲稿”。

同步播放时，停止后保留放映画面；退出放映时是否停止配音，可在“放映联动”中设置。放映准备失败时，软件会显示提示，可手动开始对应 PPT 放映后再次点击播放。页内动画位置无法对齐时，可从本页第一段开始播放。

PPT 移动、改名，或在其他设备导入工程包后，可在“历史记录列表”中选中记录，点击“关联 PPT”选择对应文稿。未关联的记录也可加载并播放配音，此时进入“受限模式”，同步翻页、视频导出和写回 PPT 备注暂不可用；关联后恢复这些功能，并保留已有讲稿和配音。分享工程包时，可将对应 PPT 一同发送给接收方，导入后再关联文稿。

编辑并保存 PPTX 备注后，主页会提示讲稿有更新。点击“查看更新”可查看变化页码和新增、修改、删除的情况，确认后更新讲稿并生成配音；也可在导入按钮的菜单中选择“更新 PPT 讲稿”。若当前讲稿经过编辑或 AI 改写，更新会替换这些内容。再次启动软件时，可以选择恢复上次讲稿，回到上次所在的段落后自行开始播放。“继续上次讲稿”默认开启，可在设置的“常规”中关闭启动提示。

“信息统计”可查看讲稿总字数、段数、配音总时长和逐页统计，以及最长、最短配音所在的页码和段落。配音生成前，也可先查看字数与段数。

## AI 优化

在实用工具页可进入AI 优化窗口，可点击窗口中的“接口配置”填写连接参数，目前支持 OpenAI 格式。填写后点击“测试连接”确认可用，即可开始改写。

## 格式规范

为了便于快速上手，本项目提供PPT模板和Word模板，在本项目的`example`目录下。

您可以使用本软件导入，快速预览效果，了解其实现方式。

## 进阶使用

### 本地 NaturalVoiceSAPIAdapter 接入

在Windows系统中，「本地 · TTSx3」通过调用 SAPI 5 text-to-speech (TTS) engine 实现，默认引擎均为微软的普通语音包，发音较为生硬。

推荐本地部署[NaturalVoiceSAPIAdapter](https://github.com/gexgd0419/NaturalVoiceSAPIAdapter)，能够为本软件提供微软自然语音的SAPI 5 TTS引擎。下载本地自然语音包后，启动相关功能即可，具体部署方法请参见该项目文档。

### 本地 qwentts.cpp 接入

兼容 [ServeurpersoCom 原始项目](https://github.com/ServeurpersoCom/qwentts.cpp) 与 [Panda-Panta Windows 便携版](https://github.com/Panda-Panta/qwentts.cpp/releases)。模型下载和服务启动在 qwentts.cpp 中完成，本软件负责连接服务并生成讲稿音频。

1. 按对应项目说明准备 GGUF 模型并启动服务。Windows 便携版可解压后运行 `Qwen3-TTS-Launcher.exe`，选择模型、语言并启动；原始项目使用 `tts-server` 启动 HTTP 服务。
2. 在本软件设置页选择「本地 · qwentts.cpp」，填写后端服务地址，默认 `http://127.0.0.1:8080`。
3. 点击「测试连接」，读取当前模型与音色，再选择音色。
4. 按需调整设置，点击试听确认效果，再保存设置。
5. 导入讲稿或重新生成音频。

使用 Windows 便携版的 Base 克隆模型时，可先在服务网页中创建复刻音色，再回本软件刷新音色列表。

## 如何打包

本项目提供 Windows 64 位可执行文件。如果您想从源码运行或打包，可在 Windows 64 位 Python 环境中安装依赖：

```shell
python -m pip install -r requirements.txt
python main.py
```

使用pyinstaller：

```shell
python -m pip install pyinstaller
python -m PyInstaller PowerPointReviewer.spec
```

如需生成安装器、便携包和增量更新包，先安装 .NET SDK 与 Velopack CLI（`vpk`），再执行：

```shell
python build.py --notes CHANGELOG.md
```

最新版本的更新内容见 [CHANGELOG.md](CHANGELOG.md)。

如果遇到任何bug，或者有任何建议，欢迎提交issue，谢谢。
