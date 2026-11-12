"""[xiaozhi-hub] 模型图标（设计画布第 42 块）：对照表里的文件都在、各处带上 logo、/admin/logos 只给图标目录里的 svg。"""
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import catalog, config_store, pages
from core.admin.admin_handler import STATIC_DIR, AdminHandler
from core.agent import photo_ask

LOGO_DIR = STATIC_DIR / "logos"
DEVICE_LOGOS = Path(__file__).resolve().parents[5] / "client" / "web" / "logos"


def test_every_logo_file_exists_here_and_on_the_device():
    for key, name in catalog.LOGOS.items():
        assert (LOGO_DIR / f"{name}.svg").is_file(), key
        if DEVICE_LOGOS.is_dir():   # 只拷了 server/ 跑测试时没有客户端目录
            assert (DEVICE_LOGOS / f"{name}.svg").is_file(), key   # 设备上用同一套文件名


def test_vendors_have_logos_except_the_nameless_ones():
    missing = {v["id"] for v in catalog.LLM_VENDORS if v["id"] not in catalog.LOGOS}
    assert missing == {"custom"}
    missing = {p for p in photo_ask.PROVIDERS if p not in catalog.LOGOS}
    assert missing <= {"custom"}


def test_entry_logo(tmp_path):
    p = tmp_path / ".config.yaml"
    p.write_text("LLM:\n  Agent_hermes:\n    type: agent_api\n    name: Hermes\n"
                 "  Agent_flow:\n    type: dify\n    name: 小助理\n  Agent_mine:\n    type: agent_api\n    name: 我的\n",
                 encoding="utf-8")
    merged = config_store.load_merged(custom_path=p)
    assert pages.entry_logo(merged, pages.CLAUDE_CLI_ENTRY) == "claudecode-color"
    assert pages.entry_logo(merged, pages.DSH_ENTRY) == "deepseek-color"
    assert pages.entry_logo(merged, "SiliconFlowLLM") == "siliconcloud-color"
    assert pages.entry_logo(merged, "MagpieLLM") == "magpie"
    assert pages.entry_logo(merged, "CustomOpenAILLM") == ""
    assert pages.entry_logo(merged, "GrokBot_小G") == "grok"
    agents = {a["entry"]: a for a in pages.custom_agents(merged)}
    if agents:   # 智能体条目的格式以 custom_agents 认的为准
        assert pages.entry_logo(merged, "Agent_mine") == ""
        assert pages.agent_logo({"kind": "dify", "name": "x"}) == "dify-color"
        assert pages.agent_logo({"kind": "openai", "name": "Hermes 助理"}) == "hermesagent"
    assert pages.entry_logo(merged, "SomethingElse") == ""
    assert all(v["logo"] == catalog.LOGOS.get(v["id"], "") for v in pages.describe_llm(merged)["vendors"])


@pytest.fixture
async def client(tmp_path):
    p = tmp_path / ".config.yaml"
    p.write_text("", encoding="utf-8")
    app = web.Application()
    AdminHandler({"selected_module": {}, "LLM": {}}, ws_server=None, config_path=p).register(app)
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_logo_route(client):
    r = await client.get("/admin/logos/deepseek-color.svg")   # 不用登录
    assert r.status == 200 and r.headers["Content-Type"].startswith("image/svg+xml")
    assert (await r.text()).startswith("<svg")
    for bad in ("nope.svg", "..%2Findex.html", "deepseek-color.png", "Deepseek-color.svg"):
        assert (await client.get(f"/admin/logos/{bad}")).status == 404, bad
