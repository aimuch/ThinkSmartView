"""儿童模式 (kids.py), 做菜模式 (cook.py), 待机页 and the album.

Run like test_barge_in.py (same stubs).
"""
import asyncio
import datetime
import json
import sys
import types

import numpy as np
import pytest

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))
if 'thinksmart_client.opus' not in sys.modules:
    opus = types.ModuleType('thinksmart_client.opus')
    opus.Encoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'')
    opus.Decoder = lambda *a: types.SimpleNamespace(decode=lambda p: np.zeros(0, np.float32))
    sys.modules['thinksmart_client.opus'] = opus
try:
    import thinksmart_client.speex_aec  # noqa: F401  (needs libspeexdsp)
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)

from thinksmart_client import app, config, cook, kids, mcp  # noqa: E402

D = datetime.datetime


def test_kids_limits():
    s = kids.normalize(None, {'enabled': True, 'daily_minutes': 30, 'bedtime': '20:30'})
    assert kids.blocked(s, 0, D(2026, 11, 9, 10)) is None
    assert kids.blocked(s, 30 * 60, D(2026, 11, 9, 10)) == 'limit'
    assert kids.blocked(s, 0, D(2026, 11, 9, 21)) == 'bedtime'
    assert kids.blocked(s, 0, D(2026, 11, 10, 6, 0)) == 'bedtime'
    assert kids.blocked(s, 0, D(2026, 11, 10, 6, 30)) is None
    assert kids.blocked({**s, 'daily_minutes': 0, 'bedtime': ''}, 10 ** 6, D(2026, 11, 9, 23)) is None
    assert kids.blocked({**s, 'enabled': False}, 10 ** 6, D(2026, 11, 9, 23)) is None
    with pytest.raises(ValueError):
        kids.normalize(s, {'age': 0})
    with pytest.raises(ValueError):
        kids.normalize(s, {'bedtime': '25:00'})
    assert kids.shift_time('23:45', 30) == '00:15'


class FakeKws:
    def __init__(self, model, keywords, *a):
        self.keywords = keywords

    def feed(self, block):
        return None


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    monkeypatch.setattr(kids, 'USAGE', tmp_path / 'kids.json')
    monkeypatch.setattr(app, 'Kws', FakeKws)
    monkeypatch.setattr(app, 'ALBUM', tmp_path / 'album')
    monkeypatch.setattr(app, 'HUB_ALBUM', tmp_path / 'album-hub')
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.cfg = {**config.DEFAULTS, 'device_id': 'dev1', 'wake_words': list(config.DEFAULTS['wake_words'])}
    c.kws = FakeKws('', c.cfg['wake_words'])
    c.verifier = None
    c.wake_lock = asyncio.Lock()
    c.screen = types.SimpleNamespace(poke=lambda: None)
    c.played = []
    c.play = c.played.append
    pushed = []
    c.audio = types.SimpleNamespace(player=types.SimpleNamespace(push=pushed.append, trim=lambda n: None))
    c.pushed = pushed
    c.said = []

    async def speech(text):
        c.said.append(text)
        return None
    c._speech = speech
    c.hub_calls = []

    async def hub_http(path, body=None):
        c.hub_calls.append((path, body))
        return {'ok': True}
    c._hub_http = hub_http
    c.hub = types.SimpleNamespace(connected=False)
    from thinksmart_client import hub_album
    c.hub_album = hub_album.HubAlbum(tmp_path / 'album-hub', lambda: 'http://hub', 'dev1', tmp_path / 'album')
    c.album_now, c.album_want = None, 0
    c.in_turn = lambda: False
    c.kids = kids.normalize(None)
    c.kids_seconds = 0.0
    c.kids_clips = {}
    c.cards = []
    c.alarms_made = []

    async def create_alarm(kind, **kw):
        c.alarms_made.append((kind, kw))
    c.create_alarm = create_alarm
    return c


