"""在实际冻结进程中按功能加载依赖，不使用源码环境的 site-packages。"""

import json
import sys
from pathlib import Path


def check_documents(root):
    from docx import Document
    from PIL import Image
    from pptx import Presentation

    from app.documents.export import write_docx
    from app.documents.io import parse_docx, parse_pptx

    ppt = Presentation()
    slide = ppt.slides.add_slide(ppt.slide_layouts[6])
    slide.notes_slide.notes_text_frame.text = '发布包测试讲稿'
    source = root / '测试文稿.pptx'
    ppt.save(source)
    assert parse_pptx(source).notes[1].strip() == '发布包测试讲稿'
    target = root / '讲稿.docx'
    write_docx(target, [{'page': 1, 'text': '发布包测试讲稿'}], [0.5])
    assert parse_docx(target).notes[1].strip() == '发布包测试讲稿'
    assert Document(target).tables
    for kind in ('PNG', 'JPEG'):
        image = root / ('image.' + kind.lower())
        Image.new('RGB', (20, 20), 'white').save(image, format=kind)
        with Image.open(image) as loaded:
            loaded.load()
            assert loaded.size == (20, 20)


def check_speech(root, base_url):
    import requests

    from app.paths import resource_root
    from engines import bailian, edge_tts, qwen_clone

    catalog = edge_tts._load_catalog_file(resource_root() / 'engines' / 'edge_voices.json')
    assert catalog and catalog['zh-CN'], '内置 Edge 音色资源不可用'
    source = root / '参考音频.wav'
    source.write_bytes(requests.get(base_url + '/audio.wav', timeout=5).content)
    url = qwen_clone._upload_reference_audio(
        str(source), model='voice-enrollment', api_key='test-key', base_url=base_url + '/api/v1'
    )
    assert url == 'oss://test/参考音频.wav', url
    # 只替换端点选择，保留 SDK 的请求转换、HTTP 传输、响应解析和下载实现。
    qwen_clone._service_base = lambda *_args: base_url + '/api/v1'
    target = root / '合成结果.wav'
    qwen_clone.save(
        '合成测试',
        str(target),
        model=qwen_clone.VC_MODELS[0],
        voice='test-voice',
        api_key='test-key',
    )
    assert target.read_bytes() == source.read_bytes()
    qwen_clone._maas_base = lambda *_args: base_url + '/api/v1'
    qwen_clone.save(
        '风格语音测试',
        str(target),
        model=qwen_clone.AUDIO_TTS_MODELS[0],
        voice='test-voice',
        api_key='test-key',
        instructions='平稳朗读',
        rate=1.1,
        volume=65,
        pitch=1.0,
    )
    assert target.read_bytes() == source.read_bytes()
    for key, expected in (('rejected-key', 'API Key'), ('unknown-error-key', 'test-service-error')):
        try:
            qwen_clone.save(
                '错误测试',
                str(root / '错误.wav'),
                model=qwen_clone.VC_MODELS[0],
                voice='test-voice',
                api_key=key,
            )
        except RuntimeError as error:
            assert expected in str(error), str(error)
        else:
            raise AssertionError('SDK 未传递服务端错误')

    from dashscope.audio.tts_v2 import SpeechSynthesizer

    call = SpeechSynthesizer.call

    def synthesize(synthesizer, _text):
        synthesizer.callback.on_open()
        synthesizer.callback.on_data(source.read_bytes())
        synthesizer.callback.on_complete()
        synthesizer.callback.on_close()

    try:
        SpeechSynthesizer.call = synthesize
        bailian.save('回调测试', str(target), api_key='test-key')
        assert target.read_bytes() == source.read_bytes()
    finally:
        SpeechSynthesizer.call = call


