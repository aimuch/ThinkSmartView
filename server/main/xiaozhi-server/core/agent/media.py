"""[xiaozhi-hub] 媒体（设计画布第 58、60 块）：歌、故事、英语、动画、菜谱视频，按用途分区。

来源（实现同一个接口 Source，可以都开）：
- Navidrome（n.）：家里 NAS 上的音乐服务器，Subsonic API，token + salt 认证（只存 token，不存密码）。
- Jellyfin（j.）：家里 NAS 上的影音服务器，视频和音频都能放。后台填用户名密码换 AccessToken（只存 token），
  或者直接填 API Key。设备上放视频：H.264/MP4 且不高于 720p 直接发；别的让 Jellyfin 转成 720p H.264 的分片 MP4。
  （设备的 WebKit 实测：720p H.264 30 帧不掉帧，1080p 约 26 帧，VP8 只有 7 帧，见设计画布第 60 块。）
- Emby（e.）：和 Jellyfin 同一套接口（Emby 是 Jellyfin 的前身），认证头不同，其余一样（Jellyfin 的类直接用）。
- 网关本机 music/ 文件夹（l.）：没有 NAS 的人用，音频、视频都行，后台能上传。

分区（sections）按用途，不按来源：儿童区（kid=True）「儿歌」「睡前故事」「英语启蒙」「动画」，大人区「音乐」「英语学习」「菜谱」，
能改名、能自己加。每个分区标进来源里的文件夹、媒体库、歌单、剧集或单个；放法 play：shuffle（打乱）、course（按顺序、
记住每个人放到哪）、lookup（按名字找，比如菜谱按菜名）。儿童模式、或者这一轮认出说话的是孩子时，只从儿童区找。

设备不碰任何密码：找、挑、放、封面都走网关的 /kiosk/media/*（admin_handler 里，只认本机来的请求，设备经隧道来）。
配置在 data/media.json（权限 0600，第一次读时从 58 块的 data/music.json 搬过来），进度在 data/media_progress.json。
"""
import asyncio
import base64
import difflib
import hashlib
import json
import os
import random
import re
import secrets
import threading
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from config.config_loader import get_project_dir
from core.agent import services

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma", ".ape"}
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png", "album.jpg", "poster.jpg")
MAX_UPLOAD = 2 * 1024 * 1024 * 1024
CLIENT = "xiaomai"
API_VERSION = "1.16.1"
REF_TYPES = ("folder", "playlist", "album", "artist", "item")
PLAYS = {"shuffle": "随机", "course": "按顺序 · 记进度", "lookup": "按名字找"}
INDEX_TTL = 600          # 全部名字（模糊找用）缓存 10 分钟
INDEX_MAX = 5000
DIRECT_MAX_HEIGHT = 720  # 高于这个的视频让 Jellyfin 转码
DONE_RATIO = 0.9         # 放到 90% 算放完，下次从下一个开始

# 预设分区：id 固定（设备、口令、进度都认 id），名字能改；words 是除名字以外也认的说法
PRESETS = [
    {"id": "songs", "name": "儿歌", "kid": True, "play": "shuffle", "words": ["儿歌"]},
    {"id": "stories", "name": "睡前故事", "kid": True, "play": "shuffle", "words": ["睡前故事", "故事"]},
    {"id": "kid_english", "name": "英语启蒙", "kid": True, "play": "course", "words": ["英语", "英文", "英语启蒙", "英语课"]},
    {"id": "cartoons", "name": "动画", "kid": True, "play": "course", "words": ["动画", "动画片", "卡通"]},
    {"id": "music", "name": "音乐", "kid": False, "play": "shuffle", "words": ["音乐", "歌"]},
    {"id": "english", "name": "英语学习", "kid": False, "play": "course", "words": ["英语", "英文", "英语学习", "英语课"]},
    {"id": "recipes", "name": "菜谱", "kid": False, "play": "lookup", "words": ["菜谱", "做菜视频"]},
]
OLD_TAGS = {"kids": "songs", "bedtime": "stories"}   # 58 块的标签 → 分区
MAX_SECTIONS = 20

_lock = threading.Lock()


class MediaError(Exception):
    """给人看的错误（不含地址和口令）。"""


MusicError = MediaError   # 58 块的名字


# ---------------------------------------------------------------- 配置

def _path() -> str:
    return os.path.join(get_project_dir(), "data", "media.json")


def _old_path() -> str:
    return os.path.join(get_project_dir(), "data", "music.json")


def _progress_path() -> str:
    return os.path.join(get_project_dir(), "data", "media_progress.json")


def local_root() -> Path:
    return Path(get_project_dir()) / "music"


def _empty() -> dict:
    return {"navidrome": {"url": "", "user": "", "salt": "", "token": "", "on": True},
            "jellyfin": {"url": "", "user": "", "user_id": "", "token": "", "device": "", "key": False, "on": True},
            "emby": {"url": "", "user": "", "user_id": "", "token": "", "device": "", "key": False, "on": True},
            "local": {"on": True},
            "sections": [dict({k: v for k, v in p.items() if k != "words"}, refs=[]) for p in PRESETS]}


def _section(x, preset=None):
    if not isinstance(x, dict):
        return None
    sid = re.sub(r"[^0-9a-zA-Z_]", "", str(x.get("id") or ""))[:24]
    name = str(x.get("name") or "").strip()[:12]
    if not sid or not name:
        return None
    play = x.get("play") if x.get("play") in PLAYS else (preset or {}).get("play", "shuffle")
    return {"id": sid, "name": name, "kid": bool(x.get("kid")), "play": play,
            "refs": [r for r in (_ref(y) for y in x.get("refs") or []) if r][:30]}


def load() -> dict:
    d = _empty()
    raw = None
    for path in (_path(), _old_path()):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            break
        except (OSError, ValueError):
            continue
    if not isinstance(raw, dict):
        return d
    for src, keys in (("navidrome", ("url", "user", "salt", "token")), ("jellyfin", ("url", "user", "user_id", "token", "device")),
                      ("emby", ("url", "user", "user_id", "token", "device"))):
        got = raw.get(src) if isinstance(raw.get(src), dict) else {}
        for k in keys:
            if isinstance(got.get(k), str):
                d[src][k] = got[k]
        for k in ("on", "key"):
            if isinstance(got.get(k), bool) and k in d[src]:
                d[src][k] = got[k]
    lo = raw.get("local") if isinstance(raw.get("local"), dict) else {}
    if isinstance(lo.get("on"), bool):
        d["local"]["on"] = lo["on"]
    presets = {p["id"]: p for p in PRESETS}
    if isinstance(raw.get("sections"), list):
        secs = [s for s in (_section(x, presets.get(str((x or {}).get("id")))) for x in raw["sections"]) if s]
        have = {s["id"] for s in secs}
        secs += [s for s in d["sections"] if s["id"] not in have]     # 新版加的预设分区
        d["sections"] = secs
    elif isinstance(raw.get("tags"), dict):                            # 58 块：儿歌、睡前故事两个标签
        for tag, sid in OLD_TAGS.items():
            refs = [r for r in (_ref(x) for x in raw["tags"].get(tag) or []) if r]
            next(s for s in d["sections"] if s["id"] == sid)["refs"] = refs
    return d


def save(d: dict):
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    tmp = _path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())


def _ref(x):
    """分区里的一项：{"type": folder|playlist|album|artist|item, "id": "n.xxx", "name", "media"?}。"""
    if not isinstance(x, dict) or x.get("type") not in REF_TYPES:
        return None
    rid = str(x.get("id") or "")
    if not re.fullmatch(r"[njel]\.[\w\-=./]{0,400}", rid):
        return None
    out = {"type": x["type"], "id": rid, "name": str(x.get("name") or "")[:60]}
    if x.get("media") in ("audio", "video"):
        out["media"] = x["media"]
    return out


