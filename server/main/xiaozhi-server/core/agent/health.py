"""[xiaozhi-hub] 状态页的记录（设计画布第 68 块）：每小时各服务成没成功、说完到开始回话多久、每小时在线几台、最近发生的事。

都存在 data/health.json，留 7 天，不出家里的网络。记录在内存里攒着，最多 30 秒写一次盘（重启最多丢半分钟的）。
大模型的成败不在这里记：费用页的 usage.db 每次调用都有 ok，状态页直接从那里算。
"""

import json
import os
import statistics
import threading
import time

from config.config_loader import get_project_dir

KEEP = 7 * 86400
FLUSH_EVERY = 30
KINDS = ("asr", "tts", "ext")
MAX_EVENTS = 300
MAX_LAT = 400          # 每小时最多留这么多个耗时
MAX_TURNS = 2000       # 每段耗时最多留这么多次对话（设计画布第 78 块）
STAGES = ("asr", "prep", "llm", "tts")

_lock = threading.RLock()
_d = None
_dirty = 0.0
_flushed = 0.0
_timer = None


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "health.json")


def _hour(t=None) -> int:
    t = time.time() if t is None else t
    return int(t // 3600) * 3600


def _load() -> dict:
    global _d
    if _d is None:
        try:
            with open(_path(), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        for k in ("hours", "lat", "online", "flags"):
            d.setdefault(k, {})
        d.setdefault("events", [])
        d.setdefault("turns", [])
        _d = d
    return _d


def _prune(d: dict):
    cut = _hour() - KEEP
    for k in ("hours", "lat", "online"):
        for h in [h for h in d[k] if int(h) < cut]:
            del d[k][h]
    d["events"] = [e for e in d["events"] if e.get("t", 0) >= time.time() - KEEP][-MAX_EVENTS:]
    d["turns"] = [x for x in d.get("turns") or [] if x[0] >= time.time() - KEEP][-MAX_TURNS:]


def flush(force: bool = False):
    global _dirty, _flushed
    with _lock:
        if not _dirty or (not force and time.time() - _flushed < FLUSH_EVERY):
            return
        d = _load()
        _prune(d)
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, _path())
        except OSError:
            return
        _dirty, _flushed = 0.0, time.time()


def _touch():
    """记了新东西：离上次写盘满 30 秒就写；不满的话 30 秒后补写一次（之后没有新记录也不会丢在内存里）。"""
    global _dirty, _timer
    _dirty = _dirty or time.time()
    flush()
    if _dirty and _timer is None:
        def later():
            global _timer
            _timer = None
            flush(force=True)
        _timer = threading.Timer(FLUSH_EVERY, later)
        _timer.daemon = True
        _timer.start()


def record(kind: str, ok: bool, t: float = None):
    """一次识别（asr）、一段合成（tts）、一次外部服务（ext）成没成功。不会抛异常。"""
    if kind not in KINDS:
        return
    try:
        with _lock:
            h = _load()["hours"].setdefault(str(_hour(t)), {})
            c = h.setdefault(kind, [0, 0])
            c[0 if ok else 1] += 1
            _touch()
    except Exception:  # noqa: BLE001 —— 记录出问题不能影响对话
        pass


def latency(ms: float, device: str = "", t: float = None):
    """说完到开始回话（第一段语音发出去）多少毫秒。"""
    if ms is None or ms < 0 or ms > 120000:
        return
    try:
        with _lock:
            xs = _load()["lat"].setdefault(str(_hour(t)), [])
            if len(xs) < MAX_LAT:
                xs.append(int(ms))
            _touch()
    except Exception:  # noqa: BLE001
        pass


def stages(marks: dict, now: float, device: str = "", t: float = None):
    """一次对话每段多少毫秒（设计画布第 78 块）。marks：said 说完、text 识别出字、llm 开始问大模型、first 说出第一句
    （都是 monotonic 秒），now 第一段语音发出去。没问大模型（本地命令）的大模型那段记 None，理解算到合成开始前。
    只记时间、设备，不记说了什么。"""
    try:
        said, text = marks.get("said"), marks.get("text")
        if said is None or text is None or now < said or now - said > 120:
            return
        ms = lambda a, b: max(0, round((b - a) * 1000))
        llm, first = marks.get("llm"), marks.get("first")
        if llm is not None and first is not None and text <= llm <= first <= now:
            row = [ms(said, text), ms(text, llm), ms(llm, first), ms(first, now)]
        else:
            row = [ms(said, text), ms(text, now), None, 0]
        with _lock:
            _load()["turns"].append([int(time.time() if t is None else t), device[-5:], *row, bool(marks.get("tool"))])
            _touch()
    except Exception:  # noqa: BLE001
        pass


def stage_view(since: float, recent: int = 8) -> dict:
    """since 以来每段的中位数、慢的时候（九成以内）、次数；最近几次的明细。"""
    with _lock:
        rows = [x for x in _load().get("turns") or [] if x[0] >= since]
    def pct(xs, q):
        xs = sorted(x for x in xs if x is not None)
        return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None
    out = {k: {"median": _median([r[2 + i] for r in rows if r[2 + i] is not None]), "p90": pct([r[2 + i] for r in rows], .9)}
           for i, k in enumerate(STAGES)}
    totals = [sum(v for v in r[2:6] if v is not None) for r in rows]
    return {"n": len(rows), "local": sum(1 for r in rows if r[4] is None), "tool": sum(1 for r in rows if r[6]),
            "stages": out, "total": {"median": _median(totals), "p90": pct(totals, .9)},
            "recent": [{"t": r[0], "device": r[1], "asr": r[2], "prep": r[3], "llm": r[4], "tts": r[5], "tool": r[6]} for r in rows[-recent:]][::-1]}


def online(device: str, t: float = None):
    """这一小时这台设备在线过（心跳或对话时调）。"""
    if not device:
        return
    try:
        with _lock:
            xs = _load()["online"].setdefault(str(_hour(t)), [])
            if device not in xs:
                xs.append(device)
                _touch()
    except Exception:  # noqa: BLE001
        pass


def event(kind: str, text: str, device: str = "", t: float = None, key: str = ""):
    """最近发生的事：kind 是 start / online / offline / update / error / ok / album 之类，text 是一句话。
    key：同一件事只记一次（比如同一个服务连续出错），直到用 clear_flag(key) 清掉。"""
    try:
        with _lock:
            d = _load()
            if key:
                if d["flags"].get(key):
                    return
                d["flags"][key] = int(time.time())
            d["events"].append({"t": int(t or time.time()), "kind": kind, "text": str(text)[:120], "device": device})
            d["events"] = d["events"][-MAX_EVENTS:]
            _touch()
    except Exception:  # noqa: BLE001
        pass


def clear_flag(key: str) -> bool:
    """清掉 event(key=...) 的记号；原来有记号返回 True（用来记「恢复了」）。"""
    with _lock:
        d = _load()
        if d["flags"].pop(key, None) is None:
            return False
        _touch()
        return True


def flag(key: str):
    with _lock:
        return _load()["flags"].get(key)


def _median(xs):
    return int(statistics.median(xs)) if xs else None


def view(since: int, hours: int = 24, llm_rows=()) -> dict:
    """状态页要的：since 起每小时的 [成, 败]（识别、大模型、合成、外部服务）、回话耗时中位数、在线台数，最近的事。"""
    with _lock:
        d = _load()
        rows = {k: [] for k in ("asr", "llm", "tts", "ext")}
        lat, onl = [], []
        for i in range(hours):
            h = str(since + i * 3600)
            hh = d["hours"].get(h) or {}
            for k in KINDS:
                rows[k].append(list(hh.get(k) or [0, 0]))
            rows["llm"].append([0, 0])
            lat.append(_median(d["lat"].get(h) or []))
            onl.append(len(d["online"].get(h) or []))
        for r in llm_rows:
            i = int((r["ts"] - since) // 3600)
            if 0 <= i < hours:
                rows["llm"][i][0 if r.get("ok", 1) else 1] += 1
        now = time.time()
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        today = [x for h, xs in d["lat"].items() if int(h) >= midnight for x in xs]
        yday = [x for h, xs in d["lat"].items() if midnight - 86400 <= int(h) < midnight for x in xs]
        events = sorted(reversed(d["events"]), key=lambda e: e["t"], reverse=True)[:30]   # 同一秒的新的在前
    return {"since": since, "rows": rows, "latency": {"hours": lat, "today": _median(today), "yesterday": _median(yday), "n": len(today)},
            "online": onl, "events": events}
