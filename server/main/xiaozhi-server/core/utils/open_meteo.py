"""[xiaozhi-hub] 天气：Open-Meteo（免费，不用注册、不用 Key）。

上游的查天气插件用项目共用的和风天气 Key，2026-11 起返回 401，天气一直拿不到。这里换成 Open-Meteo：
地名先查 geocoding-api（GeoNames 的中文名，要写全称：「青浦区」能查到、「上海青浦」「青浦」查不到，
所以按几种写法依次试），再查 7 天预报和当前空气质量，拼成一段中文交给大模型。
文档：https://open-meteo.com/en/docs
"""
import asyncio
import datetime
import time

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
TIMEOUT = httpx.Timeout(6.0, connect=3.0)

# WMO 天气代码 https://open-meteo.com/en/docs#weathervariables
WMO = {
    0: "晴", 1: "晴间少云", 2: "多云", 3: "阴", 45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴有冰雹", 99: "强雷阵雨伴有冰雹",
}
WEEKDAYS = "一二三四五六日"
_SUFFIXES = ("", "区", "市", "县")
_PROVINCES = ("内蒙古", "黑龙江", "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "江苏", "浙江",
              "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "四川", "贵州",
              "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆", "香港", "澳门", "台湾")
_geo_cache = {}   # 地名 -> (时间, 结果)；地名对应的坐标不会变，缓存一天
GEO_TTL = 86400


class WeatherError(Exception):
    pass


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT)


def candidates(name: str) -> list:
    """要试的地名写法：原样、加「区/市/县」；「上海青浦」这类省市连写的，再试去掉前面省份/城市的部分。
    返回 [(查询词, 要求结果所在省市包含的前缀)]。"""
    name = name.strip().replace(" ", "")
    for tail in ("省", "自治区", "特别行政区"):
        name = name.replace(tail, "")
    out = [(name + s, "") for s in _SUFFIXES]
    for p in _PROVINCES:
        if name.startswith(p) and len(name) > len(p):
            rest = name[len(p):].lstrip("市")
            out += [(rest + s, p) for s in _SUFFIXES]
            break
    # 「杭州市西湖区」：再试最后一级（西湖区），要求在杭州
    if "市" in name.rstrip("市"):
        head, _, last = name.rstrip("市").rpartition("市")
        for p in _PROVINCES:
            head = head[len(p):] if head.startswith(p) and len(head) > len(p) else head
        out += [(last + s, head[:2]) for s in _SUFFIXES]
        if head:   # 区县查不到就用它所在的城市
            out += [(head + "市", ""), (head, "")]
    seen, uniq = set(), []
    for q, prefix in out:
        if q and q not in seen:
            seen.add(q)
            uniq.append((q, prefix))
    return uniq


def _label(r: dict) -> str:
    parts = [r.get("admin1"), r.get("admin2"), r.get("name")]
    out = []
    for p in parts:
        if p and (not out or p not in out[-1]):
            out.append(p)
    return "".join(out) if r.get("country_code") == "CN" else "，".join(reversed(out)) + f"（{r.get('country', '')}）"


async def geocode(client: httpx.AsyncClient, name: str) -> dict:
    hit = _geo_cache.get(name)
    if hit and time.time() - hit[0] < GEO_TTL:
        return hit[1]
    found = None
    tries = candidates(name)

    async def lookup(query):
        resp = await client.get(GEOCODE_URL, params={"name": query, "count": 5, "language": "zh"})
        resp.raise_for_status()
        return resp.json().get("results") or []

    # 几种写法同时查（每次约 0.5～1 秒，一个个试要好几秒），按顺序取第一个查到的
    answers = await asyncio.gather(*(lookup(q) for q, _ in tries), return_exceptions=True)
    errors = [a for a in answers if isinstance(a, Exception)]
    for (query, prefix), results in zip(tries, answers):
        if isinstance(results, Exception):
            continue
        if prefix:
            results = [r for r in results if prefix in (r.get("admin1") or "") + (r.get("admin2") or "")]
        if results:
            # 同名的地方取人口多的（「东京」是日本东京，不是镇江的东京村），一样多时优先中国的
            results.sort(key=lambda r: (-(r.get("population") or 0), r.get("country_code") != "CN"))
            r = results[0]
            found = {"name": _label(r), "latitude": r["latitude"], "longitude": r["longitude"]}
            break
    if not found and errors and len(errors) == len(answers):
        raise errors[0]
    if not found:
        raise WeatherError(f"没找到「{name}」这个地方，换个写法试试（比如写成「青浦区」「杭州市」）")
    _geo_cache[name] = (time.time(), found)
    return found


