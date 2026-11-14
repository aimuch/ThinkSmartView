"""[xiaozhi-hub] 孩子和学习（设计画布第 61 块）：孩子名单、儿童模式全家一份（谁新听谁的）、学一学的内容、记录和周报。"""
import datetime
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import admin_handler as ah
from core.agent import family, media, services
from core.utils import device_prefs, local_voiceprint


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(family, "_path", lambda: str(tmp_path / "kids.json"))
    monkeypatch.setattr(family, "_records_path", lambda: str(tmp_path / "kid_records.json"))
    monkeypatch.setattr(device_prefs, "_path", lambda: str(tmp_path / "device_prefs.json"))
    monkeypatch.setattr(media, "_path", lambda: str(tmp_path / "media.json"))
    monkeypatch.setattr(media, "_old_path", lambda: str(tmp_path / "music.json"))
    monkeypatch.setattr(media, "_progress_path", lambda: str(tmp_path / "media_progress.json"))
    monkeypatch.setattr(media, "local_root", lambda: tmp_path / "music")
    monkeypatch.setattr(services, "_path", lambda: str(tmp_path / "services.json"))
    monkeypatch.setattr(local_voiceprint, "people", lambda device="": [
        {"name": "宝宝", "samples": 3, "child": True, "devices": ["dev-1"], "here": True},
        {"name": "妈妈", "samples": 3, "child": False, "devices": ["dev-1"], "here": True}])
    media._members.clear()
    yield


def test_children_from_voices_and_manual():
    assert [c["name"] for c in family.children()] == ["宝宝"]
    family.apply({"op": "child", "name": "宝宝", "call": "小宝", "age": 5, "sections": ["songs", "kid_english"]})
    family.apply({"op": "add_child", "name": "朵朵", "age": 8})
    kids = {c["name"]: c for c in family.children()}
    assert kids["宝宝"]["call"] == "小宝" and kids["宝宝"]["age"] == 5 and kids["宝宝"]["voice"] and not kids["宝宝"]["manual"]
    assert kids["朵朵"]["manual"] and not kids["朵朵"]["voice"] and kids["朵朵"]["sections"] is None
    assert family.allowed_sections("宝宝") == ["songs", "kid_english"] and family.allowed_sections("朵朵") is None
    with pytest.raises(ValueError, match="家人声音"):
        family.apply({"op": "remove_child", "name": "宝宝"})
    with pytest.raises(ValueError, match="已经有"):
        family.apply({"op": "add_child", "name": "朵朵"})
    family.apply({"op": "remove_child", "name": "朵朵"})
    assert [c["name"] for c in family.children()] == ["宝宝"]


def test_rules_newest_wins():
    device_prefs.put_kids("dev-1", {"enabled": True, "age": 5})
    k = family.device_kids("dev-1")
    assert k["enabled"] and k["age"] == 5 and k["daily_minutes"] == 30 and k["bedtime"] == "20:30"
    t0 = k["updated"]
    family.apply({"op": "rules", "daily_minutes": 45, "bedtime": "21:00"})          # 后台改：全家一份
    k1, k2 = family.device_kids("dev-1"), family.device_kids("dev-2")
    assert k1["daily_minutes"] == 45 and k2["bedtime"] == "21:00" and k1["updated"] > t0 and not k2["enabled"]
    k = family.from_device("dev-2", {"enabled": True, "age": 7, "voice": "child", "daily_minutes": 60, "bedtime": "21:00"})
    assert k["enabled"] and k["age"] == 7 and family.device_kids("dev-1")["daily_minutes"] == 60   # 设备上改的写回全家
    before = family.load()["rules"]["updated"]
    family.from_device("dev-1", {"enabled": True, "age": 5, "daily_minutes": 60, "bedtime": "21:00", "voice": "child"})
    assert family.load()["rules"]["updated"] == before                                      # 没变：不算新的
    with pytest.raises(ValueError, match="HH:MM"):
        family.apply({"op": "rules", "bedtime": "9点"})


def test_learn_content():
    family.apply({"op": "learn", "chars": "人 口 手、日月水火山石田土木禾竹", "poems": ["静夜思", "春晓"], "math": "20"})
    a = family.learn_text("chars", 5)
    assert "人、口、手、日、月、水、火、山、石、田" in a and "5岁" in a
    b = family.learn_text("chars", 5)
    assert b.startswith("和5岁的孩子玩认字卡：按顺序从这些字里出，一次只出一个：土、木、禾、竹、人")        # 接着往后出
    assert "20 以内" in family.learn_text("math", 6)
    seen = {family.learn_text("poem", 6).split("《")[1].split("》")[0] for _ in range(2)}
    assert seen == {"静夜思", "春晓"}                                                       # 一轮里不重复
    assert family.learn_text("nope") == ""
    with pytest.raises(ValueError, match="口算范围"):
        family.apply({"op": "learn", "math": "1000"})


