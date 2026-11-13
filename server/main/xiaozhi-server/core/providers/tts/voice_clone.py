"""[xiaozhi-hub] 复刻的声音（设计画布第 50 块）：用阿里云百炼复刻出的音色说话。

不是在后台「语音合成」页里选的引擎：某台设备在「回答的声音」里选了复刻的声音时，
core/utils/device_settings.apply 给这个连接设 TTS = CloneTTS（音色、模型、百炼 Key 都带上）。
接口调用在 core/utils/voice_clone_api.py。
"""
import os

from core.providers.tts.base import TTSProviderBase
from core.utils import voice_clone_api as api


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = str(config.get("api_key") or "").strip()
        self.voice = str(config.get("voice") or "")
        self.model = str(config.get("model") or api.TARGET_MODEL)
        self.base = str(config.get("url") or api.BASE)
        self.audio_file_type = "wav"

    async def text_to_speak(self, text, output_file):
        try:
            wav = await api.synthesize(self.api_key, self.voice, text, self.model, self.base, self.tts_timeout)
        except Exception as e:
            raise Exception(f"复刻的声音合成失败: {e}")
        if output_file:
            os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
            with open(output_file, "wb") as f:
                f.write(wav)
            return None
        return wav
