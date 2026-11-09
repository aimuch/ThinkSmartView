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
    from thinksmart_client import offline

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
    assert v['playing'] is None and len(v['stations']) == 6 and {s['id'] for s in v['sounds']} == {'rain', 'ocean', 'fan', 'white'}
