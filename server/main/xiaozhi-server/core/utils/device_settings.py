"""[xiaozhi-hub] 每台设备在网关后台「设备」页单独设的东西（设计画布第 19 块）：名字、默认模型、音色、人设、插件。

存在 data/device_settings.json：{设备 ID: {"name", "model", "tts": {"entry", "voice"}, "prompt", "plugins": {插件: true/false}}}。
某一项没有（或是 None）就是「跟着全局」。

插件（设计画布第 40 块）：只记这台改过的那几个，true 是这台开、false 是这台关，没记的跟着全局。
旧版存的是整份清单（list），读的时候按当时的全局算出差异，下次保存换成新格式。

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


def global_plugins(config: dict) -> list:
    """全局的插件列表（意图识别 function_call 的 functions，含后台不管的）。"""
    return [str(f) for f in (((config.get("Intent") or {}).get("function_call") or {}).get("functions") or [])]


def overrides(own, global_list) -> dict:
    """这台设备改过的插件 {插件: 开/关}，按后台的插件顺序。own 是存的值（None、新格式 dict、旧格式 list）。"""
    from core.admin import catalog

    names = [p["name"] for p in catalog.PLUGINS]
    if isinstance(own, dict):
        return {n: bool(own[n]) for n in names if n in own}
    if isinstance(own, list):
        glob = set(global_list or [])
        return {n: n in own for n in names if (n in own) != (n in glob)}
    return {}


def effective(global_list, ov: dict) -> list:
    """全局列表套上这台的改动：关掉的去掉，开着的补在后面。"""
    out = [f for f in global_list or [] if ov.get(f, True)]
    return out + [n for n, on in ov.items() if on and n not in out]


def plugins_for(config: dict, device: str) -> list:
    """这台设备实际用的插件。"""
    glob = global_plugins(config)
    return effective(glob, overrides(get(device).get("plugins"), glob)) if device else glob


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
            if t is not None and str(t.get("entry") or "") == "CloneTTS":   # 复刻的声音（设计画布第 50 块）
                from core.agent import voice_clone
                v = voice_clone.find(str(t.get("voice") or ""))
                if not v:
                    raise ValueError("没有这个复刻的声音")
                if v["status"] != "OK":
                    raise ValueError("这个声音还没复刻好")
                t = {"entry": voice_clone.ENTRY, "voice": v["voice_id"]}
            elif t is not None:
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
        glob = global_plugins(merged)
        if "plugins" in v:
            pl = v.get("plugins")
            if isinstance(pl, list):        # 旧格式：整份清单，换成和全局的差异
                pl = overrides([str(x) for x in pl], glob)
            elif isinstance(pl, dict):
                if any(not isinstance(x, bool) for x in pl.values()):
                    raise ValueError("插件格式不对")
                pl = overrides(pl, glob)
            elif pl is not None:
                raise ValueError("插件格式不对")
            cur["plugins"] = pl or None
        if "plugin" in v:                   # 只改一个：{"name", "state": "on" | "off" | null（跟着全局）}
            one = v.get("plugin") or {}
            pname, state = str(one.get("name") or ""), one.get("state")
            if pname not in catalog.PLUGIN_NAMES or state not in ("on", "off", None):
                raise ValueError("插件格式不对")
            ov = overrides(cur.get("plugins"), glob)
            if state is None:
                ov.pop(pname, None)
            else:
                ov[pname] = state == "on"
            cur["plugins"] = overrides(ov, glob) or None
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
    if t and t.get("entry") == "CloneTTS":
        from core.agent import voice_clone
        conf = voice_clone.provider_conf(cfg, t["voice"])
        if conf:
            cfg.setdefault("TTS", {})["CloneTTS"] = conf
            cfg.setdefault("selected_module", {})["TTS"] = "CloneTTS"
            done.append("复刻的声音")
    elif t and isinstance((cfg.get("TTS") or {}).get(t["entry"]), dict):
        cfg.setdefault("selected_module", {})["TTS"] = t["entry"]
        cfg["TTS"][t["entry"]] = {**cfg["TTS"][t["entry"]], "voice": t["voice"]}
        done.append(f"音色 {t['voice']}")
    if s.get("plugins") is not None and isinstance((cfg.get("Intent") or {}).get("function_call"), dict):
        fc = cfg["Intent"]["function_call"]
        glob = [str(f) for f in fc.get("functions") or []]
        ov = overrides(s["plugins"], glob)
        if ov:
            cfg["Intent"]["function_call"] = {**fc, "functions": effective(glob, ov)}   # 后台不管的插件照旧留着
            done.append(describe(ov))
    return done


def describe(ov: dict) -> str:
    """「插件 开 4 个 · 关 1 个」（日志、设备页的插件列）。"""
    on, off = sum(1 for x in ov.values() if x), sum(1 for x in ov.values() if not x)
    return "插件 " + " · ".join(t for t in (f"开 {on} 个" if on else "", f"关 {off} 个" if off else "") if t)
