"""[xiaozhi-hub] 设计画布第 14、15 块：名称和图标、保持登录、改密码、设备心跳、状态页的活动数据、
看图模型沿用 Key、DSH 的设置、读取模型列表、局域网识别和合成服务。全部用临时配置文件。"""

import json
import os
import stat
import sys
import textwrap
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler as ah
from core.admin import auth, brand, catalog, config_store, pages, presence
from core.agent import photo_ask

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
"""


@pytest.fixture
def cfg_path(tmp_path) -> Path:
    p = tmp_path / ".config.yaml"
    p.write_text(CUSTOM_YAML, encoding="utf-8")
    return p


def merged(path):
    return config_store.load_merged(custom_path=path)


@pytest.fixture
async def client(cfg_path, tmp_path):
    from core import billing
    from core.agent import chat_history
    from core.billing.store import UsageDB

    presence.reset()
    chat_history.set_db(chat_history.ChatDB(tmp_path / "chat.db"))
    billing.set_db(UsageDB(tmp_path / "usage.db"))
    app = web.Application()
    config = {"selected_module": {"LLM": "DeepSeekLLM", "Intent": "function_call"}, "LLM": {}, "Intent": {}}
    handler = ah.AdminHandler(config, ws_server=None, config_path=cfg_path)
    handler.register(app)
    async with TestClient(TestServer(app)) as c:
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        c.handler = handler
        yield c
    chat_history.set_db(None)
    billing.set_db(None)


# ---------- 名称和图标 ----------

def test_brand_defaults_validation_and_favicon(tmp_path):
    b = brand.load(tmp_path)
    assert b["name"] == "小麦网关" and b["icon"]["kind"] == "wheat" and b["repo_url"] == ""
    body, ctype = brand.favicon(tmp_path)
    assert ctype == "image/svg+xml" and b"<svg" in body and b"linearGradient" in body
    saved = brand.save(tmp_path, {"name": "  我家  音箱 ", "icon": {"kind": "emoji", "value": "🏠"},
                                  "repo_url": "https://github.com/me/thinksmart"})
    assert saved["name"] == "我家 音箱" and saved["repo_label"] == "thinksmart"
    assert "🏠" in brand.favicon(tmp_path)[0].decode()
    for bad in ({"name": "x" * 21}, {"icon": {"kind": "emoji", "value": ""}}, {"icon": {"kind": "image", "value": "data:image/jpeg;base64,AA"}},
                {"repo_url": "javascript:alert(1)"}, {"icon": {"kind": "svg", "value": "<svg>"}}):
        with pytest.raises(ValueError):
            brand.save(tmp_path, bad)
    import base64
    png = ah._test_image()
    saved = brand.save(tmp_path, {"icon": {"kind": "image", "value": "data:image/png;base64," + base64.b64encode(png).decode()}})
    assert saved["icon"]["kind"] == "image" and saved["name"] == "小麦网关"
    assert brand.favicon(tmp_path) == (png, "image/png")


async def test_brand_endpoints(client):
    st = await (await client.get("/admin/api/state")).json()
    assert st["brand"]["name"] == "小麦网关" and st["online"] == 0 and st["logged_in"]
    r = await client.post("/admin/api/brand", json={"name": "小麦", "icon": {"kind": "emoji", "value": "🤖"}})
    assert (await r.json())["name"] == "小麦"
    fav = await client.get("/admin/favicon")
    assert fav.status == 200 and fav.content_type == "image/svg+xml" and "🤖" in await fav.text()
    assert (await client.post("/admin/api/brand", json={"repo_url": "ftp://x"})).status == 400
    await client.post("/admin/api/logout", json={})
    assert (await (await client.get("/admin/api/brand")).json())["name"] == "小麦"   # 登录页要用：不登录也能读
    assert (await client.post("/admin/api/brand", json={"name": "别人"})).status == 401
    assert (await (await client.get("/admin/api/state")).json())["brand"]["name"] == "小麦"


# ---------- 保持登录、改密码 ----------

def test_remembered_sessions_survive_restart(tmp_path):
    kept = tmp_path / ".kept.json"
    a = auth.SessionStore(kept_path=kept)
    long_token, short_token = a.create(remember=True), a.create()
    assert a.expiry(long_token) - time.time() > 29 * 86400 and a.expiry(short_token) - time.time() < 13 * 3600
    assert stat.S_IMODE(os.stat(kept).st_mode) == 0o600 and long_token not in kept.read_text()
    b = auth.SessionStore(kept_path=kept)     # 网关重启
    assert b.valid(long_token) and not b.valid(short_token)
    b.revoke(long_token)
    assert not auth.SessionStore(kept_path=kept).valid(long_token)


async def test_login_remember_cookie_and_password_change(client, cfg_path):
    await client.post("/admin/api/logout", json={})
    r = await client.post("/admin/api/login", json={"password": "pass-1234", "remember": True})
    cookie = r.cookies[ah.COOKIE_NAME]
    assert int(cookie["max-age"]) == auth.REMEMBER_SECONDS
    st = await (await client.get("/admin/api/state")).json()
    assert st["session_expires"] - time.time() > 29 * 86400
    other = client.handler.sessions.create(remember=True)
    assert (await client.post("/admin/api/password", json={"old": "wrong", "new": "newpass-1"})).status == 400
    assert (await client.post("/admin/api/password", json={"old": "pass-1234", "new": "123"})).status == 400
    r = await client.post("/admin/api/password", json={"old": "pass-1234", "new": "newpass-1"})
    assert (await r.json())["ok"]
    assert not client.handler.sessions.valid(other)                       # 别的浏览器失效
    assert (await client.get("/admin/api/status")).status == 200          # 这个浏览器还在
    await client.post("/admin/api/logout", json={})
    assert (await client.post("/admin/api/login", json={"password": "pass-1234"})).status == 401
    r = await client.post("/admin/api/login", json={"password": "newpass-1"})
    assert r.status == 200 and not r.cookies[ah.COOKIE_NAME]["max-age"]    # 没勾保持登录：关掉浏览器就失效


# ---------- 心跳、状态页 ----------

def test_presence_states(monkeypatch):
    presence.reset()
    presence.beat("dev-a", "0.1.625")
    presence.beat("dev-b")
    presence._seen["dev-b"]["last"] -= presence.ONLINE_SECONDS + 5
    snap = {d["device_id"]: d for d in presence.snapshot(["dev-c"])}
    assert snap["dev-a"]["state"] == "idle" and snap["dev-a"]["version"] == "0.1.625"
    assert snap["dev-b"]["state"] == "offline" and snap["dev-c"]["state"] == "talking"
    assert [d["device_id"] for d in presence.snapshot(["dev-c"])][0] == "dev-c"
    assert presence.online_count(["dev-c"]) == 2


async def test_heartbeat_and_status_activity(client):
    r = await client.post("/kiosk/reports", json={"device": "aa:bb", "quiet": False, "version": "0.1.625"})
    assert r.status == 200
    d = await (await client.get("/admin/api/devices")).json()
    assert d["seen"] == [{"device_id": "aa:bb", "state": "idle", "last_seen": d["seen"][0]["last_seen"], "version": "0.1.625"}]
    assert (await (await client.get("/admin/api/state")).json())["online"] == 1
    s = await (await client.get("/admin/api/status")).json()
    assert s["uptime"] >= 0 and len(s["hours"]) in (0, 24) and "today" in s["cost"]
    assert [x["id"] for x in s["stages"]] == ["listen", "asr", "llm", "tts", "play"]


# ---------- 看图模型 ----------

def test_vision_reuses_llm_key_and_local_needs_none(cfg_path, monkeypatch):
    v = {p["id"]: p for p in photo_ask.vision_settings(merged(cfg_path))["providers"]}
    assert list(v)[:3] == ["deepseek", "zhipu", "qwen"] and len(v) >= 12
    assert v["deepseek"]["shared_key"] and v["deepseek"]["configured"] and not v["deepseek"]["own_key"]
    assert v["ollama"]["configured"] and v["ollama"]["local"]
    changed = photo_ask.save_vision("deepseek", "", cfg_path)        # 不用填 Key
    assert "selected" in changed and "api_key" not in config_store.load_custom(cfg_path)["VLLM"]["DeepSeekVLLM"]
    name, entry, key = photo_ask._vision_entry(merged(cfg_path))
    assert name == "DeepSeekVLLM" and key == "sk-ds-1234567890" and entry["model_name"] == "deepseek-flash"
    photo_ask.save_vision("ollama", "", cfg_path, model="qwen2.5vl:7b", url="http://10.0.0.5:11434/v1")
    name, entry, key = photo_ask._vision_entry(merged(cfg_path))
    assert entry["url"] == "http://10.0.0.5:11434/v1" and key == "local"
    with pytest.raises(ValueError):
        photo_ask.save_vision("custom", "", cfg_path, model="m")      # 自定义要接口地址
    with pytest.raises(ValueError):
        photo_ask.save_vision("zhipu", "", cfg_path)                  # 大模型页也没填智谱的 Key


async def test_models_list_from_ollama_like_server(client):
    async def models(request):
        return web.json_response({"data": [{"id": "qwen3:8b"}, {"id": "qwen2.5vl:7b"}]})

    async def tags(request):
        return web.json_response({"models": [{"name": "qwen2.5vl:7b", "size": 6_000_000_000, "details": {"families": ["qwen25vl"]}},
                                             {"name": "qwen3:8b", "size": 5_200_000_000, "details": {"families": ["qwen3"]}}]})
    app = web.Application()
    app.add_routes([web.get("/v1/models", models), web.get("/api/tags", tags)])
    async with TestServer(app) as srv:
        r = await (await client.post("/admin/api/models/list", json={"url": str(srv.make_url("/v1"))})).json()
    assert r["ok"] and {m["id"]: m.get("vision", False) for m in r["models"]} == {"qwen3:8b": False, "qwen2.5vl:7b": True}
    assert r["models"][1]["size"] == "6.0 GB"
    bad = await (await client.post("/admin/api/models/list", json={"url": "http://127.0.0.1:9/v1"})).json()
    assert bad["ok"] is False and "连不上" in bad["error"]
    assert (await client.post("/admin/api/models/list", json={"url": "file:///etc"})).status == 400


# ---------- DSH ----------

def test_save_dsh_creates_entry_and_call_name(cfg_path):
    d = pages.describe_dsh(merged(cfg_path))
    assert not d["exists"] and d["shared_key"] and d["values"]["model"] == "deepseek-flash"
    changed = pages.save_dsh({"model": "deepseek-v4-pro", "effort": "high", "permission": "read-only"}, merged(cfg_path), cfg_path)
    assert {"model", "effort"} <= set(changed)
    m = merged(cfg_path)
    assert m["LLM"]["DshCLI"]["type"] == "dsh_cli" and "api_key" not in m["LLM"]["DshCLI"]
    calls = {x["entry"]: x["aliases"] for x in pages.describe_call_names(m)["models"]}
    assert "dsh" in calls["DshCLI"] and "claude" in calls["ClaudeCLI"]   # 默认的称呼表抄进来，再加上 DSH
    assert pages.llm_kind(m, "DshCLI") == "dsh" and pages.desired_intent(m, "DshCLI", cfg_path) == "nointent"
    assert any(t["entry"] == "DshCLI" for t in pages.call_targets(m))
    with pytest.raises(ValueError):
        pages.save_dsh({"permission": "root"}, m, cfg_path)
    assert pages.save_dsh({}, merged(cfg_path), cfg_path) == []


async def test_dsh_admin_and_kiosk(client, monkeypatch):
    monkeypatch.setattr(ah.AdminHandler, "_dsh_install", lambda self, merged, version=True: {"installed": True, "node": "/x", "version": "0.2.2", "path": "/x/dsh"})
    monkeypatch.setattr(ah.AdminHandler, "_claude_install", lambda self, merged, version=True: {"installed": False})
    r = await client.post("/admin/api/llm/dsh", json={"model": "deepseek-flash", "effort": "low", "permission": "read-only"})
    assert (await r.json())["ok"]
    d = await (await client.get("/admin/api/llm")).json()
    assert d["dsh"]["exists"] and d["dsh"]["install"]["version"] == "0.2.2" and d["callable"]["DshCLI"][0] == "dsh"
    await client.post("/admin/api/logout", json={})
    k = await (await client.get("/kiosk/llm")).json()
    assert k["options"][0]["kind"] == "dsh" and k["options"][0]["entry"] == "DshCLI"


FAKE_DSH = r'''#!{python}
import json, sys, os
args = sys.argv[1:]
task = sys.stdin.read()
sid = args[args.index("--session-id") + 1] if "--session-id" in args else "session-new"
log = os.environ["FAKE_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({{"args": args, "task": task, "key": os.environ.get("DEEPSEEK_API_KEY"), "home": os.environ.get("DSH_HOME")}}) + "\n")
if sid == "session-gone":
    print(json.dumps({{"type": "error", "message": "unknown session id session-gone: no stored Session"}}), flush=True)
    sys.exit(1)
print(json.dumps({{"type": "session", "sessionId": sid}}), flush=True)
print(json.dumps({{"type": "tool_call", "callId": "c1", "tool": "read", "input": {{}}}}), flush=True)
print(json.dumps({{"type": "tool_result", "callId": "c1", "status": "completed"}}), flush=True)
print(json.dumps({{"type": "status", "phase": "step_end", "usage": {{"inputTokens": 10, "outputTokens": 3, "cacheReadTokens": 5}}}}), flush=True)
if "bad key" in task:
    print(json.dumps({{"type": "final", "sessionId": sid, "reason": {{"kind": "error", "error": {{"code": "INVALID_CREDENTIAL", "message": "bad"}}}}, "text": ""}}), flush=True)
    sys.exit(1)
print(json.dumps({{"type": "text", "text": "好的，文件里写的是 hello。"}}), flush=True)
print(json.dumps({{"type": "final", "sessionId": sid, "reason": {{"kind": "completed"}}, "text": "好的，文件里写的是 hello。"}}), flush=True)
'''


async def test_dsh_session_with_fake_cli(tmp_path, monkeypatch):
    from core.agent.agent_session import Activity, Done, Error, ErrorKind, TextDelta
    from core.agent.dsh_session import DshSession, DshSessionConfig, write_patch

    exe = tmp_path / "dsh"
    exe.write_text(FAKE_DSH.format(python=sys.executable))
    exe.chmod(0o755)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    patch = tmp_path / "p.yml"
    write_patch(patch, "deepseek-flash", "low", "你是小麦：\"引号\"也行")
    assert 'model: "deepseek-flash"' in patch.read_text() and 'reasoningEffort: "low"' in patch.read_text()
    cfg = DshSessionConfig(dsh_path=str(exe), node_dir=None, workdir=str(tmp_path / "w"), patch_path=str(patch),
                           home=str(tmp_path / "home"), api_key="sk-x")
    usage = []
    s = DshSession(cfg, prefix="系统提示：住在青浦")
    s.on_usage = usage.append
    await s.start()
    events = [e async for e in s.send("读一下 a.txt")]
    assert any(isinstance(e, Activity) and e.tools_running == 1 for e in events)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)).startswith("好的，文件里写的是 hello。")
    assert isinstance(events[-1], Done) and s.session_id == "session-new" and usage[0]["outputTokens"] == 3
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0]["task"].startswith("系统提示：住在青浦") and calls[0]["key"] == "sk-x" and "--session-id" not in calls[0]["args"]
    assert calls[0]["args"][:6] == ["--profile", "headless", "--json", "--permission-mode", "read-only", "--patch"]
    [e async for e in s.send("接着说")]
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[1]["task"] == "接着说" and calls[1]["args"][calls[1]["args"].index("--session-id") + 1] == "session-new"
    # 接续的会话在 DSH 那边没了：自动新开
    gone = DshSession(cfg, resume_id="session-gone")
    await gone.start()
    events = [e async for e in gone.send("你好")]
    assert isinstance(events[-1], Done) and gone.session_id == "session-new"
    # Key 不对：报 AUTH，播报说清楚
    events = [e async for e in s.send("bad key")]
    assert isinstance(events[-1], Error) and events[-1].kind == ErrorKind.AUTH and "Key" in events[-1].voice_message
    with pytest.raises(Exception):
        await DshSession(DshSessionConfig(dsh_path=None, node_dir=None, workdir="/tmp", patch_path="", home="")).start()


# ---------- 局域网识别和合成 ----------

def test_lan_asr_key_optional(cfg_path):
    p = catalog.ASR_PROVIDER_BY_ID["lanasr"]
    changed = pages.save_asr_provider("lanasr", {"base_url": "http://10.0.0.5:8000/v1/audio/transcriptions",
                                                 "model_name": "Systran/faster-whisper-small"}, merged(cfg_path), cfg_path)
    assert "base_url" in changed
    entry = merged(cfg_path)["ASR"][p["entry"]]
    assert entry["type"] == "openai" and entry["api_key"] == "none"
    d = pages.describe_asr_provider(p, merged(cfg_path))
    assert d["configured"] and d["group"] == "lan"


def test_lan_tts_save_and_describe(cfg_path):
    changed = pages.save_tts("lan", {"api_url": "http://10.0.0.5:8880/v1/audio/speech", "model": "kokoro", "voice": "zm_yunxi"},
                             merged(cfg_path), cfg_path)
    assert "voice" in changed and "api_url" in changed
    e = next(x for x in pages.describe_tts(merged(cfg_path))["engines"] if x["id"] == "lan")
    assert e["configured"] and e["fields"]["voice"] == "zm_yunxi" and e["fields"]["format"] == "wav" and not e["key_set"]
    with pytest.raises(ValueError):
        pages.save_tts("lan", {"api_url": "nope"}, merged(cfg_path), cfg_path)
    with pytest.raises(ValueError):
        pages.save_tts("lan", {"format": "aiff"}, merged(cfg_path), cfg_path)


def test_save_dsh_adds_call_name_to_custom_table(tmp_path):
    p = tmp_path / ".config.yaml"
    p.write_text(CUSTOM_YAML + "call_names:\n  models:\n    ClaudeCLI: [claude]\n", encoding="utf-8")
    pages.save_dsh({}, merged(p), p)
    models = config_store.load_custom(p)["call_names"]["models"]
    assert models["ClaudeCLI"] == ["claude"] and "dsh" in models["DshCLI"]
