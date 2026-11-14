"""[xiaozhi-hub] 外部服务（设计画布第 56 块）：天气、电台、新闻、定位用到的外网服务，有预设、能改、能自己加。

- 天气：按 order 从上往下试，连不上换下一个。预设 Open-Meteo 和和风天气（和风只用于语音问天气，Key 在插件页填）；
  自己加的是 Open-Meteo 兼容的预报地址（比如自己部署的 Open-Meteo）。Open-Meteo 连不上时自动换线路
  （core/utils/net_lines.py），也可以在这里手动指定一个地址（line）。
- 电台：预设是原来设备里写死的那几个台，可以关掉、自己加；设备连网关时取 /kiosk/radio。
- 新闻：地址在插件设置里改（plugins.get_news_from_newsnow / get_news_from_chinanews），这里只看状态和测试。
- 定位：按 IP 猜城市（pconline）可以关。

用户改的存在 data/services.json；预设写在这里，随网关更新。状态（能不能用、多快、上次成功）只在内存里：
真用的时候顺便记（record），后台的「测试」也记。
"""
import json
import os
import re
import secrets
import threading
import time

from config.config_loader import get_project_dir

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
WEATHER_PRESETS = [
    {"id": "open-meteo", "name": "Open-Meteo", "kind": "open-meteo", "url": OPEN_METEO, "note": "免费，不用 Key"},
    {"id": "qweather", "name": "和风天气", "kind": "qweather", "url": "",
     "note": "只用于语音问天气 · Key 在「插件 → 查天气」里填"},
]
# 和设备客户端 media.STATIONS 一致（id 也一样：设备上「放中国之声」、场景里存的 id 都不变）
RADIO_PRESETS = [
    {"id": "zgzs", "name": "中国之声", "url": "http://ngcdn001.cnr.cn/live/zgzs/index.m3u8"},
    {"id": "jjzs", "name": "经济之声", "url": "http://ngcdn002.cnr.cn/live/jjzs/index.m3u8"},
    {"id": "dfgb", "name": "东方广播（上海）", "url": "http://satellitepull.cnr.cn/live/wx32dfgbdt/playlist.m3u8"},
    {"id": "dycj", "name": "第一财经广播", "url": "http://satellitepull.cnr.cn/live/wx32dycjgb/playlist.m3u8"},
    {"id": "jsjt", "name": "江苏交通广播", "url": "http://satellitepull.cnr.cn/live/wx32jsjtgb/playlist.m3u8"},
    {"id": "bjxw", "name": "北京新闻广播", "url": "http://satellitepull.cnr.cn/live/wxbjxwgb/playlist.m3u8"},
]
NEWS = [
    {"id": "newsnow", "name": "热点新闻（newsnow）", "plugin": "get_news_from_newsnow", "label": "热点新闻"},
    {"id": "chinanews", "name": "中新网 RSS", "plugin": "get_news_from_chinanews", "label": "中新网新闻"},
]
GEO = {"id": "geoip", "name": "按 IP 猜城市（pconline）", "url": "https://whois.pconline.com.cn/ipJson.jsp"}
LINE_HOSTS = ("api.open-meteo.com",)
MAX_CUSTOM = 20

_lock = threading.Lock()
STATUS = {}          # 服务 id → {"ok", "ms", "at", "err", "via"}


class ServicesError(ValueError):
    pass


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "services.json")


def _empty() -> dict:
    return {"weather": {"order": [], "off": [], "custom": []}, "line": {}, "radio": {"off": [], "custom": []},
            "geoip": True}


def load() -> dict:
    d = _empty()
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return d
    if not isinstance(raw, dict):
        return d
    for group in ("weather", "radio"):
        g = raw.get(group) if isinstance(raw.get(group), dict) else {}
        for k in d[group]:
            if isinstance(g.get(k), list):
                d[group][k] = g[k]
    if isinstance(raw.get("line"), dict):
        d["line"] = {h: ip for h, ip in raw["line"].items() if h in LINE_HOSTS and _is_ip(ip)}
    if isinstance(raw.get("geoip"), bool):
        d["geoip"] = raw["geoip"]
    return d


def save(d: dict):
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())


def _is_ip(s) -> bool:
    return isinstance(s, str) and re.fullmatch(r"(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}", s) is not None


