"""外部服务（设计画布第 56 块）：预设、开关、顺序、自己加的、线路；换线路；天气按顺序换下一个。不联网。"""
import socket
import struct

import httpx
import pytest

from core.agent import services
from core.utils import net_lines, open_meteo


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "_path", lambda: str(tmp_path / "services.json"))
    services.STATUS.clear()
    net_lines._pins.clear()
    net_lines._skipped.clear()
    yield


def test_presets_and_edits():
    ws = services.weather_all()
    assert [w["id"] for w in ws] == ["open-meteo", "qweather"] and all(w["on"] for w in ws)
    assert [r["id"] for r in services.radio_list()][:2] == ["zgzs", "jjzs"]
    services.apply({"op": "add", "group": "weather", "name": "NAS", "url": "http://10.0.0.2:8080/v1/forecast"})
    custom = services.weather_all()[-1]
    assert custom["kind"] == "custom" and custom["custom"] and custom["name"] == "NAS"
    services.apply({"op": "order", "ids": [custom["id"], "open-meteo", "qweather"]})
    assert [w["id"] for w in services.weather_chain(kinds=("open-meteo", "custom"))] == [custom["id"], "open-meteo"]
    services.apply({"op": "toggle", "group": "weather", "id": "open-meteo", "on": False})
    assert [w["id"] for w in services.weather_chain()] == [custom["id"], "qweather"]
    services.apply({"op": "delete", "group": "weather", "id": custom["id"]})
    assert [w["id"] for w in services.weather_all()] == ["open-meteo", "qweather"]
    with pytest.raises(services.ServicesError, match="预设的不能删"):
        services.apply({"op": "delete", "group": "weather", "id": "open-meteo"})
    with pytest.raises(services.ServicesError, match="http"):
        services.apply({"op": "add", "group": "radio", "name": "x", "url": "ftp://a"})
    with pytest.raises(services.ServicesError, match="台名"):
        services.apply({"op": "add", "group": "radio", "name": " ", "url": "http://a/b.m3u8"})
    with pytest.raises(services.ServicesError, match="已经有"):
        services.apply({"op": "add", "group": "radio", "name": "中国之声", "url": "http://a/b.m3u8"})
    with pytest.raises(services.ServicesError, match="对不上"):
        services.apply({"op": "order", "ids": ["open-meteo"]})


def test_radio_and_geoip_and_line():
    services.apply({"op": "add", "group": "radio", "name": "上海交通广播", "url": "http://x/shjt.m3u8"})
    services.apply({"op": "toggle", "group": "radio", "id": "bjxw", "on": False})
    names = [r["name"] for r in services.radio_list()]
    assert "上海交通广播" in names and "北京新闻广播" not in names
    assert all(set(r) == {"id", "name", "url"} for r in services.radio_list())
    services.apply({"op": "toggle", "group": "geoip", "id": "geoip", "on": False})
    assert services.geoip_on() is False
    assert services.line("api.open-meteo.com") == ""
    services.apply({"op": "line", "ip": "188.40.99.226"})
    assert services.line("api.open-meteo.com") == "188.40.99.226" and services.line("example.com") == ""
    with pytest.raises(services.ServicesError, match="IPv4"):
        services.apply({"op": "line", "ip": "not-an-ip"})
    services.apply({"op": "line", "ip": ""})
    assert services.line("api.open-meteo.com") == ""


def test_parse_dns_answer():
    q = net_lines._query("api.open-meteo.com", 0x1234)
    ans = (q[:2] + struct.pack(">HHHHH", 0x8180, 1, 2, 0, 0) + q[12:]
           + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("188.40.99.226")
           + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("51.81.167.44"))
    assert net_lines.parse_a(ans, 0x1234) == ["188.40.99.226", "51.81.167.44"]
    assert net_lines.parse_a(ans, 0x9999) == []


def _client(handler):
    from httpx._client import AsyncClient   # conftest 换掉了 httpx.AsyncClient
    return AsyncClient(transport=httpx.MockTransport(handler))


async def test_switches_line_when_the_server_is_unreachable(monkeypatch):
    seen = []

    def handle(request):
        seen.append((request.url.host, request.headers.get("host"), request.extensions.get("sni_hostname")))
        if request.url.host == "api.open-meteo.com":
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": 1})

    async def find(host):
        return ("188.40.99.226", "DNS 1.1.1.1")
    monkeypatch.setattr(net_lines, "find", find)
    async with _client(handle) as c:
        r = await net_lines.get(c, "https://api.open-meteo.com/v1/forecast", params={"a": 1})
        assert r.json() == {"ok": 1}
        assert seen[-1] == ("188.40.99.226", "api.open-meteo.com", "api.open-meteo.com")
        info = net_lines.info("api.open-meteo.com")
        assert info["ip"] == "188.40.99.226" and info["source"] == "DNS 1.1.1.1"
        # 记住了：下一次直接走这条
        seen.clear()
        await net_lines.get(c, "https://api.open-meteo.com/v1/forecast")
        assert [s[0] for s in seen] == ["188.40.99.226"]


async def test_no_other_line_raises_the_first_error(monkeypatch):
    def handle(request):
        raise httpx.ConnectError("refused", request=request)

    async def find(host):
        return None
    monkeypatch.setattr(net_lines, "find", find)
    async with _client(handle) as c:
        with pytest.raises(httpx.ConnectError):
            await net_lines.get(c, "https://api.open-meteo.com/v1/forecast")


async def test_weather_falls_over_to_the_next_source(monkeypatch):
    services.apply({"op": "add", "group": "weather", "name": "NAS", "url": "http://10.0.0.2:8080/v1/forecast"})
    forecast = {"current": {"temperature_2m": 12}, "daily": {}}

    def handle(request):
        if request.url.host == "api.open-meteo.com":
            raise httpx.ConnectTimeout("timed out", request=request)
        if request.url.host == "10.0.0.2":
            return httpx.Response(200, json=forecast)
        return httpx.Response(200, json={"current": {"pm2_5": 8}})

    async def find(host):
        return None
    monkeypatch.setattr(net_lines, "find", find)
    monkeypatch.setattr(open_meteo, "_client", lambda: _client(handle))
    place = {"name": "青浦", "latitude": 31.1, "longitude": 121.1}
    _, fc, air = await open_meteo.fetch(place)
    assert fc == forecast and air == {"current": {"pm2_5": 8}}
    assert services.status("open-meteo")["ok"] is False and services.status("open-meteo")["err"] == "连不上"
    custom = services.weather_all()[-1]["id"]
    assert services.status(custom)["ok"] is True
    # 都不通：说是哪几个
    forecast.clear()
    with pytest.raises(open_meteo.WeatherError, match="Open-Meteo连不上；NAS返回的不是天气数据"):
        await open_meteo.fetch(place)
    services.apply({"op": "toggle", "group": "weather", "id": "open-meteo", "on": False})
    services.apply({"op": "toggle", "group": "weather", "id": custom, "on": False})
    with pytest.raises(open_meteo.WeatherError, match="都关掉了"):
        await open_meteo.fetch(place)
