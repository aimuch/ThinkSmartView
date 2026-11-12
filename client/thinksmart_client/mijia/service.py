"""Mijia on the device: QR login, the device page's data, polling, controls and the voice tools' lookups.

- The session (userId, ssecurity, serviceToken; never a passToken or password) lives in
  ~/.local/share/thinksmart-client/mijia/session.json, mode 0600. Expired: scan again.
- Devices are polled only while the page shows them (every 6 s), plus on demand for a voice query;
  the device list is reloaded every 20 polls. A write is followed by one refresh of that device.
- Every cloud call has a timeout and runs off the hub connection's path (the voice tools await
  them with their own cap), so a slow Mi cloud never stalls the conversation.
- Nothing secret is logged.
"""
import asyncio
import logging
import re
import time

import aiohttp

from .. import bg
from . import devices as dv
from .cloud import REGIONS, MiCloud, MiCloudError, QRLogin, normalize_region
from .policy import needs_confirm
from .spec import SpecResolver
from .store import DIR, read_json, remove, write_json

log = logging.getLogger(__name__)

POLL_VISIBLE = 6
POLL_HOME = 30          # 首页「常用设备」显示着、家居页没开：只查常用的那几个，30 秒一次
FAVORITES_MAX = 8
RELOAD_EVERY = 20
VOICE_TIMEOUT = 12
ALL_WORDS = ('全部', '所有', '全屋', '整个家', '都')


def masked(uid):
    uid = str(uid or '')
    return '*' * len(uid) if len(uid) <= 4 else f'{uid[:2]}****{uid[-2:]}'


def norm(text):
    return re.sub(r'[\s·,，。.!！?？的]', '', str(text or '')).lower()


