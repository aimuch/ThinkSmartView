"""门口来人（设计画布第 86 块）：网关连着 Home Assistant 听门铃、人体、门磁，触发了就从长轮询 /kiosk/door/wait
送来一条；这里记下现在弹着的那个（60 秒后自己收起），页面画门口摄像头的画面（/api/door/camera → 网关
/kiosk/hass/camera）。响不响、亮不亮屏由 app.Client.door_event 按静音、勿扰、睡眠时段定。
"""
import asyncio
import logging
import time

import aiohttp

from . import bg

log = logging.getLogger(__name__)

SHOW = 60          # 弹出来多久自己收起
MORE = 60          # 「再看一分钟」
LONGEST = 300      # 最多连着开 5 分钟（摄像头一直被拉快照）


class Door:
    def __init__(self, hub_http, device_id, publish, on_event=None, now=time.time):
        self.hub_http = hub_http
        self.device_id = device_id
        self.publish = publish
        self.on_event = on_event
        self.now = now
        self.current = None        # {"id", "why", "name", "at", "camera", "until", "opened", "asked", "test"}
        self.task = None
        self._closer = None

    def view(self):
        c = self.current
        if c and self.now() >= c['until']:
            self.current = c = None
        return dict(c) if c else None

    def _changed(self):
        self.publish('door', self.view() or {'open': False})

    def _schedule_close(self):
        if self._closer and not self._closer.done():
            self._closer.cancel()

        async def close_later(event_id, at):
            await asyncio.sleep(max(0.5, at - self.now()))
            if self.current and self.current['id'] == event_id and self.now() >= self.current['until'] - 0.2:
                self.current = None
                self._changed()
        if self.current:
            self._closer = bg.spawn(close_later(self.current['id'], self.current['until']), name='door-close')

    def show(self, event, asked=False):
        """弹出来（或者同一个触发又来了：刷新时间、多留一会儿）。返回是不是新弹的。"""
        now = self.now()
        c = self.current if self.current and self.now() < self.current['until'] else None
        fresh = not (c and c['id'] == event.get('id'))
        self.current = {
            'open': True, 'id': event.get('id') or f'ask-{int(now)}', 'why': event.get('why') or '门口',
            'name': event.get('name') or '', 'at': int(event.get('at') or now), 'camera': bool(event.get('camera')),
            'asked': asked, 'test': bool(event.get('test')),
            'opened': c['opened'] if c and not fresh else now,
            'until': max(c['until'] if c and not fresh else 0, now + SHOW),
        }
        self._changed()
        self._schedule_close()
        return fresh

    def close(self):
        self.current = None
        self._changed()

    def more(self):
        if not self.current:
            raise ValueError('门口的画面已经收起了')
        now = self.now()
        self.current['until'] = min(self.current['opened'] + LONGEST, max(self.current['until'], now) + MORE)
        if self.current['until'] <= now + 1:
            raise ValueError('已经开了 5 分钟了，先收起')
        self._changed()
        self._schedule_close()

    # ---- 等网关送来
    def start(self):
        if not self.task or self.task.done():
            self.task = bg.spawn(self._run(), name='door')

    async def _run(self):
        delay = 2
        while True:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as s:
                    async with s.get(f'{self.hub_http}/kiosk/door/wait', params={'device': self.device_id}) as r:
                        if r.status == 404:            # 网关还没有门口来人：过一会儿再问
                            await asyncio.sleep(300)
                            continue
                        data = await r.json(content_type=None)
                for e in data.get('events') or []:
                    if e.get('type') == 'door' and self.on_event:
                        await self.on_event(e)
                delay = 2
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug('door wait: %s', e)
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)

    async def info(self):
        """「看看门口」之前问网关：这台开没开 Home Assistant、有没有门口摄像头。"""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(f'{self.hub_http}/kiosk/door', params={'device': self.device_id}) as r:
                if r.status == 404:
                    return {'available': False}
                return await r.json(content_type=None)

    async def camera(self):
        """门口摄像头的一张快照（JPEG 字节）；拿不到抛 ValueError（一句为什么）。"""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f'{self.hub_http}/kiosk/hass/camera', params={'device': self.device_id}) as r:
                if r.status != 200:
                    try:
                        why = (await r.json(content_type=None)).get('error')
                    except Exception:
                        why = None
                    raise ValueError(why or f'网关回 {r.status}')
                return await r.read()
