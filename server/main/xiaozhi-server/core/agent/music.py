"""[xiaozhi-hub] 音乐（设计画布第 58 块）：放儿歌、睡前故事、点歌。

音乐从哪来（两种来源实现同一个接口 Source，可以都开）：
- Navidrome：家里 NAS 上的音乐服务器，走标准的 Subsonic API（ping、search3、getAlbumList2、getPlaylists、
  getMusicFolders/getIndexes/getMusicDirectory、getAlbum、getRandomSongs、stream、getCoverArt）。
  认证用 Subsonic 的 token + salt：后台填密码时算出 t = md5(密码 + salt) 存下来，密码本身不存、不进日志；
  改了用户名要重新填密码。
- 网关本机的 music/ 文件夹（没有 NAS 的人用）：子文件夹就是专辑/文件夹，后台能上传、建文件夹、删除。

设备不直接连 Navidrome、也不拿密码：放歌、封面都走网关的中转地址 /kiosk/music/stream/<id>、/kiosk/music/cover/<id>
（admin_handler 里，只认本机来的请求，设备经 SSH 隧道来）。歌的 id 带来源前缀：n.<Navidrome 的 id>、l.<文件路径>。

后台可以把文件夹、歌单、专辑、歌手标成「儿歌」「睡前故事」：说「放儿歌」就从标好的里面挑。
配置存在 data/music.json（权限 0600）。Navidrome 的状态记在外部服务的 STATUS["music"] 里，外部服务页也显示。
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

TAGS = {"kids": "儿歌", "bedtime": "睡前故事"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma", ".ape"}
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png", "album.jpg")
MAX_UPLOAD = 300 * 1024 * 1024
CLIENT = "xiaomai"
API_VERSION = "1.16.1"
REF_TYPES = ("folder", "playlist", "album", "artist")
INDEX_TTL = 600          # Navidrome 的歌名索引（模糊找歌用）缓存 10 分钟
INDEX_MAX = 5000

_lock = threading.Lock()


class MusicError(Exception):
    """给人看的错误（不含地址和口令）。"""


# ---------------------------------------------------------------- 配置

def _path() -> str:
    return os.path.join(get_project_dir(), "data", "music.json")


def local_root() -> Path:
    return Path(get_project_dir()) / "music"


def _empty() -> dict:
    return {"navidrome": {"url": "", "user": "", "salt": "", "token": "", "on": True},
            "local": {"on": True}, "tags": {k: [] for k in TAGS}}


def load() -> dict:
    d = _empty()
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return d
    if not isinstance(raw, dict):
        return d
    nv = raw.get("navidrome") if isinstance(raw.get("navidrome"), dict) else {}
    for k in ("url", "user", "salt", "token"):
        if isinstance(nv.get(k), str):
            d["navidrome"][k] = nv[k]
    if isinstance(nv.get("on"), bool):
        d["navidrome"]["on"] = nv["on"]
    lo = raw.get("local") if isinstance(raw.get("local"), dict) else {}
    if isinstance(lo.get("on"), bool):
        d["local"]["on"] = lo["on"]
    tags = raw.get("tags") if isinstance(raw.get("tags"), dict) else {}
    for k in TAGS:
        d["tags"][k] = [r for r in (_ref(x) for x in tags.get(k) or []) if r]
    return d


def save(d: dict):
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    tmp = _path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())


def _ref(x):
    """标签里的一项：{"type": folder|playlist|album|artist, "id": "n.xxx", "name"}。"""
    if not isinstance(x, dict) or x.get("type") not in REF_TYPES:
        return None
    rid = str(x.get("id") or "")
    if not re.fullmatch(r"[nl]\.[\w\-=./]{0,400}", rid):
        return None
    return {"type": x["type"], "id": rid, "name": str(x.get("name") or "")[:60]}


def token_for(password: str, salt: str = "") -> tuple:
    """Subsonic 的 token 认证：(salt, md5(password + salt))。"""
    salt = salt or secrets.token_hex(8)
    return salt, hashlib.md5((password + salt).encode("utf-8")).hexdigest()


def _norm_url(url: str) -> str:
    url = str(url or "").strip().rstrip("/")
    if url.endswith("/rest"):
        url = url[:-5]
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise MusicError("地址要以 http:// 或 https:// 开头，比如 http://10.10.10.20:4533")
    return url


def apply(op: dict) -> dict:
    """后台的一次修改。不合法抛 MusicError。"""
    kind = op.get("op")
    with _lock:
        d = load()
        if kind == "navidrome":
            nv = d["navidrome"]
            url, user = _norm_url(op.get("url")), str(op.get("user") or "").strip()[:64]
            password = str(op.get("password") or "")
            if not url:                         # 清空：不用 Navidrome 了
                d["navidrome"] = {**_empty()["navidrome"], "on": nv["on"]}
            else:
                if not user:
                    raise MusicError("填上 Navidrome 的用户名")
                if password:
                    nv["salt"], nv["token"] = token_for(password)
                elif user != nv["user"] or not nv["token"]:
                    raise MusicError("填上密码（改了用户名也要重新填）")
                nv["url"], nv["user"] = url, user
            _index.clear()
        elif kind == "toggle":
            which = op.get("source")
            if which not in ("navidrome", "local"):
                raise MusicError("没有这个来源")
            d[which]["on"] = bool(op.get("on"))
        elif kind == "tag":
            tag = op.get("tag")
            if tag not in TAGS:
                raise MusicError("没有这个标签")
            ref = _ref(op.get("ref"))
            if not ref:
                raise MusicError("只能标文件夹、歌单、专辑或歌手")
            items = [r for r in d["tags"][tag] if r["id"] != ref["id"]]
            if op.get("on", True):
                if len(items) >= 30:
                    raise MusicError("一个标签最多 30 项")
                items.append(ref)
            d["tags"][tag] = items
        else:
            raise MusicError("不认识的操作")
        save(d)
        return d


# ---------------------------------------------------------------- 文字和模糊匹配

BRACKETS = re.compile(r"[（(【\[《<].*?[)）】\]》>]")
NOISE = re.compile(r"[\s\-_·•.,，。!！?？、:：;；'\"“”‘’~～]+")


def norm(s: str) -> str:
    """比较用：去括号里的（「(儿歌版)」）、标点空格、开头的曲目号，转小写。"""
    s = BRACKETS.sub("", str(s or ""))
    s = re.sub(r"^\s*\d{1,3}\s*[-_.、 ]\s*", "", s)
    return NOISE.sub("", s).lower()


def score(q: str, title: str) -> float:
    """说的和歌名有多像，0～1。ASR 常把歌名说长或说短一点、错一两个同音字。"""
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


def split_query(q: str) -> tuple:
    """「周杰伦的歌」→ ("周杰伦", "artist")；「两只老虎这首歌」→ ("两只老虎", "")。"""
    q = re.sub(r"(?:这首歌|那首歌|这首|那首|这个歌|歌曲)$", "", str(q or "").strip())
    m = re.fullmatch(r"(.{1,12}?)(?:的歌|唱的歌|唱的)", q)
    if m:
        return m.group(1), "artist"
    return q, ""


# ---------------------------------------------------------------- 来源

def _track(src, sid, title, artist="", album="", duration=0, cover=""):
    return {"id": f"{src}.{sid}", "title": str(title or "")[:80], "artist": str(artist or "")[:60],
            "album": str(album or "")[:60], "duration": int(duration or 0), "cover": f"{src}.{cover}" if cover else ""}


class Source:
    """来源的接口：两种来源都实现。id 都不带来源前缀（前缀由 get()/split_id() 处理）。"""
    key = ""
    name = ""

    async def ping(self) -> dict: raise NotImplementedError
    async def search(self, q: str, n: int = 20) -> dict: raise NotImplementedError       # {"songs", "albums", "artists"}
    async def index(self) -> list: raise NotImplementedError                               # 全部歌（找歌用）
    async def albums(self, offset: int = 0, size: int = 60) -> list: raise NotImplementedError
    async def playlists(self) -> list: raise NotImplementedError
    async def folders(self) -> list: raise NotImplementedError                             # 最上一层
    async def open(self, typ: str, rid: str) -> dict: raise NotImplementedError            # {"name", "items", "songs"}
    async def songs(self, typ: str, rid: str, limit: int = 300) -> list: raise NotImplementedError
    async def random(self, n: int = 30) -> list: raise NotImplementedError
    async def stream(self, request: web.Request, rid: str, preview: bool = False) -> web.StreamResponse: raise NotImplementedError
    async def cover(self, request: web.Request, rid: str, size: int = 300) -> web.StreamResponse: raise NotImplementedError


def _safe(e: Exception) -> str:
    """异常说成人话，不带地址（aiohttp 的异常文字里有完整 URL，含 token）。"""
    if isinstance(e, MusicError):
        return str(e)
    if isinstance(e, asyncio.TimeoutError):
        return "超时"
    if isinstance(e, aiohttp.ClientConnectorError):
        return "连不上"
    if isinstance(e, aiohttp.ClientResponseError):
        return f"返回 {e.status}"
    if isinstance(e, aiohttp.ClientError):
        return type(e).__name__
    return type(e).__name__


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
                        raise MusicError(f"返回 {r.status}" + ("（地址不对？）" if r.status == 404 else ""))
                    try:
                        data = await r.json(content_type=None)
                    except ValueError:
                        raise MusicError("返回的不是 Subsonic 接口（地址填 Navidrome 的首页地址，比如 http://NAS:4533）")
            body = (data or {}).get("subsonic-response") or {}
            if body.get("status") != "ok":
                err = body.get("error") or {}
                code = err.get("code")
                raise MusicError({40: "用户名或密码不对", 41: "这个服务器不支持 token 认证", 50: "这个账号没有权限",
                                  70: "找不到"}.get(code, str(err.get("message") or "出错了")[:60]))
        except Exception as e:  # noqa: BLE001
            if record:
                services.record("music", False, (time.monotonic() - t0) * 1000, _safe(e))
            raise MusicError(_safe(e)) from None
        if record:
            services.record("music", True, (time.monotonic() - t0) * 1000)
        return body

    async def ping(self) -> dict:
        body = await self.call("ping")
        libs = (await self.call("getMusicFolders")).get("musicFolders", {}).get("musicFolder") or []
        return {"ok": True, "version": body.get("serverVersion") or body.get("version") or "",
                "server": body.get("type") or "Subsonic", "libraries": [str(x.get("name") or "") for x in libs]}

    def _song(self, x):
        return _track("n", x.get("id"), x.get("title"), x.get("artist"), x.get("album"), x.get("duration"), x.get("coverArt"))

    def _album(self, x):
        return {"id": f"n.{x.get('id')}", "type": "album", "name": str(x.get("name") or x.get("title") or "")[:80],
                "sub": " · ".join(p for p in (str(x.get("artist") or ""), f"{x['songCount']} 首" if x.get("songCount") else "") if p),
                "cover": f"n.{x['coverArt']}" if x.get("coverArt") else ""}

    async def search(self, q, n=20):
        r = (await self.call("search3", query=q, songCount=n, albumCount=8, artistCount=5)).get("searchResult3") or {}
        return {"songs": [self._song(x) for x in r.get("song") or []],
                "albums": [self._album(x) for x in r.get("album") or []],
                "artists": [{"id": f"n.{x.get('id')}", "type": "artist", "name": str(x.get("name") or ""),
                             "sub": f"{x.get('albumCount') or 0} 张专辑", "cover": f"n.{x['coverArt']}" if x.get("coverArt") else ""}
                            for x in r.get("artist") or []]}

    async def index(self):
        """全部歌（search3 空查询分页取，最多 INDEX_MAX 首）：Navidrome 的搜索只认词开头，中文歌名「老虎」搜不到「两只老虎」。"""
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
            songs = [self._song(x) for x in idx.get("child") or [] if not x.get("isDir")]
            return {"name": "", "items": items, "songs": songs}
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
        raise MusicError("打不开这一项")

    async def songs(self, typ, rid, limit=300):
        """标签里的一项展开成歌：文件夹往下找 4 层，歌手展开所有专辑。"""
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

    async def _relay(self, request, method, params, ctype=None):
        """把 Navidrome 的回复原样转给设备/浏览器（带上 Range，能拖动、能从中间接着放）。"""
        headers = {}
        if request.headers.get("Range"):
            headers["Range"] = request.headers["Range"]
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=5, sock_read=30))
        try:
            upstream = await session.get(f"{self.url}/rest/{method}", params=self._params(params), headers=headers)
        except Exception as e:  # noqa: BLE001
            await session.close()
            services.record("music", False, None, _safe(e))
            raise MusicError(_safe(e)) from None
        try:
            if upstream.status not in (200, 206) or upstream.content_type in ("application/json", "text/xml"):
                raise MusicError(f"Navidrome 返回 {upstream.status}")
            resp = web.StreamResponse(status=upstream.status)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag"):
                if h in upstream.headers:
                    resp.headers[h] = upstream.headers[h]
            if ctype and "Content-Type" not in upstream.headers:
                resp.headers["Content-Type"] = ctype
            resp.headers["Cache-Control"] = "private, max-age=86400" if method == "getCoverArt" else "no-store"
            await resp.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp
        finally:
            upstream.release()
            await session.close()

    async def stream(self, request, rid, preview=False):
        return await self._relay(request, "stream", {"id": rid, "format": "raw"})

    async def cover(self, request, rid, size=300):
        return await self._relay(request, "getCoverArt", {"id": rid, "size": size}, "image/jpeg")


def _enc(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode().rstrip("=")


def _dec(rid: str) -> str:
    try:
        return base64.urlsafe_b64decode(rid + "=" * (-len(rid) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise MusicError("没有这一项") from None


class Local(Source):
    """网关本机的 music/ 文件夹：子文件夹当专辑/文件夹，文件名当歌名（去掉开头的曲目号）。"""
    key, name = "l", "网关本机"

    def __init__(self, root: Path = None):
        self.root = Path(root or local_root())

    def path(self, rid: str) -> Path:
        rel = _dec(rid) if rid else ""
        p = (self.root / rel).resolve()
        root = self.root.resolve()
        if p != root and root not in p.parents:
            raise MusicError("没有这一项")
        return p

    def _rel(self, p: Path) -> str:
        return str(p.resolve().relative_to(self.root.resolve())).replace(os.sep, "/").lstrip(".")

    def _song(self, p: Path):
        title = re.sub(r"^\s*\d{1,3}\s*[-_.、 ]\s*", "", p.stem) or p.stem
        parent = p.parent.name if p.parent.resolve() != self.root.resolve() else ""
        return _track("l", _enc(self._rel(p)), title, "", parent, 0, _enc(self._rel(p.parent)) if self._cover_of(p.parent) else "")

    def _cover_of(self, d: Path):
        for n in COVER_NAMES:
            if (d / n).is_file():
                return d / n
        return None

    def _files(self, d: Path, deep=True):
        if not d.is_dir():
            return []
        it = d.rglob("*") if deep else d.iterdir()
        return sorted((p for p in it if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith(".")),
                      key=lambda p: str(p))

    def _dir_item(self, d: Path):
        n = len(self._files(d))
        return {"id": f"l.{_enc(self._rel(d))}", "type": "folder", "name": d.name, "sub": f"{n} 首",
                "cover": f"l.{_enc(self._rel(d))}" if self._cover_of(d) else ""}

    async def ping(self):
        n = await asyncio.to_thread(lambda: len(self._files(self.root)))
        return {"ok": True, "version": "", "server": "本机文件夹", "libraries": [f"{n} 首"]}

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
        if typ not in ("folder", "album"):
            raise MusicError("打不开这一项")
        d = self.path(rid)

        def run():
            if not d.is_dir():
                raise MusicError("没有这个文件夹")
            subs = sorted((p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name)
            return {"name": d.name if d.resolve() != self.root.resolve() else "music",
                    "items": [self._dir_item(p) for p in subs], "songs": [self._song(p) for p in self._files(d, deep=False)]}
        return await asyncio.to_thread(run)

    async def songs(self, typ, rid, limit=300):
        d = self.path(rid)
        return await asyncio.to_thread(lambda: [self._song(p) for p in self._files(d)[:limit]])

    async def random(self, n=30):
        idx = await self.index()
        return random.sample(idx, min(n, len(idx)))

    async def stream(self, request, rid, preview=False):
        p = self.path(rid)
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXT:
            raise MusicError("没有这首歌")
        return web.FileResponse(p, headers={"Cache-Control": "no-store"})

    async def cover(self, request, rid, size=300):
        p = self.path(rid)
        c = self._cover_of(p if p.is_dir() else p.parent)
        if not c:
            raise MusicError("没有封面")
        return web.FileResponse(c, headers={"Cache-Control": "private, max-age=86400"})

    # ---- 后台管理
    def save_upload_path(self, dir_id: str, name: str) -> Path:
        d = self.path(dir_id)
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", os.path.basename(str(name or ""))).strip()[:120]
        if not name or name.startswith("."):
            raise MusicError("文件名不对")
        if Path(name).suffix.lower() not in AUDIO_EXT and name.lower() not in COVER_NAMES:
            raise MusicError("只能传音频（mp3、m4a、flac、ogg、wav…）或封面 cover.jpg")
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def mkdir(self, dir_id: str, name: str) -> dict:
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name or "")).strip()[:60]
        if not name or name.startswith("."):
            raise MusicError("文件夹名字不对")
        p = self.path(dir_id) / name
        p.mkdir(parents=True, exist_ok=True)
        return self._dir_item(p)

    def delete(self, rid: str):
        p = self.path(rid)
        if p.resolve() == self.root.resolve():
            raise MusicError("不能删整个 music 文件夹")
        if p.is_dir():
            if any(p.iterdir()):
                raise MusicError("文件夹不是空的，先删里面的歌")
            p.rmdir()
        elif p.is_file():
            p.unlink()
        else:
            raise MusicError("没有这一项")


# ---------------------------------------------------------------- 用哪个来源

def navidrome(d: dict = None, password: str = None, url: str = None, user: str = None):
    """配好的 Navidrome；后台「测试」可以传没保存的地址、用户、密码。没配好返回 None。"""
    nv = (d or load())["navidrome"]
    url = _norm_url(url) if url is not None else nv["url"]
    user = (user if user is not None else nv["user"]).strip()
    if password:
        salt, token = token_for(password)
    else:
        salt, token = nv["salt"], nv["token"]
    if not (url and user and token):
        return None
    return Navidrome(url, user, salt, token)


def sources(d: dict = None) -> list:
    """开着、配好的来源，Navidrome 在前。本机 music/ 没有歌时不算。"""
    d = d or load()
    out = []
    nv = navidrome(d)
    if nv and d["navidrome"]["on"]:
        out.append(nv)
    if d["local"]["on"]:
        lo = Local()
        if lo.root.is_dir() and any(lo._files(lo.root)[:1]):
            out.append(lo)
    return out


def get(key: str, d: dict = None) -> Source:
    """按 id 前缀拿来源（关掉的也能拿：已经标好的、正在放的不受影响）。"""
    d = d or load()
    if key == "n":
        nv = navidrome(d)
        if not nv:
            raise MusicError("还没在后台填 Navidrome 的地址和账号")
        return nv
    if key == "l":
        return Local()
    raise MusicError("没有这个来源")


def split_id(sid: str) -> tuple:
    m = re.fullmatch(r"([nl])\.([\w\-=./]{0,400})", str(sid or ""))
    if not m:
        raise MusicError("没有这一项")
    return m.group(1), m.group(2)


# ---------------------------------------------------------------- 设备要的：找歌、按标签挑、随便放

_index = {}           # 来源 key → (时间, [歌])


async def _cached_index(src: Source) -> list:
    hit = _index.get(src.key)
    if hit and time.time() - hit[0] < INDEX_TTL:
        return hit[1]
    songs = await src.index()
    _index[src.key] = (time.time(), songs)
    return songs


async def find(q: str, n: int = 20) -> dict:
    """「两只老虎」→ {"tracks": [...], "match": "song"|"album"|"artist"|"", "name"}。先搜，搜不到再拿全部歌名模糊比。"""
    q, want = split_query(q)
    if not norm(q):
        return {"tracks": [], "match": "", "name": ""}
    srcs = sources()
    if not srcs:
        raise MusicError("还没接音乐库：在网关后台「音乐」页填 Navidrome 的地址，或者往网关的 music 文件夹里传歌")
    best = []                                   # (分数, 歌)
    groups = []                                 # (分数, 来源, 类型, id, 名字)
    for src in srcs:
        try:
            r = await src.search(q, n)
        except MusicError:
            continue
        best += [(score(q, s["title"]), s) for s in r["songs"]]
        groups += [(score(q, a["name"]), src, "album", a["id"], a["name"]) for a in r["albums"]]
        groups += [(score(q, a["name"]) + (0.1 if want == "artist" else 0), src, "artist", a["id"], a["name"]) for a in r["artists"]]
    if want == "artist" or not best or max(sc for sc, _ in best) < 0.6:
        g = max(groups, key=lambda x: x[0], default=None)
        if g and g[0] >= (0.6 if want == "artist" else 0.8):
            songs = await g[1].songs(g[2], g[3].split(".", 1)[1], limit=100)
            if songs:
                if g[2] == "artist":
                    random.shuffle(songs)
                return {"tracks": songs[:max(n, 50)], "match": g[2], "name": g[4]}
    if not best or max(sc for sc, _ in best) < 0.6:
        for src in srcs:                        # 搜不到：全部歌名模糊比（Navidrome 中文只认开头）
            try:
                best += [(score(q, s["title"]), s) for s in await _cached_index(src)]
            except MusicError:
                continue
    seen, out = set(), []
    for sc, s in sorted(best, key=lambda x: -x[0]):
        if sc < 0.6 or s["id"] in seen:
            continue
        seen.add(s["id"])
        out.append(s)
    return {"tracks": out[:n], "match": "song" if out else "", "name": out[0]["title"] if out else ""}


async def tagged(tag: str, n: int = 40) -> dict:
    """「放儿歌」：标成儿歌的文件夹、歌单……里的歌，打乱。tag="any"：随便放。"""
    d = load()
    if tag == "any":
        out = []
        for src in sources(d):
            try:
                out += await src.random(n)
            except MusicError:
                continue
        random.shuffle(out)
        if not out and not sources(d):
            raise MusicError("还没接音乐库：在网关后台「音乐」页填 Navidrome 的地址，或者往网关的 music 文件夹里传歌")
        return {"tracks": out[:n], "name": "随便听听"}
    if tag not in TAGS:
        raise MusicError("没有这个标签")
    refs = d["tags"][tag]
    if not refs:
        raise MusicError(f"还没在网关后台「音乐」页把哪个文件夹或歌单标成「{TAGS[tag]}」")
    out, errors = [], []
    for ref in refs:
        key, rid = split_id(ref["id"])
        try:
            out += await get(key, d).songs(ref["type"], rid, limit=300)
        except MusicError as e:
            errors.append(str(e))
    if not out and errors:
        raise MusicError(f"{TAGS[tag]}放不了：{errors[0]}")
    seen, uniq = set(), []
    for s in out:
        if s["id"] not in seen:
            seen.add(s["id"])
            uniq.append(s)
    random.shuffle(uniq)
    return {"tracks": uniq[:n], "name": TAGS[tag]}


def view() -> dict:
    """后台「音乐」页（不含口令）。"""
    d = load()
    nv = d["navidrome"]
    lo = Local()
    count = len(lo._files(lo.root)) if lo.root.is_dir() else 0
    return {
        "navidrome": {"url": nv["url"], "user": nv["user"], "has_password": bool(nv["token"]), "on": nv["on"],
                      "status": services.status("music")},
        "local": {"on": d["local"]["on"], "count": count, "dir": str(lo.root)},
        "tags": d["tags"], "tag_names": TAGS,
        "ready": bool(sources(d)),
    }


def brief() -> dict:
    """外部服务页的一行。"""
    d = load()
    nv = d["navidrome"]
    lo = Local()
    count = len(lo._files(lo.root)) if lo.root.is_dir() else 0
    return {"id": "music", "name": "Navidrome（家里 NAS 上的音乐）", "url": nv["url"], "configured": bool(nv["url"] and nv["token"]),
            "on": nv["on"], "local": count if d["local"]["on"] else 0, "status": services.status("music")}
