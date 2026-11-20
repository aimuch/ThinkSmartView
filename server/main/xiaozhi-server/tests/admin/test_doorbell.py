"""[xiaozhi-hub] 门口来人（core/integrations/doorbell.py，设计画布第 86 块）：用本地假的 Home Assistant。"""

import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from core.admin import plugin_settings
from core.integrations import doorbell

TOKEN = "ha-token"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def fake_ha(events):
    """events：连上、订阅以后依次发的 state_changed（entity, old, new, attrs）。"""
    seen = {"auth": None, "subscribed": False}

    async def ws(request):
        sock = web.WebSocketResponse()
        await sock.prepare(request)
        await sock.send_json({"type": "auth_required"})
        msg = await sock.receive_json()
        seen["auth"] = msg.get("access_token")
        if msg.get("access_token") != TOKEN:
            await sock.send_json({"type": "auth_invalid"})
            await sock.close()
            return sock
        await sock.send_json({"type": "auth_ok"})
        sub = await sock.receive_json()
        seen["subscribed"] = sub.get("type") == "subscribe_events"
        await sock.send_json({"id": sub["id"], "type": "result", "success": True})
        for entity, old, new, attrs in events:
            await sock.send_json({"id": sub["id"], "type": "event", "event": {"event_type": "state_changed", "data": {
                "entity_id": entity,
                "old_state": None if old is None else {"state": old, "attributes": attrs},
                "new_state": {"state": new, "attributes": attrs}}}})
        await asyncio.sleep(5)
        return sock

    async def camera(request):
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.Response(status=401)
        if request.match_info["id"] != "camera.front":
            return web.Response(status=404)
        return web.Response(body=JPEG, content_type="image/jpeg")

    async def states(request):
        return web.json_response([
            {"entity_id": "event.doorbell", "state": "unknown", "attributes": {"device_class": "doorbell"}},
            {"entity_id": "binary_sensor.porch", "state": "off", "attributes": {"device_class": "motion"}},
            {"entity_id": "binary_sensor.window", "state": "off", "attributes": {"device_class": "window"}},
            {"entity_id": "camera.front", "state": "idle", "attributes": {}},
        ])

    app = web.Application()
    app.router.add_get("/api/websocket", ws)
    app.router.add_get("/api/camera_proxy/{id}", camera)
    app.router.add_get("/api/states", states)
    return app, seen


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_fired_rules():
    assert doorbell.fired("event.doorbell", {"state": "2026-11-20T10:00:00"}, {"state": "2026-11-20T10:05:00"})
    assert not doorbell.fired("event.doorbell", {}, {"state": "2026-11-20T10:05:00"})          # 刚出现的不算
    assert doorbell.fired("event.doorbell", {"state": "unknown"}, {"state": "2026-11-20T10:05:00"})
    assert doorbell.fired("binary_sensor.porch", {"state": "off"}, {"state": "on"})
    assert not doorbell.fired("binary_sensor.porch", {"state": "on"}, {"state": "on"})
    assert not doorbell.fired("binary_sensor.porch", {"state": "on"}, {"state": "off"})
    assert not doorbell.fired("binary_sensor.porch", {"state": "off"}, {"state": "unavailable"})
    assert doorbell.why("binary_sensor.porch", {"attributes": {"device_class": "motion"}}) == "门口有人走动"
    assert doorbell.why("event.doorbell", {}) == "门铃按了"
    assert doorbell.why("binary_sensor.door", {"attributes": {"device_class": "door"}}) == "门开了"


