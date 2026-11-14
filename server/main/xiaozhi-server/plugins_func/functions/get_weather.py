import datetime
import httpx
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.util import get_ip_info
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "获取某个地点的天气，用户应提供一个位置，比如用户说杭州天气，参数为：杭州。"
            "如果用户说的是省份，默认用省会城市。如果用户说的不是省份或城市而是一个地名，默认用该地所在省份的省会城市。"
            "问本地天气（没说地点，比如「明天下雨吗」）时不传 location，会用这台设备的住宅地址。如果系统提示里已经列出了本地未来7天天气，问本地天气就直接回答、不要调用；"
            "系统提示里没有本地天气（或写着没查到）时必须调用。天气随时在变：每次问天气都重新调用，"
            "不要拿之前对话里的天气来回答，也不要根据之前的对话说查不到——天气服务可能已经恢复。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名。国内写城市或区县的全称，如 杭州市、青浦区；用户说的是省份就传省会城市（广东→广州），"
                                   "景点换成它所在的区县或城市；国外写英文名，如 Tokyo、Paris。可选参数，问本地天气时不传",
                },
                "lang": {
                    "type": "string",
                    "description": "返回用户使用的语言code，例如zh_CN/zh_HK/en_US/ja_JP等，默认zh_CN",
                },
            },
            "required": ["lang"],
        },
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
    )
}

# 天气代码 https://dev.qweather.com/docs/resource/icons/#weather-icons
WEATHER_CODE_MAP = {
    "100": "晴",
    "101": "多云",
    "102": "少云",
    "103": "晴间多云",
    "104": "阴",
    "150": "晴",
    "151": "多云",
    "152": "少云",
    "153": "晴间多云",
    "300": "阵雨",
    "301": "强阵雨",
    "302": "雷阵雨",
    "303": "强雷阵雨",
    "304": "雷阵雨伴有冰雹",
    "305": "小雨",
    "306": "中雨",
    "307": "大雨",
    "308": "极端降雨",
    "309": "毛毛雨/细雨",
    "310": "暴雨",
    "311": "大暴雨",
    "312": "特大暴雨",
    "313": "冻雨",
    "314": "小到中雨",
    "315": "中到大雨",
    "316": "大到暴雨",
    "317": "暴雨到大暴雨",
    "318": "大暴雨到特大暴雨",
    "350": "阵雨",
    "351": "强阵雨",
    "399": "雨",
    "400": "小雪",
    "401": "中雪",
    "402": "大雪",
    "403": "暴雪",
    "404": "雨夹雪",
    "405": "雨雪天气",
    "406": "阵雨夹雪",
    "407": "阵雪",
    "408": "小到中雪",
    "409": "中到大雪",
    "410": "大到暴雪",
    "456": "阵雨夹雪",
    "457": "阵雪",
    "499": "雪",
    "500": "薄雾",
    "501": "雾",
    "502": "霾",
    "503": "扬沙",
    "504": "浮尘",
    "507": "沙尘暴",
    "508": "强沙尘暴",
    "509": "浓雾",
    "510": "强浓雾",
    "511": "中度霾",
    "512": "重度霾",
    "513": "严重霾",
    "514": "大雾",
    "515": "特强浓雾",
    "900": "热",
    "901": "冷",
    "999": "未知",
}


async def fetch_city_info(location, api_key, api_host):
    url = f"https://{api_host}/geo/v2/city/lookup?key={api_key}&location={location}&lang=zh"
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
        response = await client.get(url, headers=HEADERS)
    data = response.json()
    if data.get("error") is not None:
        logger.bind(tag=TAG).error(
            f"获取天气失败，原因：{data.get('error', {}).get('detail')}"
        )
        return None
    return data.get("location", [])[0] if data.get("location") else None


async def fetch_weather_page(url):
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.get(url, headers=HEADERS)
    return BeautifulSoup(response.text, "html.parser") if response.status_code == 200 else None


def parse_weather_info(soup):
    city_name = soup.select_one("h1.c-submenu__location").get_text(strip=True)

    current_abstract = soup.select_one(".c-city-weather-current .current-abstract")
    current_abstract = (
        current_abstract.get_text(strip=True) if current_abstract else "未知"
    )

    current_basic = {}
    for item in soup.select(
        ".c-city-weather-current .current-basic .current-basic___item"
    ):
        parts = item.get_text(strip=True, separator=" ").split(" ")
        if len(parts) == 2:
            key, value = parts[1], parts[0]
            current_basic[key] = value

    temps_list = []
    for row in soup.select(".city-forecast-tabs__row")[:7]:  # 取前7天的数据
        date = row.select_one(".date-bg .date").get_text(strip=True)
        weather_code = (
            row.select_one(".date-bg .icon")["src"].split("/")[-1].split(".")[0]
        )
        weather = WEATHER_CODE_MAP.get(weather_code, "未知")
        temps = [span.get_text(strip=True) for span in row.select(".tmp-cont .temp")]
        high_temp, low_temp = (temps[0], temps[-1]) if len(temps) >= 2 else (None, None)
        temps_list.append((date, weather, high_temp, low_temp))

    return city_name, current_abstract, current_basic, temps_list


