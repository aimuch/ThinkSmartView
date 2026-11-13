"""[xiaozhi-hub] 声音复刻（设计画布第 50 块）：复刻、状态、删除、设备选它时连接用它合成。百炼接口用假的。"""
import asyncio
import io
import wave

import pytest

from core.agent import voice_clone as vc
from core.utils import device_settings as ds
from core.utils import voice_clone_api as api

MERGED = {"selected_module": {"TTS": "EdgeTTS"}, "TTS": {"EdgeTTS": {"type": "edge", "voice": "zh-CN-XiaoxiaoNeural"},
                                                        "Qwen3TTS": {"type": "qwen3_tts", "api_key": "sk-bailian-1234"}}}


def wav(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    return buf.getvalue()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(vc, "_path", lambda: str(tmp_path / "voice_clones.json"))
    monkeypatch.setattr(ds, "_path", lambda: str(tmp_path / "device_settings.json"))
    calls = {"create": [], "status": "DEPLOYING", "deleted": []}

    async def create_voice(key, audio, mime, prefix, base=api.BASE, model=api.TARGET_MODEL):
        calls["create"].append((key, mime, prefix, len(audio)))
        return f"{model}-{prefix}-abc"

    async def query_voice(key, voice_id, base=api.BASE):
        return calls["status"]

    async def delete_voice(key, voice_id, base=api.BASE):
        calls["deleted"].append(voice_id)

    async def synthesize(key, voice_id, text, model=api.TARGET_MODEL, base=api.BASE, timeout=15):
        return b"RIFF" + text.encode()
    for f in (create_voice, query_voice, delete_voice, synthesize):
        monkeypatch.setattr(api, f.__name__, f)
    return calls


def run(coro):
    return asyncio.run(coro)


def test_key_comes_from_qwen_tts():
    assert vc.key(MERGED) == "sk-bailian-1234"
    assert vc.ready({"TTS": {"Qwen3TTS": {"api_key": "你的api_key"}}})["ready"] is False


def test_create_refresh_and_use(fake):
    v = run(vc.create(MERGED, " 妈妈 ", wav(15), "", "书房中枢屏"))
    assert v["name"] == "妈妈" and v["status"] == "DEPLOYING" and v["value"] == "clone:" + v["voice_id"] and v["seconds"] == 15
    key, mime, prefix, _ = fake["create"][0]
    assert key == "sk-bailian-1234" and mime == "audio/wav" and prefix.startswith("xm") and len(prefix) <= 10
    with pytest.raises(ValueError, match="还没复刻好"):
        ds.put("dev1", {"tts": {"entry": vc.ENTRY, "voice": v["voice_id"]}}, MERGED)
    fake["status"] = "OK"
    assert run(vc.refresh(MERGED))[0]["status"] == "OK"
    ds.put("dev1", {"tts": {"entry": vc.ENTRY, "voice": v["voice_id"]}}, MERGED)
    conn = type("C", (), {"device_id": "dev1", "config": {**MERGED, "TTS": dict(MERGED["TTS"]), "selected_module": dict(MERGED["selected_module"])}})()
    assert "复刻的声音" in ds.apply(conn)
    assert conn.config["selected_module"]["TTS"] == vc.ENTRY
    assert conn.config["TTS"][vc.ENTRY] == {"type": "voice_clone", "voice": v["voice_id"], "model": api.TARGET_MODEL,
                                            "api_key": "sk-bailian-1234"}
    assert run(vc.speak(MERGED, v["voice_id"], "你好")) == b"RIFF\xe4\xbd\xa0\xe5\xa5\xbd"


def test_bad_recordings_and_names(fake):
    with pytest.raises(ValueError, match="10–20 秒"):
        run(vc.create(MERGED, "爸爸", wav(3)))
    with pytest.raises(ValueError, match="只认"):
        run(vc.create(MERGED, "爸爸", b"not audio at all"))
    with pytest.raises(ValueError, match="起个名字"):
        run(vc.create(MERGED, "  ", wav(15)))
    run(vc.create(MERGED, "爸爸", b"ID3" + b"\0" * 2000))          # mp3：时长交给百炼判断
    with pytest.raises(ValueError, match="已经有"):
        run(vc.create(MERGED, "爸爸", wav(15)))
    with pytest.raises(ValueError, match="API Key"):
        run(vc.create({"TTS": {}}, "奶奶", wav(15)))


def test_delete_and_gone_voice_falls_back(fake):
    v = run(vc.create(MERGED, "奶奶", wav(12)))
    fake["status"] = "OK"
    run(vc.refresh(MERGED))
    ds.put("dev1", {"tts": {"entry": vc.ENTRY, "voice": v["voice_id"]}}, MERGED)
    run(vc.delete(MERGED, v["voice_id"]))
    assert fake["deleted"] == [v["voice_id"]] and vc.listing() == []
    conn = type("C", (), {"device_id": "dev1", "config": {**MERGED, "selected_module": dict(MERGED["selected_module"])}})()
    ds.apply(conn)
    assert conn.config["selected_module"]["TTS"] == "EdgeTTS"      # 声音没了：照旧用网关的
    with pytest.raises(LookupError):
        run(vc.delete(MERGED, "nope"))


def test_provider_reads_its_config():
    from core.providers.tts import voice_clone as provider
    p = provider.TTSProvider({"voice": "cosyvoice-v3-flash-xm1-abc", "api_key": "sk-x", "model": "cosyvoice-v3-flash"}, True)
    assert (p.voice, p.api_key, p.model, p.audio_file_type) == ("cosyvoice-v3-flash-xm1-abc", "sk-x", "cosyvoice-v3-flash", "wav")


def test_api_errors_are_readable():
    assert api._error({"code": "InvalidApiKey"}, 401) == "百炼的 API Key 不对"
    with pytest.raises(api.CloneError, match="API Key"):
        api._headers("你的api_key")
