"""[xiaozhi-hub] 就近唤醒（设计画布第 55 块）：几台同时听到，只让最响的那台回答。"""
import asyncio

import pytest

from core.agent import wake_arbiter as wa


async def pause(seconds):
    try:
        await asyncio.wait_for(asyncio.Event().wait(), seconds)    # conftest 把 asyncio.sleep 换成了立即返回
    except asyncio.TimeoutError:
        pass


def test_loudest_wins_and_late_one_loses():
    async def go():
        a = wa.Arbiter()
        r1, r2 = await asyncio.gather(a.claim("study", -31, 0.1), a.claim("living", -44, 0.1))
        assert r1 == {"win": True, "winner": "study"} and r2["win"] is False and r2["winner"] == "study"
        late = await a.claim("bedroom", -20, 0.1)                  # 窗口关了才到：同一声，慢了一步
        assert late["win"] is False and late["late"]
        assert a.history[0]["winner"] == "study" and set(a.history[0]["claims"]) == {"study", "living", "bedroom"}
        await pause(1.7)                                           # 过了 LATE：新的一声
        r = await a.claim("living", -50, 0.05)
        assert r["win"] is True
    asyncio.run(go())


def test_single_device_and_ties():
    async def go():
        a = wa.Arbiter()
        assert (await a.claim("study", -40, 0.05))["win"]
        await pause(1.7)
        r1, r2 = await asyncio.gather(a.claim("a", -30, 0.05), a.claim("b", -30, 0.05))
        assert r1["win"] and not r2["win"]                          # 一样响：先报的那台
    asyncio.run(go())


def test_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "_path", lambda: str(tmp_path / "wa.json"))
    assert wa.load() == {"enabled": True, "window": 0.3}
    assert wa.save(False, 0.5) == {"enabled": False, "window": 0.5}
    assert wa.save(window=0.7) == {"enabled": False, "window": 0.5}   # 不在可选里：不改
    assert wa.load()["enabled"] is False
