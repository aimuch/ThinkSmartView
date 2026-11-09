"""[xiaozhi-hub] 每台设备在网关后台「设备」页单独设的东西（设计画布第 19 块）：名字、默认模型、音色、人设、插件。

存在 data/device_settings.json：{设备 ID: {"name", "model", "tts": {"entry", "voice"}, "prompt", "plugins": [...]}}。
某一项没有（或是 None）就是「跟着全局」。

生效：设备每次叫醒都重连网关。连接建立时 apply(conn) 改这个连接自己的那份配置（conn.config 是复制出来的）：
人设 → prompt；音色 → selected_module.TTS 和那个条目的 voice；插件 → Intent.function_call.functions。
默认模型不在这里套：保存时写进这台设备「记住的模型」（llm_router 的 store），和叫名字换模型是同一个机制。
语音识别不按设备分：本地识别模型所有设备共用一个实例。
"""

import json
import os
import threading
import time

from config.config_loader import get_project_dir

NAME_MAX = 16
PROMPT_MAX = 2000
_lock = threading.Lock()

# 人设模板（抽屉里点一下填进去，可以再改）
PERSONAS = [
    {"id": "teacher", "label": "小老师",
     "text": "你叫小麦，是一个耐心的小老师，陪孩子聊天、回答问题。用孩子听得懂的短句，一次只讲一点，多鼓励、多反问，"
             "讲知识时举身边的例子。不讲吓人的内容，涉及安全的事提醒孩子找大人。"},
    {"id": "sister", "label": "温柔的姐姐",
     "text": "你叫小麦，说话温柔、有耐心，像一个贴心的姐姐。先回应对方的感受，再给建议；回答简短，不说教。"},
    {"id": "brief", "label": "简洁助手",
     "text": "你叫小麦，是一个简洁、高效的语音助手。直接给结论，一两句话说完，不寒暄，不追问。"},
    {"id": "elder", "label": "陪老人",
     "text": "你叫小麦，陪家里的老人聊天。说话慢一点、清楚一点，用常见的说法，不用网络用语和英文；"
             "老人问身体方面的事，提醒他告诉家人或看医生。"},
]


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "device_settings.json")


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get(device: str) -> dict:
    if not device:
        return {}
    with _lock:
        return dict(_load().get(device) or {})


def all_devices() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _load().items() if isinstance(v, dict)}


def name(device: str) -> str:
    return str(get(device).get("name") or "")


def put(device: str, values: dict, merged: dict) -> dict:
    """校验并保存这台设备的设置（values 里给了的项才改；值为 None 表示改回「跟着全局」）。返回保存后的。"""
    from core.admin import catalog

    if not device or len(device) > 64:
        raise ValueError("设备 ID 不对")
    v = values or {}
    with _lock:
        data = _load()
        cur = dict(data.get(device) or {})
        if "name" in v:
            n = " ".join(str(v.get("name") or "").split())
            if len(n) > NAME_MAX:
                raise ValueError(f"名字最多 {NAME_MAX} 个字")
            cur["name"] = n
        if "model" in v:
            m = v.get("model")
            if m is not None:
                from core.agent import llm_router
                if not llm_router.model_ready(merged, str(m)):
                    raise ValueError("这个模型还没配置好，先在「大模型」或「智能体」页配好")
            cur["model"] = m
        if "tts" in v:
            t = v.get("tts")
            if t is not None:
                entry, voice = str(t.get("entry") or ""), str(t.get("voice") or "").strip()
                engine = next((e for e in catalog.TTS_ENGINES if e["entry"] == entry), None)
                conf = (merged.get("TTS") or {}).get(entry)
                if engine is None or not isinstance(conf, dict):
                    raise ValueError("这个语音合成还没配置，先在「语音合成」页配好")
                if engine.get("voices") and voice not in {x["value"] for x in engine["voices"]}:
                    raise ValueError("没有这个音色")
                if not voice or len(voice) > 80 or any(c.isspace() for c in voice):
                    raise ValueError("音色不对")
                t = {"entry": entry, "voice": voice}
            cur["tts"] = t
        if "prompt" in v:
            p = v.get("prompt")
            if p is not None:
                p = str(p).strip()
                if not p:
                    raise ValueError("人设不能是空的；想用全局的就选「跟着全局」")
                if len(p) > PROMPT_MAX:
                    raise ValueError(f"人设最多 {PROMPT_MAX} 个字")
            cur["prompt"] = p
        if "plugins" in v:
            pl = v.get("plugins")
            if pl is not None:
                if not isinstance(pl, list):
                    raise ValueError("插件格式不对")
                pl = [str(x) for x in pl if str(x) in catalog.PLUGIN_NAMES]
            cur["plugins"] = pl
        cur = {k: x for k, x in cur.items() if x not in (None, "")}
        cur["updated"] = time.time()
        if set(cur) == {"updated"}:
            data.pop(device, None)
        else:
            data[device] = cur
        _save(data)
    return cur


def forget(device: str) -> None:
    with _lock:
        data = _load()
        if data.pop(device, None) is not None:
            _save(data)


def apply(conn) -> list:
    """连接建立时：把这台设备单独设的人设、音色、插件套到这个连接自己的配置上。返回套了哪些（写日志用）。"""
    s = get(getattr(conn, "device_id", None) or "")
    if not s:
        return []
    cfg, done = conn.config, []
    if s.get("prompt"):
        cfg["prompt"] = s["prompt"]
        done.append("人设")
    t = s.get("tts")
    if t and isinstance((cfg.get("TTS") or {}).get(t["entry"]), dict):
        cfg.setdefault("selected_module", {})["TTS"] = t["entry"]
        cfg["TTS"][t["entry"]] = {**cfg["TTS"][t["entry"]], "voice": t["voice"]}
        done.append(f"音色 {t['voice']}")
    if s.get("plugins") is not None and isinstance((cfg.get("Intent") or {}).get("function_call"), dict):
        from core.admin import catalog

        fc = cfg["Intent"]["function_call"]
        extra = [f for f in fc.get("functions") or [] if f not in catalog.PLUGIN_NAMES]   # 后台不管的插件照旧留着
        cfg["Intent"]["function_call"] = {**fc, "functions": list(s["plugins"]) + extra}
        done.append(f"插件 {len(s['plugins'])} 个")
    return done