def token_for(password: str, salt: str = "") -> tuple:
    """Subsonic 的 token 认证：(salt, md5(password + salt))。"""
    salt = salt or secrets.token_hex(8)
    return salt, hashlib.md5((password + salt).encode("utf-8")).hexdigest()


def _norm_url(url: str, strip=("/rest",)) -> str:
    url = str(url or "").strip().rstrip("/")
    for s in strip:
        if url.endswith(s):
            url = url[:-len(s)]
    if url.endswith("/web") or "/web/" in url:            # 从浏览器地址栏复制的 Jellyfin 地址
        url = url.split("/web")[0]
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise MediaError("地址要以 http:// 或 https:// 开头，比如 http://10.10.10.88:4533")
    return url


def section_of(d: dict, sid: str):
    return next((s for s in d["sections"] if s["id"] == sid), None)


def apply(op: dict) -> dict:
    """后台的一次修改（Jellyfin 换 token 要联网，在 apply_async 里）。不合法抛 MediaError。"""
    kind = op.get("op")
    with _lock:
        d = load()
        if kind == "navidrome":
            nv = d["navidrome"]
            url, user = _norm_url(op.get("url")), str(op.get("user") or "").strip()[:64]
            password = str(op.get("password") or "")
            if not url:
                d["navidrome"] = {**_empty()["navidrome"], "on": nv["on"]}
            else:
                if not user:
                    raise MediaError("填上 Navidrome 的用户名")
                if password:
                    nv["salt"], nv["token"] = token_for(password)
                elif user != nv["user"] or not nv["token"]:
                    raise MediaError("填上密码（改了用户名也要重新填）")
                nv["url"], nv["user"] = url, user
            _index.pop("n", None)
        elif kind in ("jellyfin_saved", "emby_saved"):       # apply_async 换好 token 以后
            which = kind.split("_")[0]
            d[which] = {**d[which], **{k: op[k] for k in ("url", "user", "user_id", "token", "device", "key") if k in op}}
            _index.pop(SERVERS[which].key, None)
        elif kind in ("jellyfin_clear", "emby_clear"):
            which = kind.split("_")[0]
            d[which] = {**_empty()[which], "on": d[which]["on"]}
            _index.pop(SERVERS[which].key, None)
        elif kind == "toggle":
            which = op.get("source")
            if which not in ("navidrome", "jellyfin", "emby", "local"):
                raise MediaError("没有这个来源")
            d[which]["on"] = bool(op.get("on"))
        elif kind in ("tag", "ref"):                         # 把一项加进 / 移出分区（tag 是 58 块的说法）
            sid = OLD_TAGS.get(op.get("tag"), op.get("tag")) if kind == "tag" else op.get("section")
            sec = section_of(d, str(sid or ""))
            if not sec:
                raise MediaError("没有这个分区")
            ref = _ref(op.get("ref"))
            if not ref:
                raise MediaError("只能加文件夹、媒体库、歌单、专辑、歌手或单个")
            refs = [r for r in sec["refs"] if r["id"] != ref["id"]]
            if op.get("on", True):
                if len(refs) >= 30:
                    raise MediaError("一个分区最多 30 项")
                refs.append(ref)
            sec["refs"] = refs
            _members.clear()
        elif kind == "section":                              # 改名、儿童区、放法
            sec = section_of(d, str(op.get("id") or ""))
            if not sec:
                raise MediaError("没有这个分区")
            if "name" in op:
                name = str(op.get("name") or "").strip()[:12]
                if not name:
                    raise MediaError("分区要有个名字（说这个名字就能放）")
                if any(s["name"] == name and s["id"] != sec["id"] for s in d["sections"]):
                    raise MediaError(f"已经有叫「{name}」的分区了")
                sec["name"] = name
            if "kid" in op:
                sec["kid"] = bool(op["kid"])
            if "play" in op:
                if op["play"] not in PLAYS:
                    raise MediaError("放法只有随机、按顺序、按名字找")
                sec["play"] = op["play"]
        elif kind == "add_section":
            name = str(op.get("name") or "").strip()[:12]
            if not name:
                raise MediaError("分区要有个名字（说这个名字就能放）")
            if any(s["name"] == name for s in d["sections"]):
                raise MediaError(f"已经有叫「{name}」的分区了")
            if len(d["sections"]) >= MAX_SECTIONS:
                raise MediaError(f"最多 {MAX_SECTIONS} 个分区")
            d["sections"].append({"id": "c" + secrets.token_hex(3), "name": name, "kid": bool(op.get("kid")),
                                  "play": op.get("play") if op.get("play") in PLAYS else "shuffle", "refs": []})
        elif kind == "delete_section":
            sid = str(op.get("id") or "")
            if sid in {p["id"] for p in PRESETS}:
                raise MediaError("预设的分区不能删，可以清空、改名")
            before = len(d["sections"])
            d["sections"] = [s for s in d["sections"] if s["id"] != sid]
            if len(d["sections"]) == before:
                raise MediaError("没有这个分区")
        else:
            raise MediaError("不认识的操作")
        save(d)
        return d


async def apply_async(op: dict) -> dict:
    """{"op": "jellyfin"|"emby", "url", "user", "password" | "api_key"}：先在服务器上换 token（密码不存），再存。"""
    which = op.get("op")
    if which not in SERVERS:
        return await asyncio.to_thread(apply, op)
    cls = SERVERS[which]
    url = _norm_url(op.get("url"), strip=("/emby",))
    if not url:
        return await asyncio.to_thread(apply, {"op": f"{which}_clear"})
    d = load()
    jf = d[which]
    user, password, key = str(op.get("user") or "").strip()[:64], str(op.get("password") or ""), str(op.get("api_key") or "").strip()
    device = jf["device"] or secrets.token_hex(8)
    if key:
        src = cls(url, "", key, device)
        uid = await src.find_user(user)
        saved = {"url": url, "user": user or src.user_name, "user_id": uid, "token": key, "device": device, "key": True}
    elif password:
        if not user:
            raise MediaError(f"填上 {cls.name} 的用户名")
        token, uid = await cls.login(url, user, password, device)
        saved = {"url": url, "user": user, "user_id": uid, "token": token, "device": device, "key": False}
    elif jf["token"] and user == jf["user"]:
        saved = {"url": url}
    else:
        raise MediaError("填上密码或者 API Key（改了用户名也要重新填）")
    return await asyncio.to_thread(apply, {"op": f"{which}_saved", **saved})


# ---------------------------------------------------------------- 文字和模糊匹配

BRACKETS = re.compile(r"[（(【\[《<].*?[)）】\]》>]")
NOISE = re.compile(r"[\s\-_·•.,，。!！?？、:：;；'\"“”‘’~～]+")


def norm(s: str) -> str:
    """比较用：去括号里的（「(儿歌版)」）、标点空格、开头的曲目号，转小写。"""
    s = BRACKETS.sub("", str(s or ""))
    s = re.sub(r"^\s*\d{1,3}\s*[-_.、 ]\s*", "", s)
    return NOISE.sub("", s).lower()


