"""统一调度 TTS 引擎，并维护按引擎隔离的配置、音色选择和缓存身份。"""

import time
import traceback
import hashlib
import json
from typing import Optional

from engines import defs
from engines import pyttsx3 as pyttsx3_engine
from engines import edge_tts as edge_tts_engine
from engines import bailian
from engines import qwen_clone as qwen_clone_engine


class TTSEngine:
    """基于引擎注册表提供统一的配置、音色和合成接口。"""

    # 初始化

    def __init__(self) -> None:
        self._engine_defs = defs.ENGINE_DEFS
        # 枚举本机 SAPI 语音需要数百毫秒，且未必用得上（在线引擎不需要），
        # 因此延迟到首次访问时再枚举。
        self._local_voices = None
        self._local_voice_names = None

        self._current_engine_index = 0
        # 每个引擎持有独立设置，切换后可以恢复该引擎上次使用的参数。
        self._engine_settings: dict[str, dict] = {}
        self._voice_index_map: dict[str, int] = {}
        # 音色列表会随地区或模型整体变化。名称按作用域保存，避免裸索引指向另一种音色，
        # 也避免同一引擎下不同地区的选择互相覆盖。
        self._voice_name_map: dict[str, str] = {}
        for engine_def in self._engine_defs:
            eid = engine_def['id']
            self._engine_settings[eid] = {
                item['key']: item['default'] for item in engine_def.get('options', [])
            }
            self._voice_index_map[eid] = 0

    # 引擎选择

    def get_engine_names(self) -> list[str]:
        """返回注册表顺序下的引擎显示名称。"""
        return [item['name'] for item in self._engine_defs]

    def get_mode(self):
        """返回当前引擎的稳定 ID。"""
        return self._engine_defs[self._current_engine_index]['id']

    def get_mode_index(self):
        """返回当前引擎在注册表中的索引。"""
        return self._current_engine_index

    def set_mode(self, mode):
        """按有效索引或引擎 ID 切换当前引擎；非法值保持原状态。"""
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
        """返回当前引擎的完整注册定义。"""
        return self._engine_defs[self._current_engine_index]

    # 生成策略

    def can_parallel_generate(self):
        """返回当前引擎是否允许并行合成。"""
        return bool(self.get_current_engine_definition().get('parallel_enabled', False))

    def get_parallel_workers(self):
        """返回当前引擎建议的并行任务数。"""
        return int(self.get_current_engine_definition().get('parallel_workers', 1))

    def get_retry_policy(self):
        """返回当前引擎的重试次数和基础退避时间。"""
        engine_def = self.get_current_engine_definition()
        return {
            'retry_times': int(engine_def.get('retry_times', 0)),
            'retry_delay': float(engine_def.get('retry_delay', 0.0)),
        }

    # 引擎选项

    def get_current_options_schema(self):
        """返回当前引擎的可配置项 schema。"""
        return self.get_current_engine_definition().get('options', [])

    def get_option_choices(self, option_schema) -> list[str]:
        """解析 choice 选项的运行期可选值。

        定义了 ``choices_provider`` 时优先调用动态来源；失败或返回空列表时使用 schema
        中的静态选项。
        """
        provider = option_schema.get('choices_provider')
        if provider == 'edge_locales':
            try:
                choices = edge_tts_engine.list_locales()
                if choices:
                    return choices
            except Exception as e:
                print(f'[TTS] 动态选项 {provider} 获取失败，使用静态列表：{e}')

        return [str(item) for item in option_schema.get('choices', [])]

    def option_default(self, engine_id: str, key: str, fallback=''):
        """返回注册表中某个配置项的默认值。

        引擎设置在构造时已按注册表填充，此处供取值时兜底，
        避免在调用点重复写死模型名之类的字面量。
        """
        for engine_def in self._engine_defs:
            if engine_def['id'] != engine_id:
                continue
            for item in engine_def.get('options', []):
                if item.get('key') == key:
                    return item.get('default', fallback)
        return fallback

    def get_current_option_values(self):
        """返回当前引擎设置的浅拷贝。"""
        mode = self.get_mode()
        return self._engine_settings.get(mode, {}).copy()

    def set_current_option(self, key, value):
        """写入当前引擎的一项设置。"""
        mode = self.get_mode()
        if mode not in self._engine_settings:
            return
        self._engine_settings[mode][key] = value

    def apply_current_options(self, option_values):
        """批量写入当前引擎设置。"""
        for key, value in option_values.items():
            self.set_current_option(key, value)

    # 持久化

    def export_persistent_state(self):
        """导出可直接写入 JSON 的引擎状态快照。"""
        return {
            'engine_mode': self.get_mode(),
            'engine_settings': {k: dict(v) for k, v in self._engine_settings.items()},
            'voice_index_map': dict(self._voice_index_map),
            'voice_name_map': dict(self._voice_name_map),
        }

    def import_persistent_state(self, state):
        """按注册表白名单恢复引擎状态，并忽略未知或损坏字段。"""
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

        voice_name_map = state.get('voice_name_map', {})
        if isinstance(voice_name_map, dict):
            # 作用域键由地区或模型动态组成，无法静态枚举，因此这里只规范化键值类型。
            for scope, name in voice_name_map.items():
                self._voice_name_map[str(scope)] = str(name)

        engine_mode = state.get('engine_mode')
        if engine_mode is not None:
            self.set_mode(engine_mode)

        return True

    # 音色选择

    def _ensure_local_voices(self):
        """按需枚举本机语音，结果只求一次。"""
        if self._local_voices is None:
            self._local_voices, self._local_voice_names = pyttsx3_engine.init_voices()
        return self._local_voices, self._local_voice_names

    def get_voices_list(self):
        """返回当前引擎和作用域下的可用音色名称。"""
        mode = self.get_mode()
        if mode == 'local':
            return self._ensure_local_voices()[1]
        if mode == 'edge':
            settings = self._engine_settings.get('edge', {})
            return edge_tts_engine.voices_for_locale(
                settings.get('locale', edge_tts_engine.DEFAULT_LOCALE))
        if mode == 'bailian':
            model = self._engine_settings['bailian'].get('model', 'cosyvoice-v3-flash')
            return bailian.MODEL_VOICES.get(model, bailian.VOICES)
        if mode == 'qwen_clone':
            settings = self._engine_settings.get('qwen_clone', {})
            try:
                voice_items = qwen_clone_engine.list_voices(
                    api_key=settings.get('api_key', ''),
                    region=settings.get('region', 'cn-beijing'),
                    page_size=100,
                    page_index=0,
                    target_model=settings.get('model', ''),
                )
                voices = [item.get('voice', '') for item in voice_items if item.get('voice', '')]
                if voices:
                    return voices
            except Exception as e:
                print(f'[TTS][qwen_clone] 获取音色列表失败：{e}')

            fallback_voice = str(settings.get('voice', '')).strip()
            return [fallback_voice] if fallback_voice else []
        return []

    def get_voice_scope(self) -> str:
        """返回当前音色选择的持久化作用域。

        Edge 按语言地区隔离，百炼按模型隔离，其余引擎直接使用引擎 ID。
        """
        mode = self.get_mode()
        settings = self._engine_settings.get(mode, {})
        if mode == 'edge':
            return f"edge:{settings.get('locale', edge_tts_engine.DEFAULT_LOCALE)}"
        if mode == 'bailian':
            return f"bailian:{settings.get('model', '')}"
        if mode == 'qwen_clone':
            # 复刻音色与 target_model 绑定，换模型后原音色不可用，需分开记录
            return f"qwen_clone:{settings.get('model', '')}"
        return mode

    def set_voice(self, index, name: Optional[str] = None):
        """保存当前音色索引，并按作用域保存可选的稳定名称。"""
        mode = self.get_mode()
        self._voice_index_map[mode] = int(index)
        if name is not None:
            self._voice_name_map[self.get_voice_scope()] = str(name).strip()

    def get_selected_voice_index(self):
        """返回当前引擎保存的音色索引。"""
        mode = self.get_mode()
        return self._voice_index_map.get(mode, 0)

    def resolve_voice_index(self, voices: list[str]) -> int:
        """在新音色列表中按名称恢复选择，再回退到仍有效的历史索引。"""
        if not voices:
            return 0

        name = self._voice_name_map.get(self.get_voice_scope(), '').strip()
        if name and name in voices:
            return voices.index(name)

        index = self._voice_index_map.get(self.get_mode(), 0)
        return index if 0 <= index < len(voices) else 0

    def get_selected_voice_name(self) -> str:
        """返回当前音色名称，供缓存身份和会话记录使用。"""
        mode = self.get_mode()
        settings = self._engine_settings.get(mode, {})

        # 千问音色列表需要联网，而该方法会在 GUI 线程保存会话时调用；直接读取配置可避免卡顿。
        if mode == 'qwen_clone':
            return str(settings.get('voice', '')).strip()

        voices = self.get_voices_list()
        if not voices:
            return ''

        return str(voices[self.resolve_voice_index(voices)])

    def get_generation_profile(self):
        """返回参与缓存键计算的完整生成配置快照。

        同一索引会因地区、模型或系统音色变化而指向不同对象，因此快照必须包含音色名称。
        """
        mode = self.get_mode()
        return {
            'mode': mode,
            'options': self._engine_settings.get(mode, {}).copy(),
            'voice_index': int(self._voice_index_map.get(mode, 0)),
            'voice_name': self._voice_name_map.get(self.get_voice_scope(), ''),
        }

    def get_output_extension(self, mode: Optional[str] = None) -> str:
        """返回指定或当前引擎的默认输出扩展名，不含点号。"""
        target_mode = mode or self.get_mode()
        if target_mode == 'edge':
            return 'mp3'
        return 'wav'

    def create_qwen_clone_voice(self, reference_audio_path: str, *, preferred_name: Optional[str] = None) -> str:
        """创建千问复刻音色，将返回的 ID 写入引擎设置。"""
        settings = self._engine_settings.get('qwen_clone', {})
        model = str(settings.get('model') or self.option_default('qwen_clone', 'model'))
        region = str(settings.get('region', 'cn-beijing'))
        api_key = str(settings.get('api_key', ''))
        audio_mime_type = str(settings.get('audio_mime_type', 'audio/mpeg'))
        name = preferred_name if preferred_name is not None else str(settings.get('preferred_name', 'ppt_reviewer'))

        voice = qwen_clone_engine.create_voice(
            reference_audio_path=reference_audio_path,
            target_model=model,
            preferred_name=name,
            audio_mime_type=audio_mime_type,
            api_key=api_key,
            region=region,
            language=str(settings.get('language_type', '')),
        )
        self._engine_settings['qwen_clone']['voice'] = voice
        return voice

    def delete_qwen_clone_voice(self, voice: str) -> None:
        """使用当前千问账户和地域删除指定复刻音色。"""
        settings = self._engine_settings.get('qwen_clone', {})
        qwen_clone_engine.delete_voice(
            voice=voice,
            api_key=str(settings.get('api_key', '')),
            region=str(settings.get('region', 'cn-beijing')),
            target_model=str(settings.get('model', '')),
        )

    def list_qwen_clone_voice_items(self) -> list[dict]:
        """返回当前千问账户下的复刻音色详情。"""
        settings = self._engine_settings.get('qwen_clone', {})
        return qwen_clone_engine.list_voices(
            api_key=str(settings.get('api_key', '')),
            region=str(settings.get('region', 'cn-beijing')),
            page_size=100,
            page_index=0,
            target_model=str(settings.get('model', '')),
        )

    @staticmethod
    def normalize_text_for_cache(text: str) -> str:
        """折叠空白差异，减少语义相同文本的重复缓存。"""
        return ' '.join(text.strip().split())

    def build_audio_cache_key(self, text: str, generation_profile: Optional[dict] = None) -> str:
        """根据规范化文本和生成配置计算稳定的 SHA-256 缓存键。"""
        profile = generation_profile or self.get_generation_profile()
        payload = {
            'version': 1,
            'text': self.normalize_text_for_cache(text),
            'profile': profile,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    # 音频合成

    def save_file_by_mode(self, mode: str, text: str, path: str,
                          rate=None, volume=None, voice_index=None, **kwargs) -> None:
        """按指定引擎合成音频，并执行该引擎注册的重试策略。

        显式参数优先于已保存设置；值为 ``None`` 时回退到对应引擎的当前设置。
        """
        if mode not in ('local', 'edge', 'bailian', 'qwen_clone'):
            raise RuntimeError(f'不支持的引擎模式：{mode}')

        # 调用方可以临时覆盖注册表中的重试策略，用于特定批处理任务。
        engine_def = next((e for e in self._engine_defs if e['id'] == mode), {})
        retry_times = int(kwargs.get('retry_times', engine_def.get('retry_times', 0)))
        retry_delay = float(kwargs.get('retry_delay', engine_def.get('retry_delay', 0.0)))

        # 显式调用参数具有最高优先级，未传入的参数再从引擎设置中补齐。
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
                        voices=self._ensure_local_voices()[0],
                        rate=r_rate, volume=r_volume, voice_index=r_voice,
                    )
                elif mode == 'edge':
                    locale = str(kwargs.get('locale', settings.get(
                        'locale', edge_tts_engine.DEFAULT_LOCALE)))
                    voices = edge_tts_engine.voices_for_locale(locale)
                    idx = int(r_voice) if voice_index is not None else self.resolve_voice_index(voices)
                    edge_tts_engine.save(
                        text, path,
                        voice=voices[idx] if 0 <= idx < len(voices) else '',
                        locale=locale,
                        rate=r_rate, volume=r_volume,
                        pitch=kwargs.get('pitch', settings.get('pitch', 0)),
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
                elif mode == 'qwen_clone':
                    selected_voice = str(settings.get('voice', '')).strip()
                    if not selected_voice:
                        voices = self.get_voices_list()
                        if voices:
                            idx = max(0, min(int(r_voice), len(voices) - 1))
                            selected_voice = voices[idx]

                    qwen_clone_engine.save(
                        text,
                        path,
                        model=str(kwargs.get('model')
                                  or settings.get('model')
                                  or self.option_default('qwen_clone', 'model')),
                        voice=selected_voice,
                        language_type=str(kwargs.get('language_type', settings.get('language_type', 'Chinese'))),
                        instructions=str(kwargs.get('instructions', settings.get('instructions', ''))),
                        optimize_instructions=bool(kwargs.get('optimize_instructions', settings.get('optimize_instructions', False))),
                        api_key=str(kwargs.get('api_key', settings.get('api_key', ''))),
                        region=str(kwargs.get('region', settings.get('region', 'cn-beijing'))),
                        workspace_id=str(kwargs.get('workspace_id', settings.get('workspace_id', ''))),
                        request_timeout=int(kwargs.get('request_timeout', settings.get('request_timeout', 60))),
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
        """使用当前引擎合成音频，并允许临时覆盖设置。"""
        self.save_file_by_mode(
            self.get_mode(), text, path,
            rate=rate, volume=volume, voice_index=voice_index, **kwargs,
        )

    def save_file_for_stable_local(self, text: str, path: str,
                                   rate=None, volume=None, voice_index=None) -> None:
        """强制使用本地引擎，供倒计时等不应依赖网络的短音频使用。"""
        self.save_file_by_mode('local', text, path, rate=rate, volume=volume, voice_index=voice_index)


if __name__ == '__main__':
    tts = TTSEngine()
