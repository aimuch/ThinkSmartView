"""[xiaozhi-hub] 网关备份（设计画布第 87 块）：data 文件夹打成一个 .tar.gz，每天自己备份到网关本机或 NAS（WebDAV），
后台能下载、能恢复。

备份的是 data/ 下面的全部（设置和各家的 Key、聊天记录和记忆、家人声音、设备各自的设置、孩子和学习、媒体分区、
相册、费用记录……），不备份：模型（在 models/，能重新下载）、设备截图、临时文件、改配置前自动留的 .bak。
SQLite 数据库用它自己的在线备份拷：正在写也不会拷到半截。

文件名 xiaozhi-年月日-时分秒.tar.gz，里面 backup.json（是哪个网关的、什么时候、多少个文件）加 data/…。
恢复：先检查（是不是网关的备份、有没有怪路径），先备份一份现在的，解到 data 旁边的临时文件夹，全部解好才和 data
对调；换下来的留在 data.before-restore-<时间>。然后重启网关。备份自己的设置（data/backup.json）、后台的登录
不跟着恢复，用现在的。
"""

import asyncio
import email.utils
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp

from core.utils.atomic_file import write_json

APP = "xiaozhi-hub"
FORMAT = 1
NAME_RE = re.compile(r"xiaozhi-\d{8}-\d{4,6}(-\d{1,3})?\.tar\.gz")
KEEP_CHOICES = (3, 7, 14, 30)
DEFAULTS = {"enabled": True, "time": "03:30", "keep": 7, "target": "local", "local_dir": "~/xiaozhi-backups",
            "webdav_url": "", "webdav_user": "", "webdav_password": ""}
SKIP_DIRS = {"screens", "tmp", "cache", "__pycache__"}          # data/ 下面这些文件夹不备份
SKIP_FILE = re.compile(r"(\.bak(-[\w.-]*)?|-wal|-shm|-journal|\.tmp|\.part)$")
# 恢复时不换的：备份自己的设置和记录、后台的登录（换成备份里那份，现在登着的人会被踢出去）
KEEP_ON_RESTORE = ("backup.json", ".admin-sessions-kept.json", ".admin-sessions.json", ".install-complete")
MAX_RESTORE = 8 * 1024 ** 3
STALE_DAYS = 3
HISTORY = 30


class BackupError(Exception):
    """给人看的一句话。"""


def _now_name(now=None) -> str:
    return time.strftime("xiaozhi-%Y%m%d-%H%M%S.tar.gz", time.localtime(now or time.time()))


def name_time(name: str):
    """文件名里的时间（秒）；不认识的文件名返回 None。"""
    m = re.fullmatch(r"xiaozhi-(\d{8})-(\d{4})(\d{2})?(?:-\d{1,3})?\.tar\.gz", name or "")
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1) + m.group(2) + (m.group(3) or "00"), "%Y%m%d%H%M%S"))
    except ValueError:
        return None


def order(name: str) -> tuple:
    """新旧顺序：文件名里的时间，同一秒的按 -2、-3。"""
    m = re.search(r"-(\d{1,3})\.tar\.gz$", name or "")
    suffix = int(m.group(1)) if m and not re.search(r"-\d{6}\.tar\.gz$", name) else 1
    return (name_time(name) or 0, suffix)


def _skip(rel_parts) -> bool:
    return (rel_parts[0] in SKIP_DIRS) or bool(SKIP_FILE.search(rel_parts[-1]))


def collect(data_dir: Path) -> list:
    """[(在备份里的路径 data/…, 文件)]：按路径排好。"""
    data_dir = Path(data_dir)
    out = []
    for root, dirs, files in os.walk(data_dir):
        rel_root = Path(root).relative_to(data_dir)
        dirs[:] = sorted(d for d in dirs if not (rel_root == Path(".") and d in SKIP_DIRS) and d != "__pycache__")
        for f in sorted(files):
            path = Path(root) / f
            rel = (rel_root / f)
            if _skip(rel.parts) or not path.is_file() or path.is_symlink():
                continue
            out.append(("data/" + rel.as_posix(), path))
    return out


