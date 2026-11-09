"""天气：Open-Meteo 的地名写法、报告文本、失败时不缓存。不联网（假的 httpx 传输层）。"""
import json
from types import SimpleNamespace

import httpx
import pytest

from core.utils import open_meteo
from plugins_func.functions import get_weather  # 导入时会读配置（asyncio.run），不能放在异步测试里

FORECAST = {
    "current": {"time": "2026-11-08T21:00", "temperature_2m": 15.6, "relative_humidity_2m": 95,
                "apparent_temperature": 15.1, "weather_code": 3, "wind_speed_10m": 14.8, "wind_direction_10m": 45,
                "precipitation": 0},
    "daily": {"time": ["2026-11-08", "2026-11-09", "2026-11-10"], "weather_code": [3, 61, 0],
              "temperature_2m_max": [17.2, 15.0, 19.4], "temperature_2m_min": [12.1, 11.0, 10.6],
              "precipitation_probability_max": [20, 80, 0], "uv_index_max": [2.1, 1.0, 4.0]},
}
QINGPU = {"name": "青浦区", "admin1": "上海市", "admin2": "上海市", "country_code": "CN", "latitude": 31.15, "longitude": 121.12}


def test_candidates_try_full_names_and_strip_province():
    qs = open_meteo.candidates("上海青浦")
    assert qs[0] == ("上海青浦", "") and ("青浦区", "上海") in qs
    assert ("杭州市", "") in open_meteo.candidates("杭州")
    assert ("西湖区", "杭州") in open_meteo.candidates("浙江省杭州市西湖区")


def test_wind_and_air_levels():
    assert open_meteo.wind_dir(45) == "东北风" and open_meteo.wind_dir(359) == "北风"
    assert open_meteo.wind_level(14.8) == 3 and open_meteo.wind_level(0) == 0
    assert open_meteo.pm25_level(20) == "优" and open_meteo.pm25_level(80) == "轻度污染"


def test_build_report():
    text = open_meteo.build_report({"name": "上海市青浦区"}, FORECAST, {"current": {"pm2_5": 22}})
    assert "您查询的位置是：上海市青浦区" in text
    assert "当前天气：阴，气温 15.6℃（体感 15.1℃），湿度 95%，东北风 3 级" in text
    assert "PM2.5 22 微克/立方米（优）" in text
    assert "今天 11月8日 周日：阴，12~17℃，降水概率 20%，紫外线指数 2" in text
    assert "明天 11月9日 周一：小雨，11~15℃，降水概率 80%" in text


def _transport(geo_hits):
    def handle(request):
        url = str(request.url)
        if url.startswith(open_meteo.GEOCODE_URL):
            name = request.url.params["name"]
            return httpx.Response(200, json={"results": geo_hits.get(name, [])})
        if url.startswith(open_meteo.FORECAST_URL):
            return httpx.Response(200, json=FORECAST)
        return httpx.Response(200, json={"current": {"pm2_5": 40}})
    return httpx.MockTransport(handle)


async def test_report_tries_variants(monkeypatch):
    open_meteo._geo_cache.clear()
    # conftest 把 httpx.AsyncClient 换成了不能联网的假类，这里用真的类配假的传输层
    from httpx._client import AsyncClient
    monkeypatch.setattr(open_meteo, "_client", lambda: AsyncClient(transport=_transport({"青浦区": [QINGPU]})))
    text = await open_meteo.report("上海青浦")
    assert text.startswith("您查询的位置是：上海市青浦区") and "（良）" in text
    with pytest.raises(open_meteo.WeatherError, match="没找到"):
        await open_meteo.report("不存在的地方")


