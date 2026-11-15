"""[xiaozhi-hub] 清单（设计画布第 11、76 块）。存在网关上（data/device_lists.json），手机在超市里打开 /lists/<令牌> 划掉的，
设备上马上能看到。

两种范围：「全家」（所有设备和家人的手机看到的是同一份，默认的购物清单）和「只在这台」（只有这台设备、和扫它的码的手机
看得到，默认的待办）。每台设备一个令牌（随机 128 位），设置页「手机看」出它的二维码，扫了看到全家的 + 这台自己的。

{设备: {"secret", "version", "lists": {清单名: [项]}, "migrated": 1}, "__family__": {"version", "lists": {...}}}
项 {"id", "text", "done", "by", "at"，可选 "due"（YYYY-MM-DD）、"time"（HH:MM）、"repeat"（core.utils.recur 的规则）、
"remind" {"mode": none|show|ring, "before": [分钟], "where": [设备 | "all"]}、"last_done"、"times"（重复的做了几次）}。
重复的划掉时不消失：排到下一次，记下这次哪天做完的。
"""
import datetime
import json
import os
import secrets
import shutil
import threading
import time

from config.config_loader import get_project_dir
from core.utils import recur

FAMILY = "__family__"
FAMILY_LISTS = ("购物清单",)
DEVICE_LISTS = ("待办",)
MAX_ITEMS = 100
MAX_LISTS = 12
REMIND_MODES = ("none", "show", "ring")
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


def _norm(t):
    return "".join(str(t or "").split())


def _item(x, by=""):
    """整理一项（以前的只有 text、done）。"""
    if not isinstance(x, dict):
        x = {"text": str(x)}
    x.setdefault("id", secrets.token_hex(4))
    x["text"] = str(x.get("text") or "").strip()[:60]
    x["done"] = bool(x.get("done"))
    x.setdefault("by", by)
    x.setdefault("at", int(time.time()))
    return x


def _family(data):
    f = data.setdefault(FAMILY, {})
    f.setdefault("version", 0)
    lists = f.setdefault("lists", {})
    for name in FAMILY_LISTS:
        lists.setdefault(name, [])
    for name in lists:
        lists[name] = [_item(x) for x in lists[name]]
    return f


def _merge(into, items):
    have = {_norm(x["text"]): x for x in into}
    for x in items:
        x = _item(x)
        old = have.get(_norm(x["text"]))
        if old is None:
            into.append(x)
            have[_norm(x["text"])] = x
        elif old["done"] and not x["done"]:     # 同名的留一条，没划掉的优先
            old["done"] = False


def _entry(data, device):
    """这台设备的那份；第一次碰到以前的格式时把它的购物清单并进全家的（先留一份备份）。"""
    fam = _family(data)
    e = data.setdefault(device, {})
    e.setdefault("secret", secrets.token_urlsafe(16))
    e.setdefault("version", 0)
    lists = e.setdefault("lists", {})
    if not e.get("migrated"):
        for name in FAMILY_LISTS:
            if name in lists:
                _merge(fam["lists"].setdefault(name, []), lists.pop(name) or [])
                fam["version"] += 1
        e["migrated"] = 1
    for name in DEVICE_LISTS:
        if name not in lists and name not in fam["lists"]:
            lists[name] = []
    for name in lists:
        lists[name] = [_item(x) for x in lists[name]]
    return e


def _backup_once():
    path = _path()
    bak = path + ".bak-76"
    if os.path.exists(path) and not os.path.exists(bak):
        data = _load()
        if any(k != FAMILY and isinstance(v, dict) and not v.get("migrated") for k, v in data.items()):
            shutil.copyfile(path, bak)


def _shown(x):
    return {**x, "repeat_text": recur.describe(x["repeat"])} if x.get("repeat") else x


