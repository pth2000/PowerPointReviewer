"""阿里百炼 TTS 引擎：内置音色列表、_FileCallback 回调类、基于 tts_v2 的音频合成。"""

import os
import threading
import traceback
from typing import Optional

import dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback, AudioFormat


_DEFAULT_WS_URL = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'

# 各模型对应的可用音色
MODEL_VOICES: dict[str, list[str]] = {
    'cosyvoice-v3-flash': [
        "longanyang",      # 龙安洋 - 社交陪伴（标杆音色）
        "longanhuan",      # 龙安欢 - 社交陪伴（标杆音色）
        "longhuhu_v3",     # 龙呼呼 - 童声（标杆音色）
        "longpaopao_v3",   # 龙泡泡 - 智能玩具/儿童故事机
        "longjielidou_v3", # 龙杰力豆 - 智能玩具/儿童故事机
        "longxian_v3",     # 龙仙 - 智能玩具/儿童故事机
        "longling_v3",     # 龙铃 - 智能玩具/儿童故事机
        "longshanshan_v3", # 龙闪闪 - 消费电子-儿童有声书
        "longniuniu_v3",   # 龙牛牛 - 消费电子-儿童有声书
        "longjiaxin_v3",   # 龙嘉欣 - 方言（粤语）
        "longjiayi_v3",    # 龙嘉怡 - 方言（粤语）
        "longanyue_v3",    # 龙安粤 - 方言（粤语）
        "longlaotie_v3",   # 龙老铁 - 方言（东北话）
        "longshange_v3",   # 龙陕哥 - 方言（陕西话）
        "longanmin_v3",    # 龙安闽 - 方言（闽南话）
        "loongkyong_v3",   # loongkyong - 出海营销（韩语）
        "loongriko_v3",    # Riko - 出海营销（日语）
        "loongtomoka_v3",  # loongtomoka - 出海营销（日语）
        "longfei_v3",      # 龙飞 - 诗词朗诵
        "longyingxiao_v3", # 龙应笑 - 电话销售
        "longyingxun_v3",  # 龙应询 - 客服
        "longyingjing_v3", # 龙应静 - 客服
        "longyingling_v3", # 龙应聆 - 客服
        "longyingtao_v3",  # 龙应桃 - 客服
        "longxiaochun_v3", # 龙小淳 - 语音助手
        "longxiaoxia_v3",  # 龙小夏 - 语音助手
        "longyumi_v3",     # YUMI - 语音助手
        "longanyun_v3",    # 龙安昀 - 语音助手
        "longanwen_v3",    # 龙安温 - 语音助手
        "longanli_v3",     # 龙安莉 - 语音助手
        "longanlang_v3",   # 龙安朗 - 语音助手
        "longyingmu_v3",   # 龙应沐 - 语音助手
        "longantai_v3",    # 龙安台 - 社交陪伴
        "longhua_v3",      # 龙华 - 社交陪伴
        "longcheng_v3",    # 龙橙 - 社交陪伴
        "longze_v3",       # 龙泽 - 社交陪伴
        "longzhe_v3",      # 龙哲 - 社交陪伴
        "longyan_v3",      # 龙颜 - 社交陪伴
        "longxing_v3",     # 龙星 - 社交陪伴
        "longtian_v3",     # 龙天 - 社交陪伴
        "longwan_v3",      # 龙婉 - 社交陪伴
        "longqiang_v3",    # 龙嫱 - 社交陪伴
        "longfeifei_v3",   # 龙菲菲 - 社交陪伴
        "longhao_v3",      # 龙浩 - 社交陪伴
        "longanrou_v3",    # 龙安柔 - 社交陪伴
        "longhan_v3",      # 龙寒 - 社交陪伴
        "longanzhi_v3",    # 龙安智 - 社交陪伴
        "longanling_v3",   # 龙安灵 - 社交陪伴
        "longanya_v3",     # 龙安雅 - 社交陪伴
        "longanqin_v3",    # 龙安亲 - 社交陪伴
        "longmiao_v3",     # 龙妙 - 有声书
        "longsanshu_v3",   # 龙三叔 - 有声书
        "longyuan_v3",     # 龙媛 - 有声书
        "longyue_v3",      # 龙悦 - 有声书
        "longxiu_v3",      # 龙修 - 有声书
        "longnan_v3",      # 龙楠 - 有声书
        "longwanjun_v3",   # 龙婉君 - 有声书
        "longyichen_v3",   # 龙逸尘 - 有声书
        "longlaobo_v3",    # 龙老伯 - 有声书
        "longlaoyi_v3",    # 龙老姨 - 有声书
        "longjiqi_v3",     # 龙机器 - 短视频配音
        "longhouge_v3",    # 龙猴哥 - 短视频配音
        "longdaiyu_v3",    # 龙黛玉 - 短视频配音
        "longanran_v3",    # 龙安燃 - 直播带货
        "longanxuan_v3",   # 龙安宣 - 直播带货
        "longshuo_v3",     # 龙硕 - 新闻播报
        "longshu_v3",      # 龙书 - 新闻播报
        "loongbella_v3",    # Bella3.0 - 新闻播报
    ],
    'cosyvoice-v3-plus': [
        "longanyang",   # 龙安洋 - 社交陪伴（标杆音色）
        "longanhuan",   # 龙安欢 - 社交陪伴（标杆音色）
    ],
    'cosyvoice-v2': [
        "longyingxiao",    # 龙应笑 - 电话销售
        "longjiqi",        # 龙机器 - 短视频配音
        "longhouge",       # 龙猴哥 - 短视频配音
        "longjixin",       # 龙机心 - 短视频配音
        "longanyue",       # 龙安粤 - 短视频配音（粤语）
        "longshange",      # 龙陕哥 - 短视频配音（陕西话）
        "longanmin",       # 龙安敏 - 短视频配音（闽南话）
        "longdaiyu",       # 龙黛玉 - 短视频配音
        "longgaoseng",     # 龙高僧 - 短视频配音
        "longanli",        # 龙安莉 - 语音助手
        "longanlang",      # 龙安朗 - 语音助手
        "longanwen",       # 龙安温 - 语音助手
        "longanyun",       # 龙安昀 - 语音助手
        "longyumi_v2",     # YUMI - 语音助手
        "longxiaochun_v2", # 龙小淳 - 语音助手
        "longxiaoxia_v2",  # 龙小夏 - 语音助手
        "longyichen",      # 龙逸尘 - 有声书
        "longwanjun",      # 龙婉君 - 有声书
        "longlaobo",       # 龙老伯 - 有声书
        "longlaoyi",       # 龙老姨 - 有声书
        "longbaizhi",      # 龙白芷 - 有声书
        "longsanshu",      # 龙三叔 - 有声书
        "longxiu_v2",      # 龙修 - 有声书
        "longmiao_v2",     # 龙妙 - 有声书
        "longyue_v2",      # 龙悦 - 有声书
        "longnan_v2",      # 龙楠 - 有声书
        "longyuan_v2",     # 龙媛 - 有声书
        "longanqin",       # 龙安亲 - 社交陪伴
        "longanya",        # 龙安雅 - 社交陪伴
        "longanshuo",      # 龙安朔 - 社交陪伴
        "longanling",      # 龙安灵 - 社交陪伴
        "longanzhi",       # 龙安智 - 社交陪伴
        "longanrou",       # 龙安柔 - 社交陪伴
        "longqiang_v2",    # 龙嫱 - 社交陪伴
        "longhan_v2",      # 龙寒 - 社交陪伴
        "longxing_v2",     # 龙星 - 社交陪伴
        "longhua_v2",      # 龙华 - 社交陪伴
        "longwan_v2",      # 龙婉 - 社交陪伴
        "longcheng_v2",    # 龙橙 - 社交陪伴
        "longfeifei_v2",   # 龙菲菲 - 社交陪伴
        "longxiaocheng_v2",# 龙小诚 - 社交陪伴
        "longzhe_v2",      # 龙哲 - 社交陪伴
        "longyan_v2",      # 龙颜 - 社交陪伴
        "longtian_v2",     # 龙天 - 社交陪伴
        "longze_v2",       # 龙泽 - 社交陪伴
        "longshao_v2",     # 龙邵 - 社交陪伴
        "longhao_v2",      # 龙浩 - 社交陪伴
        "kabuleshen_v2",   # 龙深 - 社交陪伴
        "longhuhu",        # 龙呼呼 - 童声（标杆音色）
        "longanpei",       # 龙安培 - 消费电子-教育培训
        "longwangwang",    # 龙汪汪 - 消费电子-儿童陪伴
        "longpaopao",      # 龙泡泡 - 消费电子-儿童陪伴
        "longshanshan",    # 龙闪闪 - 消费电子-儿童有声书
        "longniuniu",      # 龙牛牛 - 消费电子-儿童有声书
        "longyingmu",      # 龙应沐 - 客服
        "longyingxun",     # 龙应询 - 客服
        "longyingcui",     # 龙应催 - 客服
        "longyingda",      # 龙应答 - 客服
        "longyingjing",    # 龙应静 - 客服
        "longyingyan",     # 龙应严 - 客服
        "longyingtian",    # 龙应甜 - 客服
        "longyingbing",    # 龙应冰 - 客服
        "longyingtao",     # 龙应桃 - 客服
        "longyingling",    # 龙应聆 - 客服
        "longanran",       # 龙安燃 - 直播带货
        "longanxuan",      # 龙安宣 - 直播带货
        "longanchong",     # 龙安冲 - 直播带货
        "longanping",      # 龙安萍 - 直播带货
        "longjielidou_v2", # 龙杰力豆 - 童声
        "longling_v2",     # 龙铃 - 童声
        "longke_v2",       # 龙可 - 童声
        "longxian_v2",     # 龙仙 - 童声
        "longlaotie_v2",   # 龙老铁 - 方言（东北话）
        "longjiayi_v2",    # 龙嘉怡 - 方言（粤语）
        "longtao_v2",      # 龙桃 - 方言（粤语）
        "longfei_v2",      # 龙飞 - 诗词朗诵
        "libai_v2",        # 李白 - 诗词朗诵
        "longjin_v2",      # 龙津 - 诗词朗诵
        "longshu_v2",      # 龙书 - 新闻播报
        "loongbella_v2",   # Bella2.0 - 新闻播报
        "longshuo_v2",     # 龙硕 - 新闻播报
        "longxiaobai_v2",  # 龙小白 - 新闻播报
        "longjing_v2",     # 龙婧 - 新闻播报
        "loongstella_v2",  # loongstella - 新闻播报
        "loongyuuna_v2",   # loongyuuna - 出海营销（日语）
        "loongyuuma_v2",   # loongyuuma - 出海营销（日语）
        "loongjihun_v2",   # loongjihun - 出海营销（韩语）
        "loongeva_v2",     # loongeva - 出海营销（英式英文）
        "loongbrian_v2",   # loongbrian - 出海营销（英式英文）
        "loongluna_v2",    # loongluna - 出海营销（英式英文）
        "loongluca_v2",    # loongluca - 出海营销（英式英文）
        "loongemily_v2",   # loongemily - 出海营销（英式英文）
        "loongeric_v2",    # loongeric - 出海营销（英式英文）
        "loongabby_v2",    # loongabby - 出海营销（美式英文）
        "loongannie_v2",   # loongannie - 出海营销（美式英文）
        "loongandy_v2",    # loongandy - 出海营销（美式英文）
        "loongava_v2",     # loongava - 出海营销（美式英文）
        "loongbeth_v2",    # loongbeth - 出海营销（美式英文）
        "loongbetty_v2",   # loongbetty - 出海营销（美式英文）
        "loongcindy_v2",   # loongcindy - 出海营销（美式英文）
        "loongcally_v2",   # loongcally - 出海营销（美式英文）
        "loongdavid_v2",   # loongdavid - 出海营销（美式英文）
        "loongdonna_v2",   # loongdonna - 出海营销（美式英文）
        "loongkyong_v2",   # loongkyong - 出海营销（韩语）
        "loongtomoka_v2",  # loongtomoka - 出海营销（日语）
        "loongtomoya_v2",  # loongtomoya - 出海营销（日语）
    ],
    'cosyvoice-v1': [
        "longwan",       # 龙婉
        "longcheng",     # 龙橙
        "longhua",       # 龙华
        "longxiaochun",  # 龙小淳
        "longxiaoxia",   # 龙小夏
        "longxiaocheng", # 龙小诚
        "longxiaobai",   # 龙小白
        "longlaotie",    # 龙老铁（东北口音）
        "longshu",       # 龙书
        "longshuo",      # 龙硕
        "longjing",      # 龙婧
        "longmiao",      # 龙妙
        "longyue",       # 龙悦
        "longyuan",      # 龙媛
        "longfei",       # 龙飞
        "longjielidou",  # 龙杰力豆
        "longtong",      # 龙彤
        "longxiang",     # 龙祥
        "loongstella",   # Stella
        "loongbella",    # Bella
    ]
}
# 默认音色（以第一个模型的列表为准）
VOICES: list[str] = MODEL_VOICES['cosyvoice-v3-flash']


