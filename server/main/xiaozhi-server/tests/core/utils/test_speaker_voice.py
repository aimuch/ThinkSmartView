"""家人声音认出孩子时换童声（core/utils/device_prefs.speaker_voice）。"""
import json
import types

from core.utils import device_prefs


def _conn(voice="zh-CN-XiaoxiaoNeural"):
    tts = type("EdgeTTS", (), {"__module__": "core.providers.tts.edge"})()
    tts.voice = voice
    return types.SimpleNamespace(tts=tts, device_id="dev-1")


def test_child_gets_the_child_voice_and_adult_gets_it_back(tmp_path, monkeypatch):
    monkeypatch.setattr(device_prefs, "_path", lambda: str(tmp_path / "p.json"))
    c = _conn()
    assert device_prefs.speaker_voice(c, "小宝（孩子）")
    assert c.tts.voice == device_prefs.KIDS_VOICE_EDGE
    assert not device_prefs.speaker_voice(c, "小宝（孩子）")        # 已经是童声
    assert device_prefs.speaker_voice(c, "妈妈")
    assert c.tts.voice == "zh-CN-XiaoxiaoNeural"


def test_adult_voice_chosen_in_kids_settings_is_kept(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"dev-1": {"kids": {"enabled": False, "voice": "adult"}}}))
    monkeypatch.setattr(device_prefs, "_path", lambda: str(path))
    c = _conn()
    assert not device_prefs.speaker_voice(c, "小宝（孩子）")
    assert c.tts.voice == "zh-CN-XiaoxiaoNeural"


def test_other_tts_engines_are_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(device_prefs, "_path", lambda: str(tmp_path / "p.json"))
    tts = type("QwenTTS", (), {"__module__": "core.providers.tts.qwen3_tts"})()
    tts.voice = "Cherry"
    c = types.SimpleNamespace(tts=tts, device_id="dev-1")
    assert not device_prefs.speaker_voice(c, "小宝（孩子）")