class MijiaService:
    def __init__(self, publish, data_dir=DIR):
        self.publish = publish
        self.dir = data_dir
        self.http = None
        self.session = None
        self.settings = {}
        self.state = 'logged_out'      # logged_out, loading, ready, expired, error
        self.error = ''
        self.homes = []
        self.cards = {}                # id -> card, in display order
        self.login = {'state': 'idle'}
        self.visible = False
        self.home_visible = False      # 首页（常用设备磁贴）在屏幕上
        self.updated_at = 0
        self._login_task = None
        self._wake = asyncio.Event()
        self._load_lock = asyncio.Lock()
        self.specs = None

    # ---- lifecycle ----

    async def start(self):
        self.http = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        self.specs = SpecResolver(self.http, self.dir)
        self.settings = read_json(self.dir / 'settings.json') or {}
        self.session = read_json(self.dir / 'session.json')
        if self.session and self.session.get('service_token'):
            bg.spawn(self.reload())
        else:
            self.session = None
        bg.spawn(self._poll_loop())

    def _client(self):
        return MiCloud(self.http, self.session)

    # ---- view for the page ----

    def view(self):
        rooms, order = {}, []
        for c in self.cards.values():
            if c['room'] not in rooms:
                rooms[c['room']] = []
                order.append(c['room'])
            rooms[c['room']].append(self.card_view(c))
        s = self.session or {}
        login = {k: v for k, v in self.login.items() if k in ('state', 'login_url', 'deadline', 'region', 'error')}
        return {'state': self.state, 'error': self.error, 'login': login, 'regions': REGIONS,
                'account': {'user': masked(s.get('user_id')), 'region': s.get('region', 'cn'),
                            'expires_at': s.get('expires_at')} if s else None,
                'homes': [{'id': h['id'], 'name': h['name'], 'shared': h['shared']} for h in self.homes],
                'home_id': self.settings.get('home_id'), 'updated_at': self.updated_at,
                'rooms': [{'name': r or '未分配房间', 'cards': rooms[r]} for r in order],
                'favorites': [self.card_view(self.cards[i]) for i in self.favorites() if i in self.cards]}

    # ---- 常用设备（设计画布第 16 块；上游 client issue #1 网友建议的首页触控磁贴） ----

    def favorites(self):
        return [str(i) for i in (self.settings.get('favorites') or [])][:FAVORITES_MAX]

    def set_favorite(self, card_id, on):
        favs = [i for i in self.favorites() if i != card_id]
        if on:
            if card_id not in self.cards:
                raise ValueError('没有这个设备')
            if len(favs) >= FAVORITES_MAX:
                raise ValueError(f'首页最多放 {FAVORITES_MAX} 个常用设备')
            favs.append(card_id)
        self.settings['favorites'] = favs
        write_json(self.dir / 'settings.json', self.settings)
        self._changed()
        if on and self.home_visible:
            self._wake.set()
        return favs

    def card_view(self, c):
        f = c['feats']
        w = lambda k: k in f and dv.writable(f[k]) and not c['readonly']
        controls = {}
        if w('on') or (c['kind'] == 'cover' and (w('motor') or w('target_position'))):
            controls['power'] = True
        if w('brightness'):
            controls['brightness'] = True
        if w('color_temp'):
            controls['color_temp'] = f['color_temp'].get('range', [2700, 6500])[:2]
        if w('target_temp'):
            controls['target_temp'] = f['target_temp'].get('range', [16, 30, 1])
        if w('mode') and f['mode'].get('list'):
            controls['mode'] = [dv.zh(i['desc']) for i in f['mode']['list']]
        if w('fan_level'):
            fl = f['fan_level']
            controls['fan_level'] = [dv.zh(i['desc']) for i in fl['list']] if fl.get('list') else 'percent'
        if w('target_position'):
            controls['position'] = True
        if w('motor'):
            controls['motor'] = True
        if w('volume'):
            controls['volume'] = True
        return {'id': c['id'], 'name': c['name'], 'kind': c['kind'], 'online': c['online'],
                'summary': dv.summary(c), 'state': dv.state(c), 'controls': controls, 'readonly': c['readonly']}

    def _changed(self):
        self.publish('mijia', self.view())

    # ---- login ----

    async def login_start(self, region='cn'):
        await self.login_cancel()
        region = normalize_region(region)
        self.login = {'state': 'starting', 'region': region}
        self._changed()
        self._login_task = asyncio.create_task(self._login(region))

    async def login_cancel(self):
        t, self._login_task = self._login_task, None
        if t and not t.done():
            t.cancel()
        self.login = {'state': 'idle'}

    async def _login(self, region):
        qr = QRLogin(self.http, region)
        try:
            ticket = await qr.start()
            self.login = {'state': 'waiting', 'region': region, 'login_url': ticket['login_url'],
                          'deadline': ticket['deadline']}
            self._changed()
            session = await qr.wait(ticket)
        except asyncio.CancelledError:
            raise
        except MiCloudError as e:
            self.login = {'state': 'error', 'region': region, 'error': str(e)}
            self._changed()
            return
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.login = {'state': 'error', 'region': region, 'error': f'连不上小米账号服务（{e.__class__.__name__}）'}
            self._changed()
            return
        write_json(self.dir / 'session.json', session, private=True)
        self.session = session
        self.login = {'state': 'idle'}
        log.info('mijia: logged in (%s, %s)', masked(session['user_id']), region)
        await self.reload()

    async def logout(self):
        await self.login_cancel()
        remove(self.dir / 'session.json')
        self.session, self.homes, self.cards = None, [], {}
        self.state, self.error = 'logged_out', ''
        self._changed()

    # ---- loading ----

    async def set_home(self, home_id):
        self.settings['home_id'] = str(home_id)
        write_json(self.dir / 'settings.json', self.settings)
        await self.reload()

    async def reload(self):
        """Homes, devices and specs -> cards of the selected home, then one poll."""
        if not self.session:
            return
        async with self._load_lock:
            if not self.cards:
                self.state = 'loading'
                self._changed()
            try:
                cloud = self._client()
                self.homes = await cloud.homes()
                home = next((h for h in self.homes if h['id'] == self.settings.get('home_id')), None) \
                    or (self.homes[0] if self.homes else None)
                room_of = {}
                if home:
                    for r in home['rooms']:
                        for d in r['dids']:
                            room_of.setdefault(d, r['name'])
                records = await cloud.devices()
                have = {r['did'] for r in records}
                missing = [d for d in room_of if d not in have]
                if missing:
                    records += await cloud.devices_by_dids(missing)
                if home:
                    records = [r for r in records if r['did'] in room_of or r.get('split_parent') in room_of]
                specs = await self.specs.resolve_all(records)
                cards = {}
                for c in dv.build_home(records, specs, room_of):
                    c['shared'] = bool(home and home['shared'])
                    old = self.cards.get(c['id'])
                    if old:
                        c['values'] = old['values']
                    cards[c['id']] = c
                self.cards = cards
                self.state, self.error = 'ready', ''
            except MiCloudError as e:
                self._failed(e)
                return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                self.state, self.error = 'error', f'连不上米家云（{e.__class__.__name__}）'
                self._changed()
                return
        await self.poll()

    def _failed(self, e):
        if e.kind == 'expired':
            self.state, self.error = 'expired', '米家登录已过期，请重新扫码'
        else:
            self.state, self.error = 'error', str(e)
        log.warning('mijia: %s', e)
        self._changed()

    async def poll(self, cards=None):
        cards = [c for c in (cards or self.cards.values()) if c['online']]
        keys = [k for c in cards for k in dv.poll_keys(c)]
        if not self.session or not keys:
            self._changed()
            return
        try:
            values = await self._client().get_props(keys)
        except MiCloudError as e:
            self._failed(e)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return
        for c in cards:
            for name, f in c['feats'].items():
                v = values.get((c['did'], f['siid'], f['piid']))
                if v is not None:
                    c['values'][name] = v
        self.updated_at = time.time()
        self._changed()

    async def set_visible(self, on, home=None):
        self.visible = bool(on)
        if home is not None:
            self.home_visible = bool(home)
        if self.visible or self.home_visible:
            self._wake.set()

    async def _poll_loop(self):
        n = 0
        backoff = POLL_VISIBLE
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            favs = [self.cards[i] for i in self.favorites() if i in self.cards]
            if not self.visible and self.home_visible and favs and self.session and self.state == 'ready':
                try:
                    await self.poll(favs)      # 首页只显示常用的：只查它们
                except Exception:
                    log.exception('mijia poll favorites')
                backoff = POLL_HOME
                continue
            if not (self.visible and self.session and self.state in ('ready', 'error')):
                backoff = POLL_VISIBLE
                continue
            n += 1
            try:
                if self.state == 'error' or n % RELOAD_EVERY == 0:
                    await self.reload()
                else:
                    await self.poll()
                backoff = POLL_VISIBLE if self.state == 'ready' else min(backoff * 2, 60)
            except Exception:
                log.exception('mijia poll')
                backoff = 60

    # ---- control ----

    async def control(self, card_id, op, value=None, confirm=False, by_voice=False):
        """{'ok': True, 'text'} | {'ok': False, 'need_confirm': True, 'text'} | {'ok': False, 'text'}."""
        c = self.cards.get(card_id)
        if not c:
            return {'ok': False, 'text': '找不到这个设备'}
        if not self.session or self.state == 'expired':
            return {'ok': False, 'text': '米家还没登录或登录已过期，请在屏幕的「家居」页扫码登录'}
        if c['readonly']:
            return {'ok': False, 'text': f'{c["name"]}只能查看状态，不能远程控制'}
        if not c['online']:
            return {'ok': False, 'text': f'{c["name"]}离线了'}
        try:
            writes = dv.writes_for(c, op, value)
        except dv.ControlError as e:
            return {'ok': False, 'text': f'{c["name"]}：{e}'}
        reason = needs_confirm(c, writes, by_voice)
        if reason and not confirm:
            return {'ok': False, 'need_confirm': True, 'text': reason}
        try:
            await self._client().set_props(dv.raw_writes(c, writes))
        except MiCloudError as e:
            if e.kind == 'expired':
                self._failed(e)
            return {'ok': False, 'text': f'{c["name"]}：{e}'}
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return {'ok': False, 'text': '连不上米家云，稍后再试'}
        for k, v in writes:
            c['values'][k] = v
        self._changed()
        bg.spawn(self._refresh_soon(c))
        return {'ok': True, 'text': done_text(c, op, value)}

    async def _refresh_soon(self, card):
        await asyncio.sleep(0.8)
        await self.poll([card])

    # ---- lookups for the voice tools ----

    def find(self, query, kind=None, room=None):
        """Cards matching a spoken name ('书房台灯', '客厅的灯', '空调', '全部灯')."""
        q = norm(query)
        cards = [c for c in self.cards.values()
                 if (not kind or c['kind'] == kind) and (not room or norm(room) in norm(c['room']))]
        if not q:
            return cards
        exact = [c for c in cards if q in (norm(c['name']), norm(c['room'] + c['name']))]
        if exact:
            return exact
        named = [c for c in cards if norm(c['name']) and (norm(c['name']) in q or q in norm(c['name']))]
        rooms_in_q = [c for c in cards if c['room'] and norm(c['room']) in q]
        kinds_in_q = {k for k, words in dv.KIND_WORDS.items() if any(w in q for w in words)}
        if named:
            narrowed = [c for c in named if c in rooms_in_q] or named
            return narrowed
        pool = rooms_in_q if rooms_in_q else (cards if kinds_in_q else [])
        if kinds_in_q:
            pool = [c for c in pool if c['kind'] in kinds_in_q]
        return pool

    async def fresh(self, cards):
        """Re-read these cards before answering (bounded)."""
        try:
            await asyncio.wait_for(self.poll(cards), VOICE_TIMEOUT)
        except asyncio.TimeoutError:
            pass


def done_text(c, op, value):
    n = c['name']
    if op in ('on', 'off', 'toggle'):
        on = c['values'].get('on')
        return f'已{"打开" if on else "关闭"}{n}'
    return {'brightness': f'{n}亮度已调到{value}%', 'color_temp': f'{n}色温已调到{value}K',
            'target_temperature': f'{n}已设为{value}度', 'target_humidity': f'{n}湿度已设为{value}%',
            'mode': f'{n}已切换到{value}模式', 'fan_level': f'{n}风速已调到{value}',
            'position': f'{n}已开到{value}%', 'open': f'正在打开{n}', 'close': f'正在关闭{n}',
            'stop': f'{n}已停止', 'volume': f'{n}音量已调到{value}'}.get(op, f'{n}已设置好')