# ──────────────────────────────────────────────────────────
# 回调类
# ──────────────────────────────────────────────────────────

class _FileCallback(ResultCallback):  # type: ignore[misc]
    """将百炼 WebSocket 流式音频分片写入目标文件，并在完成时发出事件信号。"""

    def __init__(self, output_path: str) -> None:
        self.output_path = output_path
        self.file = None
        self.error_message: Optional[str] = None
        self.total_bytes: int = 0
        self.events: list[str] = []
        self.done_event = threading.Event()

    def on_open(self) -> None:
        self.file = open(self.output_path, 'wb')
        print(f'[百炼TTS] 连接建立，输出文件：{self.output_path}')

    def on_complete(self) -> None:
        print(f'[百炼TTS] 合成完成，累计字节：{self.total_bytes}')
        self.done_event.set()

    def on_error(self, message: str) -> None:
        self.error_message = message
        print(f'[百炼TTS] 回调错误：{message}')
        self.done_event.set()

    def on_close(self) -> None:
        if self.file:
            self.file.close()
            self.file = None
        print('[百炼TTS] 连接关闭')
        self.done_event.set()

    def on_event(self, message) -> None:
        msg = str(message)
        self.events.append(msg)
        if len(self.events) <= 3:  # 只打印前几条，避免刷屏
            print(f'[百炼TTS] 事件：{msg}')

    def on_data(self, data: bytes) -> None:
        if self.file:
            self.file.write(data)
            self.total_bytes += len(data)


