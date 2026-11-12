"""接入共用的：读设置（留空用默认值）、带超时的 HTTP、给用户看的错误。

用 aiohttp，不用 httpx：测试里 httpx 被替换了（tests/conftest.py）。
"""

import asyncio
import time

import aiohttp

# 插件名 -> (设置存在 plugins 下的键, 默认值)。字段和后台表单一致（core/admin/catalog.PLUGIN_SETTINGS）
DEFAULTS = {
    "smart_hub": {"url": "http://127.0.0.1:8780", "token": ""},
    "rack_status": {"prometheus": "http://127.0.0.1:9090", "url": "http://127.0.0.1:8088", "token": ""},
    "repo_status": {"url": "http://127.0.0.1:8765", "password": ""},
    "desk_hub": {"token": ""},
}
PLUGINS = tuple(DEFAULTS)
LABELS = {"smart_hub": "智能中枢", "rack_status": "机柜", "repo_status": "仓库同步", "desk_hub": "DeskHub"}


class IntegrationError(Exception):
    """接入出错：message 是给人看的一句话（屏幕、语音、后台测试按钮都直接用）。"""


def _blank(value) -> bool:
    return value is None or not str(value).strip()


def settings(config: dict, name: str) -> dict:
    """config：完整配置（conn.config 或后台的合并配置）。没填的字段用默认值，地址去掉末尾的 /。"""
    saved = (config.get("plugins") or {}).get(name)
    saved = saved if isinstance(saved, dict) else {}
    out = {}
    for key, default in DEFAULTS[name].items():
        value = saved.get(key)
        out[key] = default if _blank(value) else str(value).strip()
    for key in ("url", "prometheus"):
        if key in out:
            out[key] = out[key].rstrip("/")
    return out


def ready(config: dict, name: str) -> bool:
    """必填的设置都填了（智能中枢的令牌、DeskHub 的口令）；机柜和仓库同步没有必填项，用默认地址就行。"""
    from core.admin import plugin_settings

    return plugin_settings.configured(config, name)


def enabled_for(config: dict, device: str, name: str) -> bool:
    """这台设备能不能用这个插件：设备单独设了插件就看它的，没设看全局的。"""
    from core.utils import device_settings

    own = device_settings.get(device or "").get("plugins") if device else None
    if own is None:
        own = ((config.get("Intent") or {}).get("function_call") or {}).get("functions") or []
    return name in own


async def request(method: str, url: str, *, headers: dict = None, json=None, timeout: float = 8, auth=None):
    """-> 解析好的 JSON。连不上、超时、非 2xx 都变成 IntegrationError。"""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, connect=min(timeout, 4))) as s:
            async with s.request(method, url, headers=headers, json=json, auth=auth) as r:
                if r.status == 401:
                    raise IntegrationError("令牌或密码不对（对方回 401）")
                if r.status == 403:
                    raise IntegrationError("对方拒绝了（403）")
                try:
                    data = await r.json(content_type=None)
                except Exception:
                    data = None
                if r.status >= 400:
                    why = (data or {}).get("error") if isinstance(data, dict) else None
                    raise IntegrationError(f"对方回 {r.status}" + (f"：{why}" if why else ""))
                return data
    except IntegrationError:
        raise
    except asyncio.TimeoutError:
        raise IntegrationError(f"{timeout:g} 秒没有回应")
    except aiohttp.ClientConnectorError:
        raise IntegrationError("连不上")
    except aiohttp.ClientError as e:
        raise IntegrationError(f"请求失败：{type(e).__name__}")


class Cache:
    """几个设备同时看工作台时只查一次：key -> (时间, 值)。"""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._items = {}
        self._locks = {}

    async def get(self, key, fetch):
        hit = self._items.get(key)
        if hit and time.monotonic() - hit[0] < self.seconds:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._items.get(key)
            if hit and time.monotonic() - hit[0] < self.seconds:
                return hit[1]
            value = await fetch()
            self._items[key] = (time.monotonic(), value)
            return value

    def drop(self, key=None):
        if key is None:
            self._items.clear()
        else:
            self._items.pop(key, None)