def wind_dir(deg) -> str:
    if deg is None:
        return ""
    return ("北", "东北", "东", "东南", "南", "西南", "西", "西北")[int((deg % 360 + 22.5) // 45) % 8] + "风"


def wind_level(kmh) -> int:
    """风速（km/h）换算成蒲福风级。"""
    ms = (kmh or 0) / 3.6
    for level, top in enumerate((0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7)):
        if ms < top:
            return level
    return 12


def pm25_level(v) -> str:
    """按国标 PM2.5 浓度限值分级（24 小时均值的分档，当前值仅供参考）。"""
    for top, name in ((35, "优"), (75, "良"), (115, "轻度污染"), (150, "中度污染"), (250, "重度污染")):
        if v <= top:
            return name
    return "严重污染"


def _day_label(iso: str, today: datetime.date) -> str:
    d = datetime.date.fromisoformat(iso)
    rel = {0: "今天", 1: "明天", 2: "后天"}.get((d - today).days)
    base = f"{d.month}月{d.day}日 周{WEEKDAYS[d.weekday()]}"
    return f"{rel} {base}" if rel else base


def _num(v, nd=0):
    return "?" if v is None else (f"{v:.{nd}f}" if nd else f"{round(v)}")


def build_report(place: dict, fc: dict, air: dict = None) -> str:
    cur = fc.get("current") or {}
    daily = fc.get("daily") or {}
    lines = [f"您查询的位置是：{place['name']}", ""]
    now = f"当前天气：{WMO.get(cur.get('weather_code'), '未知')}，气温 {_num(cur.get('temperature_2m'), 1)}℃"
    if cur.get("apparent_temperature") is not None:
        now += f"（体感 {_num(cur.get('apparent_temperature'), 1)}℃）"
    if cur.get("relative_humidity_2m") is not None:
        now += f"，湿度 {_num(cur.get('relative_humidity_2m'))}%"
    if cur.get("wind_speed_10m") is not None:
        now += f"，{wind_dir(cur.get('wind_direction_10m'))} {wind_level(cur.get('wind_speed_10m'))} 级"
    if cur.get("precipitation"):
        now += f"，当前降水 {_num(cur.get('precipitation'), 1)} 毫米"
    lines.append(now)
    pm = ((air or {}).get("current") or {}).get("pm2_5")
    if pm is not None:
        lines.append(f"空气质量：PM2.5 {_num(pm)} 微克/立方米（{pm25_level(pm)}）")
    lines += ["", "未来7天预报："]
    try:
        today = datetime.date.fromisoformat(str(cur.get("time", ""))[:10])
    except ValueError:
        today = datetime.date.today()
    times = daily.get("time") or []
    for i, iso in enumerate(times):
        def at(key):
            vals = daily.get(key) or []
            return vals[i] if i < len(vals) else None
        line = (f"{_day_label(iso, today)}：{WMO.get(at('weather_code'), '未知')}，"
                f"{_num(at('temperature_2m_min'))}~{_num(at('temperature_2m_max'))}℃")
        if at("precipitation_probability_max") is not None:
            line += f"，降水概率 {_num(at('precipitation_probability_max'))}%"
        if at("uv_index_max") is not None and i == 0:
            line += f"，紫外线指数 {_num(at('uv_index_max'))}"
        lines.append(line)
    lines += ["", "（数据来自 Open-Meteo 气象模型，回答时说结论就行，不用念数据来源）"]
    return "\n".join(lines)


async def lookup(name: str) -> dict:
    """地名 → {"name", "latitude", "longitude"}（设备设住宅地址时确认用）。查不到或连不上时抛 WeatherError。"""
    try:
        async with _client() as client:
            return await geocode(client, name)
    except httpx.HTTPError as e:
        raise WeatherError(f"地名查询服务连不上（{type(e).__name__}）")


async def report(location: str) -> str:
    """地名 → 当前天气、空气质量和 7 天预报的中文文本。查不到或连不上时抛 WeatherError。"""
    return await report_at(None, location)


async def report_at(place: dict = None, location: str = None) -> str:
    """已知坐标的地方（设备的住宅地址，{"name", "latitude", "longitude"}）直接查，不再查地名；
    place 为 None 时按 location 查地名。"""
    try:
        async with _client() as client:
            if place is None:
                place = await geocode(client, location)
            loc = {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto"}
            forecast_req = client.get(FORECAST_URL, params={
                **loc, "forecast_days": 7,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
                           "wind_speed_10m,wind_direction_10m,precipitation",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max",
            })
            air_req = client.get(AIR_URL, params={**loc, "current": "pm2_5"})
            resp, air_resp = await asyncio.gather(forecast_req, air_req, return_exceptions=True)
            if isinstance(resp, Exception):
                raise resp
            resp.raise_for_status()
            forecast = resp.json()
            # 空气质量拿不到不影响天气
            air = air_resp.json() if not isinstance(air_resp, Exception) and air_resp.status_code == 200 else None
    except httpx.HTTPError as e:
        raise WeatherError(f"天气服务连不上（{type(e).__name__}）")
    return build_report(place, forecast, air)