def score(q: str, title: str) -> float:
    """说的和名字有多像，0～1。ASR 常把名字说长或说短一点、错一两个同音字。"""
    a, b = norm(q), norm(title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 2 and (a in b or b in a):
        return 0.9 - 0.2 * abs(len(a) - len(b)) / max(len(a), len(b))
    r = difflib.SequenceMatcher(None, a, b).ratio()
    pairs = lambda s: {s[i:i + 2] for i in range(len(s) - 1)} or {s}
    pa, pb = pairs(a), pairs(b)
    overlap = len(pa & pb) / max(1, min(len(pa), len(pb)))
    return max(r, overlap * 0.85)


def score_track(q: str, t: dict) -> float:
    """名字最要紧；剧集名、歌手、专辑也算（「小猪佩奇」找它的每一集），分数打点折。"""
    return max(score(q, t.get("title")), score(q, t.get("artist")) * 0.9, score(q, t.get("album")) * 0.85)


def split_query(q: str) -> tuple:
    """「周杰伦的歌」→ ("周杰伦", "artist")；「两只老虎这首歌」→ ("两只老虎", "")。"""
    q = re.sub(r"(?:这首歌|那首歌|这首|那首|这个歌|歌曲|的视频|视频|这一集|那一集)$", "", str(q or "").strip())
    m = re.fullmatch(r"(.{1,12}?)(?:的歌|唱的歌|唱的)", q)
    if m:
        return m.group(1), "artist"
    return q, ""


# ---------------------------------------------------------------- 来源

def _track(src, sid, title, artist="", album="", duration=0, cover="", media="audio", height=0):
    t = {"id": f"{src}.{sid}", "title": str(title or "")[:80], "artist": str(artist or "")[:60],
         "album": str(album or "")[:60], "duration": int(duration or 0), "cover": f"{src}.{cover}" if cover else "",
         "media": media}
    if height:
        t["height"] = int(height)
    return t


class Source:
    """来源的接口：三种来源都实现。id 都不带来源前缀（前缀由 get()/split_id() 处理）。"""
    key = ""
    name = ""

    async def ping(self) -> dict: raise NotImplementedError
    async def search(self, q: str, n: int = 20) -> dict: raise NotImplementedError       # {"songs", "albums", "artists"}
    async def index(self) -> list: raise NotImplementedError                               # 全部能放的（找用）
    async def albums(self, offset: int = 0, size: int = 60) -> list: raise NotImplementedError
    async def playlists(self) -> list: raise NotImplementedError
    async def folders(self) -> list: raise NotImplementedError                             # 最上一层
    async def open(self, typ: str, rid: str) -> dict: raise NotImplementedError            # {"name", "items", "songs"}
    async def songs(self, typ: str, rid: str, limit: int = 300) -> list: raise NotImplementedError   # 按顺序
    async def random(self, n: int = 30) -> list: raise NotImplementedError
    async def info(self, rid: str) -> dict: return {"direct": True}                         # 视频：能不能直接拖动
    async def stream(self, request: web.Request, rid: str) -> web.StreamResponse: raise NotImplementedError
    async def cover(self, request: web.Request, rid: str, size: int = 300) -> web.StreamResponse: raise NotImplementedError


def _safe(e: Exception) -> str:
    """异常说成人话，不带地址（aiohttp 的异常文字里有完整 URL，可能含 token）。"""
    if isinstance(e, MediaError):
        return str(e)
    if isinstance(e, asyncio.TimeoutError):
        return "超时"
    if isinstance(e, aiohttp.ClientConnectorError):
        return "连不上"
    if isinstance(e, aiohttp.ClientResponseError):
        return f"返回 {e.status}"
    return type(e).__name__


async def _relay(request, url, params, headers=None, ctype=None, cache=False, sid="music"):
    """把来源的回复原样转给设备/浏览器（带上 Range：能拖动、能从中间接着放）。"""
    hdrs = dict(headers or {})
    if request.headers.get("Range"):
        hdrs["Range"] = request.headers["Range"]
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=5, sock_read=60))
    try:
        upstream = await session.get(url, params=params, headers=hdrs)
    except Exception as e:  # noqa: BLE001
        await session.close()
        services.record(sid, False, None, _safe(e))
        raise MediaError(_safe(e)) from None
    try:
        if upstream.status not in (200, 206) or upstream.content_type in ("application/json", "text/xml", "text/html"):
            raise MediaError(f"返回 {upstream.status}")
        resp = web.StreamResponse(status=upstream.status)
        for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
            if h in upstream.headers:
                resp.headers[h] = upstream.headers[h]
        if ctype and "Content-Type" not in upstream.headers:
            resp.headers["Content-Type"] = ctype
        resp.headers["Cache-Control"] = "private, max-age=86400" if cache else "no-store"
        await resp.prepare(request)
        async for chunk in upstream.content.iter_chunked(64 * 1024):
            await resp.write(chunk)
        await resp.write_eof()
        return resp
    finally:
        upstream.release()
        await session.close()


def _known(name) -> str:
    """Navidrome 没有标签时给「[Unknown Artist]」「[Unknown Album]」：当成没有。"""
    name = str(name or "")
    return "" if re.fullmatch(r"\[Unknown [A-Za-z ]+\]", name) else name


