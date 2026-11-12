"""[xiaozhi-hub] 四个接入（core/integrations，设计画布第 38、39 块）：用本地假服务代替智能中枢、Prometheus、
rack-screen、repo-manager，Mac 上的 DeskHub 用测试客户端模拟。"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler as ah
from core.admin import config_store, plugin_settings, presence
from core.agent import chat_history
from core.integrations import common, deskhub, rack, repos, smarthub
from core.utils import device_settings as ds
from plugins_func.functions import integrations as fn
from plugins_func.register import Action

TOKEN = "hub-token-123456"
DESK = "desk-pass-123456"


def fake_upstreams():
    """一个假服务同时扮演智能中枢、Prometheus、rack-screen、repo-manager。"""
    lights = {"verge": {"id": "verge", "name": "补光灯 Verge", "type": "AmaranLight",
                        "actions": {a: "" for a in ("on", "off", "toggle", "brightness", "cct", "refresh")},
                        "state": {"on": True, "brightness": 60, "mode": "cct", "kelvin": 4300},
                        "meta": {"kind": "light", "group": False, "cct_min": 2700, "cct_max": 6500},
                        "link": {"route": {"radio": "node:office"}}},
              "desk": {"id": "desk", "name": "台灯", "type": "AmaranLight", "actions": {"on": "", "off": "", "brightness": ""},
                       "state": {"on": False}, "meta": {"kind": "light"}},
              "all": {"id": "all", "name": "全部灯", "type": "AmaranLight", "actions": {"on": "", "off": ""},
                      "state": {"on": True}, "meta": {"kind": "light", "group": True}},
              "psu": {"id": "psu", "name": "工作台电源", "type": "RidenPsu", "actions": {"on": "", "off": "", "set": ""},
                      "state": {"online": True, "output": True, "v_out": 12.0, "i_out": 1.2, "p_out": 14.4},
                      "meta": {"kind": "psu"}}}
    calls = []

    def bearer(request):
        return request.headers.get("Authorization") == f"Bearer {TOKEN}"

    async def devices(request):
        if not bearer(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(list(lights.values()))

    async def action(request):
        if not bearer(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        d, a = lights[request.match_info["id"]], request.match_info["action"]
        body = await request.json()
        calls.append((d["id"], a, body))
        st = dict(d["state"])
        if a in ("on", "off"):
            st["on"] = a == "on"
        if a == "brightness":
            st["brightness"] = body.get("value", (st.get("brightness") or 50) + body.get("delta", 0))
        if a == "cct":
            st["kelvin"], st["mode"] = body["kelvin"], "cct"
        d["state"] = st
        return web.json_response({"ok": True, "state": st})

    def vec(rows):
        return web.json_response({"status": "success", "data": {"result": [
            {"metric": m, "value": [0, str(v)]} for m, v in rows]}})

    async def prom(request):
        q = request.query["query"]
        if q.startswith("up"):
            return vec([({"instance": "10.0.0.1:9100", "job": "node"}, 1), ({"instance": "10.0.0.2:9100", "job": "node"}, 0)])
        if q == "node_uname_info":
            return vec([({"instance": "10.0.0.1:9100", "nodename": "PVE"}, 1)])
        if "cpu" in q:
            return vec([({"instance": "10.0.0.1:9100"}, 23.4)])
        if "temp" in q:
            return vec([({"instance": "10.0.0.1:9100"}, 75)])
        if q == "probe_success":
            return vec([({"instance": "1.1.1.1"}, 1), ({"instance": "8.8.8.8"}, 0)])
        if q.startswith("probe_duration"):
            return vec([({"instance": "1.1.1.1"}, 9.2)])
        return vec([])

    async def monitors(request):
        if request.headers.get("X-Token") != "rack-tok":
            return web.json_response({"error": "log in"}, status=401)
        return web.json_response({"monitors": [{"name": "家庭影院", "state": "down", "latency_ms": None, "msg": "timeout"},
                                               {"name": "博客", "state": "up", "latency_ms": 80}]})

    async def overview(request):
        return web.json_response({"repos": {"total": 10, "views": {"synced": 7, "failed": 2, "dirty": 1}},
                                  "watches": {"total": 2, "ok": 1, "failed": 1}, "hosts": {"total": 1, "online": 1},
                                  "guard7": {"before": 1}, "attention_total": 2,
                                  "attention": [{"name": "AI/autogen", "kind": "local_changes", "state": "failed", "host_name": "飞牛"}],
                                  "events": [{"at": "2026-11-12T04:00:00", "msg": "同步完成"}]})

    app = web.Application()
    app.router.add_get("/api/devices", devices)
    app.router.add_post("/api/devices/{id}/{action}", action)
    app.router.add_get("/api/v1/query", prom)
    app.router.add_get("/api/monitors", monitors)
    app.router.add_get("/api/overview", overview)
    return app, calls


@pytest.fixture
async def upstream():
    app, calls = fake_upstreams()
    async with TestServer(app) as server:
        yield str(server.make_url("")).rstrip("/"), calls
    for c in (smarthub._cache, rack._cache, repos._cache):
        c.drop()


def yaml_for(base: str) -> str:
    return f"""\