def _url_ok(url: str) -> bool:
    return isinstance(url, str) and len(url) <= 300 and re.match(r"^https?://[^\s/]+", url) is not None


# ---------- 天气

def weather_all(d: dict = None) -> list:
    """全部天气服务，按用户排的顺序（没排过的：预设在前、自己加的在后），带 on。"""
    d = d or load()
    items = {p["id"]: dict(p, custom=False) for p in WEATHER_PRESETS}
    for c in d["weather"]["custom"]:
        if isinstance(c, dict) and c.get("id") and _url_ok(c.get("url")):
            items[c["id"]] = {"id": c["id"], "name": str(c.get("name") or "自己加的"), "kind": "custom",
                              "url": c["url"], "note": "", "custom": True}
    order = [i for i in d["weather"]["order"] if i in items]
    order += [i for i in items if i not in order]
    off = set(d["weather"]["off"])
    return [dict(items[i], on=i not in off) for i in order]


def weather_chain(d: dict = None, kinds=("open-meteo", "custom", "qweather")) -> list:
    return [w for w in weather_all(d) if w["on"] and w["kind"] in kinds]


def line(host: str) -> str:
    """手动指定的地址（没指定是空字符串：自动）。"""
    return load()["line"].get(host, "") if host in LINE_HOSTS else ""


# ---------- 电台

def radio_all(d: dict = None) -> list:
    d = d or load()
    off = set(d["radio"]["off"])
    out = [dict(p, custom=False, on=p["id"] not in off) for p in RADIO_PRESETS]
    for c in d["radio"]["custom"]:
        if isinstance(c, dict) and c.get("id") and c.get("name") and _url_ok(c.get("url")):
            out.append({"id": c["id"], "name": str(c["name"]), "url": c["url"], "custom": True, "on": c["id"] not in off})
    return out


def radio_list() -> list:
    """设备上能放的台：[{"id", "name", "url"}]。"""
    return [{"id": r["id"], "name": r["name"], "url": r["url"]} for r in radio_all() if r["on"]]


def geoip_on() -> bool:
    return load()["geoip"]


# ---------- 改

def apply(op: dict) -> dict:
    """后台的一次修改。不认识或不合法时抛 ServicesError。"""
    kind = op.get("op")
    with _lock:
        d = load()
        if kind == "toggle":
            group, sid, on = op.get("group"), str(op.get("id") or ""), bool(op.get("on"))
            if group == "geoip":
                d["geoip"] = on
            elif group in ("weather", "radio"):
                ids = {x["id"] for x in (weather_all(d) if group == "weather" else radio_all(d))}
                if sid not in ids:
                    raise ServicesError("没有这个服务")
                off = [x for x in d[group]["off"] if x != sid]
                d[group]["off"] = off if on else off + [sid]
            else:
                raise ServicesError("没有这一类")
        elif kind == "order":
            ids = [str(x) for x in op.get("ids") or []]
            have = [w["id"] for w in weather_all(d)]
            if sorted(ids) != sorted(have):
                raise ServicesError("顺序里的服务对不上，刷新一下再排")
            d["weather"]["order"] = ids
        elif kind == "add":
            group = op.get("group")
            name = str(op.get("name") or "").strip()[:20]
            url = str(op.get("url") or "").strip()
            if group not in ("weather", "radio"):
                raise ServicesError("这一类不能自己加")
            if not _url_ok(url):
                raise ServicesError("地址要以 http:// 或 https:// 开头")
            if group == "radio" and not name:
                raise ServicesError("填个台名：说这个名字就能放")
            if len(d[group]["custom"]) >= MAX_CUSTOM:
                raise ServicesError(f"最多自己加 {MAX_CUSTOM} 个")
            names = {x["name"] for x in (weather_all(d) if group == "weather" else radio_all(d))}
            if name and name in names:
                raise ServicesError(f"已经有叫「{name}」的了")
            d[group]["custom"].append({"id": "c" + secrets.token_hex(3), "name": name or "自己加的", "url": url})
        elif kind == "delete":
            group, sid = op.get("group"), str(op.get("id") or "")
            if group not in ("weather", "radio"):
                raise ServicesError("这一类没有自己加的")
            before = len(d[group]["custom"])
            d[group]["custom"] = [c for c in d[group]["custom"] if c.get("id") != sid]
            if len(d[group]["custom"]) == before:
                raise ServicesError("预设的不能删，可以关掉")
            d[group]["off"] = [x for x in d[group]["off"] if x != sid]
            if group == "weather":
                d["weather"]["order"] = [x for x in d["weather"]["order"] if x != sid]
            STATUS.pop(sid, None)
        elif kind == "line":
            host, ip = str(op.get("host") or LINE_HOSTS[0]), str(op.get("ip") or "").strip()
            if host not in LINE_HOSTS:
                raise ServicesError("这个域名不能指定地址")
            if ip and not _is_ip(ip):
                raise ServicesError("地址写成 IPv4，比如 188.40.99.226")
            d["line"] = {k: v for k, v in d["line"].items() if k != host}
            if ip:
                d["line"][host] = ip
        else:
            raise ServicesError("不认识的操作")
        save(d)
        return d


