"""[xiaozhi-hub] Home Assistant（core/integrations/hass.py，设计画布第 84 块）：用本地假的 HA 代替真的。"""

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from core.integrations import common, hass
from plugins_func.functions import integrations as fn
from plugins_func.register import Action

TOKEN = "ha-long-lived-token"


def fake_ha():
    states = {
        "light.living": {"entity_id": "light.living", "state": "on",
                         "attributes": {"friendly_name": "吸顶灯", "brightness": 178, "supported_color_modes": ["color_temp"],
                                        "min_color_temp_kelvin": 2700, "max_color_temp_kelvin": 6500}},
        "climate.ac": {"entity_id": "climate.ac", "state": "off",
                       "attributes": {"friendly_name": "空调", "current_temperature": 24.5, "temperature": 26,
                                      "hvac_modes": ["off", "cool", "heat", "auto"], "min_temp": 16, "max_temp": 30}},
        "cover.curtain": {"entity_id": "cover.curtain", "state": "open", "attributes": {"friendly_name": "窗帘", "current_position": 60}},
        "switch.bed": {"entity_id": "switch.bed", "state": "off", "attributes": {"friendly_name": "床头插座"}},
        "lock.door": {"entity_id": "lock.door", "state": "locked", "attributes": {"friendly_name": "门锁"}},
        "sensor.t": {"entity_id": "sensor.t", "state": "23.8", "attributes": {"friendly_name": "温度", "device_class": "temperature", "unit_of_measurement": "°C"}},
        "sensor.battery": {"entity_id": "sensor.battery", "state": "80", "attributes": {"friendly_name": "手机电量", "device_class": "battery"}},
        "automation.x": {"entity_id": "automation.x", "state": "on", "attributes": {"friendly_name": "自动化"}},
        "scene.movie": {"entity_id": "scene.movie", "state": "scening", "attributes": {"friendly_name": "观影"}},
        "light.lost": {"entity_id": "light.lost", "state": "unavailable", "attributes": {"friendly_name": "坏灯", "supported_color_modes": ["onoff"]}},
    }
    areas = {"light.living": "客厅", "climate.ac": "客厅", "cover.curtain": "客厅", "sensor.t": "客厅", "switch.bed": "卧室", "lock.door": "玄关"}
    calls = []

    def ok(request):
        return request.headers.get("Authorization") == f"Bearer {TOKEN}"

    async def all_states(request):
        if not ok(request):
            return web.json_response({"message": "401"}, status=401)
        return web.json_response(list(states.values()))

    async def one(request):
        return web.json_response(states[request.match_info["id"]])

    async def template(request):
        body = await request.json()
        assert "area_name" in body["template"]
        return web.Response(text="".join(f"{e}\t{areas.get(e, '')}\n" for e in states))

    async def service(request):
        d, svc = request.match_info["domain"], request.match_info["service"]
        body = await request.json()
        calls.append((d, svc, body))
        st = states.get(body["entity_id"])
        if st and svc == "turn_on":
            st["state"] = "on" if d != "climate" else "cool"
            if "brightness_pct" in body:
                st["attributes"]["brightness"] = round(body["brightness_pct"] * 255 / 100)
        if st and svc == "turn_off":
            st["state"] = "off"
        if st and svc == "set_temperature":
            st["attributes"]["temperature"] = body["temperature"]
        return web.json_response([])

    app = web.Application()
    app.router.add_get("/api/states", all_states)
    app.router.add_get("/api/states/{id}", one)
    app.router.add_post("/api/template", template)
    app.router.add_post("/api/services/{domain}/{service}", service)
    return app, calls


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_overview_control_and_voice():
    async def go():
        app, calls = fake_ha()
        server = TestServer(app)
        await server.start_server()
        s = {"base_url": str(server.make_url("")).rstrip("/"), "api_key": TOKEN}
        try:
            v = await hass.overview(s, fresh=True)
            names = {a["name"]: [c["name"] for c in a["cards"]] for a in v["areas"]}
            assert names["客厅"] == ["吸顶灯", "空调", "窗帘", "温度"] and names["卧室"] == ["床头插座"]
            assert "手机电量" not in str(names) and "自动化" not in str(names)          # 不列的
            assert [a["name"] for a in v["areas"]][-1] == hass.OTHER and v["scenes"] == [{"id": "scene.movie", "name": "观影"}]
            cards = {c["id"]: c for a in v["areas"] for c in a["cards"]}
            assert cards["light.living"]["summary"] == "开 · 亮度 70%" and cards["light.living"]["controls"]["cct"]["max"] == 6500
            assert cards["climate.ac"]["summary"] == "关 · 室内 24.5℃" and cards["cover.curtain"]["summary"] == "开了 60%"
            assert cards["lock.door"]["readonly"] and cards["light.lost"]["summary"] == "离线"
            done = await hass.act(s, v, "light.living", "brightness", 30)
            assert calls[-1] == ("light", "turn_on", {"entity_id": "light.living", "brightness_pct": 30}) and done["brightness"] == 30
            await hass.act(s, v, "climate.ac", "target", 40)
            assert calls[-1][2]["temperature"] == 30                                     # 限在空调的范围里
            await hass.act(s, v, "switch.bed", "toggle")
            assert calls[-1][:2] == ("switch", "toggle")
            with pytest.raises(common.IntegrationError, match="只能看"):
                await hass.act(s, v, "lock.door", "toggle")                              # 门锁不开
            with pytest.raises(common.IntegrationError, match="读数"):
                await hass.act(s, v, "sensor.t", "on")
            assert hass.find(v, "客厅的吸顶灯")[0]["id"] == "light.living" and hass.find(v, "空调")[0]["id"] == "climate.ac"
            # 语音
            conn = SimpleNamespace(config={"plugins": {"home_assistant": s}})
            monkey = fn.common.ready
            fn.common.ready = lambda config, name: True
            try:
                r = await fn.home_assistant(conn, "on", "客厅的空调")
                assert r.action == Action.RESPONSE and r.response.startswith("已打开客厅的空调")
                r = await fn.home_assistant(conn, "scene", "观影")
                assert r.response == "已启动观影。" and calls[-1][:2] == ("scene", "turn_on")
                r = await fn.home_assistant(conn, "status", "客厅")
                assert r.action == Action.REQLLM and "吸顶灯" in r.result
                r = await fn.home_assistant(conn, "on", "门锁")
                assert r.action == Action.REQLLM and "只能看" in r.result
            finally:
                fn.common.ready = monkey
            with pytest.raises(common.IntegrationError, match="401"):
                await hass.overview({**s, "api_key": "wrong"}, fresh=True)
            with pytest.raises(common.IntegrationError, match="还没填"):
                await hass.overview({**s, "api_key": "你的home assistant api访问令牌"}, fresh=True)
        finally:
            await server.close()
    run(go())
