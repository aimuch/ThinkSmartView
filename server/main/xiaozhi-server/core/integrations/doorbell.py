"""[xiaozhi-hub] 门口来人（设计画布第 86 块）：网关连着 Home Assistant 的 WebSocket 听 state_changed，选好的门铃、
人体、门磁一触发，就给每台开着 Home Assistant 插件的设备塞一条（设备长轮询 /kiosk/door/wait）；设备弹出门口
摄像头的画面（/kiosk/hass/camera，网关去拿 Home Assistant 的快照，令牌不出网关）。

设置在 plugins.home_assistant 里：door_triggers（实体，逗号分开）、door_camera（摄像头实体，选填）、
door_night（on：睡眠时段也弹）。不开门锁、不录像。
"""

import asyncio
import json
import re
import time
import uuid

import aiohttp

from core.integrations import common

WAIT = 25              # 设备一次长轮询最多等多久
SEEN = 120             # 多久没来问过的设备不再给它塞
REPEAT = 30            # 同一个触发 30 秒内再触发：只刷新时间，不重复响
RECONNECT_MAX = 300
FIRE_STATES = {"on", "open", "opening", "detected", "ringing", "home", "occupied", "unlocked"}
SHORT_NAME = {"event": "门铃按了", "button": "门铃按了", "input_button": "门铃按了"}
WATCH = None           # 网关里那一个（AdminHandler 建）：后台「测试」用它推一次


def door_settings(config: dict) -> dict:
    s = common.settings(config, "home_assistant")
    triggers = [t.strip() for t in re.split(r"[,，\s]+", s.get("door_triggers") or "") if t.strip()]
    return {"base_url": s["base_url"], "api_key": s["api_key"], "triggers": triggers,
            "camera": (s.get("door_camera") or "").strip(), "night": (s.get("door_night") or "off") == "on"}


def fired(entity: str, old: dict, new: dict) -> bool:
    """这次 state_changed 算不算「门口来人」。"""
    if not new:
        return False
    domain = entity.split(".", 1)[0]
    ns, os_ = new.get("state"), (old or {}).get("state")
    if ns in ("unavailable", "unknown", None):
        return False
    if domain in ("event", "button", "input_button"):
        # 状态是上次按的时间：变了就是又按了一下。没有旧状态（Home Assistant 刚启动、实体刚出现）不算
        return bool(old) and ns != os_
    return ns in FIRE_STATES and os_ != ns


def why(entity: str, new: dict) -> str:
    domain = entity.split(".", 1)[0]
    if domain in SHORT_NAME:
        return SHORT_NAME[domain]
    cls = ((new or {}).get("attributes") or {}).get("device_class") or ""
    if cls in ("door", "opening", "garage_door"):
        return "门开了"
    if cls in ("motion", "occupancy", "presence"):
        return "门口有人走动"
    return "门口有动静"