def _is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sqlite_copy(src: Path, dst: Path) -> None:
    """SQLite 的在线备份：别的连接正在写也拷得到一份完整的。"""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def build(data_dir: Path, dest: Path, version: str = "", now: float = None) -> dict:
    """把 data 打进 dest（.tar.gz）。返回 {"files", "size"}。"""
    data_dir = Path(data_dir)
    files = collect(data_dir)
    manifest = {"app": APP, "format": FORMAT, "created": int(now or time.time()), "version": version,
                "files": len(files)}
    with tempfile.TemporaryDirectory(prefix="hub-backup-") as tmp, tarfile.open(dest, "w:gz", compresslevel=6) as tar:
        raw = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
        info = tarfile.TarInfo("backup.json")
        info.size, info.mtime, info.mode = len(raw), manifest["created"], 0o600
        tar.addfile(info, io.BytesIO(raw))
        for arc, path in files:
            src = path
            if path.suffix in (".db", ".sqlite", ".sqlite3") and _is_sqlite(path):
                src = Path(tmp) / (str(abs(hash(arc))) + ".db")
                try:
                    _sqlite_copy(path, src)
                except sqlite3.Error:
                    src = path   # 拷不了（坏了）就照原样带上
            try:
                tar.add(str(src), arcname=arc, recursive=False)
            except FileNotFoundError:   # 打包时刚好被删了（临时的照片）
                continue
    return {"files": len(files), "size": Path(dest).stat().st_size}


def check(archive: Path) -> dict:
    """看一眼备份文件对不对：返回里面的 backup.json；不对抛 BackupError（一句为什么）。"""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            manifest = None
            total = 0
            for m in tar:
                name = m.name
                if name == "backup.json" and m.isfile():
                    try:
                        manifest = json.loads(tar.extractfile(m).read().decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise BackupError("备份里的 backup.json 坏了")
                    continue
                parts = Path(name).parts
                if name.startswith("/") or ".." in parts or not parts or parts[0] != "data" or not (m.isfile() or m.isdir()):
                    raise BackupError(f"备份里有不该有的东西：{name[:80]}")
                total += m.size
                if total > MAX_RESTORE:
                    raise BackupError("备份太大了（超过 8 GB）")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise BackupError(f"不是能打开的 .tar.gz：{type(e).__name__}")
    if not isinstance(manifest, dict) or manifest.get("app") != APP:
        raise BackupError("这不是小麦网关的备份（没有 backup.json）")
    if int(manifest.get("format") or 0) > FORMAT:
        raise BackupError("这份备份是更新的网关做的，先把网关更新到最新再恢复")
    return manifest


def extract(archive: Path, into: Path) -> int:
    """只解 data/ 下面的普通文件和文件夹到 into（= 新的 data），返回文件数。check() 过了才调。"""
    into = Path(into)
    count = 0
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            parts = Path(m.name).parts
            if not parts or parts[0] != "data" or ".." in parts:
                continue
            target = into.joinpath(*parts[1:]) if len(parts) > 1 else into
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                os.chmod(target, 0o600 if target.name.startswith(".") or target.suffix == ".db" else 0o644)
                count += 1
    return count


# ---------------------------------------------------------------- 放在哪

class LocalTarget:
    kind = "local"

    def __init__(self, folder: str):
        self.dir = Path(os.path.expanduser(folder or DEFAULTS["local_dir"]))

    def label(self) -> str:
        return "网关本机"

    async def put(self, path: Path, name: str) -> None:
        def go():
            self.dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dir, 0o700)
            tmp = self.dir / (name + ".part")
            shutil.copyfile(path, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.dir / name)
        await asyncio.to_thread(go)

    async def list(self) -> list:
        def go():
            if not self.dir.is_dir():
                return []
            out = []
            for p in self.dir.iterdir():
                if NAME_RE.fullmatch(p.name) and p.is_file():
                    st = p.stat()
                    out.append({"name": p.name, "size": st.st_size, "at": int(name_time(p.name) or st.st_mtime)})
            return out
        return await asyncio.to_thread(go)

    async def fetch(self, name: str, dest: Path) -> None:
        src = self.dir / name
        if not NAME_RE.fullmatch(name) or not src.is_file():
            raise BackupError("没有这份备份")
        await asyncio.to_thread(shutil.copyfile, src, dest)

    def path(self, name: str):
        p = self.dir / name
        return p if NAME_RE.fullmatch(name) and p.is_file() else None

    async def delete(self, name: str) -> None:
        if NAME_RE.fullmatch(name):
            await asyncio.to_thread(lambda: (self.dir / name).unlink(missing_ok=True))

    async def test(self) -> str:
        def go():
            self.dir.mkdir(parents=True, exist_ok=True)
            probe = self.dir / ".write-test"
            probe.write_bytes(b"ok")
            probe.unlink()
            return shutil.disk_usage(self.dir).free
        try:
            free = await asyncio.to_thread(go)
        except OSError as e:
            raise BackupError(f"写不进 {self.dir}：{e.strerror or e}")
        return f"能写，{self.dir} 还剩 {free / 1024 ** 3:.1f} GB"


