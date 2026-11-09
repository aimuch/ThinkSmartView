"""[xiaozhi-hub] 每台设备在设置页里改的、网关要知道的偏好（目前是儿童模式，设计画布第 10 块）。

存在 data/device_prefs.json：{设备 ID: {"kids": {"enabled", "age", "voice"}}}。设备是权威：每次启动、改设置时
发一份过来（/kiosk/prefs）。网关在这台设备的连接建立时读：儿童模式打开就给系统提示加一段儿童模式的要求，
Edge TTS 时换成童声（云夏）。时长、睡觉时间这些限制在设备上算，网关不管。
"""
import json
import os
import threading
import time

from config.config_loader import get_project_dir

KIDS_VOICE_EDGE = "zh-CN-YunxiaNeural"   # Edge TTS 的童声
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "device_prefs.json")


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def kids(device: str):
    """这台设备的儿童模式 {"enabled", "age", "voice"}；没开返回 None。"""
    if not device:
        return None
    with _lock:
        k = (_load().get(device) or {}).get("kids") or {}
    return k if k.get("enabled") else None


def put_kids(device: str, value: dict):
    if not device:
        raise ValueError("缺少设备 ID")
    v = value or {}
    age = int(v.get("age") or 3)
    if not 1 <= age <= 14:
        raise ValueError("年龄要在 1 到 14 岁之间")
    voice = v.get("voice") or "child"
    if voice not in ("child", "adult"):
        raise ValueError("voice 只能是 child 或 adult")
    with _lock:
        data = _load()
        data.setdefault(device, {})["kids"] = {"enabled": bool(v.get("enabled")), "age": age, "voice": voice,
                                              "updated": time.time()}
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def apply_voice(tts, device: str):
    """儿童模式要童声时，把这个连接的 Edge TTS 换成童声（其他引擎不动）。"""
    k = kids(device)
    if k and k.get("voice") == "child" and type(tts).__module__.endswith(".edge") and hasattr(tts, "voice"):
        tts.voice = KIDS_VOICE_EDGE
        return True
    return False