def test_watch_pushes_to_waiting_devices_and_camera():
    async def go():
        app, seen = fake_ha([
            ("binary_sensor.window", "off", "on", {"device_class": "window"}),           # 没选的不弹
            ("binary_sensor.porch", "off", "on", {"friendly_name": "门口人体", "device_class": "motion"}),
            ("binary_sensor.porch", "on", "off", {"friendly_name": "门口人体", "device_class": "motion"}),
            ("binary_sensor.porch", "off", "on", {"friendly_name": "门口人体", "device_class": "motion"}),   # 30 秒内又来
        ])
        server = TestServer(app)
        await server.start_server()
        cfg = {"plugins": {"home_assistant": {"base_url": str(server.make_url("")).rstrip("/"), "api_key": TOKEN,
                                              "door_triggers": "binary_sensor.porch, event.doorbell",
                                              "door_camera": "camera.front"}}}
        enabled = {"dev-a": True, "dev-b": False}
        w = doorbell.DoorWatch(lambda: cfg, enabled_fn=lambda c, d: enabled.get(d, False))
        orig_ready = doorbell.common.ready
        doorbell.common.ready = lambda c, n: True
        try:
            wa = asyncio.ensure_future(w.wait("dev-a", timeout=5))
            wb = asyncio.ensure_future(w.wait("dev-b", timeout=0.5))
            task = asyncio.ensure_future(w.run())
            got = await wa
            assert seen["auth"] == TOKEN and seen["subscribed"] and w.state["events"] >= 1   # 假的 HA 发完就断（conftest 把 sleep 变快了）
            assert got[0]["why"] == "门口有人走动" and got[0]["name"] == "门口人体" and got[0]["camera"]
            assert not got[0]["repeat"]
            more = await w.wait("dev-a", timeout=2)
            assert more and more[0]["repeat"] and more[0]["id"] == got[0]["id"]      # 30 秒内：同一条，只刷新
            assert await wb == []                                                     # 这台没开 Home Assistant
            assert await w.snapshot() == JPEG
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            doorbell.common.ready = orig_ready
            await server.close()
    run(go())


def test_wrong_token_and_missing_camera():
    async def go():
        app, _ = fake_ha([])
        server = TestServer(app)
        await server.start_server()
        url = str(server.make_url("")).rstrip("/")
        try:
            w = doorbell.DoorWatch(lambda: {})
            with pytest.raises(doorbell.common.IntegrationError, match="令牌不对"):
                await w._listen({"base_url": url, "api_key": "wrong", "triggers": ["event.doorbell"]})
            cfg = {"plugins": {"home_assistant": {"base_url": url, "api_key": TOKEN, "door_camera": "camera.back"}}}
            with pytest.raises(doorbell.common.IntegrationError, match="没有 camera.back"):
                await w.snapshot(cfg)
            with pytest.raises(doorbell.common.IntegrationError, match="还没选"):
                await w.snapshot({"plugins": {"home_assistant": {"base_url": url, "api_key": TOKEN}}})
            c = await doorbell.candidates(doorbell.door_settings({"plugins": {"home_assistant": {"base_url": url, "api_key": TOKEN}}}))
            assert c["triggers"] == ["binary_sensor.porch", "event.doorbell"] and c["cameras"] == ["camera.front"]
        finally:
            await server.close()
    run(go())


def test_settings_fields_and_test_message():
    merged = {"plugins": {"home_assistant": {"base_url": "http://ha:8123", "api_key": "x" * 20}}}
    with pytest.raises(ValueError, match="只能选"):
        plugin_settings._values("home_assistant", {"door_night": "maybe"}, merged)
    v = plugin_settings._values("home_assistant", {"door_night": "on", "door_triggers": "event.doorbell"}, merged)
    assert v["door_night"] == "on" and v["door_triggers"] == "event.doorbell"
    d = plugin_settings.describe(merged, "home_assistant")
    assert [f["key"] for f in d["fields"]][-3:] == ["door_triggers", "door_camera", "door_night"]

    async def go():
        app, _ = fake_ha([])
        server = TestServer(app)
        await server.start_server()
        url = str(server.make_url("")).rstrip("/")
        try:
            s = {"base_url": url, "api_key": TOKEN}
            assert "event.doorbell" in await plugin_settings._test_door(s)          # 没填：列出能用的
            with pytest.raises(ValueError, match="没有：event.nope"):
                await plugin_settings._test_door({**s, "door_triggers": "event.nope"})
            w = doorbell.DoorWatch(lambda: {"plugins": {"home_assistant": {**s, "door_triggers": "event.doorbell"}}},
                                   enabled_fn=lambda c, d: True)
            doorbell.WATCH = w
            w.seen["dev-a"] = time.time()
            msg = await plugin_settings._test_door({**s, "door_triggers": "event.doorbell", "door_camera": "camera.front"})
            assert "推了一次测试到 1 台" in msg
            ev = (await w.wait("dev-a", timeout=1))[0]
            assert ev["test"] and ev["why"].startswith("测试")
        finally:
            doorbell.WATCH = None
            await server.close()
    run(go())