class WebDavTarget:
    """NAS 上开的 WebDAV（飞牛、群晖、威联通都有）：PUT 上传、PROPFIND 列、GET 下载、DELETE 删旧的。"""
    kind = "webdav"

    def __init__(self, url: str, user: str = "", password: str = "", timeout: float = 120):
        url = (url or "").strip()
        if not re.match(r"https?://", url):
            raise BackupError("NAS 的 WebDAV 地址要以 http:// 或 https:// 开头")
        self.url = url.rstrip("/") + "/"
        self.auth = aiohttp.BasicAuth(user, password) if user else None
        self.timeout = timeout

    def label(self) -> str:
        return "NAS"

    def _session(self, timeout=None):
        return aiohttp.ClientSession(auth=self.auth, timeout=aiohttp.ClientTimeout(total=timeout or self.timeout))

    @staticmethod
    def _why(status: int) -> str:
        return {401: "用户名或密码不对", 403: "这个账号没有权限写这个文件夹", 404: "地址里的文件夹不存在",
                405: "这个地址不是 WebDAV（NAS 上要先打开 WebDAV）", 409: "上一级文件夹不存在",
                507: "NAS 的空间满了"}.get(status, f"NAS 回了 HTTP {status}")

    async def _call(self, method: str, url: str, **kw):
        try:
            async with self._session(kw.pop("timeout", None)) as s:
                async with s.request(method, url, **kw) as r:
                    body = await r.read() if method in ("PROPFIND", "GET") and r.status < 300 else b""
                    return r.status, body, dict(r.headers)
        except asyncio.TimeoutError:
            raise BackupError("NAS 连不上（超时）")
        except aiohttp.ClientError as e:
            raise BackupError(f"NAS 连不上：{type(e).__name__}")

    async def _ensure_folder(self) -> None:
        status, _, _ = await self._call("PROPFIND", self.url, headers={"Depth": "0"}, timeout=20)
        if status in (207, 200):
            return
        if status == 404:
            status, _, _ = await self._call("MKCOL", self.url, timeout=20)
            if status in (201, 405):
                return
        raise BackupError(self._why(status))

    async def put(self, path: Path, name: str) -> None:
        await self._ensure_folder()
        try:
            async with self._session() as s:
                with open(path, "rb") as f:
                    async with s.put(self.url + name, data=f, headers={"Content-Type": "application/gzip"}) as r:
                        if r.status not in (200, 201, 204):
                            raise BackupError(self._why(r.status))
        except asyncio.TimeoutError:
            raise BackupError("传到 NAS 超时了")
        except aiohttp.ClientError as e:
            raise BackupError(f"NAS 连不上：{type(e).__name__}")

    async def list(self) -> list:
        status, body, _ = await self._call("PROPFIND", self.url, headers={"Depth": "1"}, timeout=20)
        if status == 404:
            return []
        if status not in (207, 200):
            raise BackupError(self._why(status))
        return parse_propfind(body)

    async def fetch(self, name: str, dest: Path) -> None:
        if not NAME_RE.fullmatch(name):
            raise BackupError("没有这份备份")
        try:
            async with self._session() as s, s.get(self.url + name) as r:
                if r.status != 200:
                    raise BackupError("没有这份备份" if r.status == 404 else self._why(r.status))
                with open(dest, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
        except asyncio.TimeoutError:
            raise BackupError("从 NAS 取备份超时了")
        except aiohttp.ClientError as e:
            raise BackupError(f"NAS 连不上：{type(e).__name__}")

    async def delete(self, name: str) -> None:
        if NAME_RE.fullmatch(name):
            await self._call("DELETE", self.url + name, timeout=20)

    async def test(self) -> str:
        await self._ensure_folder()
        status, _, _ = await self._call("PUT", self.url + ".write-test", data=b"ok", timeout=20)
        if status not in (200, 201, 204):
            raise BackupError(self._why(status))
        await self._call("DELETE", self.url + ".write-test", timeout=20)
        return f"能写，{len(await self.list())} 份备份在上面"


def parse_propfind(body: bytes) -> list:
    """PROPFIND Depth 1 的回复 → [{"name", "size", "at"}]（只要 xiaozhi-….tar.gz）。"""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise BackupError("NAS 回的不是 WebDAV 的格式")
    out = []
    for resp in root.iter("{DAV:}response"):
        href = resp.findtext("{DAV:}href") or ""
        name = urllib.parse.unquote(href.rstrip("/").rsplit("/", 1)[-1])
        if not NAME_RE.fullmatch(name):
            continue
        size = resp.findtext(".//{DAV:}getcontentlength") or "0"
        mod = resp.findtext(".//{DAV:}getlastmodified") or ""
        at = name_time(name)
        if at is None and mod:
            try:
                at = email.utils.parsedate_to_datetime(mod).timestamp()
            except (TypeError, ValueError):
                at = 0
        out.append({"name": name, "size": int(size) if size.isdigit() else 0, "at": int(at or 0)})
    return out


# ---------------------------------------------------------------- 总管

class Backups:
    def __init__(self, data_dir: Path, version=lambda: "", restart=None, state_path: Path = None):
        self.data_dir = Path(data_dir)
        self.state_path = Path(state_path) if state_path else self.data_dir / "backup.json"
        self.version = version
        self.restart = restart
        self._lock = asyncio.Lock()
        self._file_lock = threading.Lock()
        self.running = None          # 正在做的：{"reason", "started"}
        self._names = set()          # 这次开机做过的文件名：同一秒做两份（备份完马上恢复）时加 -2

    # ---- 设置和记录（data/backup.json，0600：里面有 NAS 的密码）
    def _state(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        with self._file_lock:
            write_json(self.state_path, state, mode=0o600, ensure_ascii=False, indent=1)

    def settings(self) -> dict:
        s = {**DEFAULTS, **(self._state().get("settings") or {})}
        s["keep"] = s["keep"] if s["keep"] in KEEP_CHOICES else DEFAULTS["keep"]
        s["target"] = s["target"] if s["target"] in ("local", "webdav") else "local"
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(s["time"])):
            s["time"] = DEFAULTS["time"]
        return s

    def public_settings(self) -> dict:
        s = self.settings()
        pw = s.pop("webdav_password", "")
        return {**s, "webdav_password_set": bool(pw)}

    def save_settings(self, body: dict) -> dict:
        state = self._state()
        s = self.settings()
        if "enabled" in body:
            s["enabled"] = bool(body["enabled"])
        if "time" in body:
            t = str(body["time"]).strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                raise BackupError("时间写成 03:30 这样")
            s["time"] = t
        if "keep" in body:
            try:
                keep = int(body["keep"])
            except (TypeError, ValueError):
                keep = 0
            if keep not in KEEP_CHOICES:
                raise BackupError("留几份只能选 3、7、14、30")
            s["keep"] = keep
        if "target" in body:
            if body["target"] not in ("local", "webdav"):
                raise BackupError("放在网关本机或 NAS")
            s["target"] = body["target"]
        for key in ("local_dir", "webdav_url", "webdav_user"):
            if key in body:
                s[key] = str(body[key] or "").strip()[:500]
        if str(body.get("webdav_password") or ""):
            s["webdav_password"] = str(body["webdav_password"])[:500]
        if s["target"] == "webdav":
            WebDavTarget(s["webdav_url"])   # 地址格式不对就在这里说
        state["settings"] = s
        self._save(state)
        return self.public_settings()

    def target(self, s: dict = None):
        s = s or self.settings()
        if s["target"] == "webdav":
            return WebDavTarget(s["webdav_url"], s["webdav_user"], s["webdav_password"])
        return LocalTarget(s["local_dir"])

    def local(self) -> LocalTarget:
        return LocalTarget(self.settings()["local_dir"])

    def _record(self, entry: dict) -> None:
        state = self._state()
        hist = [e for e in state.get("history") or [] if isinstance(e, dict)]
        hist.insert(0, entry)
        state["history"] = hist[:HISTORY]
        self._save(state)

    # ---- 做一份
    async def run(self, reason: str = "手动") -> dict:
        """做一份，放到设好的地方，删掉超出份数的旧的。NAS 不行时先放在网关本机（也算没成功，要处理里会说）。"""
        async with self._lock:
            self.running = {"reason": reason, "started": int(time.time())}
            s = self.settings()
            name = _now_name()
            n = 1
            while name in self._names:
                n += 1
                name = _now_name().replace(".tar.gz", f"-{n}.tar.gz")
            self._names.add(name)
            entry = {"at": int(time.time()), "name": name, "reason": reason, "target": s["target"], "ok": False}
            tmp_dir = Path(tempfile.mkdtemp(prefix="hub-backup-", dir=str(self.data_dir.parent)))
            try:
                path = tmp_dir / name
                info = await asyncio.to_thread(lambda: build(self.data_dir, path, self.version()))
                entry.update(size=info["size"], files=info["files"])
                target = self.target(s)
                try:
                    await target.put(path, name)
                    entry["ok"] = True
                    entry["where"] = target.label()
                except BackupError as e:
                    entry["error"] = str(e)
                    if target.kind != "local":
                        await self.local().put(path, name)
                        entry["where"] = "网关本机（NAS 不行时先放这里）"
                if entry["ok"]:
                    await self._prune(target, s["keep"])
            except BackupError as e:
                entry["error"] = str(e)
            except OSError as e:
                entry["error"] = f"备份没做成：{e.strerror or e}"
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self.running = None
            await asyncio.to_thread(self._record, entry)
            return entry

    async def _prune(self, target, keep: int) -> None:
        try:
            have = sorted(await target.list(), key=lambda x: order(x["name"]), reverse=True)
        except BackupError:
            return
        for old in have[keep:]:
            try:
                await target.delete(old["name"])
            except BackupError:
                pass

    # ---- 看
    async def view(self) -> dict:
        s = self.settings()
        state = await asyncio.to_thread(self._state)
        hist = state.get("history") or []
        rows, err = {}, ""
        targets = [self.local()]
        if s["target"] == "webdav":
            try:
                targets.insert(0, self.target(s))
            except BackupError as e:
                err = str(e)
        for tgt in targets:     # NAS 上的在前：同名的两边都有时算 NAS 的
            try:
                for x in await tgt.list():
                    rows.setdefault(x["name"], {**x, "where": tgt.kind})
            except BackupError as e:
                err = str(e)
        files = sorted(rows.values(), key=lambda x: order(x["name"]), reverse=True)
        last_ok = next((e for e in hist if e.get("ok")), None)
        return {"settings": self.public_settings(), "backups": files, "list_error": err,
                "history": hist[:10], "last": hist[0] if hist else None, "last_ok": last_ok,
                "running": self.running, "included": await asyncio.to_thread(self.included)}

    def included(self) -> dict:
        """备份里大概有什么、多大（后台「备份里有什么」）。"""
        files = collect(self.data_dir)
        size = 0
        for _, p in files:
            try:
                size += p.stat().st_size
            except OSError:
                pass
        return {"files": len(files), "size": size}

    def attention(self, now: float = None) -> list:
        """状态页「要处理」：最近一次没成功；或开着定期备份却好几天没成功。"""
        now = now or time.time()
        s = self.settings()
        hist = self._state().get("history") or []
        if not hist:
            return []
        last = hist[0]
        out = []
        if not last.get("ok") and now - last.get("at", 0) < 7 * 86400:
            out.append({"level": "bad", "text": "网关备份没成功", "sub": str(last.get("error") or "")[:80],
                        "page": "backup", "action": "去看看"})
        else:
            ok = next((e for e in hist if e.get("ok")), None)
            if s["enabled"] and ok and now - ok.get("at", 0) > STALE_DAYS * 86400:
                out.append({"level": "warn", "text": f"网关 {int((now - ok['at']) // 86400)} 天没备份了",
                            "sub": "定期备份开着，但最近没成功过", "page": "backup", "action": "去看看"})
        return out

    # ---- 下载、恢复
    async def fetch(self, name: str, dest: Path) -> None:
        local = self.local()
        if local.path(name):
            await local.fetch(name, dest)
            return
        s = self.settings()
        if s["target"] == "webdav":
            await self.target(s).fetch(name, dest)
            return
        raise BackupError("没有这份备份")

    async def restore(self, archive: Path) -> dict:
        """检查 → 先备份现在的 → 解到旁边 → 和 data 对调 → 重启网关。"""
        manifest = await asyncio.to_thread(check, archive)
        before = await self.run("恢复前")
        if not before.get("ok") and not before.get("where"):
            raise BackupError(f"现在的数据备份不了，没恢复：{before.get('error') or ''}")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        staging = self.data_dir.parent / f"data.restore-{stamp}"
        old = self.data_dir.parent / f"data.before-restore-{stamp}"

        def swap():
            try:
                count = extract(archive, staging)
                for keep in KEEP_ON_RESTORE:
                    src = self.data_dir / keep
                    if src.is_file():
                        shutil.copy2(src, staging / keep)
                for d in SKIP_DIRS:
                    src = self.data_dir / d
                    if src.is_dir() and not (staging / d).exists():
                        shutil.copytree(src, staging / d, symlinks=True)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            os.rename(self.data_dir, old)
            os.rename(staging, self.data_dir)
            return count

        try:
            count = await asyncio.to_thread(swap)
        except OSError as e:
            raise BackupError(f"恢复没做成，现在的数据没动：{e.strerror or e}")
        entry = {"at": int(time.time()), "ok": True, "reason": "恢复", "restored": manifest.get("created"),
                 "files": count, "kept": old.name}
        await asyncio.to_thread(self._record, entry)
        if self.restart:
            asyncio.get_running_loop().call_later(1.5, self.restart)
        return {"restored": manifest.get("created"), "files": count, "kept": old.name,
                "before": before.get("name"), "restarting": bool(self.restart)}

    # ---- 每天
    def due(self, now: float = None) -> bool:
        s = self.settings()
        if not s["enabled"]:
            return False
        now = now or time.time()
        lt = time.localtime(now)
        today = time.strftime("%Y-%m-%d", lt)
        if time.strftime("%H:%M", lt) < s["time"]:
            return False
        return self._state().get("scheduled") != today

    def mark_scheduled(self, now: float = None) -> None:
        state = self._state()
        state["scheduled"] = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
        self._save(state)

    async def loop(self) -> None:
        """每分钟看一次：到了设好的时间、今天还没做过，就做一份。网关那时候没开，开了以后当天补上。"""
        await asyncio.sleep(30)
        while True:
            try:
                if await asyncio.to_thread(self.due):
                    await asyncio.to_thread(self.mark_scheduled)
                    await self.run("定时")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 常驻循环不能因为一次出错就停了
                pass
            await asyncio.sleep(60)