def _view(data, device):
    fam, e = data[FAMILY], data[device]
    lists, scopes = {}, {}
    for name, items in fam["lists"].items():
        lists[name], scopes[name] = [_shown(x) for x in items], "family"
    for name, items in e["lists"].items():
        lists[name], scopes[name] = [_shown(x) for x in items], "device"
    return {"secret": e["secret"], "version": fam["version"] * 100000 + e["version"], "lists": lists, "scopes": scopes}


def view(device):
    if not device or device == FAMILY:
        raise ValueError("缺少设备 ID")
    with _lock:
        _backup_once()
        data = _load()
        _entry(data, device)
        _save(data)
        return _view(data, device)


def count(device) -> int:
    """这台设备自己的清单里一共几项（只读）。"""
    with _lock:
        e = _load().get(device) or {}
    return sum(len(v or []) for v in (e.get("lists") or {}).values())


def forget(device) -> None:
    """删除设备时用：这台设备自己的清单（连同手机分享链接的密钥）整条删掉；全家的不动。"""
    with _lock:
        data = _load()
        if device != FAMILY and data.pop(device, None) is not None:
            _save(data)


def by_secret(secret):
    with _lock:
        for device, e in _load().items():
            if device != FAMILY and secret and isinstance(e, dict) and secrets.compare_digest(str(e.get("secret") or ""), secret):
                return device
    return None


def _remind(r):
    if not r:
        return None
    if isinstance(r, str):
        r = {"mode": r}
    mode = r.get("mode") if r.get("mode") in REMIND_MODES else "ring"
    if mode == "none":
        return None
    before = sorted({int(b) for b in (r.get("before") or [0]) if isinstance(b, (int, float)) and 0 <= b <= 60 * 24 * 30})[:4] or [0]
    where = [str(w) for w in (r.get("where") or []) if str(w).strip()][:8]
    return {"mode": mode, "before": before, "where": where}


def _fields(x, kw, today):
    """改一项的日期、时间、重复、提醒（kw 里有的才改；None 表示清掉）。"""
    if "text" in kw and str(kw["text"] or "").strip():
        x["text"] = str(kw["text"]).strip()[:60]
    if "due" in kw:
        if kw["due"]:
            try:
                x["due"] = datetime.date.fromisoformat(str(kw["due"])).isoformat()
            except ValueError:
                raise ValueError("日期要写成 2026-11-20 这样")
        else:
            x.pop("due", None)
    if "time" in kw:
        t = str(kw["time"] or "").strip()
        if t:
            try:
                hh, mm = (int(v) for v in t.split(":"))
                assert 0 <= hh < 24 and 0 <= mm < 60
            except (ValueError, AssertionError):
                raise ValueError("时间要写成 09:00 这样")
            x["time"] = f"{hh:02d}:{mm:02d}"
        else:
            x.pop("time", None)
    if "repeat" in kw:
        rule = recur.normalize(kw["repeat"])
        if rule:
            x["repeat"] = rule
            if not x.get("due"):
                x["due"] = (recur.first_due(rule, today) or today).isoformat()
        else:
            x.pop("repeat", None)
    if "remind" in kw:
        r = _remind(kw["remind"])
        if r:
            x["remind"] = r
        else:
            x.pop("remind", None)
    if x.get("time") and not x.get("due"):
        x["due"] = today.isoformat()


def _match(lst, keys):
    want = {_norm(k) for k in keys} | {str(k) for k in keys}
    return [x for x in lst if x["id"] in want or _norm(x["text"]) in want]