async def test_get_weather_failure_not_cached(monkeypatch):
    from core.utils.cache.manager import cache_manager, CacheType

    async def boom(place=None, location=None):
        raise open_meteo.WeatherError("天气服务连不上（ConnectTimeout）")
    monkeypatch.setattr(open_meteo, "report_at", boom)
    conn = SimpleNamespace(config={"plugins": {"get_weather": {"home_location": "测试区"}}}, client_ip=None)
    r = await get_weather.get_weather(conn)
    assert r.response == "failed" and "连不上" in r.result and "不要编造" in r.result
    import datetime
    hour = f"{datetime.datetime.now():%Y%m%d%H}"
    assert cache_manager.get(CacheType.WEATHER, f"full_weather_测试区_zh_CN_{hour}") is None

    async def ok(place=None, location=None):
        return f"您查询的位置是：{location}"
    monkeypatch.setattr(open_meteo, "report_at", ok)
    r = await get_weather.get_weather(conn)
    assert r.response is None and r.result == "您查询的位置是：测试区"
    # 用户说了别的城市就查那个
    assert (await get_weather.get_weather(conn, location="杭州")).result == "您查询的位置是：杭州"


def test_location_priority():
    conn = SimpleNamespace(config={"plugins": {"get_weather": {"home_location": "青浦区", "api_key": "upstream"}}}, client_ip=None)
    assert get_weather.resolve_location(conn, "北京") == "北京"
    assert get_weather.resolve_location(conn) == "青浦区"
    conn.config = {"plugins": {"get_weather": {"default_location": "广州"}}}
    assert get_weather.resolve_location(conn) == "广州"
    # 上游的共用 Key（api_key）不算自己的和风 Key
    assert get_weather.weather_settings({"plugins": {"get_weather": {"api_key": "x", "api_host": "y"}}})["qweather_key"] == ""


def test_device_home_store(tmp_path, monkeypatch):
    from core.utils import device_homes

    monkeypatch.setattr(device_homes, "_path", lambda: str(tmp_path / "device_homes.json"))
    assert device_homes.get("dev-1") is None
    device_homes.put("dev-1", {"name": "浙江省杭州市西湖区", "latitude": 30.26, "longitude": 120.13})
    assert device_homes.get("dev-1")["name"] == "浙江省杭州市西湖区" and device_homes.get("dev-2") is None
    device_homes.put("dev-1", None)
    assert device_homes.get("dev-1") is None
    with pytest.raises(ValueError):
        device_homes.put("", {"name": "x", "latitude": 0, "longitude": 0})


async def test_weather_uses_the_device_home(tmp_path, monkeypatch):
    """没说地点：用这台设备在设置页设的住宅地址（坐标直接查，不再查地名）；后台的设备所在地只是默认值。"""
    from core.utils import device_homes
    from core.utils.cache.manager import cache_manager, CacheType

    monkeypatch.setattr(device_homes, "_path", lambda: str(tmp_path / "device_homes.json"))
    home = {"name": "浙江省杭州市西湖区", "latitude": 30.26, "longitude": 120.13}
    device_homes.put("dev-1", home)
    seen = []

    async def report_at(place=None, location=None):
        seen.append((place and place["name"], location))
        return f"您查询的位置是：{(place or {}).get('name') or location}"
    monkeypatch.setattr(open_meteo, "report_at", report_at)
    conn = SimpleNamespace(config={"plugins": {"get_weather": {"home_location": "青浦区"}}}, client_ip=None, device_id="dev-1")
    assert get_weather.resolve_location(conn) == "浙江省杭州市西湖区"
    import datetime
    hour = f"{datetime.datetime.now():%Y%m%d%H}"
    for name in ("浙江省杭州市西湖区", "东京", "青浦区"):
        cache_manager.delete(CacheType.WEATHER, f"full_weather_{name}_zh_CN_{hour}")
    assert (await get_weather.get_weather(conn)).result == "您查询的位置是：浙江省杭州市西湖区"
    assert seen[-1] == ("浙江省杭州市西湖区", "浙江省杭州市西湖区")
    await get_weather.get_weather(conn, location="东京")
    assert seen[-1] == (None, "东京")
    conn.device_id = "dev-2"   # 没设过住宅地址的设备
    assert get_weather.resolve_location(conn) == "青浦区"


async def test_lookup(monkeypatch):
    open_meteo._geo_cache.clear()
    from httpx._client import AsyncClient
    monkeypatch.setattr(open_meteo, "_client", lambda: AsyncClient(transport=_transport({"青浦区": [QINGPU]})))
    assert (await open_meteo.lookup("青浦"))["name"] == "上海市青浦区"
    with pytest.raises(open_meteo.WeatherError, match="没找到"):
        await open_meteo.lookup("不存在的地方")
