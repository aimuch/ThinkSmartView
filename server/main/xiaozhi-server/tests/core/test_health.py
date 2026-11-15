"""状态页的记录（设计画布第 68 块）：每小时成败、回话耗时、在线台数、最近发生的事。"""
import json
import time

from core.admin import presence
from core.agent import health, services


def test_hours_latency_and_online():
    since = health._hour() - 23 * 3600
    health.record("asr", True)
    health.record("asr", False)
    health.record("tts", True)
    health.record("nope", True)                 # 不认识的不记
    health.latency(1800)
    health.latency(2200)
    health.latency(-5)
    health.latency(999999)
    health.online("dev-1")
    health.online("dev-1")
    health.online("dev-2")
    v = health.view(since, 24, llm_rows=[{"ts": time.time(), "ok": 1}, {"ts": time.time(), "ok": 0}, {"ts": since - 10, "ok": 0}])
    assert v["rows"]["asr"][-1] == [1, 1] and v["rows"]["tts"][-1] == [1, 0] and v["rows"]["llm"][-1] == [1, 1]
    assert v["rows"]["ext"][-1] == [0, 0] and len(v["rows"]["asr"]) == 24
    assert v["latency"]["hours"][-1] == 2000 and v["latency"]["today"] == 2000 and v["latency"]["n"] == 2
    assert v["online"][-1] == 2


def test_flush_and_prune(tmp_path):
    health.record("asr", True)
    health.event("start", "网关启动")
    health.flush(force=True)
    d = json.load(open(health._path()))
    assert d["events"][0]["text"] == "网关启动"
    old = str(health._hour() - 8 * 86400)
    health._load()["hours"][old] = {"asr": [1, 0]}
    health._load()["events"].insert(0, {"t": int(time.time()) - 8 * 86400, "kind": "x", "text": "旧的"})
    health._touch()
    health.flush(force=True)
    d = json.load(open(health._path()))
    assert old not in d["hours"] and all(e["text"] != "旧的" for e in d["events"])


def test_service_failure_and_recovery_logged_once():
    services.record("open-meteo", False, err="timeout")
    services.record("open-meteo", False, err="timeout")
    services.record("open-meteo", True, 120)
    texts = [e["text"] for e in health.view(health._hour(), 1)["events"]]
    assert texts == ["Open-Meteo恢复了", "Open-Meteo连不上：timeout"]
    assert health.view(health._hour(), 1)["rows"]["ext"][0] == [1, 2]
    services.STATUS.pop("open-meteo", None)


def test_device_update_offline_and_back(monkeypatch):
    presence.reset()
    presence.beat("dev-1", "0.1.690")
    presence.beat("dev-1", "0.1.691")
    presence._seen["dev-1"]["last"] = time.time() - 600
    presence.note_offline(presence.snapshot())
    presence.note_offline(presence.snapshot())    # 同一次离线只记一次
    presence.beat("dev-1", "0.1.691")
    kinds = [(e["kind"], e["device"]) for e in sorted(health._load()["events"], key=lambda e: e["t"])]
    assert ("update", "dev-1") in kinds and kinds.count(("offline", "dev-1")) == 1 and kinds[-1] == ("online", "dev-1")
    presence.reset()


def test_late_records_still_reach_disk(monkeypatch):
    """刚写过盘时再记一条：不等下一条记录，过一会儿自己补写。"""
    monkeypatch.setattr(health, "FLUSH_EVERY", 0.05)
    health.record("asr", True)
    health.flush(force=True)
    health.online("dev-9")
    time.sleep(0.2)
    d = json.load(open(health._path()))
    assert any("dev-9" in v for v in d["online"].values())


def test_stage_timings():
    """工作原理的耗时条（设计画布第 78 块）：四段的中位数、慢的时候、口令不算大模型那段，只记时间和设备。"""
    t0 = 1000.0
    health.stages({"said": t0, "text": t0 + 0.5, "llm": t0 + 0.7, "first": t0 + 2.7, "tool": True}, t0 + 3.3, "aa:bb:cc:dd:ee")
    health.stages({"said": t0, "text": t0 + 0.4, "llm": t0 + 0.5, "first": t0 + 1.5}, t0 + 2.0, "dev-2")
    health.stages({"said": t0, "text": t0 + 0.6}, t0 + 1.2, "dev-2")                      # 口令：没问大模型
    health.stages({"said": t0, "text": t0 + 0.6}, t0 - 5, "dev-2")                        # 时间倒了：不记
    v = health.stage_view(time.time() - 60)
    assert v["n"] == 3 and v["local"] == 1 and v["tool"] == 1
    assert v["stages"]["asr"]["median"] == 500 and v["stages"]["llm"]["median"] == 1500 and v["stages"]["llm"]["p90"] == 2000
    assert v["recent"][0]["llm"] is None and v["recent"][0]["prep"] == 600 and v["recent"][-1]["device"] == "dd:ee"
    assert v["total"]["median"] == 2000
