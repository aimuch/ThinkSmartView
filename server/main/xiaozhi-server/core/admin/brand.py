"""[xiaozhi-hub] 后台的名称和图标（设计画布第 14 块 E、F）：侧栏、登录页、浏览器标签都用这里的设置。

存在 data/admin_brand.json，所有浏览器一致。图标三种：
- wheat：默认的麦穗（白色麦穗在蓝青渐变的圆角块上）；
- emoji：一个表情，画在同样的渐变块上；
- image：用户上传的图片，页面先缩成 128×128 的 PNG 再传上来（网关没有 PIL，不在这边缩）。
仓库链接默认不显示，填了才在账号菜单里出现。
"""

import base64
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_NAME = "小麦网关"
NAME_MAX = 20
IMAGE_MAX_BYTES = 96 * 1024
_PNG_PREFIX = "data:image/png;base64,"
_lock = threading.Lock()

# 麦穗：主干 + 顶上一粒 + 三对叶片。叶片比旧版大（半长 3.4），整体撑满 24 的画布高度的 88%
WHEAT_LEAVES = [(9.6, 9.6, -30), (14.4, 9.6, 30), (9.5, 13.8, -32), (14.5, 13.8, 32), (9.4, 18, -34), (14.6, 18, 34)]
_LEAF = "M0 -3.4Q2.3 0 0 3.4Q-2.3 0 0 -3.4Z"


def wheat_paths(pairs: int = 3, stroke: float = 1.7) -> str:
    """24×24 画布里的麦穗，白色。pairs 是叶片对数：16px 的小图标省掉最下面一对，免得糊成一团。"""
    leaves = "".join(f'<path transform="translate({x} {y}) rotate({r})" d="{_LEAF}"/>' for x, y, r in WHEAT_LEAVES[:pairs * 2])
    return (f'<path d="M12 22.5V8.6" stroke="#fff" stroke-width="{stroke}" stroke-linecap="round" fill="none"/>'
            f'<path d="M12 1.4Q14.3 4.9 12 8.3Q9.7 4.9 12 1.4Z"/>{leaves}')


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "admin_brand.json"


def load(data_dir: Path) -> dict:
    try:
        data = json.loads(_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    icon = data.get("icon") if isinstance(data.get("icon"), dict) else {}
    kind = icon.get("kind") if icon.get("kind") in ("wheat", "emoji", "image") else "wheat"
    value = str(icon.get("value") or "")
    if kind == "image" and not value.startswith(_PNG_PREFIX):
        kind, value = "wheat", ""
    return {"name": str(data.get("name") or DEFAULT_NAME)[:NAME_MAX],
            "icon": {"kind": kind, "value": value if kind != "wheat" else ""},
            "repo_url": str(data.get("repo_url") or ""), "repo_label": str(data.get("repo_label") or "")}


def save(data_dir: Path, payload: dict) -> dict:
    """校验并保存；返回保存后的设置。不合法时抛 ValueError（写明原因）。"""
    name = re.sub(r"\s+", " ", str(payload.get("name") or "")).strip() or DEFAULT_NAME
    if len(name) > NAME_MAX:
        raise ValueError(f"名称最多 {NAME_MAX} 个字")
    icon = payload.get("icon") if isinstance(payload.get("icon"), dict) else {}
    kind = icon.get("kind") or "wheat"
    value = str(icon.get("value") or "").strip()
    if kind == "emoji":
        if not value or len(value) > 8 or any(c.isspace() for c in value):
            raise ValueError("图标请选一个表情")
    elif kind == "image":
        if not value.startswith(_PNG_PREFIX):
            raise ValueError("图片格式不对，请重新选择")
        try:
            raw = base64.b64decode(value[len(_PNG_PREFIX):], validate=True)
        except ValueError:
            raise ValueError("图片格式不对，请重新选择")
        if not raw.startswith(b"\x89PNG") or len(raw) > IMAGE_MAX_BYTES:
            raise ValueError("图片太大了，请换一张")
    elif kind == "wheat":
        value = ""
    else:
        raise ValueError("不支持这种图标")
    repo_url = str(payload.get("repo_url") or "").strip()
    if repo_url:
        u = urlparse(repo_url)
        if u.scheme not in ("http", "https") or not u.netloc or len(repo_url) > 300:
            raise ValueError("仓库链接要以 http:// 或 https:// 开头")
    repo_label = str(payload.get("repo_label") or "").strip()[:40]
    if repo_url and not repo_label:
        parts = [p for p in urlparse(repo_url).path.split("/") if p]
        repo_label = parts[-1] if parts else urlparse(repo_url).netloc
    data = {"name": name, "icon": {"kind": kind, "value": value}, "repo_url": repo_url, "repo_label": repo_label}
    path = _path(data_dir)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    return load(data_dir)


def favicon(data_dir: Path):
    """浏览器标签的小图标：(内容, content_type)。"""
    b = load(data_dir)
    kind, value = b["icon"]["kind"], b["icon"]["value"]
    if kind == "image":
        return base64.b64decode(value[len(_PNG_PREFIX):]), "image/png"
    tile = ('<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6c8cff"/>'
            '<stop offset="1" stop-color="#5ee0ff"/></linearGradient></defs><rect width="64" height="64" rx="15" fill="url(#g)"/>')
    if kind == "emoji":
        from html import escape
        inner = f'<text x="32" y="45" font-size="40" text-anchor="middle">{escape(value)}</text>'
    else:
        inner = f'<g transform="translate(4.5 4.5) scale(2.29)" fill="#fff">{wheat_paths(2, 2.1)}</g>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{tile}{inner}</svg>'
    return svg.encode("utf-8"), "image/svg+xml"