def change(device, op, name, items=(), done=None, by="", scope=None, new_name=None, today=None, **kw):
    """op: add / remove / check / clear_done / edit / delete_list / create_list / rename_list / set_scope / reset_secret。
    items：文字或项的 id。kw：add 和 edit 时的 text、due、time、repeat、remind。返回整个 view。"""
    today = today or datetime.date.today()
    name = str(name or "").strip() or "购物清单"
    if len(name) > 20:
        raise ValueError("清单名字太长")
    keys = [str(i).strip()[:60] for i in (items or []) if str(i).strip()]
    with _lock:
        _backup_once()
        data = _load()
        fam, e = _family(data), _entry(data, device)
        own, shared = e["lists"], fam["lists"]
        where = own if name in own else shared if name in shared else None
        if op == "reset_secret":
            e["secret"] = secrets.token_urlsafe(16)
        elif where is None:
            if op not in ("add", "create_list"):
                raise ValueError(f"没有「{name}」这个清单")
            if len(own) + len(shared) >= MAX_LISTS:
                raise ValueError(f"最多 {MAX_LISTS} 个清单")
            where = shared if (scope or ("family" if name in FAMILY_LISTS else "device")) == "family" else own
            where[name] = []
        elif op == "create_list":
            raise ValueError(f"已经有「{name}」这个清单了")
        lst = where[name] if where is not None else []
        if op == "add":
            have = {_norm(x["text"]) for x in lst}
            for it in keys:
                if _norm(it) not in have:
                    x = _item({"text": it}, by)
                    _fields(x, kw, today)
                    lst.append(x)
                    have.add(_norm(it))
                elif kw:
                    for x in _match(lst, [it]):
                        _fields(x, kw, today)
            if len(lst) > MAX_ITEMS:
                raise ValueError(f"一个清单最多 {MAX_ITEMS} 项")
        elif op == "edit":
            hits = _match(lst, keys)
            if not hits:
                raise ValueError("清单里没有这一项")
            for x in hits:
                _fields(x, kw, today)
        elif op == "remove":
            gone = {id(x) for x in _match(lst, keys)}
            where[name] = [x for x in lst if id(x) not in gone]
        elif op == "check":
            for x in _match(lst, keys):
                want = True if done is None else bool(done)
                if want and x.get("repeat") and x.get("due"):
                    x["times"] = int(x.get("times") or 0) + 1
                    x["last_done"] = today.isoformat()
                    nxt = recur.next_due(x["repeat"], max(datetime.date.fromisoformat(x["due"]), today), x["times"])
                    if nxt:
                        x["due"], x["done"] = nxt.isoformat(), False     # 重复的：排到下一次，不划掉
                        continue
                x["done"] = want
        elif op == "clear_done":
            where[name] = [x for x in lst if not x["done"]]
        elif op == "delete_list":
            if name in FAMILY_LISTS + DEVICE_LISTS:
                raise ValueError("默认的清单不能删")
            where.pop(name, None)
        elif op == "rename_list":
            new = str(new_name or "").strip()[:20]
            if not new or new in own or new in shared:
                raise ValueError("新名字不能空着，也不能和别的清单重名")
            if name in FAMILY_LISTS + DEVICE_LISTS:
                raise ValueError("默认的清单不能改名")
            where[new] = where.pop(name)
        elif op == "set_scope":
            target = shared if scope == "family" else own if scope == "device" else None
            if target is None:
                raise ValueError("范围只能是全家或这台")
            if target is not where:
                if name in target:
                    raise ValueError("那边已经有同名的清单")
                target[name] = where.pop(name)
        elif op not in ("create_list", "reset_secret"):
            raise ValueError("不支持的操作")
        fam["version"] += 1
        e["version"] += 1
        _save(data)
        return _view(data, device)


def upcoming(device, today=None):
    """这台设备该提醒的项（全家的和自己的，没划掉、有日期、要提醒、在这台响或没指定）：给设备排闹钟用。"""
    today = today or datetime.date.today()
    v = view(device)
    out = []
    for name, items in v["lists"].items():
        for x in items:
            r = x.get("remind")
            if x["done"] or not x.get("due") or not r:
                continue
            where = r.get("where") or []
            if where and "all" not in where and device not in where:
                continue                     # 没指定在哪响：看得到这项的设备都提醒
            out.append({"list": name, **x})
    return out
