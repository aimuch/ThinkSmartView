"""[xiaozhi-hub] 认人打招呼（设计画布第 57 块）：喊「小麦小麦」时听出是家里的谁，用他的称呼念一句再开始听。

设备把唤醒词那几秒的声音送到 /kiosk/wake/greet（和复核唤醒词同时），这里用「家人声音」（core/utils/local_voiceprint.py）
认人。唤醒词只有 1 秒多，比平时认人的一句话短，所以要够像（≥ 严格程度对应的分数，LEVELS）、又要比第二像的人明显更像（≥ MARGIN），
才算认出；否则设备照旧只「叮」。叫错名字比不叫更糟。

说法：孩子一套、大人一套（一行一句，随机念一句，{称呼} 换成这个人的称呼）；每个人可以单独写、可以关掉。
存在 data/greetings.json：{"enabled", "child": [...], "adult": [...], "people": {名字: {"call", "lines", "off"}}}。
最近几次怎么认的、念没念（设备开始听时告诉网关，listen 消息里的 greet_id / greet_said）只在内存里。
"""
import json
import os
import random
import secrets
import threading
import time
from collections import deque

from config.config_loader import get_project_dir

# 严格程度：最像的那个人要多像才叫名字。用合成的几种声音试过（唤醒词 1 秒左右）：录过的人 0.75～0.85，
# 没录过的大人 0.35～0.50，没录过的别的孩子可能到 0.63～0.70（孩子的声音彼此像）。家里常有别的孩子来就用「严格」。
LEVELS = {"loose": 0.55, "normal": 0.65, "strict": 0.72}
THRESHOLD = LEVELS["normal"]
MARGIN = 0.06
MAX_LINES = 5
MAX_CHARS = 30
DEFAULTS = {
    "enabled": True,
    "level": "normal",
    "child": ["你好呀，{称呼}！想聊什么？", "{称呼}，我在呢", "嗨，{称呼}，今天开心吗？"],
    "adult": ["我在，{称呼}", "{称呼}，你说"],
    "people": {},
}

_lock = threading.Lock()
history = deque(maxlen=20)       # {"id", "t", "device", "speaker", "score", "second", "text", "said"}


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "greetings.json")


def _lines(v) -> list:
    if isinstance(v, str):
        v = v.split("\n")
    out = []
    for x in v if isinstance(v, list) else []:
        x = str(x).strip()[:MAX_CHARS]
        if x and x not in out:
            out.append(x)
    return out[:MAX_LINES]


def load() -> dict:
    d = json.loads(json.dumps(DEFAULTS))
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return d
    if not isinstance(raw, dict):
        return d
    if isinstance(raw.get("enabled"), bool):
        d["enabled"] = raw["enabled"]
    if raw.get("level") in LEVELS:
        d["level"] = raw["level"]
    for k in ("child", "adult"):
        if k in raw:
            d[k] = _lines(raw[k])
    for name, p in (raw.get("people") or {}).items():
        if isinstance(p, dict):
            d["people"][str(name)[:12]] = {"call": str(p.get("call") or "").strip()[:12], "lines": _lines(p.get("lines")),
                                          "off": bool(p.get("off"))}
    return d


def save(body: dict) -> dict:
    """整份存（页面上改完一起保存）。"""
    with _lock:
        d = load()
        if isinstance(body.get("enabled"), bool):
            d["enabled"] = body["enabled"]
        if body.get("level") in LEVELS:
            d["level"] = body["level"]
        for k in ("child", "adult"):
            if k in body:
                d[k] = _lines(body[k])
        if isinstance(body.get("people"), dict):
            d["people"] = {}
            for name, p in body["people"].items():
                if isinstance(p, dict):
                    d["people"][str(name)[:12]] = {"call": str(p.get("call") or "").strip()[:12],
                                                  "lines": _lines(p.get("lines")), "off": bool(p.get("off"))}
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path())
        return d


def call_of(name: str, cfg: dict) -> str:
    return ((cfg["people"].get(name) or {}).get("call") or name).strip()


def lines_for(name: str, child: bool, cfg: dict) -> list:
    """这个人的说法（已经换好称呼）；关掉了返回 []。"""
    p = cfg["people"].get(name) or {}
    if p.get("off"):
        return []
    raw = p.get("lines") or cfg["child" if child else "adult"]
    call = call_of(name, cfg)
    return [x.replace("{称呼}", call) for x in raw]


def all_lines(cfg: dict, people: list) -> list:
    """全家要用到的每一句（预先合成用）。"""
    out = []
    for p in people:
        for x in lines_for(p["name"], p["child"], cfg):
            if x not in out:
                out.append(x)
    return out


def decide(device: str, ranked: list, cfg: dict = None) -> dict:
    """ranked 是 local_voiceprint.rank 的结果。返回 {"id", "speaker", "child", "score", "second", "text"}；
    没认出、没把握、关掉了时 text 是空的。都记进 history。"""
    cfg = cfg or load()
    top = ranked[0] if ranked else None
    second = ranked[1][1] if len(ranked) > 1 else None
    entry = {"id": secrets.token_hex(4), "t": int(time.time()), "device": device, "speaker": None, "child": False,
             "score": round(top[1], 2) if top else None, "best": top[0] if top else None,
             "second": round(second, 2) if second is not None else None, "text": "", "said": None, "why": ""}
    if not top:
        entry["why"] = "声音太短或家里还没录声音"
    elif top[1] < LEVELS.get(cfg.get("level"), THRESHOLD):
        entry["why"] = "没认出"
    elif second is not None and top[1] - second < MARGIN:
        entry["why"] = f"和{ranked[1][0]}分不太清"
    else:
        entry.update(speaker=top[0], child=bool(top[2]))
        lines = lines_for(top[0], top[2], cfg)
        if not cfg["enabled"]:
            entry["why"] = "打招呼关着"
        elif not lines:
            entry["why"] = "这个人设成只「叮」"
        else:
            entry["text"] = random.choice(lines)
    history.appendleft(entry)
    return entry


def mark(greet_id: str, said: bool):
    """设备开始听时告诉的：这一句念了没有（喊完唤醒词接着就问的话不念）。"""
    for e in history:
        if e["id"] == greet_id:
            e["said"] = bool(said)
            return e
    return None


def speech_part(x, rate: int = 16000):
    """唤醒词那几秒里真有声音的一段（比最响的 20 毫秒小 30 dB 以内的，从头一帧到最后一帧，前后各留 0.15 秒）：
    大段的安静不算进声纹。"""
    import numpy as np

    n = rate // 50
    frames = len(x) // n
    if frames < 5:
        return x
    db = 10 * np.log10(np.mean(x[:frames * n].reshape(frames, n) ** 2, axis=1) + 1e-12)
    loud = np.nonzero(db > db.max() - 30)[0]
    a, b = max(0, int(loud[0]) - 8), min(frames, int(loud[-1]) + 9)     # 前后各留 0.15 秒
    return x[a * n:b * n]