def test_kids_setting_and_refusing_wakes(tmp_path, monkeypatch):
    import datetime as real_dt
    import types

    class Afternoon(real_dt.datetime):   # 固定在下午：晚上八点以后跑，会撞上「几点后不陪聊」
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 9, 15, 0)

    fake = types.ModuleType('datetime'); fake.__dict__.update(real_dt.__dict__); fake.datetime = Afternoon
    monkeypatch.setattr(kids, 'datetime', fake)
    c = make_client(tmp_path, monkeypatch)

    async def run():
        v = await c.set_kids(enabled=True, age=4, minutes_step=-10)
        assert v['enabled'] and v['age'] == 4 and v['daily_minutes'] == 20
        assert c.hub_calls[-1] == ('/kiosk/prefs', {'device': 'dev1', 'kids': c.kids})
        assert json.loads(config.PATH.read_text())['kids']['age'] == 4
        v = await c.set_kids(bedtime_shift=-30)
        assert v['bedtime'] == '20:00'
        assert not await c.kids_refuse()
        c.kids_seconds = 20 * 60
        assert await c.kids_refuse() and c.said[-1] == kids.SAY_LIMIT and c.played == ['miss']
    asyncio.run(run())


def test_voice_tool_can_only_turn_kids_mode_on(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    r = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call',
                                'params': {'name': 'self.kids_mode.get', 'arguments': {'enable': True}}}, client=c))
    out = json.loads(r['result']['content'][0]['text'])
    assert out['enabled'] is True and '设置页' in out['note']
    names = [t['name'] for t in asyncio.run(mcp.handle({'id': 1, 'method': 'tools/list'}))['result']['tools']]
    assert 'self.kids_mode.get' in names and 'self.cooking.start' in names and len(names) == len(set(names))


RECIPE = '## 番茄炒蛋\n1. 鸡蛋打散加盐\n2. 炒到七八分熟盛出\n3. 番茄下锅，中火炒 2 分钟\n4. 倒回鸡蛋'


def test_cooking_mode(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.cards = [{'id': 'c1', 'role': 'card', 'text': '番茄炒蛋', 'card': {'markdown': RECIPE}}]

    async def run():
        v = await c.cook_start('c1')
        assert v['on'] and v['i'] == 0 and len(v['steps']) == 4 and c.said[-1] == '第 1 步，鸡蛋打散加盐'
        assert c.kws.keywords[-len(cook.WORDS):][0].endswith('@下一步')    # commands added to the wake word model
        await c.voice_wake('下一步', None)
        await c.voice_wake('下一步', None)
        assert c.cook_view()['i'] == 2 and c.cook_view()['minutes'] == 2
        await c.voice_wake('开始计时', None)
        assert c.alarms_made[-1] == ('timer', {'label': '番茄炒蛋 第 3 步', 'seconds': 120})
        await c.voice_wake('再说一遍', None)
        assert c.said[-1].startswith('第 3 步')
        await c.cook_go(to=3)
        await c.cook_go(1)
        assert '最后一步' in c.said[-1]
        await c.voice_wake('退出做菜', None)
        assert c.cooking is None and c.kws.keywords == c.cfg['wake_words']
        with pytest.raises(ValueError):
            await c.cook_start('nope')
    asyncio.run(run())


def test_cooking_tool_uses_the_newest_recipe(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.cards = [{'id': 'c1', 'role': 'card', 'text': '番茄炒蛋', 'card': {'markdown': RECIPE}},
               {'id': 'c2', 'role': 'card', 'text': '购物清单', 'card': {'markdown': '- 酱油\n- 醋'}}]
    r = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'self.cooking.start', 'arguments': {}}}, client=c))
    out = json.loads(r['result']['content'][0]['text'])
    assert out['action'] == 'RESPONSE' and '番茄炒蛋' in out['response'] and '第 1 步，鸡蛋打散加盐' in out['response']
    assert c.said == []   # the hub says the first step in its reply


