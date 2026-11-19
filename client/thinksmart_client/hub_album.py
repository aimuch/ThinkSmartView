"""网关上的家庭相册（设计画布第 66 块）：待机页从这里挑着放。

每 10 分钟（和改了设置时）问一次网关 /kiosk/album：这台设备的待机页设置、该放的照片（最多 300 张）。
照片缓存在 album-hub/<id>.jpg（长边 1280，每张约 200KB），网关连不上时照样放缓存里的。
设置两边都能改：带 updated（改的时间），谁新听谁的。后台点了「收进相册」时，把本机原来的照片传上去。
"""

import asyncio
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path

import aiohttp

from .fileio import write_atomic

log = logging.getLogger(__name__)

KEYS = ('source', 'albums', 'style', 'interval', 'order', 'night')
SOURCES = ('hub', 'local', 'none')
STYLES = ('corner', 'photo', 'clock')
ORDERS = ('shuffle', 'time')
NIGHTS = ('clock', 'play')
INTERVALS = (10, 15, 30, 60)
SYNC_EVERY = 600
ID = re.compile(r'[0-9a-f]{12}')


def clean(body):
    """设备页、网关给的设置里认得的那几项。"""
    out = {}
    if body.get('source') in SOURCES:
        out['source'] = body['source']
    if 'albums' in body:
        a = body['albums']
        out['albums'] = None if a is None else [str(x) for x in a if isinstance(x, str)][:50] or None
    if body.get('style') in STYLES:
        out['style'] = body['style']
    if body.get('order') in ORDERS:
        out['order'] = body['order']
    if body.get('night') in NIGHTS:
        out['night'] = body['night']
    try:
        if int(body.get('interval', 0)) in INTERVALS:
            out['interval'] = int(body['interval'])
    except (TypeError, ValueError):
        pass
    return out


class HubAlbum:
    def __init__(self, folder, hub_url, device_id, local_dir, on_change=None):
        self.dir = Path(folder)
        self.hub_url = hub_url          # 返回网关地址的函数（设置里会改）
        self.device_id = device_id
        self.local_dir = Path(local_dir)
        self.on_change = on_change or (lambda: None)
        self.photos = []                # [{id, taken, w, h, otd, albums}]
        self.albums = []                # [{id, name}]
        self.total = 0
        self.synced = 0
        self.kick = asyncio.Event()
        self._load()

    def _load(self):
        try:
            d = json.loads((self.dir / 'list.json').read_text(encoding='utf-8'))
            self.photos = [p for p in d.get('photos') or [] if ID.fullmatch(str(p.get('id', '')))]
            self.albums = d.get('albums') or []
            self.total = d.get('total') or 0
        except (OSError, ValueError, AttributeError):
            pass

    def _save(self):
        write_atomic(self.dir / 'list.json', json.dumps({'photos': self.photos, 'albums': self.albums, 'total': self.total}, ensure_ascii=False))

    def cached(self):
        """缓存好了的照片（文件在的）。"""
        return [p for p in self.photos if (self.dir / f"{p['id']}.jpg").is_file()]

    def show(self):
        """待机页轮播用的：[{u, id, otd, taken, p（竖照）, a（第一个相册名）}]。"""
        return [{'u': f"/api/album/hub/{p['id']}.jpg", 'id': p['id'], 'otd': bool(p.get('otd')), 'taken': p.get('taken'),
                 'p': bool(p.get('h') and p.get('w') and p['h'] > p['w']), 'a': (p.get('albums') or [''])[0]} for p in self.cached()]

    def path(self, name):
        if not re.fullmatch(r'[0-9a-f]{12}\.jpg', name or ''):
            return None
        p = self.dir / name
        return p if p.is_file() else None

    def info(self, pid):
        return next((p for p in self.photos if p['id'] == pid), None)

    async def _get(self, s, path):
        async with s.get(self.hub_url() + path) as r:
            if r.status != 200:
                raise RuntimeError(f'{path}: HTTP {r.status}')
            return await r.read()

    async def sync(self, local_settings):
        """问一次网关。返回网关上更新过的设置（比本机新时），没有就 None；本机更新的推给网关。"""
        local = sorted(self.local_dir.glob('*.jpg')) if self.local_dir.is_dir() else []
        q = urllib.parse.urlencode({'device': self.device_id, 'local': len(local)})
        newer = None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            data = json.loads(await self._get(s, f'/kiosk/album?{q}'))
            if not data.get('ok'):
                raise RuntimeError(data.get('error') or 'not ok')
            hub = data.get('settings') or {}
            mine = int(local_settings.get('updated') or 0)
            if int(hub.get('updated') or 0) > mine:
                newer = {**clean(hub), 'updated': int(hub['updated'])}
            elif mine > int(hub.get('updated') or 0):
                body = {'device': self.device_id, **clean(local_settings), 'updated': mine}
                async with s.post(self.hub_url() + '/kiosk/album/settings', json=body) as r:
                    await r.read()
            self.photos = [p for p in data.get('photos') or [] if ID.fullmatch(str(p.get('id', '')))]
            self.albums = [{'id': a.get('id'), 'name': a.get('name')} for a in data.get('albums') or []]
            self.total = data.get('total') or 0
            self._save()
            want = {p['id'] for p in self.photos}
            for f in self.dir.glob('*.jpg'):
                if f.stem not in want:
                    f.unlink(missing_ok=True)
            got = 0
            for p in self.photos:
                dst = self.dir / f"{p['id']}.jpg"
                if dst.is_file():
                    continue
                try:
                    b = await self._get(s, f"/kiosk/album/photo/{p['id']}")
                except Exception as e:
                    log.info('album photo %s: %s', p['id'], e)
                    continue
                tmp = dst.with_suffix('.part')
                tmp.write_bytes(b)
                tmp.replace(dst)
                got += 1
                if got % 20 == 0:
                    self.on_change()
            if data.get('collect') and local:
                n = 0
                for f in local:
                    q = urllib.parse.urlencode({'device': self.device_id, 'name': f.name})
                    try:
                        async with s.post(self.hub_url() + f'/kiosk/album/upload?{q}', data=f.read_bytes()) as r:
                            n += r.status == 200
                    except Exception as e:
                        log.info('album upload %s: %s', f.name, e)
                async with s.post(self.hub_url() + '/kiosk/album/collected', json={'device': self.device_id}) as r:
                    await r.read()
                log.info('album: sent %d of %d local photos to the hub', n, len(local))
                self.kick.set()     # 传上去的照片下一轮就能下回来
        self.synced = time.time()
        if got:
            log.info('album: %d new photos from the hub (%d in all)', got, len(self.photos))
        return newer

    async def hide(self, pid):
        """「别放这张了」：告诉网关，这台以后跳过；缓存里也删掉。"""
        self.photos = [p for p in self.photos if p['id'] != pid]
        (self.dir / f'{pid}.jpg').unlink(missing_ok=True)
        self._save()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(self.hub_url() + '/kiosk/album/hide', json={'device': self.device_id, 'id': pid}) as r:
                await r.read()

    async def push(self, settings):
        """本机改了设置，马上推给网关（连不上就等下次同步）。"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                body = {'device': self.device_id, **clean(settings), 'updated': int(settings.get('updated') or time.time())}
                async with s.post(self.hub_url() + '/kiosk/album/settings', json=body) as r:
                    await r.read()
        except Exception as e:
            log.info('album settings to the hub: %s', e)
        self.kick.set()
