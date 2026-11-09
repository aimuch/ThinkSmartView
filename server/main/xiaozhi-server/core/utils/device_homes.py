"""[xiaozhi-hub] 每台设备的住宅地址（音箱所在地）：设备设置页「天气播报」里按住说出来、确认后发过来。

存在 data/device_homes.json：{设备 ID: {"name", "latitude", "longitude", "updated"}}。
问天气没说地点时、系统提示里的「设备所在地」都用它；没设过的设备用后台「查天气 → 设置」里的设备所在地。
地点由设备决定（存在设备上，升级、换网关都不丢），设备每次启动会再发一次，这里只是网关这边的副本。
"""
import json
import os
import threading
import time

from config.config_loader import get_project_dir

_lock = threading.Lock()
_cache = {"mtime": None, "data": {}}


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "device_homes.json")


def _load() -> dict:
    path = _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache["mtime"] != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        _cache["mtime"], _cache["data"] = mtime, data if isinstance(data, dict) else {}
    return _cache["data"]


def get(device: str):
    """这台设备的住宅地址 {"name", "latitude", "longitude"}，没设过返回 None。"""
    if not device:
        return None
    with _lock:
        home = _load().get(device)
    if isinstance(home, dict) and home.get("name") and home.get("latitude") is not None:
        return home
    return None


def put(device: str, place: dict = None):
    """设成 place（{"name", "latitude", "longitude"}）；place 为 None 时删掉这台设备的。"""
    if not device:
        raise ValueError("缺少设备 ID")
    with _lock:
        data = dict(_load())
        if place:
            data[device] = {"name": str(place["name"]), "latitude": float(place["latitude"]),
                            "longitude": float(place["longitude"]), "updated": time.time()}
        else:
            data.pop(device, None)
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        _cache["mtime"] = None