selected_module:
  Intent: function_call
Intent:
  function_call:
    type: function_call
    functions: [get_weather, smart_hub, rack_status, repo_status, desk_hub]
plugins:
  smart_hub: {{url: "{base}", token: {TOKEN}}}
  rack_status: {{prometheus: "{base}", url: "{base}", token: rack-tok}}
  repo_status: {{url: "{base}"}}
  desk_hub: {{token: {DESK}}}
"""


@pytest.fixture
async def client(upstream, tmp_path, monkeypatch):
    base, _ = upstream
    p = tmp_path / ".config.yaml"
    p.write_text(yaml_for(base), encoding="utf-8")
    monkeypatch.setattr(ds, "_path", lambda: str(tmp_path / "device_settings.json"))
    monkeypatch.setattr(deskhub, "hub", deskhub.DeskHub())
    presence.reset()
    chat_history.set_db(chat_history.ChatDB(tmp_path / "chat.db"))
    app = web.Application()
    handler = ah.AdminHandler(config_store.load_merged(custom_path=p), ws_server=None, config_path=p)
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        c.cfg_path = p
        yield c
    chat_history.set_db(None)


async def test_smart_hub_rack_repos(client, upstream):
    _, calls = upstream
    d = await (await client.get("/kiosk/integrations?device=study")).json()
    assert d["available"] == {"smart_hub": True, "rack_status": True, "repo_status": True, "desk_hub": True}
    names = {x["name"]: x for x in d["smart_hub"]["devices"]}
    assert names["补光灯 Verge"]["summary"] == "60% · 4300K" and names["补光灯 Verge"]["via"] == "office"
    assert names["台灯"]["summary"] == "关" and names["工作台电源"]["summary"] == "12.00 V · 1.20 A · 14.4 W"
    r = d["rack_status"]
    assert r["up"] == 1 and r["total"] == 2 and r["hosts"][0]["name"] == "PVE" and r["hosts"][0]["hot"]
    assert r["hosts"][1]["name"] == "10.0.0.2" and r["hosts"][1]["down_minutes"] == 0
    assert "Ping 8.8.8.8 不通" in r["problems"] and "家庭影院检测失败" in r["problems"]
    assert set(rack.alert_keys(r)) == {"host:10.0.0.2:9100", "probe:8.8.8.8", "mon:家庭影院"}
    assert d["repo_status"]["failed"] == 2 and d["repo_status"]["suspect7"] == 1
    assert d["repo_status"]["attention"][0]["why"] == "本地修改冲突"
    # 屏幕上点灯：色温按 100 取整、限在灯的范围里
    r = await (await client.post("/kiosk/smarthub", json={"device": "study", "id": "verge", "action": "cct", "value": 9000})).json()
    assert r["ok"] and r["device"]["kelvin"] == 6500 and calls[-1] == ("verge", "cct", {"kelvin": 6500})
    r = await client.post("/kiosk/smarthub", json={"device": "study", "id": "desk", "action": "cct", "value": 3000})
    assert r.status == 502 and "不支持" in (await r.json())["error"]
    # 这台设备单独关掉插件：工作台就没有它们
    ds.put("living", {"plugins": ["get_weather"]}, config_store.load_merged(custom_path=client.cfg_path))
    d = await (await client.get("/kiosk/integrations?device=living")).json()
    assert not any(d["available"].values()) and "smart_hub" not in d


async def test_kiosk_integrations_only_local(client, monkeypatch):
    monkeypatch.setattr(web.BaseRequest, "remote", property(lambda self: "192.168.1.20"))
    assert (await client.get("/kiosk/integrations?device=x")).status == 403
    assert (await client.post("/kiosk/smarthub", json={})).status == 403


async def test_deskhub_round_trip(client):
    hdr = {"Authorization": f"Bearer {DESK}"}
    assert (await client.post("/deskhub/push", json={"messages": []})).status == 401
    msgs = ['{"op":"sources_begin","count":2}', '{"op":"source","i":0,"id":"mac","name":"Mac mini","port":"dp1"}',
            '{"op":"source","i":1,"id":"win","name":"Windows","port":"hdmi1"}', '{"op":"sources_end"}',
            '{"op":"current","id":"mac"}',
            json.dumps({"op": "usage", "ok": True, "p": "Claude", "plan": "Max", "w": [{"m": 300, "r": 62, "s": 7200}, {"m": 10080, "r": 18, "s": 259200}],
                        "c": 128, "e": 0, "t": 1200000}),
            '{"op":"todos_begin","n":2,"total":5}',
            '{"op":"todo","i":0,"id":"aaaaaaaaaaaa","t":"交物业费","l":"家","k":"o","y":2026,"m":11,"d":11,"hm":-1}',
            '{"op":"todo","i":1,"id":"bbbbbbbbbbbb","t":"写周报","l":"工作","k":"t","y":2026,"m":11,"d":12,"hm":1020}',
            '{"op":"todos_end"}']
    r = await client.post("/deskhub/push", json={"name": "Mac mini", "messages": msgs}, headers=hdr)
    assert r.status == 200
    v = (await (await client.get("/kiosk/integrations?device=study")).json())["desk_hub"]
    assert v["online"] and v["current"] == "mac" and [s["port"] for s in v["sources"]] == ["DP 1", "HDMI 1"]
    assert v["usage"]["windows"][0] == {"name": "5 小时", "left": 62, "reset_s": 7200}
    assert [t["when"] for t in v["todos"]] == ["11月11日（过期）", "今天 17:00"] and v["todos_total"] == 5
    assert "5 小时额度还剩 62%" in deskhub.spoken(v, "usage") and "交物业费" in deskhub.spoken(v, "todos")

    async def mac():
        """Mac 的长轮询拿到指令，照触摸屏的格式回结果。"""
        got = await (await client.get("/deskhub/wait", headers=hdr)).json()
        m = json.loads(got["commands"][0])
        assert m == {"op": "switch", "id": "win", "req": m["req"]}
        await client.post("/deskhub/result", headers=hdr,
                          json={"message": json.dumps({"op": "result", "req": m["req"], "outcome": "confirmed", "detail": ""})})

    task = asyncio.create_task(mac())
    r = await (await client.post("/kiosk/deskhub", json={"device": "study", "op": "switch", "id": "win"})).json()
    await task
    assert r["ok"] and r["text"] == "已切到Windows，显示器确认了"
    r = await (await client.post("/kiosk/deskhub", json={"device": "study", "op": "switch", "id": "nope"})).json()
    assert not r["ok"] and "没有这个输入源" in r["text"]


async def test_deskhub_wrong_token_locks(client):
    for _ in range(deskhub.FAILS):
        assert (await client.get("/deskhub/wait", headers={"Authorization": "Bearer wrong-wrong-wrong"})).status == 401
    r = await client.get("/deskhub/wait", headers={"Authorization": f"Bearer {DESK}"})
    assert r.status == 429


async def test_voice_functions(upstream, tmp_path, monkeypatch):
    base, calls = upstream
    monkeypatch.setattr(ds, "_path", lambda: str(tmp_path / "device_settings.json"))
    monkeypatch.setattr(deskhub, "hub", deskhub.DeskHub())
    p = tmp_path / ".config.yaml"
    p.write_text(yaml_for(base), encoding="utf-8")
    conn = SimpleNamespace(config=config_store.load_merged(custom_path=p), device_id="study")
    r = await fn.smart_hub(conn, "brightness", "补光灯", 35)
    assert r.action == Action.RESPONSE and r.response.startswith("已调好补光灯 Verge") and calls[-1] == ("verge", "brightness", {"value": 35})
    r = await fn.smart_hub(conn, "off", "所有灯")
    assert r.response == "已关掉全部灯。"
    r = await fn.smart_hub(conn, "on", "灯泡")
    assert r.action == Action.REQLLM and "可选" in r.result
    r = await fn.smart_hub(conn, "dimmer", "补光")
    assert calls[-1] == ("verge", "brightness", {"delta": -20})
    r = await fn.rack_status(conn, "pve")
    assert "PVE在线" in r.result and "75℃" in r.result
    r = await fn.repo_status(conn)
    assert "失败 2 个" in r.result and "疑似删库 1 次" in r.result
    r = await fn.desk_hub(conn, "switch", "windows")
    assert r.response.startswith("Mac 上的 DeskHub 没连上网关")
    conn.config["plugins"].pop("smart_hub")
    r = await fn.smart_hub(conn, "status")
    assert "还没接入" in r.result


async def test_plugin_settings_tests(upstream):
    base, _ = upstream
    merged = {"plugins": {}}
    r = await plugin_settings.run_test("smart_hub", {"url": base, "token": TOKEN}, merged)
    assert r["message"] == "连上了：4 个设备（2 盏灯、1 台电源）"
    with pytest.raises(ValueError, match="401"):
        await plugin_settings.run_test("smart_hub", {"url": base, "token": "bad-token"}, merged)
    r = await plugin_settings.run_test("rack_status", {"prometheus": base, "url": base, "token": "rack-tok"}, merged)
    assert "2 台主机（1 台在线）" in r["message"] and "rack-screen 2 个检测项" in r["message"]
    r = await plugin_settings.run_test("repo_status", {"url": base}, merged)
    assert r["message"] == "连上了：10 个仓库，失败 2 个"
    with pytest.raises(ValueError, match="至少要 12"):
        plugin_settings.test_values("desk_hub", {"token": "short"}, merged)
    with pytest.raises(ValueError, match="http"):
        plugin_settings.test_values("rack_status", {"prometheus": "localhost:9090"}, merged)
