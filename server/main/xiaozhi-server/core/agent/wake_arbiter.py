"""[xiaozhi-hub] 就近唤醒（设计画布第 55 块）：几台小麦同时听到唤醒词时，只让离得最近的那台回答。

设备听到唤醒词先不响，把听到的音量（唤醒词那一段最响 0.5 秒的 dBFS）报到 /kiosk/wake/claim；网关等 window 秒
收齐这一声的报告，选音量最大的那台回 win，其他回 lose（不出声，回到待机）。窗口关了以后 LATE 秒内才到的报告
算同一声（比如那台设备慢了一点），直接 lose。都在内存里；设置存在 data/wake_arbiter.json。
"""
import asyncio
import json
import os
import threading
import time
from collections import deque

from config.config_loader import get_project_dir
from core.utils.atomic_file import write_json

WINDOWS = (0.2, 0.3, 0.5)
LATE = 1.5
DEFAULTS = {"enabled": True, "window": 0.3}


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "wake_arbiter.json")


def load() -> dict:
    d = dict(DEFAULTS)
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw.get("enabled"), bool):
            d["enabled"] = raw["enabled"]
        if raw.get("window") in WINDOWS:
            d["window"] = raw["window"]
    except (OSError, ValueError, AttributeError):
        pass
    return d


_lock = threading.Lock()   # 后台的保存在线程池里跑：读改写要串行，不然同时改开关和窗口时一个会丢


def save(enabled=None, window=None) -> dict:
    with _lock:
        d = load()
        if isinstance(enabled, bool):
            d["enabled"] = enabled
        if window in WINDOWS:
            d["window"] = window
        write_json(_path(), d)
    return d


class Arbiter:
    def __init__(self):
        self.round = None              # {"start", "claims": {device: score}, "decided": Event, "winner"}
        self.history = deque(maxlen=20)

    async def claim(self, device: str, score: float, window: float) -> dict:
        now = time.monotonic()
        r = self.round
        if r and r["decided"].is_set() and now - r["start"] > window + LATE:
            r = None                   # 上一声早就结束了：这是新的一声
        if r is None:
            r = self.round = {"start": now, "claims": {}, "decided": asyncio.Event(), "winner": None, "t": time.time()}
            asyncio.get_running_loop().call_later(window, self._decide, r)
        if r["decided"].is_set():      # 窗口关了才到：同一声，慢了一步
            if device == r["winner"]:
                return {"win": True, "winner": device}
            r["claims"].setdefault(device, score)
            return {"win": False, "winner": r["winner"], "late": True}
        r["claims"][device] = max(score, r["claims"].get(device, -200))
        try:
            await asyncio.wait_for(r["decided"].wait(), window + 1)
        except asyncio.TimeoutError:
            return {"win": True, "winner": device}
        return {"win": r["winner"] == device, "winner": r["winner"]}

    def _decide(self, r):
        # 一样响时先报的那台（dict 保持插入顺序）
        r["winner"] = max(r["claims"], key=lambda d: r["claims"][d]) if r["claims"] else None
        r["decided"].set()
        self.history.appendleft({"t": int(r["t"]), "winner": r["winner"], "claims": r["claims"]})   # 同一个 dict：慢一步的也记上