class DoorWatch:
    def __init__(self, config_fn, enabled_fn=None, now=time.time):
        """config_fn() → 合并后的配置；enabled_fn(config, device) → 这台开没开 Home Assistant 插件。"""
        self.config_fn = config_fn
        self.enabled_fn = enabled_fn or (lambda config, device: common.enabled_for(config, device, "home_assistant"))
        self.now = now
        self.queues = {}          # 设备 → asyncio.Queue
        self.seen = {}            # 设备 → 上次来问的时间
        self.last = {}            # 触发实体 → (时间, 事件 id)
        self.state = {"connected": False, "error": "", "since": 0, "events": 0}
        self._ws = None

    # ---- 设备这边
    def _queue(self, device: str) -> asyncio.Queue:
        if device not in self.queues:
            self.queues[device] = asyncio.Queue(maxsize=20)
        return self.queues[device]

    async def wait(self, device: str, timeout: float = WAIT) -> list:
        self.seen[device] = self.now()
        q = self._queue(device)
        try:
            first = await asyncio.wait_for(q.get(), timeout)
        except asyncio.TimeoutError:
            return []
        out = [first]
        while not q.empty():
            out.append(q.get_nowait())
        self.seen[device] = self.now()
        return out

    def fire(self, entity: str, name: str, reason: str, test: bool = False) -> dict:
        """给每台最近来问过、开着 Home Assistant 插件的设备塞一条。返回那条事件。"""
        config = self.config_fn()
        s = door_settings(config)
        now = self.now()
        prev = self.last.get(entity)
        repeat = bool(prev and now - prev[0] < REPEAT) and not test
        event_id = prev[1] if repeat else uuid.uuid4().hex[:12]
        self.last[entity] = (now if not repeat else prev[0], event_id)
        ev = {"type": "door", "id": event_id, "at": int(now), "entity": entity, "name": name, "why": reason,
              "camera": bool(s["camera"]), "night": s["night"], "repeat": repeat, "test": test}
        sent = 0
        for device, at in list(self.seen.items()):
            if now - at > SEEN:
                continue
            try:
                if not self.enabled_fn(config, device):
                    continue
            except Exception:  # noqa: BLE001
                continue
            q = self._queue(device)
            if q.full():
                q.get_nowait()
            q.put_nowait(ev)
            sent += 1
        ev["devices"] = sent
        self.state["events"] += 1
        return ev

    # ---- 摄像头
    async def snapshot(self, config: dict = None) -> bytes:
        s = door_settings(config or self.config_fn())
        if not s["camera"]:
            raise common.IntegrationError("还没选门口摄像头")
        headers = {"Authorization": f"Bearer {s['api_key']}"}
        url = f"{s['base_url']}/api/camera_proxy/{s['camera']}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8, connect=4)) as sess:
                async with sess.get(url, headers=headers) as r:
                    if r.status == 401:
                        raise common.IntegrationError("令牌不对（Home Assistant 回 401）")
                    if r.status == 404:
                        raise common.IntegrationError(f"Home Assistant 里没有 {s['camera']}")
                    if r.status >= 400:
                        raise common.IntegrationError(f"摄像头没给画面（{r.status}）")
                    body = await r.read()
                    if len(body) > 8 * 1024 * 1024:
                        raise common.IntegrationError("画面太大")
                    return body
        except asyncio.TimeoutError:
            raise common.IntegrationError("摄像头 8 秒没给画面")
        except aiohttp.ClientError as e:
            raise common.IntegrationError(f"连不上 Home Assistant：{type(e).__name__}")

    # ---- Home Assistant 这边
    async def run(self) -> None:
        """常驻：没填好就隔一会儿再看；填好了连 WebSocket，断了越等越久再连。设置改了就重连。"""
        delay = 5
        while True:
            try:
                config = self.config_fn()
                s = door_settings(config)
                if not s["triggers"] or not common.ready(config, "home_assistant"):
                    self.state.update(connected=False, error="")
                    await asyncio.sleep(30)
                    continue
                await self._listen(s)
                delay = 5
            except asyncio.CancelledError:
                raise
            except common.IntegrationError as e:
                self.state.update(connected=False, error=str(e))
            except Exception as e:  # noqa: BLE001
                self.state.update(connected=False, error=f"{type(e).__name__}")
            await asyncio.sleep(delay)
            delay = min(RECONNECT_MAX, delay * 2)

    async def _listen(self, s: dict) -> None:
        url = re.sub(r"^http", "ws", s["base_url"]) + "/api/websocket"
        sig = (s["base_url"], s["api_key"], tuple(s["triggers"]))
        triggers = set(s["triggers"])
        async with aiohttp.ClientSession() as sess:
            try:
                ws = await sess.ws_connect(url, heartbeat=30, timeout=10)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise common.IntegrationError(f"连不上 Home Assistant 的 WebSocket：{type(e).__name__}")
            self._ws = ws
            try:
                msg = await ws.receive_json(timeout=10)
                if msg.get("type") == "auth_required":
                    await ws.send_json({"type": "auth", "access_token": s["api_key"]})
                    msg = await ws.receive_json(timeout=10)
                if msg.get("type") != "auth_ok":
                    raise common.IntegrationError("令牌不对（Home Assistant 没让登录）")
                await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                self.state.update(connected=True, error="", since=int(self.now()))
                checked = time.monotonic()
                while True:
                    if time.monotonic() - checked > 60:     # 设置改了：重连
                        checked = time.monotonic()
                        n = door_settings(self.config_fn())
                        if (n["base_url"], n["api_key"], tuple(n["triggers"])) != sig:
                            return
                    try:
                        m = await ws.receive(timeout=60)
                    except asyncio.TimeoutError:
                        continue
                    if m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                        raise common.IntegrationError("Home Assistant 断开了")
                    if m.type != aiohttp.WSMsgType.TEXT:
                        continue
                    self.handle(json.loads(m.data), triggers)
            finally:
                self._ws = None
                self.state["connected"] = False
                await ws.close()

    def handle(self, msg: dict, triggers: set):
        if msg.get("type") != "event":
            return None
        data = (msg.get("event") or {}).get("data") or {}
        entity = data.get("entity_id") or ""
        if entity not in triggers:
            return None
        new, old = data.get("new_state") or {}, data.get("old_state") or {}
        if not fired(entity, old, new):
            return None
        name = (new.get("attributes") or {}).get("friendly_name") or entity
        return self.fire(entity, name, why(entity, new))


async def candidates(s: dict) -> dict:
    """后台「测试」用：Home Assistant 里能当门铃、人体、门磁、摄像头的实体。"""
    states = await common.request("GET", s["base_url"].rstrip("/") + "/api/states",
                                  headers={"Authorization": f"Bearer {s['api_key']}"}, timeout=8)
    if not isinstance(states, list):
        raise common.IntegrationError("Home Assistant 回的不是实体列表")
    trig, cams = [], []
    for st in states:
        eid = st.get("entity_id") or ""
        domain = eid.split(".", 1)[0]
        cls = (st.get("attributes") or {}).get("device_class") or ""
        if domain == "camera":
            cams.append(eid)
        elif (domain == "event" and cls in ("doorbell", "")) or (domain in ("button", "input_button") and "door" in eid):
            trig.append(eid)
        elif domain == "binary_sensor" and cls in ("motion", "occupancy", "presence", "door", "opening"):
            trig.append(eid)
    return {"triggers": sorted(trig), "cameras": sorted(cams), "all": {st.get("entity_id") for st in states}}
