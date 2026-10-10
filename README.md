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
- 支持讲稿往返编辑：导出为 Word 表格或 JSON，修改后可直接导回
- 支持导出 PPT备注、Markdown、SRT字幕、音频文件（逐条或合并）及工程包格式
- 支持导出PPT放映视频，包括放映录制和静态合成两种方式
- 支持多TTS引擎切换与参数独立保存
- 支持在线TTS引擎（Edge / 阿里百炼 / 千问复刻）
- 支持连接本机或局域网的 qwentts.cpp 服务，使用本地模型合成语音
- 支持本地 qwentts.cpp 语速调节，保持音高
- 支持所有TTS引擎设置音频头尾最低留白，改善短句连播衔接
- 支持AI改写讲稿，兼容 OpenAI 格式
- 支持历史记录列表，支持会话恢复
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
3. 在主页点击导入按钮，选择您的文件路径。之后，软件会将讲稿文本导入，并转换为语音文件，这可能需要一点时间。
4. 软件导入完毕后，即可使用播放控制功能。您可以选择播放、停止、重置音频，跳转播放页码，查看统计信息。
5. 如果启用倒计时播放功能，点击播放后，软件将先播放倒计时，再播放正文讲稿。
6. 如果启用PPT同步翻页功能，请先打开对应文稿并开始放映，软件会随配音自动翻页。可在设置页选择翻页方式。
7. 可在“历史记录列表”中快速加载过往会话。支持 Ctrl / Shift 多选与全选，可统一管理记录。
8. 可在实用工具页对已导入讲稿进行进一步处理。

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
