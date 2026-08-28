"""管理应用偏好，并作为 settings.json 的唯一读写入口。

``TTSEngine`` 管理引擎参数，``AppSettings`` 管理其余偏好；``ConfigStore`` 将二者合并
到同一文件，避免多个写入方相互覆盖。
"""

import copy
import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from app import rewrite, theme


class AppSettings:
    """保存引擎无关的应用偏好，并对持久化数据逐字段容错。"""

    DEFAULTS = {
        # 外观
        'theme_mode': 'auto',
        'theme_color': theme.DEFAULT_COLOR,
        # 讲稿与播放
        'mark': '●',
        'countdown_enabled': True,
        'countdown_seconds': 5,
        'scroll_enabled': False,
        # 试听
        'preview_text': '这是一个试听音频，用于测试当前的语音设置',
        # 导出
        'export_dir': '',
        # AI 改写（OpenAI 兼容接口）
        'llm_base_url': 'https://api.openai.com/v1',
        'llm_api_key': '',
        'llm_model': 'gpt-4o-mini',
        'llm_timeout': 120,
        'llm_style': rewrite.DEFAULT_STYLE_NAME,
        'llm_styles': rewrite.default_styles(),
        'llm_templates': rewrite.default_templates(),
    }

    def __init__(self):
        self._values = dict(self.DEFAULTS)

    def get(self, key, default=None):
        """读取偏好；未知键回退到字段默认值或调用方默认值。"""
        return self._values.get(key, self.DEFAULTS.get(key, default))

    def set(self, key, value):
        """写入已声明的偏好，忽略未知键。"""
        if key in self.DEFAULTS:
            self._values[key] = value

    def update(self, mapping):
        """批量写入已声明的偏好。"""
        for key, value in dict(mapping).items():
            self.set(key, value)

    def to_dict(self) -> dict:
        """导出可序列化副本，防止调用方通过容器引用修改内部状态。"""
        return {k: (copy.deepcopy(v) if isinstance(v, (list, dict)) else v)
                for k, v in self._values.items()}

    def load_from(self, data):
        """从配置片段恢复已知字段，无法转换的值保留当前默认。"""
        if not isinstance(data, dict):
            return

        for key, default in self.DEFAULTS.items():
            if key not in data:
                continue
            value = data[key]
            try:
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, str):
                    value = str(value)
                elif isinstance(default, (list, dict)):
                    if not isinstance(value, type(default)):
                        raise TypeError(f'期望 {type(default).__name__}')
                    value = copy.deepcopy(value)
            except (TypeError, ValueError):
                print(f'[配置] 忽略无法解析的字段 {key}={value!r}')
                continue
            self._values[key] = value


class ConfigStore(QObject):
    """合并并持久化引擎状态与应用偏好。

    界面编辑可能密集触发保存请求，例如逐字输入密钥；单次定时器会将连续请求合并，
    减少重复写盘。
    """

    SAVE_DELAY_MS = 400

    def __init__(self, path: Path, tts_engine, app_settings: AppSettings):
        super().__init__()
        self.path = Path(path)
        self.tts_engine = tts_engine
        self.app_settings = app_settings

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save)

    def save_later(self):
        """安排一次延迟保存，并合并等待期间的后续请求。"""
        self._save_timer.start()

    def flush(self):
        """立即提交仍在等待的保存请求。"""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.save()

    def load(self) -> bool:
        """读取配置并分发状态；文件不存在或解析失败时返回 ``False``。"""
        if not self.path.exists():
            return False

        try:
            with self.path.open('r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f'[配置] 读取失败，将使用默认配置：{e}')
            return False

        self.tts_engine.import_persistent_state(data)
        self.app_settings.load_from(data.get('app', {}))
        print(f'[配置] 已加载：{self.path}')
        return True

    def save(self) -> bool:
        """原子写入合并后的配置，成功时返回 ``True``。"""
        self._save_timer.stop()
        data = self.tts_engine.export_persistent_state()
        data['app'] = self.app_settings.to_dict()

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 旁路写入后再替换，避免进程中断留下无法解析的半截 JSON。
            temp_path = self.path.with_suffix('.json.tmp')
            with temp_path.open('w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_path.replace(self.path)
        except Exception as e:
            print(f'[配置] 保存失败：{e}')
            return False

        return True
