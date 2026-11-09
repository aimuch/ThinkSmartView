"""[xiaozhi-hub] 设备的心跳：设备只在对话时连着 WebSocket，说完就断开，所以「连着」不等于「在线」。

设备客户端每 10 秒查一次 /kiosk/reports（待汇报），顺手报上版本号；这里记下最近一次的时间。
ONLINE_SECONDS 内有心跳算在线（待机），WebSocket 连着算对话中，都没有算离线。
只在内存里：网关刚重启的头 10 秒里会短暂显示离线。
"""

import threading
import time

ONLINE_SECONDS = 35      # 心跳 10 秒一次，漏两次还算在线
FORGET_SECONDS = 7 * 86400

_lock = threading.Lock()
_seen = {}   # device_id -> {"last": 时间戳, "version": 客户端版本}


def beat(device_id: str, version: str = "") -> None:
    if not device_id:
        return
    with _lock:
        item = _seen.setdefault(device_id, {})
        item["last"] = time.time()
        if version:
            item["version"] = str(version)[:20]


def snapshot(connected_ids=()) -> list:
    """全部见过的设备：[{"device_id", "state": talking|idle|offline, "last_seen", "version"}]，在线的在前。"""
    now = time.time()
    connected = set(connected_ids)
    with _lock:
        for k in [k for k, v in _seen.items() if now - v.get("last", 0) > FORGET_SECONDS and k not in connected]:
            del _seen[k]
        items = {k: dict(v) for k, v in _seen.items()}
    for k in connected:
        items.setdefault(k, {"last": now})
    out = []
    for k, v in items.items():
        state = "talking" if k in connected else "idle" if now - v.get("last", 0) <= ONLINE_SECONDS else "offline"
        out.append({"device_id": k, "state": state, "last_seen": int(v.get("last", 0)), "version": v.get("version", "")})
    order = {"talking": 0, "idle": 1, "offline": 2}
    return sorted(out, key=lambda d: (order[d["state"]], -d["last_seen"]))


def online_count(connected_ids=()) -> int:
    return sum(1 for d in snapshot(connected_ids) if d["state"] != "offline")


def forget(device_id: str) -> None:
    with _lock:
        _seen.pop(device_id, None)


def reset() -> None:
    with _lock:
        _seen.clear()
