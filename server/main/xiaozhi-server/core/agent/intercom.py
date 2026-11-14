"""[xiaozhi-hub] 视频对讲（设计画布第 51 块）：家人手机经 Tailscale 打开 /call，打给家里的设备。

网关只转交呼叫、接听、挂断和连接描述（SDP，非 trickle：两边收齐候选地址后一次发完），音视频由手机和设备
直接连（网关把家里网段发布给 tailnet）。都在内存里，网关重启时进行中的通话就断了。

手机：WebSocket /call/ws ⇄ 这里的 Call；设备：长轮询 /kiosk/call/wait 拿事件，POST /kiosk/call/event 回应。
想法来自 fubin0478/thinksmart-view（MIT）。
"""
import asyncio
import secrets
import time

RING_SECONDS = 45          # 没人接就算未接
READY_SECONDS = 40         # 设备这么久之内来等过事件，才算「能接电话」
MAX_SDP = 20000


class Call:
    def __init__(self, device: str, caller: str, login: str, offer: str, send):
        self.id = secrets.token_hex(6)
        self.device, self.caller, self.login, self.offer = device, caller, login, offer
        self.send = send                     # async: 给手机发一条消息
        self.state = "ringing"               # ringing / connected / ended
        self.created = time.time()
        self.timer = None


class Intercom:
    def __init__(self):
        self.calls = {}                      # id -> Call
        self.queues = {}                     # device -> asyncio.Queue（给设备的事件）
        self.seen = {}                       # device -> 上次来等事件的时间

    # ---- 设备这边 ----

    def _queue(self, device: str) -> asyncio.Queue:
        q = self.queues.get(device)
        if q is None:
            q = self.queues[device] = asyncio.Queue()
        return q

    def ready(self, device: str) -> bool:
        return time.time() - self.seen.get(device, 0) < READY_SECONDS

    def busy(self, device: str) -> bool:
        return any(c.device == device and c.state != "ended" for c in self.calls.values())

    async def wait(self, device: str, timeout: float = 25) -> list:
        """设备长轮询：等到有事件（最多 timeout 秒），一次拿走全部。"""
        self.seen[device] = time.time()
        q = self._queue(device)
        events = []
        try:
            events.append(await asyncio.wait_for(q.get(), timeout))
        except asyncio.TimeoutError:
            pass
        while not q.empty():
            events.append(q.get_nowait())
        self.seen[device] = time.time()
        return events

    async def device_event(self, device: str, call_id: str, kind: str, sdp: str = None) -> bool:
        call = self.calls.get(call_id)
        if not call or call.device != device or call.state == "ended":
            return False
        if kind == "answer" and call.state == "ringing":
            if not sdp or len(sdp) > MAX_SDP:
                return False
            call.state = "connected"
            self._cancel_timer(call)
            await call.send({"type": "answer", "sdp": sdp})
        elif kind in ("decline", "busy", "hangup"):
            await self._end(call, {"decline": "declined", "busy": "busy", "hangup": "hangup"}[kind], notify_device=False)
        else:
            return False
        return True

    # ---- 手机这边 ----

    async def start(self, device: str, caller: str, login: str, offer: str, send) -> Call:
        if not offer or len(offer) > MAX_SDP:
            raise ValueError("连接描述不对")
        if not self.ready(device):
            raise LookupError("这台设备现在不在线，或者客户端版本太旧")
        if self.busy(device):
            raise ValueError("这台设备正在通话")
        call = Call(device, caller, login, offer, send)
        self.calls[call.id] = call
        await self._queue(device).put({"type": "ring", "id": call.id, "caller": caller, "sdp": offer})
        call.timer = asyncio.get_running_loop().call_later(
            RING_SECONDS, lambda: asyncio.ensure_future(self._end(call, "timeout")))
        return call

    async def phone_hangup(self, call_id: str):
        call = self.calls.get(call_id)
        if call and call.state != "ended":
            await self._end(call, "hangup", notify_phone=False)

    # ---- 结束 ----

    def _cancel_timer(self, call: Call):
        if call.timer:
            call.timer.cancel()
            call.timer = None

    async def _end(self, call: Call, reason: str, notify_phone=True, notify_device=True):
        if call.state == "ended":
            return
        missed = call.state == "ringing"
        call.state = "ended"
        self._cancel_timer(call)
        if notify_device:
            await self._queue(call.device).put({"type": "hangup", "id": call.id,
                                                "reason": "missed" if missed and reason in ("timeout", "hangup") else reason})
        if notify_phone:
            try:
                await call.send({"type": "ended", "reason": reason})
            except Exception:
                pass
        self.calls.pop(call.id, None)


def _decode(value: str) -> str:
    """Tailscale 对非 ASCII 的名字用 RFC 2047（=?utf-8?q?...?=）编码。"""
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def caller_of(headers) -> tuple:
    """Tailscale serve 转来的请求带登录身份：(显示名, 登录名)；没有返回 (None, None)。"""
    login = _decode((headers.get("Tailscale-User-Login") or "").strip())
    if not login:
        return None, None
    name = _decode((headers.get("Tailscale-User-Name") or "").strip()) or login.split("@")[0]
    return name[:20], login[:80]


_ts_cache = [0.0, None]


async def tailnet_url():
    """这台网关在 tailnet 里的 https 地址（tailscale status 里的 DNSName）；没装、没登录返回 None。60 秒缓存。"""
    import json
    import shutil
    if time.time() - _ts_cache[0] < 60:
        return _ts_cache[1]
    url = None
    if shutil.which("tailscale"):
        try:
            proc = await asyncio.create_subprocess_exec("tailscale", "status", "--json", stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
            name = ((json.loads(out or b"{}").get("Self") or {}).get("DNSName") or "").rstrip(".")
            url = f"https://{name}/call" if name else None
        except (OSError, ValueError, asyncio.TimeoutError):
            url = None
    _ts_cache[:] = [time.time(), url]
    return url
