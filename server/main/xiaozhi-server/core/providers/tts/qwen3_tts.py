"""[xiaozhi-hub] 千问 TTS（阿里云百炼 qwen3-tts-flash）：普通话之外有上海话、北京话、四川话、粤语等方言音色。

接口调用在 core/utils/qwen3_tts_api.py（SSE 流式取 24 kHz PCM，超过 600 字分段），这里包成 WAV 交给基类解码、重采样。
"""
import os

from core.providers.tts.base import TTSProviderBase
from core.utils import qwen3_tts_api as api
from core.utils.qwen3_tts_api import DEFAULT_URL, audio_chunk, pcm_to_wav, split_text, sse_events  # noqa: F401


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = str(config.get("api_key") or "").strip()
        self.model = str(config.get("model") or "qwen3-tts-flash")
        self.voice = str(config.get("private_voice") or config.get("voice") or "Cherry")
        self.language_type = str(config.get("language_type") or "Chinese")
        self.url = str(config.get("url") or DEFAULT_URL)
        self.audio_file_type = "wav"

    async def synthesize(self, text: str) -> bytes:
        """合成整段文字，返回 24 kHz 16 位单声道 PCM。"""
        return await api.synthesize(text, self.api_key, self.voice, self.model, self.language_type, self.url,
                                    self.tts_timeout)

    async def text_to_speak(self, text, output_file):
        try:
            pcm = await self.synthesize(text)
        except Exception as e:
            raise Exception(f"千问 TTS 请求失败: {e}")
        if not pcm:
            return None
        wav = pcm_to_wav(pcm)
        if output_file:
            os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
            with open(output_file, "wb") as f:
                f.write(wav)
            return None
        return wav
