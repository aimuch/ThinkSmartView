"""[xiaozhi-hub] 设备的健康状况和屏幕截图（设计画布第 83 块）。

设备每分钟 POST /kiosk/health 报一份（health.py）：只留每台最新的一份，存 data/device_health.json（网关重启后
后台还看得到最后一份）。后台「设备」页点「截一张」→ request_screenshot 记下 → 设备下次查 /kiosk/reports（10 秒内）
拿到 screenshot: true → 截好 POST /kiosk/screenshot → 存 data/screens/<设备>.png，只留最新一张。
"""
import os
import re
import threading
import time

from config.config_loader import get_project_dir
from core.utils.atomic_file import write_bytes, write_json

MAX_REPORT = 64 * 1024          # 一份报告最多这么大（JSON 字节）
MAX_SHOT = 8 * 1024 * 1024
PENDING_SECONDS = 60            # 点了「截一张」多久内设备来拿算数
_lock = threading.Lock()
_reports = None                 # device -> 最新一份（带 at）
_pending = {}                   # device -> 点「截一张」的时间
_shot_errors = {}               # device -> (时间, 为什么没截成)


def _dir():
    return os.path.join(get_project_dir(), "data")


def _path():
    return os.path.join(_dir(), "device_health.json")


def _safe(device: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", device)[:64]


def shot_path(device: str) -> str:
    return os.path.join(_dir(), "screens", _safe(device) + ".png")


def _load():
    global _reports
    if _reports is None:
        try:
            import json
            with open(_path(), encoding="utf-8") as f:
                data = json.load(f)
            _reports = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _reports = {}
    return _reports


def put(device: str, report: dict):
    if not device or not isinstance(report, dict):
        return
    with _lock:
        reports = _load()
        reports[device] = {**report, "at": round(time.time(), 1)}
        write_json(_path(), reports, ensure_ascii=False)


def request_screenshot(device: str):
    with _lock:
        _pending[device] = time.time()
        _shot_errors.pop(device, None)


def take_pending(device: str) -> bool:
    """设备来查待汇报时：要不要截一张（拿走就清掉）。"""
    with _lock:
        at = _pending.pop(device, None)
    return at is not None and time.time() - at < PENDING_SECONDS


def save_screenshot(device: str, png: bytes):
    write_bytes(shot_path(device), png)


def shot_failed(device: str, why: str):
    with _lock:
        _shot_errors[device] = (time.time(), str(why)[:200])


def view() -> dict:
    """{device: {report…, at, screenshot_at, screenshot_error, pending}}。"""
    with _lock:
        reports = dict(_load())
        pending = dict(_pending)
        errors = dict(_shot_errors)
    out = {}
    for device in set(reports) | set(pending) | set(errors):
        item = dict(reports.get(device) or {})
        try:
            item["screenshot_at"] = round(os.path.getmtime(shot_path(device)), 1)
        except OSError:
            item["screenshot_at"] = None
        if device in errors:
            item["screenshot_error"] = {"at": round(errors[device][0], 1), "text": errors[device][1]}
        item["pending"] = device in pending and time.time() - pending[device] < PENDING_SECONDS
        out[device] = item
    return out


TROUBLE_SECONDS = 3600          # 一小时内出过错才算（侧栏心跳的黄点，设计画布第 83 块）


def trouble(device: str, now=None) -> str:
    """这台设备最近出过错：一句话（常驻循环重启、一小时内的错误）；没事是空字符串。"""
    now = now or time.time()
    with _lock:
        r = dict(_load().get(device) or {})
    if not r or now - r.get("at", 0) > 3 * 3600:
        return ""                # 很久没报了：在不在线由心跳管
    parts = []
    restarts = {k: n for k, n in (r.get("restarts") or {}).items() if n}
    if restarts:
        parts.append("常驻循环重启 " + "、".join(f"{k} {n} 次" for k, n in restarts.items()))
    errs = [e for e in r.get("errors") or [] if e.get("level") in ("error", "critical") and now - (e.get("t") or 0) < TROUBLE_SECONDS]
    if errs:
        parts.append(f"一小时内 {len(errs)} 条错误：{errs[-1].get('msg', '')[:60]}")
    return "；".join(parts)
