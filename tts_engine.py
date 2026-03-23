import time
import traceback
import hashlib
import json
from typing import Optional

from engines import defs
from engines import pyttsx3 as pyttsx3_engine
from engines import edge_tts as edge_tts_engine
from engines import bailian


class TTSEngine:
    """可扩展 TTS 管理器：按引擎注册能力，并动态暴露可配置项"""

    # ──────────────────────────────────────────────────────────
    # §1  初始化
    # ──────────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._engine_defs = defs.ENGINE_DEFS
        self._local_voices, self._local_voice_names = pyttsx3_engine.init_voices()

        self._current_engine_index = 0
        # 为每个引擎维护独立配置，切换引擎后自动恢复其上次配置
        self._engine_settings: dict[str, dict] = {}
        self._voice_index_map: dict[str, int] = {}
        for engine_def in self._engine_defs:
            eid = engine_def['id']
            self._engine_settings[eid] = {
                item['key']: item['default'] for item in engine_def.get('options', [])
            }
            self._voice_index_map[eid] = 0

    # ──────────────────────────────────────────────────────────
    # §2  引擎模式管理
    # ──────────────────────────────────────────────────────────

    def get_engine_names(self) -> list[str]:
        """返回引擎名称列表，用于下拉框展示"""
        return [item['name'] for item in self._engine_defs]

    def get_mode(self):
        """获取当前引擎 ID"""
        return self._engine_defs[self._current_engine_index]['id']

    def get_mode_index(self):
        """获取当前引擎索引"""
        return self._current_engine_index

    def set_mode(self, mode):
        """设置当前引擎，支持索引(int)或引擎ID(str)"""
        if isinstance(mode, int):
            if 0 <= mode < len(self._engine_defs):
                self._current_engine_index = mode
            return

        if isinstance(mode, str):
            for i, item in enumerate(self._engine_defs):
                if item['id'] == mode:
                    self._current_engine_index = i
                    return

    def get_current_engine_definition(self):
        """返回当前引擎定义（含可配置项 schema）"""
        return self._engine_defs[self._current_engine_index]

    # ──────────────────────────────────────────────────────────
    # §3  并行 / 重试策略
    # ──────────────────────────────────────────────────────────

    def can_parallel_generate(self):
        """当前引擎是否建议启用并行生成"""
        return bool(self.get_current_engine_definition().get('parallel_enabled', False))

    def get_parallel_workers(self):
        """获取当前引擎建议并行线程数"""
        return int(self.get_current_engine_definition().get('parallel_workers', 1))

    def get_retry_policy(self):
        """获取当前引擎自动重试策略"""
        engine_def = self.get_current_engine_definition()
        return {
            'retry_times': int(engine_def.get('retry_times', 0)),
            'retry_delay': float(engine_def.get('retry_delay', 0.0)),
        }

    # ──────────────────────────────────────────────────────────
    # §4  选项配置管理
    # ──────────────────────────────────────────────────────────

    def get_current_options_schema(self):
        """返回当前引擎可调节项"""
        return self.get_current_engine_definition().get('options', [])

    def get_current_option_values(self):
        """返回当前引擎可调节项的当前值"""
        mode = self.get_mode()
        return self._engine_settings.get(mode, {}).copy()

    def set_current_option(self, key, value):
        """设置当前引擎某个配置项"""
        mode = self.get_mode()
        if mode not in self._engine_settings:
            return
        self._engine_settings[mode][key] = value

    def apply_current_options(self, option_values):
        """批量应用当前引擎配置"""
        for key, value in option_values.items():
            self.set_current_option(key, value)

    # ──────────────────────────────────────────────────────────
    # §5  持久化状态
    # ──────────────────────────────────────────────────────────

    def export_persistent_state(self):
        """导出可持久化状态（用于保存到本地配置文件）"""
        return {
            'engine_mode': self.get_mode(),
            'engine_settings': {k: dict(v) for k, v in self._engine_settings.items()},
            'voice_index_map': dict(self._voice_index_map)
        }

    def import_persistent_state(self, state):
        """加载持久化状态并进行字段级容错"""
        if not isinstance(state, dict):
            return False

        engine_settings = state.get('engine_settings', {})
        if isinstance(engine_settings, dict):
            engine_option_map = {
                item['id']: {opt.get('key') for opt in item.get('options', [])}
                for item in self._engine_defs
            }
            for engine_id, settings in engine_settings.items():
                if engine_id not in self._engine_settings or not isinstance(settings, dict):
                    continue
                valid_keys = engine_option_map.get(engine_id, set())
                for key, value in settings.items():
                    if key in valid_keys:
                        self._engine_settings[engine_id][key] = value

        voice_index_map = state.get('voice_index_map', {})
        if isinstance(voice_index_map, dict):
            for engine_id, index in voice_index_map.items():
                if engine_id in self._voice_index_map:
                    try:
                        self._voice_index_map[engine_id] = int(index)
                    except Exception:
                        pass

        engine_mode = state.get('engine_mode')
        if engine_mode is not None:
            self.set_mode(engine_mode)

        return True

    # ──────────────────────────────────────────────────────────
    # §6  发音人管理
    # ──────────────────────────────────────────────────────────

    def get_voices_list(self):
        """获取当前引擎可用发音人列表"""
        mode = self.get_mode()
        if mode == 'local':
            return self._local_voice_names
        if mode == 'edge':
            return edge_tts_engine.VOICES
        if mode == 'bailian':
            model = self._engine_settings['bailian'].get('model', 'cosyvoice-v3-flash')
            return bailian.MODEL_VOICES.get(model, bailian.VOICES)
        return []

    def set_voice(self, index):
        """设置当前引擎发音人索引"""
        mode = self.get_mode()
        self._voice_index_map[mode] = index

    def get_selected_voice_index(self):
        """获取当前引擎已选择发音人索引"""
        mode = self.get_mode()
        return self._voice_index_map.get(mode, 0)

    def get_generation_profile(self):
        """返回当前生成配置快照，用于缓存键计算"""
        mode = self.get_mode()
        return {
            'mode': mode,
            'options': self._engine_settings.get(mode, {}).copy(),
            'voice_index': int(self._voice_index_map.get(mode, 0)),
        }

    @staticmethod
    def normalize_text_for_cache(text: str) -> str:
        """最小化规整文本，降低空白差异导致的重复生成"""
        return ' '.join(text.strip().split())

    def build_audio_cache_key(self, text: str, generation_profile: Optional[dict] = None) -> str:
        """基于文本与生成配置计算稳定缓存键"""
        profile = generation_profile or self.get_generation_profile()
        payload = {
            'version': 1,
            'text': self.normalize_text_for_cache(text),
            'profile': profile,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    # ──────────────────────────────────────────────────────────
    # §7  公开保存接口
    # ──────────────────────────────────────────────────────────

    def save_file_by_mode(self, mode: str, text: str, path: str,
                          rate=None, volume=None, voice_index=None, **kwargs) -> None:
        """按指定引擎模式保存语音，是推荐的通用扩展入口。

        所有参数（rate/volume/voice_index 等）若为 None，则自动从引擎当前配置读取默认值。
        """
        if mode not in ('local', 'edge', 'bailian'):
            raise RuntimeError(f'不支持的引擎模式：{mode}')

        # 查找该引擎的默认重试策略
        engine_def = next((e for e in self._engine_defs if e['id'] == mode), {})
        retry_times = int(kwargs.get('retry_times', engine_def.get('retry_times', 0)))
        retry_delay = float(kwargs.get('retry_delay', engine_def.get('retry_delay', 0.0)))

        # 从当前引擎配置中解析所有需要的参数（优先使用调用方传入值）
        settings = self._engine_settings.get(mode, {})
        r_rate = rate if rate is not None else settings.get('rate')
        r_volume = volume if volume is not None else settings.get('volume')
        r_voice = voice_index if voice_index is not None else self._voice_index_map.get(mode, 0)

        last_error: Optional[Exception] = None
        for attempt in range(retry_times + 1):
            try:
                if mode == 'local':
                    pyttsx3_engine.save(
                        text, path,
                        voices=self._local_voices,
                        rate=r_rate, volume=r_volume, voice_index=r_voice,
                    )
                elif mode == 'edge':
                    edge_tts_engine.save(
                        text, path,
                        rate=r_rate, volume=r_volume,
                        pitch=kwargs.get('pitch', settings.get('pitch', 0)),
                        voice_index=r_voice,
                    )
                elif mode == 'bailian':
                    bailian.save(
                        text, path,
                        voice_index=r_voice,
                        rate=r_rate, volume=r_volume,
                        pitch=kwargs.get('pitch', settings.get('pitch', 1.0)),
                        model=kwargs.get('model', settings.get('model', 'cosyvoice-v3-flash')),
                        api_key=kwargs.get('api_key', settings.get('api_key', '')),
                        ws_url=kwargs.get('ws_url', settings.get(
                            'ws_url', 'wss://dashscope.aliyuncs.com/api-ws/v1/inference')),
                    )
                return
            except Exception as e:
                last_error = e
                print(f'[TTS][{mode}] 第 {attempt + 1}/{retry_times + 1} 次保存失败：{e}')
                traceback.print_exc()
                if attempt < retry_times and retry_delay > 0:
                    time.sleep(retry_delay * (attempt + 1))

        raise RuntimeError(f'语音保存失败，已重试 {retry_times} 次：{last_error}') from last_error

    def save_file(self, text: str, path: str,
                  rate=None, volume=None, voice_index=None, **kwargs) -> None:
        """按当前引擎保存语音，支持通过参数临时覆盖当前配置"""
        self.save_file_by_mode(
            self.get_mode(), text, path,
            rate=rate, volume=volume, voice_index=voice_index, **kwargs,
        )

    def save_file_for_stable_local(self, text: str, path: str,
                                   rate=None, volume=None, voice_index=None) -> None:
        """强制本地引擎保存（适合倒计时等需稳定离线场景）"""
        self.save_file_by_mode('local', text, path, rate=rate, volume=volume, voice_index=voice_index)


if __name__ == '__main__':
    tts = TTSEngine()