# ──────────────────────────────────────────────────────────
# 音频合成
# ──────────────────────────────────────────────────────────

def save(text: str, path: str, *, voices: list[str] = VOICES, voice_index: int = 0,
         rate: float = 1.0, volume: int = 50, pitch: float = 1.0,
         model: str = 'cosyvoice-v3-flash', api_key: str = '',
         ws_url: str = _DEFAULT_WS_URL) -> None:
    """使用阿里百炼 tts_v2 将文本合成为音频文件。

    Args:
        text:        要合成的文本。
        path:        输出文件路径。
        voices:      百炼音色名称列表，默认使用模块内置 VOICES。
        voice_index: 发音人索引。
        rate:        语速倍率（0.5~2.0）。
        volume:      音量（0~100）。
        pitch:       音调倍率（0.5~2.0）。
        model:       百炼语音模型名称。
        api_key:     阿里百炼 API Key；为空时自动读取环境变量 DASHSCOPE_API_KEY。
        ws_url:      WebSocket 服务地址。
    """
    if SpeechSynthesizer is None:
        raise RuntimeError('未安装 dashscope，请先执行: pip install dashscope')

    if not api_key:
        api_key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('未配置阿里百炼 API Key，请在设置页填写后重试')

    dashscope.api_key = api_key
    voice_name = voices[voice_index] if 0 <= voice_index < len(voices) else voices[0]

    def _call() -> None:
        callback = _FileCallback(path)
        synthesizer = SpeechSynthesizer(
            model=model,
            voice=voice_name,
            format=AudioFormat.WAV_22050HZ_MONO_16BIT,
            volume=int(volume),
            speech_rate=float(rate),
            pitch_rate=float(pitch),
            callback=callback,
            url=ws_url,
        )
        try:
            synthesizer.call(text)
        except Exception as e:
            print(f'[百炼TTS] call() 抛出异常：{e}')
            traceback.print_exc()
            raise RuntimeError(f'百炼 TTS 调用异常：{e}') from e

        # tts_v2 回调异步到达，call() 返回后仍可能在写文件，等待完成信号
        completed = callback.done_event.wait(timeout=30)
        if not completed:
            raise RuntimeError('百炼 TTS 在 30 秒内未完成，可能是网络超时或服务阻塞')
        if callback.error_message:
            raise RuntimeError(f'百炼 TTS 回调报错：{callback.error_message}')
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            event_tail = callback.events[-3:] if callback.events else []
            raise RuntimeError(
                f'百炼 TTS 输出文件为空；累计字节={callback.total_bytes}；最近事件={event_tail}'
            )

    _call()