def test_records_and_week():
    today = datetime.date(2026, 11, 15)
    for d, sec in (("2026-11-11", 2460), ("2026-11-15", 600)):
        family.record("dev-1", "talk", {"seconds": sec}, d)
    family.record("dev-1", "talk", {"seconds": 300}, "2026-11-15")                    # 覆盖时取大的（同一天的累计）
    family.record("dev-1", "learn", {"learn": "math"}, "2026-11-15")
    family.record("dev-1", "learn", {"learn": "math"}, "2026-11-14")
    family.record("dev-2", "study", {"minutes": 90, "present_minutes": 80, "focus_pct": 80, "reminders": 3}, "2026-11-13")
    family.record("dev-1", "played", {"title": "两只老虎", "section": "songs"}, "2026-11-15")
    family.record("dev-1", "played", {"title": "两只老虎", "section": "songs"}, "2026-11-15")
    w = family.week(today.isoformat(), {"dev-1": "客厅"})
    assert w["from"] == "2026-11-09" and w["talk"] == 51 and w["study"] == 90 and w["learn"] == {"math": 2}
    assert w["days"][2]["devices"] == {"客厅": 41} and w["days"][6]["talk"] == 10
    assert "聊了 51 分钟" in w["summary"] and "周三最多 41 分" in w["summary"] and "口算 2" in w["summary"]
    assert "学习陪伴 1 小时 30 分，专心 80%，提醒 3 次" in w["summary"] and "最常放《两只老虎》" in w["summary"]
    with pytest.raises(ValueError):
        family.record("dev-1", "secret", {})


@pytest.fixture
async def client(tmp_path):
    p = tmp_path / ".config.yaml"
    p.write_text("", encoding="utf-8")
    app = web.Application()
    ah.AdminHandler({"selected_module": {}, "LLM": {}}, ws_server=None, config_path=p).register(app)
    async with TestClient(TestServer(app)) as c:
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        yield c


async def test_admin_and_kiosk(client, tmp_path, monkeypatch):
    monkeypatch.setattr(ah.AdminHandler, "_device_names", lambda self: {"dev-1": "客厅", "dev-2": "书房"})
    d = await (await client.get("/admin/api/kids")).json()
    assert [c["name"] for c in d["children"]] == ["宝宝"] and [x["name"] for x in d["devices"]] == ["客厅", "书房"]
    assert {s["id"] for s in d["sections"] if s["kid"]} == {"songs", "stories", "kid_english", "cartoons"}
    d = await (await client.post("/admin/api/kids", json={"op": "device", "id": "dev-1", "enabled": True, "age": 5})).json()
    assert d["devices"][0]["enabled"] and d["devices"][0]["age"] == 5
    await client.post("/admin/api/kids", json={"op": "rules", "daily_minutes": 20})
    r = await (await client.get("/kiosk/kids?device=dev-1")).json()
    assert r["kids"]["enabled"] and r["kids"]["daily_minutes"] == 20
    r = await (await client.post("/kiosk/prefs", json={"device": "dev-1", "kids": {"enabled": True, "age": 6, "voice": "child",
                                                                               "daily_minutes": 40, "bedtime": "20:30"}})).json()
    assert r["kids"]["daily_minutes"] == 40 and r["kids"]["age"] == 6
    assert "认字卡" in (await (await client.get("/kiosk/kids/learn?kind=chars&age=5")).json())["text"]
    assert (await client.post("/kiosk/kids/report", json={"device": "dev-1", "kind": "learn", "learn": "chars"})).status == 200
    assert (await client.post("/kiosk/kids/report", json={"device": "dev-1", "kind": "talk", "seconds": 600})).status == 200
    d = await (await client.get("/admin/api/kids")).json()
    assert d["devices"][0]["today"] == 10 and d["week"]["talk"] == 10
    # 孩子能用的分区：没勾的不给
    media.apply({"op": "ref", "section": "songs", "ref": {"type": "folder", "id": "l.", "name": "music"}})
    media.apply({"op": "ref", "section": "cartoons", "ref": {"type": "folder", "id": "l.", "name": "music"}})
    await client.post("/admin/api/kids", json={"op": "child", "name": "宝宝", "sections": ["songs"]})
    monkeypatch.setattr(ah.connections, "all_connections", lambda: [SimpleNamespace(device_id="dev-1", turn_speaker="宝宝（孩子）")])
    v = await (await client.get("/kiosk/media?device=dev-1")).json()
    assert [s["id"] for s in v["sections"]] == ["songs"]
    r = await (await client.get("/kiosk/media/section/动画?device=dev-1")).json()
    assert "没给宝宝开" in r["error"]
    await client.post("/admin/api/logout", json={})
    assert (await client.get("/admin/api/kids")).status == 401
    assert (await client.get("/kiosk/kids?device=dev-1", headers={"X-Forwarded-For": "1.2.3.4"})).status == 403
