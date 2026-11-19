"""[xiaozhi-hub] 家庭相册（设计画布第 66 块）：全家一份放在网关上，每台设备的待机页挑着放。

照片从三处来：后台上传（拖进去、手机打开后台传）、网关上的一个文件夹（NAS 共享文件夹挂到网关上，子文件夹名当相册名），
设备上原来存的照片（后台点「收进相册」，设备下次同步时传上来）。

存法：data/album/p/<id>.jpg 长边 2560（原图不留），t/<id>.jpg 长边 480 当缩略图，d/<id>.jpg 长边 1280 发给设备（用到时才做）。
缩放用 ffmpeg（网关的 venv 里没有 Pillow；ffmpeg 会按 EXIF 的方向转正）；拍摄时间自己从 JPEG 的 EXIF 里读。
照片不出家里的网络，不做人脸识别。

每台设备的待机页设置（放哪些相册、样式、多久换一张、顺序、睡眠时段放不放）存在这里，设备上改了也写回来，谁新听谁的。
"""

import hashlib
import json
import os
import random
import re
import struct
import subprocess
import threading
import time
from datetime import date, datetime

from config.config_loader import get_project_dir
from config.logger import setup_logging
from core.utils.atomic_file import write_json

TAG = __name__
logger = setup_logging()

SIZES = {"t": 480, "d": 1280, "p": 2560}
EXTS = (".jpg", ".jpeg", ".png", ".webp")
MAX_UPLOAD = 40 * 1024 * 1024
MAX_PHOTOS = 5000            # 全部照片最多这么多（文件夹里更多的取最新的）
DEVICE_MAX = 300             # 一台设备最多缓存这么多
SCAN_EVERY = 3600            # 文件夹每小时看一次
OTD_DAYS = 3                 # 那年今日：往年今天前后 3 天
STYLES = ("corner", "photo", "clock")         # 照片 + 角落时钟、只放照片、时钟和天气（不放照片）
SOURCES = ("hub", "local", "none")            # 家庭相册（网关）、只放这台的照片、不放照片
ORDERS = ("shuffle", "time")
NIGHTS = ("clock", "play")
INTERVALS = (10, 15, 30, 60)
DEFAULT_DEVICE = {"source": "hub", "albums": None, "style": "corner", "interval": 15, "order": "shuffle", "night": "clock", "updated": 0}

_lock = threading.RLock()
_scan = {"running": False, "done": 0, "total": 0}


def _root() -> str:
    return os.path.join(get_project_dir(), "data", "album")


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "album.json")


def load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("photos", {})
    d.setdefault("albums", [])
    d.setdefault("folder", {"path": "", "on": False, "scanned": 0, "error": ""})
    d.setdefault("devices", {})
    d.setdefault("hidden", {})
    d.setdefault("local", {})      # 设备上报的本机照片张数 {device: {"count", "at"}}
    d.setdefault("collect", [])    # 后台点了「收进相册」、还没传完的设备
    return d


def save(d: dict):
    with _lock:
        write_json(_path(), d, ensure_ascii=False, indent=1)


class AlbumError(Exception):
    pass


# ---------------------------------------------------------------- EXIF：拍摄时间

