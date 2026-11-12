"""[xiaozhi-hub] 设备页（设计画布第 19 块）：每台设备单独设名字、默认模型、音色、人设、插件；连接建立时套到这个连接的配置上。"""

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler as ah
from core.admin import config_store, presence
from core.agent import chat_history, llm_router
from core.agent.session_store import SessionStore
from core.utils import device_settings as ds

CUSTOM_YAML = """\
selected_module:
  LLM: DeepSeekLLM
  Intent: function_call
  TTS: EdgeTTS
prompt: 全局人设
LLM:
  DeepSeekLLM:
    type: openai
    url: https://api.deepseek.com
    model_name: deepseek-flash
    api_key: sk-ds-1234567890
  KimiLLM:
    type: openai
    base_url: https://api.moonshot.cn/v1
    model_name: kimi-latest
    api_key: sk-kimi-1234567890
Intent:
  function_call:
    type: function_call
    functions: [get_weather, get_news_from_newsnow, play_music, my_upstream_plugin]
"""


@pytest.fixture
def cfg_path(tmp_path, monkeypatch) -> Path:
    p = tmp_path / ".config.yaml"
    p.write_text(CUSTOM_YAML, encoding="utf-8")
    monkeypatch.setattr(ds, "_path", lambda: str(tmp_path / "device_settings.json"))
    monkeypatch.setattr(llm_router, "_store", SessionStore(tmp_path / "agent_sessions.json", "last_model"))
    return p


def merged(path):
    return config_store.load_merged(custom_path=path)


def test_put_validate_and_apply(cfg_path):
    m = merged(cfg_path)
    ds.put("dev-1", {"name": "  儿童房 ", "prompt": ds.PERSONAS[0]["text"], "plugins": ["get_weather", "nope"],
                     "tts": {"entry": "EdgeTTS", "voice": "zh-CN-YunxiaNeural"}, "model": "KimiLLM"}, m)
    s = ds.get("dev-1")
    # 旧格式的整份清单存成和全局的差异
    assert s["name"] == "儿童房" and s["plugins"] == {"get_news_from_newsnow": False, "play_music": False} and s["model"] == "KimiLLM"
    for bad in ({"model": "ClaudeAPI"}, {"tts": {"entry": "EdgeTTS", "voice": "zh-CN-NoSuchNeural"}},
                {"tts": {"entry": "Qwen3TTS", "voice": "Cherry"}}, {"prompt": "   "}, {"name": "x" * 17}):
        with pytest.raises(ValueError):
            ds.put("dev-1", bad, m)
    # 连接自己的那份配置：人设、音色、插件（后台不管的插件留着）
    conn = SimpleNamespace(device_id="dev-1", config=copy.deepcopy(m))
    done = ds.apply(conn)
    assert conn.config["prompt"].startswith("你叫小麦，是一个耐心的小老师")
    assert conn.config["TTS"]["EdgeTTS"]["voice"] == "zh-CN-YunxiaNeural" and conn.config["selected_module"]["TTS"] == "EdgeTTS"
    assert conn.config["Intent"]["function_call"]["functions"] == ["get_weather", "my_upstream_plugin"]
    assert len(done) == 3 and m["prompt"] == "全局人设"   # 全局的那份没动
    # 改回跟着全局；全部跟着全局时这台设备的记录删掉
    ds.put("dev-1", {"prompt": None, "plugins": None, "tts": None, "model": None, "name": ""}, m)
    assert ds.get("dev-1") == {}
    assert ds.apply(SimpleNamespace(device_id="dev-1", config=copy.deepcopy(m))) == []


@pytest.fixture
async def client(cfg_path, tmp_path):
    presence.reset()
    chat_history.set_db(chat_history.ChatDB(tmp_path / "chat.db"))
    app = web.Application()
    handler = ah.AdminHandler(merged(cfg_path), ws_server=None, config_path=cfg_path)
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        yield c
    chat_history.set_db(None)


