"""[xiaozhi-hub] 视频对讲（设计画布第 51 块）：网关转交呼叫、接听、挂断；手机页只认 Tailscale 转来的请求。"""
import asyncio
from types import SimpleNamespace

import pytest

from core.admin.admin_handler import AdminHandler
from core.agent import intercom as ic


def run(coro):
    return asyncio.run(coro)


async def pause(seconds):
    """真的等一会儿（tests/conftest.py 把 asyncio.sleep 换成了立即返回）。"""
    try:
        await asyncio.wait_for(asyncio.Event().wait(), seconds)
    except asyncio.TimeoutError:
        pass


def test_call_answer_and_hangup():
    async def go():
        hub, phone = ic.Intercom(), []

        async def send(m):
            phone.append(m)
        with pytest.raises(LookupError):                       # 设备没来等过：不在线
            await hub.start("dev1", "妈妈", "mom@x", "v=0 offer", send)
        waiting = asyncio.ensure_future(hub.wait("dev1", 2))
        await pause(0.05)
        call = await hub.start("dev1", "妈妈", "mom@x", "v=0 offer", send)
        ring = (await waiting)[0]
        assert ring == {"type": "ring", "id": call.id, "caller": "妈妈", "sdp": "v=0 offer"}
        with pytest.raises(ValueError, match="正在通话"):
            await hub.start("dev1", "爸爸", "dad@x", "v=0", send)
        assert not await hub.device_event("dev2", call.id, "answer", "v=0 answer")   # 别的设备不能接
        assert await hub.device_event("dev1", call.id, "answer", "v=0 answer")
        assert phone[-1] == {"type": "answer", "sdp": "v=0 answer"} and call.state == "connected"
        await hub.phone_hangup(call.id)
        assert await hub.wait("dev1", 0.1) == [{"type": "hangup", "id": call.id, "reason": "hangup"}]
        assert call.id not in hub.calls and not hub.busy("dev1")
    run(go())


def test_decline_missed_and_timeout(monkeypatch):
    async def go():
        hub, phone = ic.Intercom(), []

        async def send(m):
            phone.append(m)
        hub.seen["dev1"] = ic.time.time()
        call = await hub.start("dev1", "妈妈", "m", "o", send)
        await hub.device_event("dev1", call.id, "decline")
        assert phone[-1] == {"type": "ended", "reason": "declined"}
        assert [e["type"] for e in await hub.wait("dev1", 0.05)] == ["ring"]           # 设备自己挂的：不再通知设备
        call = await hub.start("dev1", "妈妈", "m", "o", send)
        await hub.phone_hangup(call.id)                                               # 响铃时手机挂了：未接
        assert (await hub.wait("dev1", 0.05))[-1] == {"type": "hangup", "id": call.id, "reason": "missed"}
        monkeypatch.setattr(ic, "RING_SECONDS", 0.05)
        call = await hub.start("dev1", "妈妈", "m", "o", send)
        await pause(0.15)
        assert phone[-1] == {"type": "ended", "reason": "timeout"}
        assert (await hub.wait("dev1", 0.05))[-1]["reason"] == "missed"
    run(go())


def test_caller_identity_from_tailscale_headers():
    assert ic.caller_of({}) == (None, None)
    assert ic.caller_of({"Tailscale-User-Login": "mom@example.com"}) == ("mom", "mom@example.com")
    assert ic.caller_of({"Tailscale-User-Login": "a@b", "Tailscale-User-Name": "=?utf-8?q?=E5=A6=88=E5=A6=88?="}) == ("妈妈", "a@b")


def test_only_tailscale_serve_requests_count():
    req = lambda remote, headers: SimpleNamespace(remote=remote, headers=headers)
    hdr = {"Tailscale-User-Login": "mom@example.com", "X-Forwarded-For": "100.64.0.5"}
    assert AdminHandler._from_tailnet(req("127.0.0.1", hdr)) == ("mom", "mom@example.com")
    assert AdminHandler._from_tailnet(req("10.10.10.9", hdr)) == (None, None)      # 局域网伪造的头不认
    assert AdminHandler._from_tailnet(req("127.0.0.1", {})) == (None, None)
    assert AdminHandler._from_this_machine(req("127.0.0.1", hdr)) is False          # 转来的请求碰不到 /kiosk 接口