class Navidrome(Source):
    key, name = "n", "Navidrome"

    def __init__(self, url, user, salt, token, timeout=8.0):
        self.url, self.user, self.salt, self.token = url.rstrip("/"), user, salt, token
        self.timeout = timeout

    def _params(self, extra=None) -> dict:
        p = {"u": self.user, "t": self.token, "s": self.salt, "v": API_VERSION, "c": CLIENT, "f": "json"}
        p.update({k: v for k, v in (extra or {}).items() if v is not None})
        return p

    async def call(self, method: str, record=True, **params) -> dict:
        t0 = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout, connect=4)) as s:
                async with s.get(f"{self.url}/rest/{method}", params=self._params(params)) as r:
                    if r.status != 200:
                        raise MediaError(f"返回 {r.status}" + ("（地址不对？）" if r.status == 404 else ""))
                    try:
                        data = await r.json(content_type=None)
                    except ValueError:
                        raise MediaError("返回的不是 Subsonic 接口（地址填 Navidrome 的首页地址，比如 http://NAS:4533）")
            body = (data or {}).get("subsonic-response") or {}
            if body.get("status") != "ok":
                err = body.get("error") or {}
                raise MediaError({40: "用户名或密码不对", 41: "这个服务器不支持 token 认证", 50: "这个账号没有权限",
                                  70: "找不到"}.get(err.get("code"), str(err.get("message") or "出错了")[:60]))
        except Exception as e:  # noqa: BLE001
            if record:
                services.record("music", False, (time.monotonic() - t0) * 1000, _safe(e))
            raise MediaError(_safe(e)) from None
        if record:
            services.record("music", True, (time.monotonic() - t0) * 1000)
        return body

    async def ping(self) -> dict:
        body = await self.call("ping")
        libs = (await self.call("getMusicFolders")).get("musicFolders", {}).get("musicFolder") or []
        return {"ok": True, "version": body.get("serverVersion") or body.get("version") or "",
                "server": body.get("type") or "Subsonic", "libraries": [str(x.get("name") or "") for x in libs]}

    def _song(self, x):
        return _track("n", x.get("id"), x.get("title"), _known(x.get("artist")), _known(x.get("album")), x.get("duration"), x.get("coverArt"))

    def _album(self, x):
        return {"id": f"n.{x.get('id')}", "type": "album", "name": str(x.get("name") or x.get("title") or "")[:80],
                "sub": " · ".join(p for p in (_known(x.get("artist")), f"{x['songCount']} 首" if x.get("songCount") else "") if p),
                "cover": f"n.{x['coverArt']}" if x.get("coverArt") else ""}

    async def search(self, q, n=20):
        r = (await self.call("search3", query=q, songCount=n, albumCount=8, artistCount=5)).get("searchResult3") or {}
        return {"songs": [self._song(x) for x in r.get("song") or []],
                "albums": [self._album(x) for x in r.get("album") or []],
                "artists": [{"id": f"n.{x.get('id')}", "type": "artist", "name": str(x.get("name") or ""),
                             "sub": f"{x.get('albumCount') or 0} 张专辑", "cover": f"n.{x['coverArt']}" if x.get("coverArt") else ""}
                            for x in r.get("artist") or []]}

    async def index(self):
        """全部歌（search3 空查询分页取）：Navidrome 的搜索只认词开头，中文歌名「老虎」搜不到「两只老虎」。"""
        out, offset = [], 0
        while len(out) < INDEX_MAX:
            r = (await self.call("search3", query="", songCount=500, songOffset=offset, albumCount=0, artistCount=0)
                 ).get("searchResult3") or {}
            page = r.get("song") or []
            out += [self._song(x) for x in page]
            if len(page) < 500:
                break
            offset += 500
        return out

    async def albums(self, offset=0, size=60):
        r = await self.call("getAlbumList2", type="alphabeticalByName", size=size, offset=offset)
        return [self._album(x) for x in (r.get("albumList2") or {}).get("album") or []]

    async def playlists(self):
        r = await self.call("getPlaylists")
        return [{"id": f"n.{x.get('id')}", "type": "playlist", "name": str(x.get("name") or ""),
                 "sub": f"{x.get('songCount') or 0} 首", "cover": f"n.{x['coverArt']}" if x.get("coverArt") else ""}
                for x in (r.get("playlists") or {}).get("playlist") or []]

    async def folders(self):
        r = await self.call("getMusicFolders")
        return [{"id": f"n.lib{x.get('id')}", "type": "folder", "name": str(x.get("name") or "音乐库"), "sub": "音乐库", "cover": ""}
                for x in (r.get("musicFolders") or {}).get("musicFolder") or []]

    async def open(self, typ, rid):
        if typ == "folder" and rid.startswith("lib"):
            r = await self.call("getIndexes", musicFolderId=rid[3:])
            idx = r.get("indexes") or {}
            items = [{"id": f"n.{a.get('id')}", "type": "folder", "name": str(a.get("name") or ""), "sub": "", "cover": ""}
                     for i in idx.get("index") or [] for a in i.get("artist") or []]
            return {"name": "", "items": items, "songs": [self._song(x) for x in idx.get("child") or [] if not x.get("isDir")]}
        if typ == "folder":
            d = (await self.call("getMusicDirectory", id=rid)).get("directory") or {}
            kids = d.get("child") or []
            return {"name": str(d.get("name") or ""),
                    "items": [{"id": f"n.{x.get('id')}", "type": "folder", "name": str(x.get("title") or x.get("name") or ""),
                               "sub": str(x.get("artist") or ""), "cover": f"n.{x['coverArt']}" if x.get("coverArt") else ""}
                              for x in kids if x.get("isDir")],
                    "songs": [self._song(x) for x in kids if not x.get("isDir")]}
        if typ == "playlist":
            p = (await self.call("getPlaylist", id=rid)).get("playlist") or {}
            return {"name": str(p.get("name") or ""), "items": [], "songs": [self._song(x) for x in p.get("entry") or []]}
        if typ == "album":
            a = (await self.call("getAlbum", id=rid)).get("album") or {}
            return {"name": str(a.get("name") or ""), "items": [], "songs": [self._song(x) for x in a.get("song") or []]}
        if typ == "artist":
            a = (await self.call("getArtist", id=rid)).get("artist") or {}
            return {"name": str(a.get("name") or ""), "items": [self._album(x) for x in a.get("album") or []], "songs": []}
        if typ == "item":
            s = (await self.call("getSong", id=rid)).get("song") or {}
            return {"name": str(s.get("title") or ""), "items": [], "songs": [self._song(s)] if s else []}
        raise MediaError("打不开这一项")

    async def songs(self, typ, rid, limit=300):
        """分区里的一项展开成歌（按顺序）：文件夹往下找 4 层，歌手展开所有专辑。"""
        got = await self.open(typ, rid)
        out = list(got["songs"])
        if typ in ("folder", "artist"):
            todo = [(x["type"], x["id"].split(".", 1)[1], 1) for x in got["items"]]
            while todo and len(out) < limit:
                t, i, depth = todo.pop(0)
                sub = await self.open(t, i)
                out += sub["songs"]
                if depth < 4:
                    todo += [(x["type"], x["id"].split(".", 1)[1], depth + 1) for x in sub["items"]]
        return out[:limit]

    async def random(self, n=30):
        r = await self.call("getRandomSongs", size=n)
        return [self._song(x) for x in (r.get("randomSongs") or {}).get("song") or []]

    async def stream(self, request, rid):
        return await _relay(request, f"{self.url}/rest/stream", self._params({"id": rid, "format": "raw"}))

    async def cover(self, request, rid, size=300):
        return await _relay(request, f"{self.url}/rest/getCoverArt", self._params({"id": rid, "size": size}), ctype="image/jpeg", cache=True)


# Jellyfin 的类型：文件夹一类能打开，其余能放
JF_FOLDERS = {"CollectionFolder", "Folder", "Series", "Season", "BoxSet", "MusicAlbum", "Playlist", "UserView", "MusicArtist",
              "PhotoAlbum", "AggregateFolder"}
JF_VIDEO = {"Movie", "Episode", "Video", "MusicVideo", "Trailer"}
JF_AUDIO = {"Audio", "AudioBook"}
JF_PLAYABLE = ",".join(sorted(JF_VIDEO | JF_AUDIO))
JF_ORDER = "ParentIndexNumber,IndexNumber,SortName"