async def test_device_page_endpoints(client):
    await client.post("/kiosk/reports", json={"device": "aa:bb", "version": "0.1.627"})
    chat_history.db().insert("cc:dd", "DeepSeekLLM", "user", "你好")
    d = await (await client.get("/admin/api/device-settings")).json()
    ids = [x["device_id"] for x in d["devices"]]
    assert ids == ["aa:bb", "cc:dd"] and d["devices"][0]["state"] == "idle" and d["devices"][1]["data"]["messages"] == 1
    assert d["global"]["model"] == "DeepSeek" and "get_weather" in d["global"]["plugins"]
    assert {m["entry"] for m in d["options"]["models"]} >= {"DeepSeekLLM", "KimiLLM"} and d["options"]["tts"][0]["entry"] == "EdgeTTS"
    r = await (await client.post("/admin/api/device-settings", json={"device": "aa:bb", "name": "客厅小麦", "model": "KimiLLM"})).json()
    assert r["ok"] and llm_router.store().get("aa:bb")["model"] == "KimiLLM"   # 默认模型写进记住的模型
    assert (await (await client.get("/admin/api/devices")).json())["seen"][0]["name"] == "客厅小麦"
    bad = await client.post("/admin/api/device-settings", json={"device": "aa:bb", "model": "NoSuchLLM"})
    assert bad.status == 400
    await client.post("/admin/api/device-settings", json={"device": "aa:bb", "model": None})
    assert llm_router.store().get("aa:bb") is None                            # 跟着全局：忘掉记住的
    await client.post("/admin/api/device-settings/forget", json={"device": "aa:bb", "password": "pass-1234"})   # 在线要密码
    d = await (await client.get("/admin/api/device-settings")).json()
    assert [x["device_id"] for x in d["devices"]] == ["cc:dd"] and ds.get("aa:bb") == {}


async def test_delete_device(client, tmp_path, monkeypatch):
    """删除设备（设计画布第 37 块）：只有聊天记录的测试设备删得掉；在线的要管理员密码。"""
    from core.utils import device_homes, device_lists, device_prefs, local_voiceprint

    for mod, name in ((device_homes, "homes"), (device_prefs, "prefs"), (device_lists, "lists"), (local_voiceprint, "vp")):
        monkeypatch.setattr(mod, "_path", lambda n=name: str(tmp_path / f"{n}.json"))
    db = chat_history.db()
    for t in ("今天天气", "明天呢"):
        db.insert("weather-test-01", "DeepSeekLLM", "user", t)
    db.insert("keep-me", "DeepSeekLLM", "user", "你好")
    device_homes.put("weather-test-01", {"name": "青浦", "latitude": 31.1, "longitude": 121.1})
    device_prefs.put_kids("weather-test-01", {"enabled": True, "age": 5})
    device_lists.change("weather-test-01", "add", "购物", ["牛奶"])
    assert device_lists.count("weather-test-01") == 1
    d = await (await client.get("/admin/api/device-settings")).json()
    assert {x["device_id"] for x in d["devices"]} == {"weather-test-01", "keep-me"}
    # 离线：不用密码，默认连聊天记录和其它数据一起删，删完列表里没了
    r = await (await client.post("/admin/api/device-settings/forget", json={"device": "weather-test-01"})).json()
    assert r["ok"] and r["deleted"] == 2
    d = await (await client.get("/admin/api/device-settings")).json()
    assert [x["device_id"] for x in d["devices"]] == ["keep-me"]
    assert device_homes.get("weather-test-01") is None and device_prefs.kids("weather-test-01") is None
    assert device_lists.count("weather-test-01") == 0 and db.message_count("keep-me") == 1
    # 不勾聊天记录：设置删了，记录还在，所以还留在列表里
    await client.post("/admin/api/device-settings", json={"device": "keep-me", "name": "留着"})
    await client.post("/admin/api/device-settings/forget", json={"device": "keep-me", "chat": False})
    assert ds.get("keep-me") == {} and db.message_count("keep-me") == 1
    # 在线（有新鲜心跳）：没密码、密码错都不删，对了才删；错密码不是 401（页面会当成登录过期）
    await client.post("/kiosk/reports", json={"device": "aa:bb", "version": "0.1.657"})
    await client.post("/admin/api/device-settings", json={"device": "aa:bb", "name": "书房"})
    for body in ({"device": "aa:bb"}, {"device": "aa:bb", "password": "wrong-pass"}):
        res = await client.post("/admin/api/device-settings/forget", json=body)
        assert res.status == 403 and ds.name("aa:bb") == "书房"
    r = await (await client.post("/admin/api/device-settings/forget", json={"device": "aa:bb", "password": "pass-1234"})).json()
    assert r["ok"] and ds.get("aa:bb") == {}