# ---------- 状态

def record(sid: str, ok: bool, ms: float = None, err: str = "", via: str = ""):
    s = STATUS.setdefault(sid, {})
    s.update({"ok": bool(ok), "ms": None if ms is None else int(ms), "at": int(time.time()), "err": str(err or "")[:120],
              "via": via})
    if ok:
        s["last_ok"] = s["at"]


def status(sid: str) -> dict:
    return dict(STATUS.get(sid) or {})


class timer:
    """with services.timer("open-meteo") as t: ...；出异常记失败（原因是异常文字），否则记成功和耗时。
    t.via = "..." 可以写上走的哪条线路；t.skip() 表示这次不算（比如没填 Key 就跳过了）。"""

    def __init__(self, sid: str):
        self.sid, self.via, self._skip = sid, "", False

    def skip(self):
        self._skip = True

    def __enter__(self):
        self.t0 = time.monotonic()
        return self

    def __exit__(self, et, ev, tb):
        if not self._skip:
            record(self.sid, et is None, (time.monotonic() - self.t0) * 1000, "" if et is None else str(ev) or et.__name__, self.via)
        return False


# ---------- 测试（后台的「测试」「全部测一遍」）

LAST_ALL = {"at": 0}
TEST_PLACE = {"name": "上海", "latitude": 31.23, "longitude": 121.47}


def _test_place() -> dict:
    from core.utils import device_homes

    try:
        homes = device_homes._load()
    except Exception:  # noqa: BLE001
        homes = {}
    for h in (homes or {}).values():
        if isinstance(h, dict) and "latitude" in h:
            return h
    return TEST_PLACE


async def _test_weather(src: dict, merged: dict):
    from core.utils import open_meteo
    from plugins_func.functions import get_weather

    if src["kind"] == "qweather":
        s = get_weather.weather_settings(merged)
        if not (s["qweather_key"] and s["qweather_host"]):
            raise ServicesError("没填 Key，跳过")
        t0 = time.monotonic()
        try:
            await get_weather.qweather_report(s["home_location"] or s["default_location"], s["qweather_key"], s["qweather_host"])
        except Exception as e:  # noqa: BLE001
            record(src["id"], False, (time.monotonic() - t0) * 1000, str(e))
            return
        record(src["id"], True, (time.monotonic() - t0) * 1000)
        return
    try:
        await open_meteo.fetch(_test_place(), None, sources=[src])   # 自己会记状态
    except open_meteo.WeatherError:
        pass


