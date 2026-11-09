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
    assert s["name"] == "儿童房" and s["plugins"] == ["get_weather"] and s["model"] == "KimiLLM"
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
    await client.post("/admin/api/device-settings/forget", json={"device": "aa:bb"})
    d = await (await client.get("/admin/api/device-settings")).json()
    assert [x["device_id"] for x in d["devices"]] == ["cc:dd"] and ds.get("aa:bb") == {}
