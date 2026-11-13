"""[xiaozhi-hub] 阿里云百炼的声音复刻（设计画布第 50 块）：复刻、查状态、删除、用复刻的声音合成。

复刻：POST /api/v1/services/audio/tts/customization，model=voice-enrollment，action=create_voice，
录音直接用 Base64 Data URI（不用先传到公网），target_model 定了以后合成必须用同一个模型。
合成：POST /api/v1/services/audio/tts/SpeechSynthesizer，返回一个音频下载地址，再下载 WAV。
创建音色免费，合成按字数计费。文档：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-cloning
"""
import base64

import aiohttp

BASE = "https://dashscope.aliyuncs.com"
TARGET_MODEL = "cosyvoice-v3-flash"
CUSTOMIZE = "/api/v1/services/audio/tts/customization"
SYNTH = "/api/v1/services/audio/tts/SpeechSynthesizer"
SAMPLE_RATE = 24000
MAX_CHARS = 2000


class CloneError(Exception):
    pass


def _headers(key: str) -> dict:
    if not key or "你" in key:
        raise CloneError("还没有百炼的 API Key：到网关后台「语音合成 → 千问 TTS」填上")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _error(data: dict, status: int) -> str:
    code, msg = data.get("code") or "", data.get("message") or ""
    if status == 401 or code in ("InvalidApiKey", "Unauthorized"):
        return "百炼的 API Key 不对"
    if "Arrearage" in code:
        return "百炼账号欠费了"
    return f"百炼返回 {status} {code} {msg}".strip()[:200]


async def _call(key: str, path: str, body: dict, base: str = BASE, timeout: float = 30) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.post(base.rstrip("/") + path, json=body, headers=_headers(key)) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {}
            if r.status != 200 or data.get("code"):
                raise CloneError(_error(data, r.status))
            return data


async def create_voice(key: str, audio: bytes, mime: str, prefix: str, base: str = BASE,
                       model: str = TARGET_MODEL) -> str:
    uri = f"data:{mime};base64,{base64.b64encode(audio).decode()}"
    data = await _call(key, CUSTOMIZE, {"model": "voice-enrollment", "input": {
        "action": "create_voice", "target_model": model, "prefix": prefix, "audio": uri}}, base, 60)
    vid = (data.get("output") or {}).get("voice_id")
    if not vid:
        raise CloneError("百炼没给出音色编号")
    return vid


async def query_voice(key: str, voice_id: str, base: str = BASE) -> str:
    """DEPLOYING（审核、处理中）/ OK（能用了）/ UNDEPLOYED（没通过）。"""
    data = await _call(key, CUSTOMIZE, {"model": "voice-enrollment", "input": {"action": "query_voice", "voice_id": voice_id}}, base)
    return str((data.get("output") or {}).get("status") or "DEPLOYING")


async def delete_voice(key: str, voice_id: str, base: str = BASE):
    await _call(key, CUSTOMIZE, {"model": "voice-enrollment", "input": {"action": "delete_voice", "voice_id": voice_id}}, base)


async def synthesize(key: str, voice_id: str, text: str, model: str = TARGET_MODEL, base: str = BASE,
                     timeout: float = 15) -> bytes:
    """用复刻的声音合成整段文字，返回 WAV（24 kHz）。"""
    data = await _call(key, SYNTH, {"model": model, "input": {
        "text": text[:MAX_CHARS], "voice": voice_id, "format": "wav", "sample_rate": SAMPLE_RATE}}, base, timeout)
    url = ((data.get("output") or {}).get("audio") or {}).get("url")
    if not url:
        raise CloneError("百炼没给出音频")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise CloneError(f"下载合成的声音失败（{r.status}）")
            return await r.read()