def exif_taken(path: str):
    """JPEG 里 EXIF 的 DateTimeOriginal（没有就用 DateTime），返回秒；读不到给 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
    except OSError:
        return None
    if head[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(head):
        if head[i] != 0xFF:
            return None
        marker, size = head[i + 1], struct.unpack(">H", head[i + 2:i + 4])[0]
        if marker == 0xE1 and head[i + 4:i + 10] == b"Exif\0\0":
            return _parse_tiff(head[i + 10:i + 2 + size])
        if marker in (0xDA, 0xD9):
            return None
        i += 2 + size
    return None


def _parse_tiff(t: bytes):
    if len(t) < 8:
        return None
    end = "<" if t[:2] == b"II" else ">" if t[:2] == b"MM" else None
    if not end:
        return None

    def ifd(off):
        out = {}
        if off + 2 > len(t):
            return out
        n = struct.unpack(end + "H", t[off:off + 2])[0]
        for k in range(min(n, 200)):
            e = off + 2 + k * 12
            if e + 12 > len(t):
                break
            tag, typ, cnt = struct.unpack(end + "HHI", t[e:e + 8])
            if typ == 2:      # ASCII
                p = e + 8 if cnt <= 4 else struct.unpack(end + "I", t[e + 8:e + 12])[0]
                out[tag] = t[p:p + cnt].split(b"\0")[0].decode("ascii", "ignore")
            elif typ == 4:    # LONG
                out[tag] = struct.unpack(end + "I", t[e + 8:e + 12])[0]
        return out

    ifd0 = ifd(struct.unpack(end + "I", t[4:8])[0])
    sub = ifd(ifd0[0x8769]) if isinstance(ifd0.get(0x8769), int) else {}
    for raw in (sub.get(0x9003), sub.get(0x9004), ifd0.get(0x0132)):
        if isinstance(raw, str):
            try:
                return int(datetime.strptime(raw.strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp())
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------- 缩放

def _ffmpeg(src: str, dst: str, long_side: int):
    tmp = dst + ".part.jpg"
    vf = f"scale='if(gt(iw,ih),min({long_side},iw),-2)':'if(gt(iw,ih),-2,min({long_side},ih))'"
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", vf, "-frames:v", "1", "-q:v", "3", tmp],
                           capture_output=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AlbumError(f"转不了这张：{e}") from e
    if r.returncode or not os.path.isfile(tmp) or not os.path.getsize(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        raise AlbumError("转不了这张（不是照片，或者格式不认识；HEIC 先在手机上存成 JPEG）")
    os.replace(tmp, dst)


def _size(path: str):
    """JPEG 的宽高（从 SOF 段读）。"""
    try:
        with open(path, "rb") as f:
            b = f.read(128 * 1024)
    except OSError:
        return 0, 0
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            break
        m, size = b[i + 1], struct.unpack(">H", b[i + 2:i + 4])[0]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + size
    return 0, 0


def image(pid: str, kind: str = "d") -> str:
    """照片文件的路径（kind：t 缩略图、d 给设备、p 存的那张）；d 用到时才做。没有给 ""。"""
    if not re.fullmatch(r"[0-9a-f]{12}", pid or "") or kind not in SIZES:
        return ""
    big = os.path.join(_root(), "p", pid + ".jpg")
    if not os.path.isfile(big):
        return ""
    if kind == "p":
        return big
    path = os.path.join(_root(), kind, pid + ".jpg")
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            _ffmpeg(big, path, SIZES[kind])
        except AlbumError:
            return big
    return path


def _import(src: str, source: str, name: str, key: str, albums=None, d: dict = None) -> dict:
    """把一张照片收进来（缩到长边 2560、做缩略图）；key 一样的算同一张，不重复收。返回这张的记录。"""
    pid = hashlib.sha1(key.encode()).hexdigest()[:12]
    d = d if d is not None else load()
    if pid in d["photos"]:
        return d["photos"][pid]
    root = _root()
    for k in ("p", "t"):
        os.makedirs(os.path.join(root, k), exist_ok=True)
    big = os.path.join(root, "p", pid + ".jpg")
    _ffmpeg(src, big, SIZES["p"])
    try:
        _ffmpeg(big, os.path.join(root, "t", pid + ".jpg"), SIZES["t"])
    except AlbumError:
        pass
    w, h = _size(big)
    rec = {"id": pid, "name": name[:80], "taken": exif_taken(src), "added": int(time.time()), "w": w, "h": h,
           "source": source, "albums": list(albums or []), "key": key}
    d["photos"][pid] = rec
    return rec


def _remove_files(pid: str):
    for k in SIZES:
        p = os.path.join(_root(), k, pid + ".jpg")
        if os.path.exists(p):
            os.remove(p)


# ---------------------------------------------------------------- 上传、删除、相册

def upload(tmp_path: str, name: str, source: str = "upload", device: str = "") -> dict:
    """收一张上传的照片（tmp_path 是收好的临时文件，用完删掉）。"""
    try:
        with open(tmp_path, "rb") as f:
            digest = hashlib.sha1(f.read()).hexdigest()
        with _lock:
            d = load()
            if len(d["photos"]) >= MAX_PHOTOS:
                raise AlbumError(f"相册满了（最多 {MAX_PHOTOS} 张），先删一些")
            rec = _import(tmp_path, source if not device else f"device:{device}", name or "照片", "sha1:" + digest, d=d)
            save(d)
        return _pub(rec, d)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _album_id(name: str) -> str:
    return "a" + hashlib.sha1(name.encode()).hexdigest()[:7]


def _ensure_album(d: dict, name: str) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:20]
    if not name:
        raise AlbumError("相册要有名字")
    for a in d["albums"]:
        if a["name"] == name:
            return a["id"]
    aid = _album_id(name)
    d["albums"].append({"id": aid, "name": name})
    return aid


def apply(body: dict) -> dict:
    """后台的修改：add_album / rename_album / delete_album / tag（照片加进、移出相册）/ delete（删照片）
    / folder（文件夹地址和开关）/ scan / device（某台设备的待机页设置）/ collect（让设备把本机照片传上来）/ unhide。"""
    op = body.get("op")
    with _lock:
        d = load()
        if op == "add_album":
            _ensure_album(d, body.get("name"))
        elif op == "rename_album":
            name = str(body.get("name") or "").strip()[:20]
            if not name:
                raise AlbumError("相册要有名字")
            for a in d["albums"]:
                if a["id"] == body.get("id"):
                    a["name"] = name
        elif op == "delete_album":
            aid = body.get("id")
            d["albums"] = [a for a in d["albums"] if a["id"] != aid]
            for p in d["photos"].values():
                p["albums"] = [x for x in p["albums"] if x != aid]
            for s in d["devices"].values():
                if isinstance(s.get("albums"), list):
                    s["albums"] = [x for x in s["albums"] if x != aid] or None
        elif op == "tag":
            aid, on = body.get("album"), bool(body.get("on", True))
            if aid not in {a["id"] for a in d["albums"]}:
                raise AlbumError("没有这个相册")
            for pid in body.get("ids") or []:
                p = d["photos"].get(pid)
                if p:
                    p["albums"] = sorted(set(p["albums"]) | {aid}) if on else [x for x in p["albums"] if x != aid]
        elif op == "delete":
            for pid in body.get("ids") or []:
                p = d["photos"].pop(pid, None)
                if p:
                    _remove_files(pid)
                    if p.get("source") == "folder":   # 文件夹里的照片删了下次扫描还会回来：记下不要
                        d.setdefault("skip", []).append(p.get("key"))
        elif op == "folder":
            path = str(body.get("path", d["folder"].get("path", "")) or "").strip()
            if path and not os.path.isabs(path):
                raise AlbumError("文件夹要写完整路径，比如 /mnt/nas/photos")
            if path and not os.path.isdir(path):
                raise AlbumError(f"网关上没有这个文件夹：{path}")
            d["folder"].update({"path": path, "on": bool(body.get("on", True)) and bool(path), "error": ""})
            if path != d["folder"].get("scanned_path"):
                d["folder"]["scanned"] = 0
        elif op == "device":
            dev = str(body.get("device") or "")
            if not dev:
                raise AlbumError("哪台设备？")
            _set_device(d, dev, body, int(time.time()))
        elif op == "collect":
            dev = str(body.get("device") or "")
            if dev and dev not in d["collect"]:
                d["collect"].append(dev)
        elif op == "unhide":
            d["hidden"].pop(str(body.get("device") or ""), None)
        elif op != "scan":
            raise AlbumError("不认识的操作")
        save(d)
    if op in ("folder", "scan"):
        start_scan(force=True)
    return view()


# ---------------------------------------------------------------- 网关上的文件夹

def _walk(root: str):
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith((".", "@", "#"))]   # 群晖、飞牛的 @eaDir 这类缩略图目录跳过
        for fn in files:
            if fn.lower().endswith(EXTS) and not fn.startswith("."):
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size > 2048:
                    out.append((st.st_mtime, p, st.st_size))
        if len(out) > MAX_PHOTOS * 4:
            break
    out.sort(reverse=True)
    return out[:MAX_PHOTOS]


def scan_folder():
    """读文件夹：新照片收进来（子文件夹名当相册名），文件夹里没了的去掉。在线程里跑。"""
    d = load()
    folder = dict(d["folder"])
    root = folder.get("path") or ""
    try:
        if not folder.get("on") or not root:
            return
        if not os.path.isdir(root):
            raise AlbumError(f"网关上没有这个文件夹：{root}（NAS 没挂上？）")
        files = _walk(root)
        _scan.update(total=len(files), done=0)
        keys = set()
        skip = set(d.get("skip") or [])
        for mtime, path, size in files:
            rel = os.path.relpath(path, root)
            key = f"folder:{rel}:{size}:{int(mtime)}"
            keys.add(key)
            _scan["done"] += 1
            if key in skip:
                continue
            with _lock:
                d = load()
                if any(p.get("key") == key for p in d["photos"].values()):
                    continue
                sub = rel.split(os.sep)[0] if os.sep in rel else ""
                try:
                    albums = [_ensure_album(d, sub)] if sub else []
                    _import(path, "folder", os.path.basename(path), key, albums, d=d)
                    save(d)
                except AlbumError as e:
                    logger.bind(tag=TAG).info(f"相册跳过 {rel}：{e}")
        with _lock:
            d = load()
            gone = [pid for pid, p in d["photos"].items() if p.get("source") == "folder" and p.get("key") not in keys]
            for pid in gone:
                d["photos"].pop(pid, None)
                _remove_files(pid)
            d["folder"].update({"scanned": int(time.time()), "scanned_path": root, "error": ""})
            save(d)
    except AlbumError as e:
        with _lock:
            d = load()
            d["folder"].update({"scanned": int(time.time()), "error": str(e)})
            save(d)
    finally:
        _scan["running"] = False


def start_scan(force: bool = False):
    folder = load()["folder"]
    if _scan["running"] or not folder.get("on") or not folder.get("path"):
        return
    if not force and time.time() - (folder.get("scanned") or 0) < SCAN_EVERY:
        return
    _scan["running"] = True
    threading.Thread(target=scan_folder, name="album-scan", daemon=True).start()


# ---------------------------------------------------------------- 设备

def _set_device(d: dict, dev: str, body: dict, updated: int) -> bool:
    cur = {**DEFAULT_DEVICE, **(d["devices"].get(dev) or {})}
    if updated and updated < (cur.get("updated") or 0):
        return False      # 旧的改动，不要
    new = dict(cur)
    if body.get("source") in SOURCES:
        new["source"] = body["source"]
    if "albums" in body:
        a = body["albums"]
        known = {x["id"] for x in d["albums"]}
        new["albums"] = None if a is None else [x for x in a if x in known] or None
    if body.get("style") in STYLES:
        new["style"] = body["style"]
    if body.get("order") in ORDERS:
        new["order"] = body["order"]
    if body.get("night") in NIGHTS:
        new["night"] = body["night"]
    try:
        if int(body.get("interval", 0)) in INTERVALS:
            new["interval"] = int(body["interval"])
    except (TypeError, ValueError):
        pass
    new["updated"] = updated or int(time.time())
    d["devices"][dev] = new
    return True


def device_settings(dev: str, d: dict = None) -> dict:
    d = d or load()
    return {**DEFAULT_DEVICE, **(d["devices"].get(dev) or {})}


def _otd(taken, today: date) -> bool:
    if not taken:
        return False
    t = datetime.fromtimestamp(taken).date()
    if t.year >= today.year:
        return False
    try:
        same = t.replace(year=today.year)
    except ValueError:      # 2 月 29 日
        same = date(today.year, 3, 1)
    return abs((same - today).days) <= OTD_DAYS


def for_device(dev: str, local: int = None, today: date = None) -> dict:
    """设备同步时要的：这台的设置、该放的照片（最多 300 张：那年今日的全放，其余按天打乱挑）、要不要把本机照片传上来。"""
    today = today or date.today()
    with _lock:
        d = load()
        if local is not None:
            d["local"][dev] = {"count": int(local), "at": int(time.time())}
            save(d)
    start_scan()
    s = device_settings(dev, d)
    hidden = set(d["hidden"].get(dev) or [])
    names = {a["id"]: a["name"] for a in d["albums"]}
    pick = [p for p in d["photos"].values() if p["id"] not in hidden
            and (s["albums"] is None or set(p["albums"]) & set(s["albums"]))]
    otd = [p for p in pick if _otd(p.get("taken"), today)]
    rest = [p for p in pick if p not in otd]
    if len(otd) + len(rest) > DEVICE_MAX:
        random.Random(f"{dev}:{today.isoformat()}").shuffle(rest)
        rest = rest[:max(0, DEVICE_MAX - len(otd))]
    photos = [{"id": p["id"], "taken": p.get("taken"), "w": p.get("w"), "h": p.get("h"), "otd": p in otd,
               "albums": [names[a] for a in p["albums"] if a in names]} for p in otd + rest]
    if s["order"] == "time":
        photos.sort(key=lambda x: x["taken"] or 0, reverse=True)
    return {"settings": s, "photos": photos, "albums": [{"id": a["id"], "name": a["name"]} for a in d["albums"]],
            "collect": dev in d["collect"], "total": len(d["photos"])}


def device_set(dev: str, body: dict) -> dict:
    """设备上改了待机页的设置：带 updated（设备上改的时间），比网关这里新才算。"""
    with _lock:
        d = load()
        try:
            updated = int(body.get("updated") or 0)
        except (TypeError, ValueError):
            updated = 0
        _set_device(d, dev, body, updated or int(time.time()))
        save(d)
        return device_settings(dev, d)


def hide(dev: str, pid: str):
    """「别放这张了」：这台设备以后跳过它。"""
    with _lock:
        d = load()
        if pid in d["photos"]:
            h = d["hidden"].setdefault(dev, [])
            if pid not in h:
                h.append(pid)
            save(d)


def collected(dev: str):
    with _lock:
        d = load()
        d["collect"] = [x for x in d["collect"] if x != dev]
        save(d)


# ---------------------------------------------------------------- 后台看的

def _pub(p: dict, d: dict) -> dict:
    names = {a["id"]: a["name"] for a in d["albums"]}
    return {"id": p["id"], "name": p.get("name", ""), "taken": p.get("taken"), "added": p.get("added"), "w": p.get("w"), "h": p.get("h"),
            "source": p.get("source", ""), "albums": [a for a in p["albums"] if a in names]}


def view(device_names: dict = None) -> dict:
    d = load()
    photos = sorted(d["photos"].values(), key=lambda p: p.get("taken") or p.get("added") or 0, reverse=True)
    today = date.today()
    counts = {}
    for p in photos:
        for a in p["albums"]:
            counts[a] = counts.get(a, 0) + 1
    by_source = {}
    for p in photos:
        k = "device" if str(p.get("source", "")).startswith("device:") else p.get("source", "upload")
        by_source[k] = by_source.get(k, 0) + 1
    names = device_names or {}
    devs = sorted(set(names) | set(d["devices"]) | set(d["local"]))
    return {
        "photos": [{**_pub(p, d), "otd": _otd(p.get("taken"), today)} for p in photos],
        "albums": [{**a, "count": counts.get(a["id"], 0)} for a in d["albums"]],
        "folder": {**{k: v for k, v in d["folder"].items() if k != "scanned_path"}, "scanning": _scan["running"],
                   "done": _scan["done"], "total": _scan["total"], "count": by_source.get("folder", 0)},
        "sources": by_source,
        "devices": [{"id": x, "name": names.get(x, x), "settings": device_settings(x, d), "hidden": len(d["hidden"].get(x) or []),
                     "local": (d["local"].get(x) or {}).get("count"), "collecting": x in d["collect"]} for x in devs],
        "max": MAX_PHOTOS, "device_max": DEVICE_MAX,
        "choices": {"style": STYLES, "source": SOURCES, "order": ORDERS, "night": NIGHTS, "interval": INTERVALS},
    }