class Jellyfin(Source):
    """Jellyfin 的 REST API（Emby 也用这个类，只是认证头不同）。认证放在请求头里（不进 URL，也就不会出现在任何日志里）。"""
    key, name, sid = "j", "Jellyfin", "jellyfin"

    def __init__(self, url, user_id, token, device, timeout=10.0):
        self.url, self.user_id, self.token, self.device = url.rstrip("/"), user_id, token, device or "xiaozhi-hub"
        self.timeout = timeout
        self.user_name = ""

    @staticmethod
    def _auth(device, token=""):
        h = f'MediaBrowser Client="{CLIENT}", Device="xiaozhi-hub", DeviceId="{device}", Version="1.0"'
        return h + (f', Token="{token}"' if token else "")

    @classmethod
    def _auth_headers(cls, device, token=""):
        return {"Authorization": cls._auth(device, token)}

    def _headers(self):
        return self._auth_headers(self.device, self.token)

    @classmethod
    async def login(cls, url, user, password, device):
        """用户名密码换 AccessToken（密码只用这一次）→ (token, user_id)。"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10, connect=4)) as s:
                async with s.post(f"{url}/Users/AuthenticateByName", json={"Username": user, "Pw": password},
                                  headers=cls._auth_headers(device)) as r:
                    if r.status in (401, 403):
                        raise MediaError("用户名或密码不对")
                    if r.status != 200:
                        raise MediaError(f"返回 {r.status}" + (f"（地址不对？填 {cls.name} 网页的地址，比如 http://NAS:8096）" if r.status == 404 else ""))
                    data = await r.json(content_type=None)
        except MediaError:
            raise
        except Exception as e:  # noqa: BLE001
            raise MediaError(_safe(e)) from None
        token, uid = (data or {}).get("AccessToken"), ((data or {}).get("User") or {}).get("Id")
        if not token or not uid:
            raise MediaError("返回的不是 Jellyfin 接口")
        return token, uid

    async def call(self, path, record=True, **params):
        t0 = time.monotonic()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout, connect=4)) as s:
                async with s.get(f"{self.url}{path}", params={k: v for k, v in params.items() if v is not None},
                                 headers=self._headers()) as r:
                    if r.status == 401:
                        raise MediaError("登录过期或 API Key 不对：在「媒体」页重新填一次")
                    if r.status != 200:
                        raise MediaError(f"返回 {r.status}")
                    try:
                        data = await r.json(content_type=None)
                    except ValueError:
                        raise MediaError("返回的不是 Jellyfin 接口")
        except Exception as e:  # noqa: BLE001
            if record:
                services.record(self.sid, False, (time.monotonic() - t0) * 1000, _safe(e))
            raise MediaError(_safe(e)) from None
        if record:
            services.record(self.sid, True, (time.monotonic() - t0) * 1000)
        return data

    async def find_user(self, name):
        """填的是 API Key：按用户名找用户 id（没填用户名用第一个管理员）。"""
        users = await self.call("/Users")
        pick = next((u for u in users if name and u.get("Name") == name), None) or \
            next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), None) or (users[0] if users else None)
        if not pick:
            raise MediaError("这个 Jellyfin 上没有用户")
        self.user_id, self.user_name = pick["Id"], pick.get("Name") or ""
        return pick["Id"]

    async def ping(self):
        info = await self.call("/System/Info")
        views = await self.call(f"/Users/{self.user_id}/Views")
        return {"ok": True, "version": info.get("Version") or "", "server": info.get("ServerName") or "Jellyfin",
                "libraries": [str(v.get("Name") or "") for v in (views or {}).get("Items") or []]}

    def _cover_of(self, x):
        if (x.get("ImageTags") or {}).get("Primary"):
            return x.get("Id")
        return x.get("SeriesId") or x.get("AlbumId") or x.get("ParentId") or ""

    def _media(self, x):
        typ = x.get("Type")
        video = typ in JF_VIDEO
        title = x.get("Name") or ""
        if typ == "Episode" and x.get("IndexNumber"):
            title = f"第 {x['IndexNumber']} 集 · {title}"
        artist = ", ".join(x.get("Artists") or []) or x.get("SeriesName") or x.get("AlbumArtist") or ""
        album = x.get("Album") or x.get("SeasonName") or ""
        dur = int((x.get("RunTimeTicks") or 0) / 1e7)
        return _track(self.key, x.get("Id"), title, artist, album, dur, self._cover_of(x), "video" if video else "audio",
                      x.get("Height") or 0)

    def _folder(self, x):
        typ = x.get("Type") or ""
        kind = {"Playlist": "playlist", "MusicAlbum": "album", "MusicArtist": "artist"}.get(typ, "folder")
        sub = {"CollectionFolder": "媒体库", "UserView": "媒体库", "Series": "剧集", "Season": "季", "BoxSet": "合集",
               "MusicAlbum": "专辑", "Playlist": "播放列表", "Folder": "文件夹", "MusicArtist": "歌手"}.get(typ, "")
        if x.get("ChildCount"):
            sub += f" · {x['ChildCount']} 个"
        cover = self._cover_of(x)
        return {"id": f"{self.key}.{x.get('Id')}", "type": kind, "name": str(x.get("Name") or "")[:80], "sub": sub,
                "cover": f"{self.key}.{cover}" if cover else ""}

    def _split(self, items):
        folders = [self._folder(x) for x in items if x.get("IsFolder") or x.get("Type") in JF_FOLDERS]
        media = [self._media(x) for x in items if not (x.get("IsFolder") or x.get("Type") in JF_FOLDERS)
                 and x.get("Type") in JF_VIDEO | JF_AUDIO]
        return folders, media

    async def items(self, **params):
        params.setdefault("Fields", "ChildCount,ParentId,Height")
        r = await self.call(f"/Users/{self.user_id}/Items", **params)
        return (r or {}).get("Items") or []

    async def search(self, q, n=20):
        got = await self.items(SearchTerm=q, Recursive="true", Limit=n * 2,
                               IncludeItemTypes=JF_PLAYABLE + ",Series,MusicAlbum,BoxSet")
        folders, media = self._split(got)
        return {"songs": media[:n], "albums": [f for f in folders if f["type"] in ("folder", "album")], "artists": []}

    async def index(self):
        got = await self.items(Recursive="true", IncludeItemTypes=JF_PLAYABLE, Limit=INDEX_MAX, SortBy="SortName")
        return self._split(got)[1]

    async def albums(self, offset=0, size=60):
        got = await self.items(Recursive="true", IncludeItemTypes="Series,MusicAlbum,BoxSet", SortBy="SortName",
                               StartIndex=offset, Limit=size)
        return self._split(got)[0]

    async def playlists(self):
        got = await self.items(Recursive="true", IncludeItemTypes="Playlist", SortBy="SortName")
        return self._split(got)[0]

    async def folders(self):
        r = await self.call(f"/Users/{self.user_id}/Views")
        return [self._folder(x) for x in (r or {}).get("Items") or []]

    async def open(self, typ, rid):
        if typ == "item":
            x = await self.call(f"/Users/{self.user_id}/Items/{rid}")
            return {"name": x.get("Name") or "", "items": [], "songs": [self._media(x)]}
        got = await self.items(ParentId=rid, SortBy=JF_ORDER)
        folders, media = self._split(got)
        return {"name": "", "items": folders, "songs": media}

    async def songs(self, typ, rid, limit=300):
        if typ == "item":
            return (await self.open("item", rid))["songs"]
        got = await self.items(ParentId=rid, Recursive="true", IncludeItemTypes=JF_PLAYABLE,
                               SortBy="SeriesSortName,ParentIndexNumber,IndexNumber,SortName", Limit=limit)
        return self._split(got)[1][:limit]

    async def random(self, n=30):
        got = await self.items(Recursive="true", IncludeItemTypes="Audio", SortBy="Random", Limit=n)
        return self._split(got)[1]

    async def _source(self, rid):
        """第一个媒体源：容器、视频编码和高度、音频编码。"""
        info = await self.call(f"/Items/{rid}/PlaybackInfo", UserId=self.user_id)
        src = ((info or {}).get("MediaSources") or [{}])[0]
        streams = src.get("MediaStreams") or []
        v = next((s for s in streams if s.get("Type") == "Video"), {})
        a = next((s for s in streams if s.get("Type") == "Audio"), {})
        return {"id": src.get("Id") or rid, "container": str(src.get("Container") or "").lower(),
                "vcodec": str(v.get("Codec") or "").lower(), "height": int(v.get("Height") or 0),
                "acodec": str(a.get("Codec") or "").lower(), "video": bool(v)}

    @staticmethod
    def direct_ok(s) -> bool:
        """设备的网页能直接放（实测）：H.264 + AAC/MP3 的 MP4，不高于 720p。"""
        return (s["container"].split(",")[0] in ("mp4", "m4v", "mov") and s["vcodec"] == "h264"
                and 0 < s["height"] <= DIRECT_MAX_HEIGHT and s["acodec"] in ("aac", "mp3", ""))

    async def info(self, rid):
        s = await self._source(rid)
        return {"direct": not s["video"] or self.direct_ok(s), "height": s["height"], "codec": s["vcodec"]}

    async def stream(self, request, rid):
        s = await self._source(rid)
        if not s["video"]:
            return await _relay(request, f"{self.url}/Audio/{rid}/stream", {"static": "true", "mediaSourceId": s["id"]},
                                self._headers(), sid=self.sid)
        if self.direct_ok(s):
            return await _relay(request, f"{self.url}/Videos/{rid}/stream", {"static": "true", "mediaSourceId": s["id"]},
                                self._headers(), sid=self.sid)
        try:
            start = max(0.0, float(request.query.get("t") or 0))
        except ValueError:
            start = 0.0
        params = {"mediaSourceId": s["id"], "VideoCodec": "h264", "AudioCodec": "aac", "MaxWidth": 1280, "MaxHeight": 720,
                  "VideoBitrate": 2500000, "AudioBitrate": 128000, "AudioChannels": 2, "static": "false",
                  "PlaySessionId": secrets.token_hex(8), "StartTimeTicks": int(start * 1e7) or None}
        return await _relay(request, f"{self.url}/Videos/{rid}/stream.mp4", {k: v for k, v in params.items() if v is not None},
                            self._headers(), ctype="video/mp4", sid=self.sid)

    async def cover(self, request, rid, size=300):
        return await _relay(request, f"{self.url}/Items/{rid}/Images/Primary", {"maxHeight": size, "quality": 85},
                            self._headers(), ctype="image/jpeg", cache=True, sid=self.sid)


class Emby(Jellyfin):
    """Emby：接口和 Jellyfin 一样，认证用 X-Emby-Authorization / X-Emby-Token。"""
    key, name, sid = "e", "Emby", "emby"

    @staticmethod
    def _auth(device, token=""):
        return f'Emby Client="{CLIENT}", Device="xiaozhi-hub", DeviceId="{device}", Version="1.0"'

    @classmethod
    def _auth_headers(cls, device, token=""):
        h = {"X-Emby-Authorization": cls._auth(device)}
        if token:
            h["X-Emby-Token"] = token
        return h


SERVERS = {"jellyfin": Jellyfin, "emby": Emby}


def _enc(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode().rstrip("=")


def _dec(rid: str) -> str:
    try:
        return base64.urlsafe_b64decode(rid + "=" * (-len(rid) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise MediaError("没有这一项") from None


class Local(Source):
    """网关本机的 music/ 文件夹：子文件夹当专辑/文件夹，文件名当名字（去掉开头的序号）；音频、视频都行。"""
    key, name = "l", "网关本机"

    def __init__(self, root: Path = None):
        self.root = Path(root or local_root())

    def path(self, rid: str) -> Path:
        rel = _dec(rid) if rid else ""
        p = (self.root / rel).resolve()
        root = self.root.resolve()
        if p != root and root not in p.parents:
            raise MediaError("没有这一项")
        return p

    def _rel(self, p: Path) -> str:
        rel = str(p.resolve().relative_to(self.root.resolve())).replace(os.sep, "/")
        return "" if rel == "." else rel

    def _song(self, p: Path):
        title = re.sub(r"^\s*\d{1,3}\s*[-_.、 ]\s*", "", p.stem) or p.stem
        parent = p.parent.name if p.parent.resolve() != self.root.resolve() else ""
        cover = _enc(self._rel(p.parent)) if self._cover_of(p.parent) else ""
        return _track("l", _enc(self._rel(p)), title, "", parent, 0, cover,
                      "video" if p.suffix.lower() in VIDEO_EXT else "audio")

    def _cover_of(self, d: Path):
        for n in COVER_NAMES:
            if (d / n).is_file():
                return d / n
        return None

    def _files(self, d: Path, deep=True):
        if not d.is_dir():
            return []
        it = d.rglob("*") if deep else d.iterdir()
        return sorted((p for p in it if p.is_file() and p.suffix.lower() in AUDIO_EXT | VIDEO_EXT and not p.name.startswith(".")),
                      key=lambda p: str(p))

    def _dir_item(self, d: Path):
        n = len(self._files(d))
        return {"id": f"l.{_enc(self._rel(d))}", "type": "folder", "name": d.name, "sub": f"{n} 个",
                "cover": f"l.{_enc(self._rel(d))}" if self._cover_of(d) else ""}

    async def ping(self):
        n = await asyncio.to_thread(lambda: len(self._files(self.root)))
        return {"ok": True, "version": "", "server": "本机文件夹", "libraries": [f"{n} 个"]}

    async def search(self, q, n=20):
        idx = await self.index()
        hits = sorted(((score(q, s["title"]), s) for s in idx), key=lambda x: -x[0])
        return {"songs": [s for sc, s in hits if sc >= 0.5][:n], "albums": [], "artists": []}

    async def index(self):
        return await asyncio.to_thread(lambda: [self._song(p) for p in self._files(self.root)[:INDEX_MAX]])

    async def albums(self, offset=0, size=60):
        def run():
            dirs = sorted({p.parent for p in self._files(self.root)} - {self.root.resolve(), self.root}, key=str)
            return [dict(self._dir_item(d), type="album") for d in dirs[offset:offset + size]]
        return await asyncio.to_thread(run)

    async def playlists(self):
        return []

    async def folders(self):
        return [{"id": "l.", "type": "folder", "name": "music", "sub": "网关本机", "cover": ""}]

    async def open(self, typ, rid):
        if typ == "item":
            p = self.path(rid)
            return {"name": p.stem, "items": [], "songs": [self._song(p)] if p.is_file() else []}
        if typ not in ("folder", "album"):
            raise MediaError("打不开这一项")
        d = self.path(rid)

        def run():
            if not d.is_dir():
                raise MediaError("没有这个文件夹")
            subs = sorted((p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name)
            return {"name": d.name if d.resolve() != self.root.resolve() else "music",
                    "items": [self._dir_item(p) for p in subs], "songs": [self._song(p) for p in self._files(d, deep=False)]}
        return await asyncio.to_thread(run)

    async def songs(self, typ, rid, limit=300):
        if typ == "item":
            return (await self.open("item", rid))["songs"]
        d = self.path(rid)
        return await asyncio.to_thread(lambda: [self._song(p) for p in self._files(d)[:limit]])

    async def random(self, n=30):
        idx = [s for s in await self.index() if s["media"] == "audio"]
        return random.sample(idx, min(n, len(idx)))

    async def info(self, rid):
        p = self.path(rid)
        return {"direct": p.suffix.lower() in (".mp4", ".m4v", ".mov") or p.suffix.lower() in AUDIO_EXT}

    async def stream(self, request, rid):
        p = self.path(rid)
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXT | VIDEO_EXT:
            raise MediaError("没有这一项")
        return web.FileResponse(p, headers={"Cache-Control": "no-store"})

    async def cover(self, request, rid, size=300):
        p = self.path(rid)
        c = self._cover_of(p if p.is_dir() else p.parent)
        if not c:
            raise MediaError("没有封面")
        return web.FileResponse(c, headers={"Cache-Control": "private, max-age=86400"})

    # ---- 后台管理
    def save_upload_path(self, dir_id: str, name: str) -> Path:
        d = self.path(dir_id)
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", os.path.basename(str(name or ""))).strip()[:120]
        if not name or name.startswith("."):
            raise MediaError("文件名不对")
        if Path(name).suffix.lower() not in AUDIO_EXT | VIDEO_EXT and name.lower() not in COVER_NAMES:
            raise MediaError("只能传音频（mp3、m4a、flac…）、视频（mp4…）或封面 cover.jpg")
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def mkdir(self, dir_id: str, name: str) -> dict:
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name or "")).strip()[:60]
        if not name or name.startswith("."):
            raise MediaError("文件夹名字不对")
        p = self.path(dir_id) / name
        p.mkdir(parents=True, exist_ok=True)
        return self._dir_item(p)

    def delete(self, rid: str):
        p = self.path(rid)
        if p.resolve() == self.root.resolve():
            raise MediaError("不能删整个 music 文件夹")
        if p.is_dir():
            if any(p.iterdir()):
                raise MediaError("文件夹不是空的，先删里面的")
            p.rmdir()
        elif p.is_file():
            p.unlink()
        else:
            raise MediaError("没有这一项")


# ---------------------------------------------------------------- 用哪个来源

def navidrome(d: dict = None, password: str = None, url: str = None, user: str = None):
    """配好的 Navidrome；后台「测试」可以传没保存的地址、用户、密码。没配好返回 None。"""
    nv = (d or load())["navidrome"]
    url = _norm_url(url) if url is not None else nv["url"]
    user = (user if user is not None else nv["user"]).strip()
    salt, token = token_for(password) if password else (nv["salt"], nv["token"])
    if not (url and user and token):
        return None
    return Navidrome(url, user, salt, token)


def jellyfin(d: dict = None, which: str = "jellyfin"):
    """配好的 Jellyfin（which="emby"：Emby）；没配好 None。"""
    jf = (d or load())[which]
    if not (jf["url"] and jf["token"] and jf["user_id"]):
        return None
    return SERVERS[which](jf["url"], jf["user_id"], jf["token"], jf["device"])


def emby(d: dict = None):
    return jellyfin(d, "emby")


def sources(d: dict = None) -> list:
    """开着、配好的来源：Navidrome、Jellyfin、本机（本机没有文件时不算）。"""
    d = d or load()
    out = []
    nv = navidrome(d)
    if nv and d["navidrome"]["on"]:
        out.append(nv)
    for which in SERVERS:
        jf = jellyfin(d, which)
        if jf and d[which]["on"]:
            out.append(jf)
    if d["local"]["on"]:
        lo = Local()
        if lo.root.is_dir() and any(lo._files(lo.root)[:1]):
            out.append(lo)
    return out


def get(key: str, d: dict = None) -> Source:
    """按 id 前缀拿来源（关掉的也能拿：已经标好的、正在放的不受影响）。"""
    d = d or load()
    if key == "n":
        src = navidrome(d)
        if not src:
            raise MediaError("还没在后台填 Navidrome 的地址和账号")
        return src
    for which, cls in SERVERS.items():
        if key == cls.key:
            src = jellyfin(d, which)
            if not src:
                raise MediaError(f"还没在后台填 {cls.name} 的地址和账号")
            return src
    if key == "l":
        return Local()
    raise MediaError("没有这个来源")


def split_id(sid: str) -> tuple:
    m = re.fullmatch(r"([njel])\.([\w\-=./]{0,400})", str(sid or ""))
    if not m:
        raise MediaError("没有这一项")
    return m.group(1), m.group(2)


# ---------------------------------------------------------------- 分区

def words_of(sec: dict) -> list:
    preset = next((p for p in PRESETS if p["id"] == sec["id"]), None)
    return [sec["name"]] + [w for w in (preset or {}).get("words", []) if w != sec["name"]]


def find_section(word: str, kid: bool = False, d: dict = None):
    """说的「英语」「儿歌」「动画片」→ 分区。孩子只认儿童区；大人两边都有（「英语」）时先大人区里有东西的。"""
    d = d or load()
    w = norm(word)
    if not w:
        return None
    hits = []
    for s in d["sections"]:
        if kid and not s["kid"]:
            continue
        ws = [norm(x) for x in words_of(s)]
        if w in ws:
            hits.append((2, s))
        elif any(len(x) >= 2 and (x in w and len(w) - len(x) <= 2) for x in ws):
            hits.append((1, s))
    if not hits:
        return None
    best = max(h[0] for h in hits)
    hits = [s for p, s in hits if p == best]
    hits.sort(key=lambda s: (not s["refs"], s["kid"]))       # 有东西的在前；都有时大人区在前（孩子已经只剩儿童区）
    return hits[0]


_members = {}         # 分区 id → (时间, [歌])
_index = {}           # 来源 key → (时间, [歌])


async def section_items(sec: dict, d: dict = None, limit: int = 300) -> list:
    """分区里的全部（按顺序，去重），缓存 10 分钟。"""
    hit = _members.get(sec["id"])
    if hit and time.time() - hit[0] < INDEX_TTL:
        return hit[1]
    d = d or load()
    out, errors = [], []
    for ref in sec["refs"]:
        key, rid = split_id(ref["id"])
        try:
            out += await get(key, d).songs(ref["type"], rid, limit=limit)
        except MediaError as e:
            errors.append(str(e))
    if not out and errors:
        raise MediaError(f"{sec['name']}放不了：{errors[0]}")
    seen, uniq = set(), []
    for s in out:
        if s["id"] not in seen:
            seen.add(s["id"])
            uniq.append(s)
    _members[sec["id"]] = (time.time(), uniq)
    return uniq


async def _cached_index(src: Source) -> list:
    hit = _index.get(src.key)
    if hit and time.time() - hit[0] < INDEX_TTL:
        return hit[1]
    songs = await src.index()
    _index[src.key] = (time.time(), songs)
    return songs


def _pick(best, n, video=None):
    seen, out = set(), []
    for sc, s in sorted(best, key=lambda x: (-x[0] - (0.05 if video and s_media(x[1]) == "video" else 0))):
        if sc < 0.6 or s["id"] in seen:
            continue
        seen.add(s["id"])
        out.append(s)
    return out[:n]


def s_media(s):
    return s.get("media") or "audio"


async def find(q: str, n: int = 20, kid: bool = False, video: bool = None, section: str = None, allowed: list = None) -> dict:
    """「两只老虎」「小猪佩奇」「红烧肉」→ {"tracks", "match": song|album|artist|"", "name", "section"}。
    kid：只在儿童区里找；video=True：只要视频；section：只在这个分区（id 或说法）里找；
    allowed：认出的孩子能用的分区 id（「孩子和学习」页设的，None 是儿童区全部）。"""
    q, want = split_query(q)
    if not norm(q):
        return {"tracks": [], "match": "", "name": ""}
    d = load()
    if section:
        sec = section_of(d, section) or find_section(section, kid, d)
        if not sec or (kid and not sec["kid"]):
            return {"tracks": [], "match": "", "name": "", "error": f"没有「{section}」这个分区" if not sec else "这个分区不是儿童区的"}
        pool = await section_items(sec, d)
        best = [(score_track(q, s), s) for s in pool if video is None or (s_media(s) == "video") == video]
        out = _pick(best, n, video)
        return {"tracks": out, "match": "song" if out else "", "name": out[0]["title"] if out else "", "section": sec["id"]}
    if kid:                                    # 孩子：只在儿童区的东西里比
        pool = []
        for sec in d["sections"]:
            if sec["kid"] and sec["refs"] and (allowed is None or sec["id"] in allowed):
                try:
                    pool += await section_items(sec, d)
                except MediaError:
                    continue
        if not pool:
            raise MediaError("儿童区还是空的：在网关后台「媒体」页把儿歌、动画的文件夹加进儿童区")
        best = [(score_track(q, s), s) for s in pool if video is None or (s_media(s) == "video") == video]
        out = _pick(best, n, video)
        return {"tracks": out, "match": "song" if out else "", "name": out[0]["title"] if out else "", "kid": True}
    srcs = sources(d)
    if not srcs:
        raise MediaError("还没接媒体库：在网关后台「媒体」页填 Navidrome 或 Jellyfin 的地址，或者往网关的 music 文件夹里传")
    if video:
        srcs = [s for s in srcs if s.key != "n"]
    best, groups = [], []
    for src in srcs:
        try:
            r = await src.search(q, n)
        except MediaError:
            continue
        best += [(score(q, s["title"]), s) for s in r["songs"] if video is None or (s_media(s) == "video") == video]
        groups += [(score(q, a["name"]), src, a["type"], a["id"], a["name"]) for a in r["albums"]]
        groups += [(score(q, a["name"]) + (0.1 if want == "artist" else 0), src, "artist", a["id"], a["name"]) for a in r["artists"]]
    if want == "artist" or not best or max(sc for sc, _ in best) < 0.6:
        g = max(groups, key=lambda x: x[0], default=None)
        if g and g[0] >= (0.6 if want == "artist" else 0.8):
            songs = await g[1].songs(g[2], g[3].split(".", 1)[1], limit=100)
            songs = [s for s in songs if video is None or (s_media(s) == "video") == video]
            if songs:
                if g[2] == "artist":
                    random.shuffle(songs)
                return {"tracks": songs[:max(n, 50)], "match": "artist" if g[2] == "artist" else "album", "name": g[4]}
    if not best or max(sc for sc, _ in best) < 0.6:
        for src in srcs:                        # 搜不到：全部名字模糊比（Navidrome 中文只认开头）
            try:
                best += [(score(q, s["title"]), s) for s in await _cached_index(src)
                         if video is None or (s_media(s) == "video") == video]
            except MediaError:
                continue
    out = _pick(best, n, video)
    return {"tracks": out, "match": "song" if out else "", "name": out[0]["title"] if out else ""}


async def play_section(word_or_id: str, kid: bool = False, who: str = "", lesson: int = None, n: int = 40,
                       allowed: list = None) -> dict:
    """「放英语」「放英语第三课」「放儿歌」→ {"section", "tracks", "start", "at", "lesson"}。
    随机的：打乱挑 n 个；按顺序的：全部按顺序，从 lesson（第几个）或这个人上次的地方开始。"""
    d = load()
    sec = section_of(d, word_or_id) or find_section(word_or_id, kid, d)
    if not sec:
        if kid and find_section(word_or_id, False, d):
            raise MediaError("这个是大人区的，孩子放不了")
        raise MediaError(f"没有「{word_or_id}」这个分区")
    if kid and not sec["kid"]:
        raise MediaError("这个是大人区的，孩子放不了")
    if kid and allowed is not None and sec["id"] not in allowed:
        raise MediaError(f"「{sec['name']}」没给{who or '这个孩子'}开：在网关后台「孩子和学习」页勾上")
    if not sec["refs"]:
        if sec["id"] == "music" and sources(d):                # 「放首歌」：音乐区空着就随便放
            r = await random_audio(n)
            return {"section": {"id": "music", "name": sec["name"], "kid": False, "play": "shuffle"}, "tracks": r["tracks"],
                    "start": 0, "at": 0.0, "lesson": 1}
        raise MediaError(f"「{sec['name']}」还是空的：在网关后台「媒体」页把文件夹或歌单加进去")
    items = await section_items(sec, d)
    if not items:
        raise MediaError(f"「{sec['name']}」里没有能放的")
    out = {"section": {"id": sec["id"], "name": sec["name"], "kid": sec["kid"], "play": sec["play"]}, "start": 0, "at": 0.0}
    if sec["play"] == "course":
        if lesson:
            if not 1 <= lesson <= len(items):
                raise MediaError(f"「{sec['name']}」一共 {len(items)} 个，没有第 {lesson} 个")
            out["start"] = lesson - 1
        else:
            p = progress_of(sec["id"], who)
            idx = next((i for i, s in enumerate(items) if s["id"] == p.get("id")), p.get("index", 0) if p else 0) if p else 0
            if p and p.get("done"):
                idx += 1
            out["start"] = idx % len(items)
            if p and not p.get("done") and items[out["start"]]["id"] == p.get("id"):
                out["at"] = float(p.get("at") or 0)
        out["tracks"] = items
    else:
        pool = list(items)
        random.shuffle(pool)
        out["tracks"] = pool[:n]
    out["lesson"] = out["start"] + 1
    return out


async def random_audio(n: int = 40) -> dict:
    out = []
    for src in sources():
        try:
            out += await src.random(n)
        except MediaError:
            continue
    random.shuffle(out)
    if not out and not sources():
        raise MediaError("还没接媒体库：在网关后台「媒体」页填 Navidrome 或 Jellyfin 的地址，或者往网关的 music 文件夹里传")
    return {"tracks": out[:n], "name": "随便听听"}


async def tagged(tag: str, n: int = 40) -> dict:
    """58 块的「放儿歌」：tag kids/bedtime/any → 分区 songs/stories/随便听。"""
    if tag == "any":
        return await random_audio(n)
    if tag not in OLD_TAGS:
        raise MediaError("没有这个标签")
    r = await play_section(OLD_TAGS[tag], n=n)
    return {"tracks": r["tracks"], "name": r["section"]["name"]}


# ---------------------------------------------------------------- 进度（按分区 + 谁）

_plock = threading.Lock()


def _load_progress() -> dict:
    try:
        with open(_progress_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def progress_of(section: str, who: str) -> dict:
    return dict(_load_progress().get(f"{section}|{who}") or {})


def save_progress(section: str, who: str, item_id: str, index: int, at: float, duration: float = 0, title: str = "") -> dict:
    """放到 90% 算放完（done）：下次从下一个开始；没放完从停下的秒数接着。"""
    if not section or not who:
        raise MediaError("缺分区或是谁")
    done = bool(duration and at >= duration * DONE_RATIO)
    rec = {"id": str(item_id)[:420], "index": int(index or 0), "at": round(float(at or 0), 1), "done": done,
           "title": str(title or "")[:80], "t": int(time.time())}
    with _plock:
        d = _load_progress()
        d[f"{section}|{who}"] = rec
        os.makedirs(os.path.dirname(_progress_path()), exist_ok=True)
        tmp = _progress_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _progress_path())
    return rec


def progress_list(section: str = None) -> list:
    out = []
    for k, v in _load_progress().items():
        sid, _, who = k.partition("|")
        if section is None or sid == section:
            out.append({"section": sid, "who": who, **v})
    return sorted(out, key=lambda x: -x.get("t", 0))


# ---------------------------------------------------------------- 后台和设备要的

def _local_count():
    lo = Local()
    return len(lo._files(lo.root)) if lo.root.is_dir() else 0


def view() -> dict:
    """后台「媒体」页（不含口令和 token）。"""
    d = load()
    nv = d["navidrome"]
    prog = progress_list()
    return {
        "navidrome": {"url": nv["url"], "user": nv["user"], "has_password": bool(nv["token"]), "on": nv["on"],
                      "status": services.status("music")},
        **{w: {"url": d[w]["url"], "user": d[w]["user"], "has_password": bool(d[w]["token"]), "key": d[w]["key"], "on": d[w]["on"],
               "status": services.status(w)} for w in SERVERS},
        "local": {"on": d["local"]["on"], "count": _local_count(), "dir": str(Local().root)},
        "sections": [dict(s, words=words_of(s)[1:], progress=[p for p in prog if p["section"] == s["id"]][:6]) for s in d["sections"]],
        "plays": PLAYS,
        "ready": bool(sources(d)),
        # 58 块的页面和测试还在用
        "tags": {t: section_of(d, sid)["refs"] for t, sid in OLD_TAGS.items()},
        "tag_names": {"kids": "儿歌", "bedtime": "睡前故事"},
    }


def device_view(who: str = "", kids: bool = False, allowed: list = None) -> dict:
    """设备的卡片和设置页：能放的分区（儿童模式只给儿童区）、按顺序的从第几个接着。"""
    d = load()
    secs = []
    for s in d["sections"]:
        if not s["refs"] or (kids and not s["kid"]) or (kids and allowed is not None and s["id"] not in allowed):
            continue
        item = {"id": s["id"], "name": s["name"], "kid": s["kid"], "play": s["play"]}
        if s["play"] == "course":
            p = progress_of(s["id"], who)
            if p:
                item["lesson"] = p.get("index", 0) + 1 + (1 if p.get("done") else 0)
        secs.append(item)
    return {"ready": bool(sources(d)), "sections": secs}


def brief() -> list:
    """外部服务页的两行：Navidrome、Jellyfin。"""
    d = load()
    nv, jf, em = d["navidrome"], d["jellyfin"], d["emby"]
    n = _local_count() if d["local"]["on"] else 0
    return [{"id": "music", "name": "Navidrome（家里 NAS 上的音乐）", "url": nv["url"], "configured": bool(nv["url"] and nv["token"]),
             "on": nv["on"], "local": n, "status": services.status("music")},
            {"id": "jellyfin", "name": "Jellyfin（家里 NAS 上的视频）", "url": jf["url"], "configured": bool(jf["url"] and jf["token"]),
             "on": jf["on"], "local": 0, "status": services.status("jellyfin")},
            {"id": "emby", "name": "Emby（家里 NAS 上的视频）", "url": em["url"], "configured": bool(em["url"] and em["token"]),
             "on": em["on"], "local": 0, "status": services.status("emby")}]
