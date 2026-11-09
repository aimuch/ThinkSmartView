"""[xiaozhi-hub] 智能体页（设计画布第 18 块）：自己加的三种智能体（OpenAI 兼容、Dify、Coze）的保存、称呼、删除、
测试按钮，OpenAI 兼容智能体的会话（用一个假的 Hermes：流式 /v1/chat/completions），设备上能切过去。"""

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler as ah
from core.admin import config_store, pages, presence
from core.agent import api_agent_session as aas
from core.agent.agent_session import Activity, Done, Error, ErrorKind, TextDelta

CUSTOM_YAML = """\
selected_module:
  LLM: DeepSeekLLM
  Intent: function_call
LLM:
  DeepSeekLLM:
    type: openai
    url: https://api.deepseek.com
    model_name: deepseek-flash
    api_key: sk-ds-1234567890
call_names:
  models:
    ClaudeCLI: [claude]
"""


@pytest.fixture
def cfg_path(tmp_path) -> Path:
    p = tmp_path / ".config.yaml"
    p.write_text(CUSTOM_YAML, encoding="utf-8")
    return p


def merged(path):
    return config_store.load_merged(custom_path=path)


class FakeHermes:
    """流式的 OpenAI 兼容接口：记下收到的请求，按 reply 一个字一个字回。"""

    def __init__(self, reply="你好，我是 Hermes，可以帮你查资料。", key="k-1234567890abcdef"):
        self.reply, self.key, self.got = reply, key, []
        self.app = web.Application()
        self.app.add_routes([web.post("/v1/chat/completions", self.chat)])

    async def chat(self, request):
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return web.json_response({"error": {"message": "bad key"}}, status=401)
        body = await request.json()
        self.got.append(body)
        base = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": body.get("model")}
        if not body.get("stream"):
            return web.json_response({"id": "c1", "object": "chat.completion", "created": 0, "model": body.get("model"),
                                      "choices": [{"index": 0, "message": {"role": "assistant", "content": self.reply},
                                                   "finish_reason": "stop"}]})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for piece in [self.reply[i:i + 4] for i in range(0, len(self.reply), 4)]:
            chunk = {**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            await resp.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp


@pytest.fixture
async def hermes():
    h = FakeHermes()
    server = TestServer(h.app)
    await server.start_server()
    h.url = str(server.make_url("/v1"))
    yield h
    await server.close()


@pytest.fixture
async def client(cfg_path):
    presence.reset()
    app = web.Application()
    config = {"selected_module": {"LLM": "DeepSeekLLM", "Intent": "function_call"}, "LLM": {}, "Intent": {}}
    handler = ah.AdminHandler(config, ws_server=None, config_path=cfg_path)
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        yield c


def test_save_three_kinds_with_call_names(cfg_path):
    e1 = pages.save_agent({"kind": "openai", "name": "Hermes", "base_url": "http://10.0.0.5:8642/v1/",
                           "api_key": "k-1", "aliases": ["hermes", "赫尔墨斯"], "background": False}, merged(cfg_path), cfg_path)
    assert e1 == "Agent_Hermes"
    m = merged(cfg_path)
    e = m["LLM"][e1]
    assert e["type"] == "agent_api" and e["base_url"] == "http://10.0.0.5:8642/v1" and e["background"] is False and e["entry"] == e1
    assert m["call_names"]["models"][e1] == ["hermes", "赫尔墨斯"] and m["call_names"]["models"]["ClaudeCLI"] == ["claude"]
    e2 = pages.save_agent({"kind": "dify", "name": "菜谱助手", "api_key": "app-xyz", "mode": "workflows/run"}, merged(cfg_path), cfg_path)
    e3 = pages.save_agent({"kind": "coze", "name": "我的扣子", "api_key": "pat_abc", "bot_id": "7412345678901"}, merged(cfg_path), cfg_path)
    m = merged(cfg_path)
    assert m["LLM"][e2]["type"] == "dify" and m["LLM"][e2]["base_url"] == "https://api.dify.ai/v1"
    assert m["LLM"][e3]["personal_access_token"] == "pat_abc" and m["LLM"][e3]["user_id"] == "xiaozhi"
    assert m["call_names"]["models"][e2] == ["菜谱助手"]   # 没给称呼：用名字
    agents = {a["entry"]: a for a in pages.custom_agents(m)}
    assert agents[e1]["ready"] and agents[e2]["ready"] and agents[e3]["ready"] and agents[e2]["mode"] == "workflows/run"
    assert "pat_abc" not in json.dumps(agents) and "k-1" not in json.dumps(agents)
    assert pages.llm_kind(m, e1) == "agent" and pages.desired_intent(m, e2, cfg_path) == "nointent"
    assert {t["entry"] for t in pages.call_targets(m)} >= {e1, e2, e3}
    # 改：Key 留空不动，换称呼；称呼不能和别人重
    pages.save_agent({"kind": "openai", "entry": e1, "name": "Hermes", "base_url": "http://10.0.0.5:8642/v1", "aliases": ["hermes"]},
                     merged(cfg_path), cfg_path)
    assert merged(cfg_path)["LLM"][e1]["api_key"] == "k-1"
    with pytest.raises(ValueError):
        pages.save_agent({"kind": "openai", "name": "另一个", "base_url": "http://x/v1", "aliases": ["claude"]}, merged(cfg_path), cfg_path)
    for bad in ({"kind": "nope", "name": "x"}, {"kind": "openai", "name": "", "base_url": "http://x"},
                {"kind": "openai", "name": "x", "base_url": "ftp://x"}, {"kind": "dify", "name": "d"},
                {"kind": "coze", "name": "c", "api_key": "p", "bot_id": "abc"},
                {"kind": "dify", "entry": e1, "name": "改类型", "api_key": "k"}):
        with pytest.raises(ValueError):
            pages.save_agent(bad, merged(cfg_path), cfg_path)
    pages.delete_agent(e3, merged(cfg_path), cfg_path)
    m = merged(cfg_path)
    assert e3 not in m["LLM"] and e3 not in m["call_names"]["models"]
    with pytest.raises(KeyError):
        pages.delete_agent("DeepSeekLLM", m, cfg_path)


async def test_agents_endpoint_test_button_and_kiosk(client, hermes):
    d = await (await client.get("/admin/api/agents")).json()
    assert [a["id"] for a in d["local"]] == ["dsh", "claude"] and d["custom"] == [] and len(d["kinds"]) == 3
    r = await (await client.post("/admin/api/agents", json={"kind": "openai", "name": "Hermes", "base_url": hermes.url,
                                                             "api_key": hermes.key, "aliases": ["hermes"]})).json()
    assert r["ok"] and r["entry"] == "Agent_Hermes"
    t = await (await client.post("/admin/api/agents/test", json={"entry": "Agent_Hermes"})).json()
    assert t["ok"] and t["reply"].startswith("你好，我是 Hermes") and hermes.got[-1]["messages"][0]["role"] == "user"
    d = await (await client.get("/admin/api/agents")).json()
    assert d["custom"][0]["aliases"] == ["hermes"] and d["custom"][0]["key_set"]
    await client.post("/admin/api/logout", json={})
    k = await (await client.get("/kiosk/llm")).json()
    assert any(o["kind"] == "agent" and o["entry"] == "Agent_Hermes" for o in k["options"])
    bad = await (await client.post("/admin/api/agents/test", json={"entry": "Agent_Hermes"})).json()
    assert bad.get("ok") is False or bad.get("error")   # 登出后要登录


async def test_api_agent_session_streams_and_keeps_history(hermes):
    aas.forget("Agent_Hermes")
    cfg = aas.ApiAgentConfig(name="Hermes", base_url=hermes.url, api_key=hermes.key, system_prompt="你在一个语音设备里",
                             entry="Agent_Hermes", device="dev-1")
    s = aas.ApiAgentSession(cfg)
    await s.start()
    events = [e async for e in s.send("你是谁")]
    assert isinstance(events[0], Activity) and events[0].tools_running == 1
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == hermes.reply and isinstance(events[-1], Done)
    events = [e async for e in s.send("再说一遍")]
    sent = hermes.got[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"] and sent[-1]["content"] == "再说一遍"
    # 换一个会话（设备重连）：接续窗口内接着记录
    assert len(aas.thread_for("Agent_Hermes", "dev-1", 3600)) == 4
    assert aas.thread_for("Agent_Hermes", "dev-2", 3600) == []
    # Key 不对：AUTH，播报带上名字
    s2 = aas.ApiAgentSession(aas.ApiAgentConfig(name="Hermes", base_url=hermes.url, api_key="wrong"))
    await s2.start()
    ev = [e async for e in s2.send("你好")][-1]
    assert isinstance(ev, Error) and ev.kind == ErrorKind.AUTH and "Hermes" in ev.voice_message
    # 服务没开：SEND_FAILED
    s3 = aas.ApiAgentSession(aas.ApiAgentConfig(name="Hermes", base_url="http://127.0.0.1:9/v1"))
    await s3.start()
    ev = [e async for e in s3.send("你好")][-1]
    assert isinstance(ev, Error) and ev.kind == ErrorKind.SEND_FAILED