def test_plugin_overrides(cfg_path):
    """插件按设备逐个开关（设计画布第 40 块）：只记改过的，全局后来勾的新插件照样跟着。"""
    m = merged(cfg_path)
    glob = ds.global_plugins(m)
    # 旧数据（整份清单）读的时候按当时的全局算差异
    assert ds.overrides(["get_weather", "rack_status"], glob) == {"get_news_from_newsnow": False, "play_music": False, "rack_status": True}
    ds.put("dev-2", {"plugin": {"name": "rack_status", "state": "on"}}, m)
    ds.put("dev-2", {"plugin": {"name": "play_music", "state": "off"}}, m)
    assert ds.get("dev-2")["plugins"] == {"play_music": False, "rack_status": True}
    assert ds.plugins_for(m, "dev-2") == ["get_weather", "get_news_from_newsnow", "my_upstream_plugin", "rack_status"]
    # 全局后来加了联网搜索：这台没改过它，所以跟着
    m2 = copy.deepcopy(m)
    m2["Intent"]["function_call"]["functions"].append("web_search")
    assert "web_search" in ds.plugins_for(m2, "dev-2") and "play_music" not in ds.plugins_for(m2, "dev-2")
    conn = SimpleNamespace(device_id="dev-2", config=copy.deepcopy(m2))
    assert ds.apply(conn) == ["插件 开 1 个 · 关 1 个"]
    assert conn.config["Intent"]["function_call"]["functions"][-1] == "rack_status"
    # 改回跟着全局；全都跟着时记录删掉
    ds.put("dev-2", {"plugin": {"name": "rack_status", "state": None}}, m)
    ds.put("dev-2", {"plugins": {"play_music": True}}, m)
    assert ds.get("dev-2")["plugins"] == {"play_music": True}
    ds.put("dev-2", {"plugins": {}}, m)
    assert ds.get("dev-2") == {}
    for bad in ({"plugin": {"name": "nope", "state": "on"}}, {"plugin": {"name": "play_music", "state": "yes"}},
                {"plugins": {"play_music": "on"}}, {"plugins": "play_music"}):
        with pytest.raises(ValueError):
            ds.put("dev-2", bad, m)


async def test_plugin_per_device_endpoints(client):
    """意图识别页列出按设备改过的插件；设备屏上的开关只改四个接入，拨到和全局一样就是跟着全局。"""
    await client.post("/kiosk/reports", json={"device": "aa:bb", "version": "0.1.659"})
    r = await (await client.post("/admin/api/device-settings", json={"device": "aa:bb", "plugin": {"name": "play_music", "state": "off"}})).json()
    assert r["ok"] and r["settings"]["plugins"] == {"play_music": False}
    d = await (await client.get("/admin/api/intent")).json()
    assert d["devices"] == [{"device_id": "aa:bb", "name": "", "state": "idle", "version": "0.1.659", "plugins": {"play_music": False}}]
    d = await (await client.get("/admin/api/device-settings")).json()
    assert d["devices"][0]["plugins"] == {"play_music": False}
    r = await (await client.post("/kiosk/plugin", json={"device": "aa:bb", "name": "rack_status", "on": True})).json()
    assert r["plugins"]["rack_status"] == {"on": True, "global": False, "own": True, "ready": True}
    assert ds.get("aa:bb")["plugins"] == {"play_music": False, "rack_status": True}
    i = await (await client.get("/kiosk/integrations?device=aa:bb")).json()
    assert i["plugins"]["rack_status"]["on"] and not i["plugins"]["repo_status"]["on"] and i["available"]["rack_status"]
    r = await (await client.post("/kiosk/plugin", json={"device": "aa:bb", "name": "rack_status", "on": False})).json()
    assert r["plugins"]["rack_status"] == {"on": False, "global": False, "own": None, "ready": True}
    assert ds.get("aa:bb")["plugins"] == {"play_music": False}
    for bad in ({"device": "aa:bb", "name": "play_music", "on": True}, {"device": "aa:bb", "name": "rack_status", "on": "1"}, {"name": "rack_status", "on": True}):
        assert (await client.post("/kiosk/plugin", json=bad)).status == 400


def test_cleared_memory_row_does_not_list_device(tmp_path):
    db = chat_history.ChatDB(tmp_path / "c.db")
    db.insert("dev-x", "DeepSeekLLM", "user", "hi")
    db.set_memory("dev-x", "喜欢猫", "test")
    assert db.memory_devices() == ["dev-x"]
    db.clear("dev-x")
    assert db.memory_devices() == []
