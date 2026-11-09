"""管理后台的密码哈希与登录会话。"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
MIN_PASSWORD_LENGTH = 6


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return hmac.compare_digest(digest, expected)


REMEMBER_SECONDS = 30 * 86400  # 勾了「保持登录」：30 天


class SessionStore:
    """内存中的登录会话；服务重启后需要重新登录（后台点了更新网关时除外，见 save/restore）。

    只存令牌的 SHA-256：交接文件被人看到也拿不到能用的令牌。"""

    def __init__(self, ttl_seconds: int = 12 * 3600, kept_path: Path = None):
        self.ttl = ttl_seconds
        self._sessions = {}  # 令牌的哈希 -> 过期时间
        # [xiaozhi-hub] 登录时勾了「保持登录」的会话（REMEMBER_SECONDS）写进 kept_path，网关重启后照样有效；
        # 只存令牌的哈希，文件权限 0600
        self.kept_path = Path(kept_path) if kept_path else None
        self._kept = set()
        if self.kept_path:
            self._load_kept()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, remember: bool = False) -> str:
        self._purge()
        token = secrets.token_urlsafe(32)
        key = self._key(token)
        self._sessions[key] = time.time() + (REMEMBER_SECONDS if remember else self.ttl)
        if remember and self.kept_path:
            self._kept.add(key)
            self._write_kept()
        return token

    def expiry(self, token):
        """这个会话什么时候过期（时间戳）；无效时 None。"""
        return self._sessions.get(self._key(token)) if self.valid(token) else None

    def valid(self, token) -> bool:
        if not token:
            return False
        key = self._key(token)
        expiry = self._sessions.get(key)
        if expiry is None or expiry < time.time():
            self._sessions.pop(key, None)
            return False
        return True

    def revoke(self, token) -> None:
        if token:
            key = self._key(token)
            self._sessions.pop(key, None)
            if key in self._kept:
                self._kept.discard(key)
                self._write_kept()

    def revoke_all(self, keep=None) -> None:
        """全部失效（改密码时用）；keep 是要留下的那个令牌（改密码的这个浏览器）。"""
        keep_key = self._key(keep) if keep else None
        self._sessions = {k: v for k, v in self._sessions.items() if k == keep_key}
        self._kept &= {keep_key}
        if self.kept_path:
            self._write_kept()

    def _load_kept(self) -> None:
        try:
            data = json.loads(self.kept_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = time.time()
        for k, v in (data.get("sessions") or {}).items() if isinstance(data, dict) else ():
            if isinstance(k, str) and len(k) == 64 and isinstance(v, (int, float)) and v > now:
                self._sessions[k] = float(v)
                self._kept.add(k)

    def _write_kept(self) -> None:
        now = time.time()
        kept = {k: self._sessions[k] for k in self._kept if self._sessions.get(k, 0) > now}
        self._kept = set(kept)
        try:
            self.kept_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.kept_path.with_suffix(".part")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"sessions": kept}, f)
            os.replace(tmp, self.kept_path)
        except OSError:
            pass

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, exp in self._sessions.items() if exp < now]:
            del self._sessions[key]

    def save(self, path: Path) -> None:
        """后台更新网关前调用：把登录会话交给重启后的新进程，更新完页面不用重新登录。"""
        self._purge()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"saved": time.time(), "sessions": self._sessions}, f)
        os.replace(tmp, path)

    def restore(self, path: Path, max_age: int = 1800) -> int:
        """启动时调用：读入 max_age 秒内交接过来的会话，读完就删掉文件。返回读入了几个。"""
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        finally:
            path.unlink(missing_ok=True)
        if not isinstance(data, dict) or time.time() - float(data.get("saved") or 0) > max_age:
            return 0
        now = time.time()
        sessions = {k: float(v) for k, v in (data.get("sessions") or {}).items()
                    if isinstance(k, str) and len(k) == 64 and isinstance(v, (int, float)) and v > now}
        self._sessions.update(sessions)
        return len(sessions)