def check_media(root):
    from app.audio.signalsmith import change_pcm16
    from app.video.mux import VideoCaption, mux_video
    from app.video.native_media import ensure_native_media_available, probe_media
    from app.video.timeline import VideoTimeline

    assert len(change_pcm16(b'\0\0' * 24000, 24000, 1, 1.25)) == 19200 * 2
    ensure_native_media_available()

    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder

    from app.video.audio import pcm_format

    decoder, loop = QAudioDecoder(), QEventLoop()
    done, errors, buffers = [], [], []
    decoder.setAudioFormat(pcm_format())
    decoder.bufferReady.connect(lambda: buffers.append(bytes(decoder.read().constData())))
    decoder.finished.connect(lambda: (done.append(True), loop.quit()))
    decoder.error.connect(lambda *_args: (errors.append(decoder.errorString()), loop.quit()))
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    decoder.setSource(QUrl.fromLocalFile(str(root / '参考音频.wav')))
    timer.start(10000)
    decoder.start()
    loop.exec()
    decoder.stop()
    timer.stop()
    assert done and buffers and not errors, errors

    from PySide6.QtGui import QImage

    fixture = Path(__file__).with_name('video_fixture.py')
    namespace = {'__name__': 'bundle_video_fixture', '__file__': str(fixture)}
    exec(compile(fixture.read_text(encoding='utf-8'), str(fixture), 'exec'), namespace)
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(0xFF334455)
    source = root / '测试画面.mp4'
    import wave

    narration = root / '视频配音.wav'
    with wave.open(str(narration), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(48000)
        writer.writeframes(b'\0\0' * 24000)
    timeline = VideoTimeline([1], [narration])
    encoder = namespace['VideoFixtureEncoder'](timeline, source, image)
    done.clear()
    errors.clear()
    encoder.finished.connect(lambda *_args: (done.append(True), loop.quit()))
    encoder.failed.connect(lambda error: (errors.append(error), loop.quit()))
    timer.start(15000)
    encoder.start()
    loop.exec()
    timer.stop()
    if encoder.state != 'done':
        encoder.cancel()
    assert done and not errors, errors
    original = probe_media(source)
    assert original.width == 320 and original.video_packets > 0, original
    for container in ('mp4', 'mkv'):
        target = root / ('字幕视频.' + container)
        mux_video(source, target, container, [VideoCaption(0, 250, '字幕测试')])
        result = probe_media(target)
        assert result.video_packets == original.video_packets, result
        assert result.audio_streams == 1, result


def check_local_speech(root):
    import wave

    from engines import pyttsx3

    voices, names = pyttsx3.init_voices()
    assert voices and names, '本机 SAPI 音色枚举失败'
    for index in range(2):
        target = root / f'系统语音{index}.wav'
        pyttsx3.save('Speech synthesis test.', str(target), voices=voices)
        with wave.open(str(target), 'rb') as reader:
            assert reader.getnframes() > 0, target

    from app.audio.captions import SENTENCE_MODE, read_captions
    from tts_engine import TTSEngine

    engine = TTSEngine()
    engine.set_mode('local')
    engine.set_current_option('sentence_mode', SENTENCE_MODE)
    target = root / '短句配音.wav'
    cache = root / 'sentence-cache'
    engine.save_file('First sentence! Second sentence!', str(target), sentence_cache_dir=cache)
    timing = read_captions(target)
    assert timing and len(timing['cues']) == 2, timing
    from app.audio.sentences import load_sentences

    timeline = load_sentences(target, 'First sentence! Second sentence!')
    assert len(timeline) == 2 and timeline.sentence(2).text == 'Second sentence!'
    assert all((cache / (cue['source_key'] + '.wav')).is_file() for cue in timing['cues'])

    def unexpected_generation(*_args, **_kwargs):
        raise AssertionError('已缓存的句子不应再次合成')

    engine._synthesize_file_by_mode = unexpected_generation
    repeated = root / '重复生成.wav'
    engine.save_file('First sentence! Second sentence!', str(repeated), sentence_cache_dir=cache)
    assert repeated.read_bytes() == target.read_bytes()


def check_ui(application):
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtWidgets import QAbstractSpinBox

    from app.app_context import AppContext
    from app.window import Window
    from tts_engine import TTSEngine

    context = AppContext(version='bundle-check', tts_engine=TTSEngine())
    context.app_settings.set('auto_check_updates', False)
    context.app_settings.set('resume_last_session', False)
    context.app_settings.set('onboarding_seen', True)
    context.app_settings.set('onboarding_tools_seen', True)
    window = Window(context)
    window.show()
    application.processEvents()
    fields = window.findChildren(QAbstractSpinBox)
    assert fields, '未创建设置页数值控件'
    for field in fields:
        field.show()
        application.processEvents()
        editor = field.lineEdit()
        assert editor.width() > 20, (field.objectName(), editor.geometry())
        image = editor.grab().toImage()
        painted = bytes(image.constBits())
        text = editor.text()
        with QSignalBlocker(editor):
            editor.setText('')
            blank = editor.grab().toImage()
            assert painted != bytes(blank.constBits()), (field.objectName(), text)
            editor.setText(text)
    window.close()
    application.processEvents()


def main():
    assert getattr(sys, 'frozen', False), '此检查必须由冻结后的检查入口启动'
    root, base_url = Path(sys.argv[2]), sys.argv[3]
    from PySide6.QtWidgets import QApplication

    from app import paths

    assert Path(paths.__file__).is_relative_to(Path(sys._MEIPASS))
    assert not any('site-packages' in item for item in sys.path), sys.path
    for resource in (
        'audio/countdown.wav',
        'audio/countdown-final.wav',
        'engines/edge_voices.json',
        'third_party/signalsmith/LICENSE-stretch.txt',
        'third_party/signalsmith/LICENSE-linear.txt',
        'third_party/lucide/LICENSE',
        'third_party/fluent_system_icons/LICENSE',
        'third_party/ffmpeg/COPYING.LGPLv2.1',
        'third_party/ffmpeg/NOTICE.md',
    ):
        assert (Path(sys._MEIPASS) / resource).is_file(), resource
    application = QApplication([])
    for label, check in (
        ('文稿与图片资源', lambda: check_documents(root)),
        ('语音 SDK、HTTP 上传与错误处理', lambda: check_speech(root, base_url)),
        ('原生音频组件与 Qt 解码', lambda: check_media(root)),
        ('系统语音连续合成', lambda: check_local_speech(root)),
        ('主窗口、设置页与数值绘制', lambda: check_ui(application)),
    ):
        check()
        print(json.dumps({'check': label, 'result': 'passed'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
