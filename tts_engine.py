import re
import pyttsx3


class TTSEngine:
    def __init__(self):
        self.tts_engine = pyttsx3.init()
        self.voices = self.tts_engine.getProperty("voices")

    def set_rate(self, rate):
        self.tts_engine.setProperty('rate', rate)

    def get_rate(self):
        return self.tts_engine.getProperty('rate')

    def set_volume(self, volume):
        self.tts_engine.setProperty('volume', volume)

    def get_volume(self):
        return self.tts_engine.getProperty('volume')

    def get_voices_list(self):
        voices_list = []
        for voice in self.voices:
            match = re.search(r'Tokens\\([^ ]+)', str(voice))
            if match:
                voice_string = match.group(1).strip()
                voices_list.append(voice_string)
        return voices_list

    def set_voice(self, index):
        self.tts_engine.setProperty("voice", self.voices[index].id)


if __name__ == '__main__':
    tts = TTSEngine()