def weather_settings(config: dict) -> dict:
    """[xiaozhi-hub] 后台「查天气 → 设置」存的值（plugins.get_weather）：
    home_location 设备所在地（填了就不按 IP 猜）；qweather_key / qweather_host 自己的和风天气 Key（填了才用和风）。
    上游配置里的 api_key 是项目共用的和风 Key（已失效），不再使用。"""
    cfg = (config.get("plugins") or {}).get("get_weather") or {}
    text = lambda k: str(cfg.get(k) or "").strip()
    # 上游 2026-11-08 起把所在地叫 location：两种写法都认
    return {"home_location": text("home_location") or text("location"), "qweather_key": text("qweather_key"),
            "qweather_host": text("qweather_host"), "default_location": text("default_location") or "上海"}


def device_home(conn):
    """[xiaozhi-hub] 这台设备在设置页设的住宅地址（core/utils/device_homes.py），没设过返回 None。"""
    from core.utils import device_homes

    return device_homes.get(getattr(conn, "device_id", None) or "")


def resolve_location(conn, location: str = None) -> str:
    """用户说的地点 > 这台设备的住宅地址 > 后台设置的所在地 > 按网关的公网 IP 推断 > 默认城市。"""
    from core.utils.cache.manager import cache_manager, CacheType

    if location:
        return location
    home = device_home(conn)
    if home:
        return home["name"]
    settings = weather_settings(conn.config)
    if settings["home_location"]:
        return settings["home_location"]
    client_ip = getattr(conn, "client_ip", None)
    if client_ip:
        info = cache_manager.get(CacheType.IP_INFO, client_ip) or get_ip_info(client_ip, logger)
        if info and info.get("city"):
            cache_manager.set(CacheType.IP_INFO, client_ip, info)
            return info["city"]
    return settings["default_location"]


async def qweather_report(location, api_key, api_host):
    city_info = await fetch_city_info(location, api_key, api_host)
    if not city_info:
        raise Exception(f"和风天气没找到「{location}」，或者 Key 不对")
    soup = await fetch_weather_page(city_info["fxLink"])
    if not soup:
        raise Exception("和风天气的网页打不开")
    city_name, current_abstract, current_basic, temps_list = parse_weather_info(soup)
    weather_report = f"您查询的位置是：{city_name}\n\n当前天气: {current_abstract}\n"
    if current_basic:
        weather_report += "详细参数：\n"
        for key, value in current_basic.items():
            if value != "0":
                weather_report += f"  · {key}: {value}\n"
    weather_report += "\n未来7天预报：\n"
    for date, weather, high, low in temps_list:
        weather_report += f"{date}: {weather}，气温 {low}~{high}\n"
    return weather_report


async def report_by_chain(place, location, settings) -> str:
    """[xiaozhi-hub] 按后台「外部服务」里天气的顺序试（设计画布第 56 块）：Open-Meteo 兼容的地址、和风天气（填了 Key 才试），
    连不上换下一个；地名查不到不往下试。"""
    import time
    from core.agent import services
    from core.utils import open_meteo

    chain = services.weather_chain()
    errors = []
    for src in chain:
        if src["kind"] == "qweather":
            if not (settings["qweather_key"] and settings["qweather_host"]):
                continue
            t0 = time.monotonic()
            try:
                report = await qweather_report(location, settings["qweather_key"], settings["qweather_host"])
            except Exception as e:  # noqa: BLE001
                services.record(src["id"], False, (time.monotonic() - t0) * 1000, str(e))
                errors.append(f"和风天气：{e}")
                continue
            services.record(src["id"], True, (time.monotonic() - t0) * 1000)
            return report
        try:
            return await open_meteo.report_at(place, location, sources=[src])
        except open_meteo.PlaceNotFound:
            raise
        except open_meteo.WeatherError as e:
            errors.append(str(e))
    if not errors:
        raise open_meteo.WeatherError("天气服务都关掉了，网关后台「外部服务」页可以打开")
    raise open_meteo.WeatherError("；".join(errors))


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def get_weather(conn: "ConnectionHandler", location: str = None, lang: str = "zh_CN"):
    """[xiaozhi-hub] 默认用 Open-Meteo（免费、不用 Key）；后台填了自己的和风天气 Key 才用和风。
    失败时 response 为 "failed"（prompt_manager 不缓存它），result 里写明原因让大模型如实告诉用户。"""
    from core.utils import open_meteo
    from core.utils.cache.manager import cache_manager, CacheType

    location = resolve_location(conn, location)
    settings = weather_settings(conn.config)
    # 没说地点（或说的就是住宅地址的名字）：用设备存的坐标，不再查一遍地名
    home = device_home(conn)
    place = home if home and location == home["name"] else None
    # [xiaozhi-hub] 按小时缓存：天气缓存本身 8 小时，跨过午夜后「今天」会变成昨天的预报，当前气温也早变了
    weather_cache_key = f"full_weather_{location}_{lang}_{datetime.datetime.now():%Y%m%d%H}"
    cached_weather_report = cache_manager.get(CacheType.WEATHER, weather_cache_key)
    if cached_weather_report:
        return ActionResponse(Action.REQLLM, cached_weather_report, None)
    try:
        weather_report = await report_by_chain(place, location, settings)
    except Exception as e:
        logger.bind(tag=TAG).warning(f"查天气失败（{location}）: {e}")
        return ActionResponse(
            Action.REQLLM, f"天气没查到：{e}。请直接告诉用户现在查不到天气和原因，不要编造天气。", "failed"
        )
    cache_manager.set(CacheType.WEATHER, weather_cache_key, weather_report)
    return ActionResponse(Action.REQLLM, weather_report, None)
