import asyncio
import re

import edge_tts
import pyttsx3


class TTSEngine:
    def __init__(self):
        self.tts_engine = pyttsx3.init()
        self.voices = self.tts_engine.getProperty("voices")
        self.is_local_mode = True

        self.voice_list_online = [
            'zh-CN-XiaoxiaoNeural',
            'zh-CN-XiaoyiNeural',
            'zh-CN-YunjianNeural',
            'zh-CN-YunxiNeural',
            'zh-CN-YunxiaNeural',
            'zh-CN-YunyangNeural',
            'zh-CN-liaoning-XiaobeiNeural',
            'zh-CN-shaanxi-XiaoniNeural',
        ]
        self.voices_selected_online = self.voice_list_online[0]

    def set_rate(self, rate):
        self.tts_engine.setProperty('rate', rate)

    def get_rate(self):
        return self.tts_engine.getProperty('rate')

    def set_volume(self, volume):
        self.tts_engine.setProperty('volume', volume)

    def get_volume(self):
        return self.tts_engine.getProperty('volume')

    def get_voices_list(self):
        if self.is_local_mode:
            return self.get_voices_list_local()
        else:
            return self.get_voices_list_online()

    def get_voices_list_local(self):
        voices_list = []
        for voice in self.voices:
            match = re.search(r'Tokens\\([^ ]+)', str(voice))
            if match:
                voice_string = match.group(1).strip()
                voices_list.append(voice_string)
        return voices_list

    def get_voices_list_online(self):
        return self.voice_list_online

    def set_voice(self, index):
        if self.is_local_mode:
            self.tts_engine.setProperty("voice", self.voices[index].id)
        else:
            self.voices_selected_online = self.voice_list_online[index]

    def save_file_local(self, text, path):
        self.tts_engine.save_to_file(text, path)
        self.tts_engine.runAndWait()

    def save_file(self, text, path):
        if self.is_local_mode:
            self.save_file_local(text, path)
        else:
            self.save_file_online(text, path)

    def save_file_online(self, text, path):
        async def get_edge_wave() -> None:
            communicate = edge_tts.Communicate(text, self.voices_selected_online)
            await communicate.save(path)

        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(get_edge_wave())
        finally:
            loop.close()


if __name__ == '__main__':
    tts = TTSEngine()