def test_standby_setting_and_album(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)

    async def run():
        v = await c.set_standby(minutes=5, album=False)
        assert v['minutes'] == 5 and v['album'] is False and v['photos'] == []
        with pytest.raises(ValueError):
            await c.set_standby(minutes=3)
        app.ALBUM.mkdir()
        (app.ALBUM / '1-0.jpg').write_bytes(b'\xff\xd8x')
        assert c.standby_view()['photos'] == ['/api/album/1-0.jpg']
        v = await c.album_delete('1-0.jpg')
        assert v['photos'] == []
        with pytest.raises(ValueError):
            await c.album_delete('../config.json')
        await c.set_standby(minutes=2, album=True)
        assert 'standby' not in json.loads(config.PATH.read_text())     # back to the default
    asyncio.run(run())


def test_cooking_and_offline_keep_each_others_words(tmp_path, monkeypatch):
    """做菜模式和断网口令可能同时开着：重建唤醒词模型时两边的词都要在（以前各自重建，会把对方的丢掉）。"""

    c = make_client(tmp_path, monkeypatch)
    c.cards = [{'id': 'c1', 'role': 'card', 'text': '番茄炒蛋', 'card': {'markdown': RECIPE}}]
    c.offline_until = 0.0
    c._play_clips = lambda names: None
    words = lambda: ' '.join(c.kws.keywords)

    async def run():
        await c.cook_start('c1', read=False)
        c.events.publish = lambda *a: None
        await c.offline_start()
        assert '@下一步' in words() and '@音量大一点' in words()      # 断网时还能「下一步」
        await c.cook_stop()
        assert '@下一步' not in words() and '@音量大一点' in words()  # 退出做菜不丢断网口令
        await c.cook_start('c1', read=False)
        assert '@下一步' in words() and '@音量大一点' in words()
        c.offline_until = 0.0                                         # 断网窗口结束：收尾任务每 0.5 秒看一次
        for _ in range(60):
            if '@音量大一点' not in words():
                break
            await asyncio.sleep(0.1)
        assert '@下一步' in words() and '@音量大一点' not in words()
    asyncio.run(run())