async def _test_radio(r: dict):
    import httpx

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), follow_redirects=True) as c:
            async with c.stream("GET", r["url"], headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status_code != 200:
                    raise ServicesError(f"返回 {resp.status_code}")
                head = b""
                async for chunk in resp.aiter_bytes():
                    head += chunk
                    if len(head) >= 512:
                        break
                ctype = resp.headers.get("content-type", "").lower()
        if (".m3u8" in r["url"].lower() or "mpegurl" in ctype) and not head.lstrip().startswith(b"#EXTM3U"):
            raise ServicesError("不是直播流（m3u8 要以 #EXTM3U 开头）")
        if not head:
            raise ServicesError("没有数据")
    except ServicesError as e:
        record(r["id"], False, (time.monotonic() - t0) * 1000, str(e))
    except (httpx.ConnectError, httpx.ConnectTimeout):
        record(r["id"], False, (time.monotonic() - t0) * 1000, "连不上")
    except httpx.TimeoutException:
        record(r["id"], False, (time.monotonic() - t0) * 1000, "超时")
    except httpx.HTTPError as e:
        record(r["id"], False, (time.monotonic() - t0) * 1000, type(e).__name__)
    else:
        record(r["id"], True, (time.monotonic() - t0) * 1000)


async def _test_news(sid: str, merged: dict):
    cfg = (merged.get("plugins") or {})
    if sid == "newsnow":
        from plugins_func.functions import get_news_from_newsnow as nn

        class _Conn:   # fetch_news_from_api 只读 conn.config
            config = {"plugins": {"get_news_from_newsnow": cfg.get("get_news_from_newsnow") or {}}}
        await nn.fetch_news_from_api(_Conn(), "thepaper")   # 自己会记状态
    else:
        from plugins_func.functions import get_news_from_chinanews as cn

        url = (cfg.get("get_news_from_chinanews") or {}).get("default_rss_url") or "https://www.chinanews.com.cn/rss/society.xml"
        await cn.fetch_news_from_rss(url)


async def _test_geo():
    import asyncio
    import requests

    def ask():
        with timer("geoip"):
            data = requests.get(GEO["url"] + "?json=true&ip=", timeout=5).json()
            if not data.get("city") and not data.get("pro"):
                raise ServicesError("没认出城市")
    try:
        await asyncio.to_thread(ask)
    except Exception:  # noqa: BLE001
        pass


async def run_test(group: str, sid: str, merged: dict):
    """测一个；状态记在 STATUS 里。不认识的抛 ServicesError。"""
    if group == "weather":
        src = next((w for w in weather_all() if w["id"] == sid), None)
        if not src:
            raise ServicesError("没有这个服务")
        await _test_weather(src, merged)
    elif group == "radio":
        r = next((x for x in radio_all() if x["id"] == sid), None)
        if not r:
            raise ServicesError("没有这个台")
        await _test_radio(r)
    elif group == "news" and sid in ("newsnow", "chinanews"):
        await _test_news(sid, merged)
    elif group == "geoip":
        await _test_geo()
    else:
        raise ServicesError("没有这一类")


async def run_all(merged: dict, group: str = None):
    """全部测一遍（关掉的不测；group 只测一类）。"""
    import asyncio

    jobs = []
    if group in (None, "weather"):
        for w in weather_all():
            if w["on"] and not (w["kind"] == "qweather"):
                jobs.append(_test_weather(w, merged))
    if group in (None, "radio"):
        jobs += [_test_radio(r) for r in radio_all() if r["on"]]
    if group is None:
        jobs += [_test_news("newsnow", merged), _test_news("chinanews", merged)]
        if geoip_on():
            jobs.append(_test_geo())
    await asyncio.gather(*jobs, return_exceptions=True)
    if group is None:
        LAST_ALL["at"] = int(time.time())


def view(merged: dict) -> dict:
    """后台「外部服务」页要的全部。"""
    from core.utils import net_lines

    def st(sid):
        return status(sid)
    news = [{"id": n["id"], "name": n["name"], "plugin": n["plugin"], "label": n["label"],
             "url": _news_url(n["id"], merged), "status": st(n["id"])} for n in NEWS]
    return {
        "weather": [dict(w, status=st(w["id"])) for w in weather_all()],
        "qweather_ready": _qweather_ready(merged),
        "line": net_lines.info(LINE_HOSTS[0]),
        "radio": [dict(r, status=st(r["id"])) for r in radio_all()],
        "news": news,
        "geoip": {**GEO, "on": geoip_on(), "status": st("geoip")},
        "checked_at": LAST_ALL["at"],
    }


def _news_url(sid: str, merged: dict) -> str:
    cfg = (merged.get("plugins") or {})
    if sid == "newsnow":
        return str((cfg.get("get_news_from_newsnow") or {}).get("url") or "https://newsnow.busiyi.world/api/s?id=")
    return str((cfg.get("get_news_from_chinanews") or {}).get("default_rss_url") or "https://www.chinanews.com.cn/rss/society.xml")


def _qweather_ready(merged: dict) -> bool:
    cfg = ((merged.get("plugins") or {}).get("get_weather") or {})
    return bool(str(cfg.get("qweather_key") or "").strip() and str(cfg.get("qweather_host") or "").strip())
