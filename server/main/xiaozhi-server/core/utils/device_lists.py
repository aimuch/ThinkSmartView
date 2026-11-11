"""[xiaozhi-hub] 清单（购物清单、待办；设计画布第 11 块）。存在网关上（data/device_lists.json），这样手机在超市里
打开 /lists/<令牌> 划掉的，设备上马上能看到。每台设备一个令牌（随机 128 位），设置页「手机看」出它的二维码。

{设备: {"secret", "version", "lists": {清单名: [{"text", "done"}]}}}
"""
import json
import os
import secrets
import threading

from config.config_loader import get_project_dir

DEFAULT_LISTS = ("购物清单", "待办")
MAX_ITEMS = 100
_lock = threading.Lock()


def _path():
    return os.path.join(get_project_dir(), "data", "device_lists.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _entry(data, device):
    e = data.setdefault(device, {})
    e.setdefault("secret", secrets.token_urlsafe(16))
    e.setdefault("version", 0)
    lists = e.setdefault("lists", {})
    for name in DEFAULT_LISTS:
        lists.setdefault(name, [])
    return e


def view(device):
    if not device:
        raise ValueError("缺少设备 ID")
    with _lock:
        data = _load()
        e = _entry(data, device)
        _save(data)
        return {"secret": e["secret"], "version": e["version"], "lists": e["lists"]}


def count(device) -> int:
    """这台设备清单里一共几项（只读，不像 view 那样顺手建出默认清单）。"""
    with _lock:
        e = _load().get(device) or {}
    return sum(len(v or []) for v in (e.get("lists") or {}).values())


def forget(device) -> None:
    """删除设备时用：这台设备的清单（连同手机分享链接的密钥）整条删掉。"""
    with _lock:
        data = _load()
        if data.pop(device, None) is not None:
            _save(data)


def by_secret(secret):
    with _lock:
        for device, e in _load().items():
            if secret and secrets.compare_digest(str(e.get("secret") or ""), secret):
                return device
    return None


def _norm(t):
    return "".join(str(t or "").split())


def change(device, op, name, items=(), done=None):
    """op: add / remove / check / clear_done. Returns the whole view."""
    name = str(name or "").strip() or "购物清单"
    if len(name) > 20:
        raise ValueError("清单名字太长")
    items = [str(i).strip()[:60] for i in (items or []) if str(i).strip()]
    with _lock:
        data = _load()
        e = _entry(data, device)
        lst = e["lists"].setdefault(name, [])
        if op == "add":
            have = {_norm(x["text"]) for x in lst}
            for it in items:
                if _norm(it) not in have:
                    lst.append({"text": it, "done": False})
                    have.add(_norm(it))
            if len(lst) > MAX_ITEMS:
                raise ValueError(f"一个清单最多 {MAX_ITEMS} 项")
        elif op == "remove":
            gone = {_norm(i) for i in items}
            e["lists"][name] = [x for x in lst if _norm(x["text"]) not in gone]
        elif op == "check":
            want = {_norm(i) for i in items}
            for x in lst:
                if _norm(x["text"]) in want:
                    x["done"] = True if done is None else bool(done)
        elif op == "clear_done":
            e["lists"][name] = [x for x in lst if not x["done"]]
        elif op == "delete_list":
            if name in DEFAULT_LISTS:
                raise ValueError("默认的清单不能删")
            e["lists"].pop(name, None)
        else:
            raise ValueError("不支持的操作")
        e["version"] += 1
        _save(data)
        return {"secret": e["secret"], "version": e["version"], "lists": e["lists"]}
