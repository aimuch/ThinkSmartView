"""[xiaozhi-hub] 千问 TTS（阿里云百炼 qwen3-tts-flash）：普通话之外有上海话、北京话、四川话、粤语等方言音色。

走百炼的 HTTP 接口，开 SSE 流式：每个分片的 output.audio.data 是 base64 的 PCM（24 kHz、16 位、单声道、小端），
拼起来包成 WAV 交给基类解码、重采样。一次最多 600 字，更长的按字数切开分几次合成再拼接。
文档：https://help.aliyun.com/zh/model-studio/qwen-tts-api
"""
import base64
import io
import json
import os
import wave

import aiohttp

from core.providers.tts.base import TTSProviderBase

DEFAULT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
SAMPLE_RATE = 24000
MAX_CHARS = 600


def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def split_text(text: str, limit: int = MAX_CHARS) -> list:
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


def sse_events(buffer: bytearray):
    """从缓冲区取出完整的 `data:` 行（JSON），剩下不完整的留在缓冲区里。"""
    while True:
        end = buffer.find(b"\n")
        if end < 0:
            return
        line = bytes(buffer[:end]).strip()
        del buffer[:end + 1]
        if line.startswith(b"data:"):
            payload = line[5:].strip()
            if payload:
                yield json.loads(payload)


def audio_chunk(event: dict) -> bytes:
    """一个流式分片里的 PCM；分片报错时抛出异常（带百炼的错误码和说明）。"""
    if event.get("code"):
        raise Exception(f"{event.get('code')}: {event.get('message') or ''}".strip())
    data = ((event.get("output") or {}).get("audio") or {}).get("data")
    return base64.b64decode(data) if data else b""


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
        if not self.api_key or "你" in self.api_key:
            raise Exception("千问 TTS 还没有填写百炼的 API Key")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "X-DashScope-SSE": "enable"}
        pcm = bytearray()
        timeout = aiohttp.ClientTimeout(total=self.tts_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for part in split_text(text):
                body = {"model": self.model,
                        "input": {"text": part, "voice": self.voice, "language_type": self.language_type}}
                async with session.post(self.url, json=body, headers=headers) as resp:
                    if resp.status != 200:
                        detail = (await resp.text())[:300]
                        raise Exception(f"千问 TTS 返回 HTTP {resp.status}: {detail}")
                    buffer = bytearray()
                    async for chunk in resp.content.iter_any():
                        buffer += chunk
                        for event in sse_events(buffer):
                            pcm += audio_chunk(event)
                    buffer += b"\n"
                    for event in sse_events(buffer):
                        pcm += audio_chunk(event)
        if len(pcm) % 2:
            pcm = pcm[:-1]
        return bytes(pcm)

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
