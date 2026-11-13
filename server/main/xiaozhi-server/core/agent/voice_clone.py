"""[xiaozhi-hub] 声音复刻（设计画布第 50 块）：家里人的声音交给阿里云百炼复刻，设备在「回答的声音」里选它。

存在 data/voice_clones.json：[{"voice_id", "name", "status", "model", "created", "source", "seconds"}]。
录音只发给百炼，这里不留。百炼的 Key 和千问 TTS 共用（后台「语音合成 → 千问 TTS」），
也认看图模型里填的百炼 Key（photo_ask.dashscope_key）。
设备选了复刻的声音：device_settings 记 {"entry": ENTRY, "voice": voice_id}，连接时 provider_conf 给出合成配置。
"""
import json
import os
import re
import secrets
import threading
import time

from config.config_loader import get_project_dir
from core.utils import voice_clone_api as api

ENTRY = "CloneTTS"
PREFIX_VALUE = "clone:"          # 设备页里复刻的声音的值：clone:<voice_id>
NAME_MAX = 12
MAX_VOICES = 20
MIN_SECONDS, MAX_SECONDS = 5, 60
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "voice_clones.json")


def load() -> list:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [v for v in data if isinstance(v, dict) and v.get("voice_id")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(voices: list):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(voices, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def find(voice_id: str):
    return next((v for v in load() if v["voice_id"] == voice_id), None)


def key(merged: dict):
    """百炼 Key：千问 TTS 或看图模型里填过的那个；没有返回 None。"""
    from core.agent import photo_ask
    from core.admin import catalog

    k = ((merged.get("TTS") or {}).get(catalog.QWEN3_TTS_ENTRY) or {}).get("api_key")
    if not photo_ask._placeholder(k):
        return str(k)
    return photo_ask.dashscope_key(merged)


def ready(merged: dict) -> dict:
    return {"ready": bool(key(merged)), "reason": None if key(merged) else "先在网关后台「语音合成 → 千问 TTS」填百炼的 API Key"}


def clean_name(name) -> str:
    return re.sub(r"[\x00-\x1f<>\"\\]", "", str(name or "")).strip()[:NAME_MAX]


def view(v: dict) -> dict:
    return {"voice_id": v["voice_id"], "value": PREFIX_VALUE + v["voice_id"], "name": v["name"], "status": v["status"],
            "created": v.get("created"), "source": v.get("source", ""), "seconds": v.get("seconds")}


def listing() -> list:
    return [view(v) for v in load()]


def wav_seconds(audio: bytes):
    """WAV 的时长（秒）；不是 WAV 返回 None（mp3、m4a 交给百炼自己判断）。"""
    import io
    import wave
    try:
        with wave.open(io.BytesIO(audio)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        return None


def mime_of(audio: bytes, filename: str = "") -> str:
    if audio[:4] == b"RIFF":
        return "audio/wav"
    if audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or filename.lower().endswith(".mp3"):
        return "audio/mpeg"
    if audio[4:8] == b"ftyp" or filename.lower().endswith(".m4a"):
        return "audio/mp4"
    raise ValueError("只认 wav、mp3、m4a 录音")


async def create(merged: dict, name: str, audio: bytes, filename: str = "", source: str = "") -> dict:
    name = clean_name(name)
    if not name:
        raise ValueError("给这个声音起个名字")
    voices = load()
    if len(voices) >= MAX_VOICES:
        raise ValueError(f"最多 {MAX_VOICES} 个复刻的声音，先删一个")
    if any(v["name"] == name for v in voices):
        raise ValueError(f"已经有叫「{name}」的声音了")
    if len(audio) > 10 * 1024 * 1024:
        raise ValueError("录音太大了（最多 10 MB）")
    mime = mime_of(audio, filename)
    seconds = wav_seconds(audio)
    if seconds is not None and not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError(f"录音要 10–20 秒（这段 {seconds:.0f} 秒）")
    k = key(merged)
    if not k:
        raise ValueError(ready(merged)["reason"])
    try:
        vid = await api.create_voice(k, audio, mime, "xm" + secrets.token_hex(3))
    except api.CloneError as e:
        raise ValueError(str(e))
    entry = {"voice_id": vid, "name": name, "status": "DEPLOYING", "model": api.TARGET_MODEL,
             "created": int(time.time()), "source": source, "seconds": seconds and round(seconds)}
    with _lock:
        voices = load()
        voices.append(entry)
        _save(voices)
    return view(entry)


async def refresh(merged: dict) -> list:
    """还在复刻的去百炼问一下状态。"""
    pending = [v for v in load() if v["status"] == "DEPLOYING"]
    k = key(merged) if pending else None
    changed = {}
    for v in pending:
        try:
            changed[v["voice_id"]] = await api.query_voice(k, v["voice_id"])
        except (api.CloneError, Exception):
            continue
    if any(changed[i] != "DEPLOYING" for i in changed):
        with _lock:
            voices = load()
            for v in voices:
                if v["voice_id"] in changed:
                    v["status"] = changed[v["voice_id"]]
            _save(voices)
    return listing()


async def delete(merged: dict, voice_id: str):
    if not find(voice_id):
        raise LookupError("没有这个声音")
    k = key(merged)
    if k:
        try:
            await api.delete_voice(k, voice_id)
        except api.CloneError:
            pass          # 百炼那边已经没了也照样删本地的
    with _lock:
        _save([v for v in load() if v["voice_id"] != voice_id])


async def speak(merged: dict, voice_id: str, text: str) -> bytes:
    v = find(voice_id)
    if not v:
        raise LookupError("没有这个声音")
    if v["status"] != "OK":
        raise ValueError("这个声音还没复刻好")
    try:
        return await api.synthesize(key(merged) or "", voice_id, text, v.get("model") or api.TARGET_MODEL)
    except api.CloneError as e:
        raise ValueError(str(e))


def provider_conf(cfg: dict, voice_id: str):
    """连接时：这台设备选了复刻的声音 → 合成配置；声音没了、Key 没了返回 None（照旧用网关的）。"""
    v = find(voice_id)
    k = key(cfg)
    if not v or v["status"] != "OK" or not k:
        return None
    return {"type": "voice_clone", "voice": voice_id, "model": v.get("model") or api.TARGET_MODEL, "api_key": k}