def test_media_card_lists_stations_before_anything_played(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.media = None
    v = c.media_view()
    assert v['playing'] is None and len(v['stations']) == 6 and {s['id'] for s in v['sounds']} == {'rain', 'ocean', 'fan', 'white', 'birds'}


def test_family_kids_sync_learn_and_reports(tmp_path, monkeypatch):
    """孩子和学习（设计画布第 61 块）：网关那份比上次用的新才照着改；设备改的记下网关的时间；学一学用网关的内容；报记录。"""
    c = make_client(tmp_path, monkeypatch)
    c.publish_kids = lambda: None
    c.say = lambda text: c.said.append(text) or asyncio.sleep(0)
    gw = {'enabled': True, 'age': 6, 'daily_minutes': 45, 'bedtime': '21:00', 'voice': 'child', 'updated': 100.0}

    async def hub_http(path, body=None):
        c.hub_calls.append((path, body))
        if path.startswith('/kiosk/kids/learn'):
            return {'ok': True, 'text': '和6岁的孩子玩认字卡：按顺序从这些字里出：人、口、手。'}
        if path.startswith('/kiosk/kids'):
            return {'ok': True, 'kids': dict(gw)}
        if path == '/kiosk/prefs':
            return {'ok': True, 'kids': {**body['kids'], 'updated': 200.0}}
        return {'ok': True}
    c._hub_http = hub_http

    async def run():
        assert await c.refresh_kids() is True
        assert c.kids['enabled'] and c.kids['daily_minutes'] == 45 and c.kids['bedtime'] == '21:00' and c.cfg['kids_synced'] == 100.0
        assert await c.refresh_kids() is False                                  # 没有更新的
        await c.set_kids(daily_minutes=20)                                      # 设备上改：网关记成最新的
        assert c.cfg['kids_synced'] == 200.0
        gw['daily_minutes'] = 90
        assert await c.refresh_kids() is False and c.kids['daily_minutes'] == 20     # 网关那份旧：不听它的
        gw['updated'] = 300.0
        assert await c.refresh_kids() is True and c.kids['daily_minutes'] == 90
        await c.learn('chars')
        assert c.said[-1].startswith('【学习·认字卡】和6岁的孩子玩认字卡：按顺序从这些字里出：人、口、手')
        await asyncio.sleep(0)
        assert any(p == '/kiosk/kids/report' and b['kind'] == 'learn' and b['learn'] == 'chars' for p, b in c.hub_calls)
    asyncio.run(run())
    assert json.loads(config.PATH.read_text())['kids_synced'] == 300.0


def test_standby_uses_the_hub_album(tmp_path, monkeypatch):
    """家庭相册在网关上（设计画布第 66 块）：同步下来的照片先放，那年今日排前面；设置谁新听谁的；「别放这张了」。"""
    from thinksmart_client import hub_album
    c = make_client(tmp_path, monkeypatch)
    hub = {'settings': {'source': 'hub', 'albums': None, 'style': 'photo', 'interval': 30, 'order': 'shuffle', 'night': 'clock', 'updated': 0},
           'photos': [{'id': 'a' * 12, 'taken': 1, 'w': 1280, 'h': 960, 'otd': False, 'albums': ['旅行']},
                      {'id': 'b' * 12, 'taken': 2, 'w': 960, 'h': 1280, 'otd': True, 'albums': ['孩子']}],
           'albums': [{'id': 'a1', 'name': '孩子'}], 'collect': False, 'total': 2, 'ok': True}
    posted = []

    async def fake_get(self, s, path):
        if path.startswith('/kiosk/album?'):
            return json.dumps(hub).encode()
        return b'\xff\xd8jpeg'

    class FakeResp:
        status = 200
        async def read(self): return b'{}'
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class FakeSession:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def post(self, url, json=None, data=None):
            posted.append((url, json))
            return FakeResp()

    monkeypatch.setattr(hub_album.HubAlbum, '_get', fake_get)
    monkeypatch.setattr(hub_album.aiohttp, 'ClientSession', FakeSession)

    async def run():
        assert c.standby_view()['show'] == []
        newer = await c.hub_album.sync(c.standby_settings())
        assert newer is None                          # 网关的没本机新
        v = c.standby_view()
        assert [x['id'] for x in v['show']] == ['b' * 12, 'a' * 12]    # 那年今日排前面
        assert v['show'][0]['p'] is True and v['show'][0]['u'] == f"/api/album/hub/{'b' * 12}.jpg"
        assert v['hub']['albums'] == [{'id': 'a1', 'name': '孩子'}] and v['hub']['count'] == 2
        # 本机改了：推给网关；网关后来又改了：下次同步取回来
        await c.set_standby(style='corner', interval=10)
        await asyncio.sleep(0)
        assert c.standby_settings()['updated'] > 0
        hub['settings'] = {**hub['settings'], 'style': 'clock', 'updated': c.standby_settings()['updated'] + 5}
        newer = await c.hub_album.sync(c.standby_settings())
        assert newer['style'] == 'clock'
        # 「这张是哪年的」「别放这张了」
        c.album_now_set('b' * 12)
        assert c.album_info()['success'] is True
        r = await c.album_hide_now()
        assert r['success'] and any(u.endswith('/kiosk/album/hide') for u, _ in posted)
        assert [x['id'] for x in c.standby_view()['show']] == ['a' * 12]
        c.album_now_set('../x')
        assert c.album_info()['success'] is False
        await c.set_standby(source='none')
        assert c.standby_view()['show'] == [] and c.standby_view()['album'] is False
    asyncio.run(run())
