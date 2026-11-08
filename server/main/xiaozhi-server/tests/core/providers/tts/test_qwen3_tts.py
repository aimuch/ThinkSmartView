"""千问 TTS：SSE 分片解析、长文本切分，以及用本地假服务跑一遍完整的流式合成。"""
import base64
import json
import wave
import io

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from core.providers.tts import qwen3_tts


def _event(pcm=b"", code=""):
    msg = {"output": {"audio": {"data": base64.b64encode(pcm).decode() if pcm else "", "url": ""}}}
    if code:
        msg = {"code": code, "message": "voice not found"}
    return b"data:" + json.dumps(msg).encode() + b"\n\n"


def test_sse_events_keep_partial_lines():
    raw = b"id:1\nevent:result\n" + _event(b"\x01\x02")
    buf = bytearray(raw[:-10])
    first = list(qwen3_tts.sse_events(buf))
    assert first == [] and buf  # 行没收完，留在缓冲区
    buf += raw[-10:]
    events = list(qwen3_tts.sse_events(buf))
    assert [qwen3_tts.audio_chunk(e) for e in events] == [b"\x01\x02"]


def test_audio_chunk_raises_service_error():
    event = next(qwen3_tts.sse_events(bytearray(_event(code="InvalidParameter"))))
    with pytest.raises(Exception, match="InvalidParameter"):
        qwen3_tts.audio_chunk(event)


def test_split_text():
    assert qwen3_tts.split_text("a" * 1201) == ["a" * 600, "a" * 600, "a"]
    assert qwen3_tts.split_text("你好") == ["你好"]


async def test_synthesize_against_fake_dashscope():
    seen = []

    async def handler(request):
        seen.append((request.headers.get("Authorization"), request.headers.get("X-DashScope-SSE"), await request.json()))
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for part in (b"\x10\x00" * 100, b"\x20\x00" * 50):
            await resp.write(b"id:1\nevent:result\n:HTTP_STATUS/200\n" + _event(part))
        await resp.write(_event())  # 最后一片只有 url，没有音频
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/gen", handler)
    async with TestServer(app) as server:
        tts = qwen3_tts.TTSProvider({"api_key": "sk-test", "voice": "Jada", "url": str(server.make_url("/gen"))}, True)
        pcm = await tts.synthesize("侬好")
        wav = await tts.text_to_speak("侬好", None)
    assert pcm == b"\x10\x00" * 100 + b"\x20\x00" * 50
    auth, sse, body = seen[0]
    assert auth == "Bearer sk-test" and sse == "enable"
    assert body == {"model": "qwen3-tts-flash", "input": {"text": "侬好", "voice": "Jada", "language_type": "Chinese"}}
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (24000, 1, 2, 150)


async def test_synthesize_without_key_fails_fast():
    tts = qwen3_tts.TTSProvider({"api_key": "你的百炼 API Key", "voice": "Jada"}, True)
    with pytest.raises(Exception, match="API Key"):
        await tts.text_to_speak("你好", None)
