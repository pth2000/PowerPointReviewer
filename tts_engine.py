import asyncio
import re
from typing import List, Optional

import edge_tts
import pyttsx3

from config import ONLINE_TTS_VOICES, DEFAULT_TTS_RATE, DEFAULT_TTS_VOLUME
from exceptions import TTSError, handle_exception
from logger_utils import get_logger
from utils import sanitize_text_for_tts

logger = get_logger()


class TTSEngine:
    """Text-to-Speech engine with support for both local and online TTS."""
    
    def __init__(self):
        """Initialize TTS engine with default settings."""
        try:
            self.tts_engine = pyttsx3.init()
            self.voices = self.tts_engine.getProperty("voices")
            logger.info(f"Initialized local TTS engine with {len(self.voices)} voices")
        except Exception as e:
            logger.error(f"Failed to initialize local TTS engine: {e}")
            raise TTSError("无法初始化本地TTS引擎", str(e))
        
        self.is_local_mode = True
        self.voice_list_online = ONLINE_TTS_VOICES.copy()
        self.voices_selected_online = self.voice_list_online[0]
        
        # Set default values
        self._set_default_properties()

    def _set_default_properties(self) -> None:
        """Set default TTS properties."""
        try:
            self.set_rate(DEFAULT_TTS_RATE)
            self.set_volume(DEFAULT_TTS_VOLUME)
            logger.debug("Set default TTS properties")
        except Exception as e:
            logger.warning(f"Failed to set default TTS properties: {e}")

    def set_rate(self, rate: int) -> None:
        """Set TTS speech rate.
        
        Args:
            rate: Speech rate (words per minute)
            
        Raises:
            TTSError: If setting rate fails
        """
        if not self.is_local_mode:
            logger.warning("Rate setting not supported in online mode")
            return
            
        try:
            self.tts_engine.setProperty('rate', rate)
            logger.debug(f"Set TTS rate to {rate}")
        except Exception as e:
            error_msg = f"Failed to set TTS rate to {rate}"
            logger.error(f"{error_msg}: {e}")
            raise TTSError(error_msg, str(e))

    def get_rate(self) -> int:
        """Get current TTS speech rate.
        
        Returns:
            Current speech rate
        """
        try:
            return self.tts_engine.getProperty('rate')
        except Exception as e:
            logger.error(f"Failed to get TTS rate: {e}")
            return DEFAULT_TTS_RATE

    def set_volume(self, volume: float) -> None:
        """Set TTS volume.
        
        Args:
            volume: Volume level (0.0 to 1.0)
            
        Raises:
            TTSError: If setting volume fails
        """
        if not self.is_local_mode:
            logger.warning("Volume setting not supported in online mode")
            return
            
        if not 0.0 <= volume <= 1.0:
            raise TTSError(f"音量必须在0.0到1.0之间，当前值: {volume}")
            
        try:
            self.tts_engine.setProperty('volume', volume)
            logger.debug(f"Set TTS volume to {volume}")
        except Exception as e:
            error_msg = f"Failed to set TTS volume to {volume}"
            logger.error(f"{error_msg}: {e}")
            raise TTSError(error_msg, str(e))

    def get_volume(self) -> float:
        """Get current TTS volume.
        
        Returns:
            Current volume level
        """
        try:
            return self.tts_engine.getProperty('volume')
        except Exception as e:
            logger.error(f"Failed to get TTS volume: {e}")
            return DEFAULT_TTS_VOLUME

    def get_voices_list(self) -> List[str]:
        """Get list of available voices.
        
        Returns:
            List of voice names
        """
        if self.is_local_mode:
            return self.get_voices_list_local()
        else:
            return self.get_voices_list_online()

    def get_voices_list_local(self) -> List[str]:
        """Get list of local TTS voices.
        
        Returns:
            List of local voice names
        """
        voices_list = []
        try:
            for voice in self.voices:
                match = re.search(r'name=([^\n]+)', str(voice))
                if match:
                    voice_name = match.group(1).strip()
                    voices_list.append(voice_name)
            logger.debug(f"Found {len(voices_list)} local voices")
        except Exception as e:
            logger.error(f"Failed to get local voices list: {e}")
        
        return voices_list

    def get_voices_list_online(self) -> List[str]:
        """Get list of online TTS voices.
        
        Returns:
            List of online voice names
        """
        return self.voice_list_online.copy()

    def set_voice(self, index: int) -> None:
        """Set TTS voice by index.
        
        Args:
            index: Voice index in the voices list
            
        Raises:
            TTSError: If setting voice fails
        """
        if self.is_local_mode:
            if not 0 <= index < len(self.voices):
                raise TTSError(f"语音索引超出范围: {index}")
            
            try:
                self.tts_engine.setProperty("voice", self.voices[index].id)
                logger.debug(f"Set local voice to index {index}")
            except Exception as e:
                error_msg = f"Failed to set local voice to index {index}"
                logger.error(f"{error_msg}: {e}")
                raise TTSError(error_msg, str(e))
        else:
            if not 0 <= index < len(self.voice_list_online):
                raise TTSError(f"在线语音索引超出范围: {index}")
            
            self.voices_selected_online = self.voice_list_online[index]
            logger.debug(f"Set online voice to {self.voices_selected_online}")

    def save_file_local(self, text: str, path: str) -> None:
        """Save text to audio file using local TTS.
        
        Args:
            text: Text to convert to speech
            path: Output file path
            
        Raises:
            TTSError: If conversion fails
        """
        if not text.strip():
            logger.warning("Empty text provided for TTS conversion")
            return
            
        sanitized_text = sanitize_text_for_tts(text)
        if not sanitized_text:
            logger.warning("Text became empty after sanitization")
            return
        
        try:
            self.tts_engine.save_to_file(sanitized_text, path)
            self.tts_engine.runAndWait()
            logger.debug(f"Saved local TTS audio to {path}")
        except Exception as e:
            error_msg = f"Failed to save local TTS audio to {path}"
            logger.error(f"{error_msg}: {e}")
            raise TTSError(error_msg, str(e))

    def save_file(self, text: str, path: str) -> None:
        """Save text to audio file using current TTS mode.
        
        Args:
            text: Text to convert to speech
            path: Output file path
            
        Raises:
            TTSError: If conversion fails
        """
        if self.is_local_mode:
            self.save_file_local(text, path)
        else:
            self.save_file_online(text, path)

    def save_file_online(self, text: str, path: str) -> None:
        """Save text to audio file using online TTS.
        
        Args:
            text: Text to convert to speech
            path: Output file path
            
        Raises:
            TTSError: If conversion fails
        """
        if not text.strip():
            logger.warning("Empty text provided for online TTS conversion")
            return
            
        sanitized_text = sanitize_text_for_tts(text)
        if not sanitized_text:
            logger.warning("Text became empty after sanitization")
            return

        async def get_edge_wave() -> None:
            """Async function to get audio from edge TTS."""
            try:
                communicate = edge_tts.Communicate(sanitized_text, self.voices_selected_online)
                await communicate.save(path)
            except Exception as e:
                raise TTSError(f"在线TTS转换失败", str(e))

        try:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(get_edge_wave())
            logger.debug(f"Saved online TTS audio to {path}")
        except Exception as e:
            error_msg = f"Failed to save online TTS audio to {path}"
            logger.error(f"{error_msg}: {e}")
            if isinstance(e, TTSError):
                raise e
            else:
                raise TTSError(error_msg, str(e))
        finally:
            try:
                loop.close()
            except Exception:
                pass  # Ignore cleanup errors

    def switch_mode(self, is_local: bool) -> None:
        """Switch between local and online TTS modes.
        
        Args:
            is_local: True for local mode, False for online mode
        """
        old_mode = "本地" if self.is_local_mode else "在线"
        new_mode = "本地" if is_local else "在线"
        
        self.is_local_mode = is_local
        logger.info(f"Switched TTS mode from {old_mode} to {new_mode}")


if __name__ == '__main__':
    # Test the TTS engine
    try:
        tts = TTSEngine()
        print("TTS Engine initialized successfully")
        print(f"Available voices: {len(tts.get_voices_list())}")
    except Exception as e:
        print(f"Failed to initialize TTS Engine: {e}")
