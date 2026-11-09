"""Weather broadcasts and the home address (broadcast.py, Client.set_broadcast / set_home / _broadcast_loop).

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

from thinksmart_client import alarms, app, broadcast as bc, config, mcp, wake_verify  # noqa: E402

D = datetime.datetime
HOME = {'name': '上海市青浦区', 'latitude': 31.15, 'longitude': 121.12}


def test_normalize_and_shift():
    s = bc.normalize(None)
    assert s == {'enabled': False, 'times': ['07:30'], 'days': 'daily', 'reminders': True, 'news': False}
    s = bc.normalize(s, {'enabled': True, 'add_time': '21:00'})
    s = bc.normalize(s, {'add_time': '6:45'})
    assert s['times'] == ['06:45', '07:30', '21:00'] and s['enabled']
    assert bc.normalize(s, {'remove_time': '07:30'})['times'] == ['06:45', '21:00']
    s = bc.normalize(s, {'add_time': '12：00'})   # a full-width colon
    with pytest.raises(ValueError, match='最多'):
        bc.normalize(s, {'add_time': '22:00'})
    with pytest.raises(ValueError, match='没有'):
        bc.normalize(s, {'remove_time': '08:00'})
    with pytest.raises(ValueError, match='HH:MM'):
        bc.normalize(s, {'add_time': '25:00'})
    with pytest.raises(ValueError, match='哪几天'):
        bc.normalize(s, {'days': 'monday'})
    assert bc.shift('23:50', 15) == '00:05' and bc.shift('00:10', -15) == '23:55'
    assert bc.about('07:30') == 'today' and bc.about('13:59') == 'today' and bc.about('21:00') == 'tomorrow'


def test_due_days_window_and_fired():
    s = bc.normalize(None, {'enabled': True, 'times': ['07:30', '21:00'], 'days': 'workdays'})
    monday = D(2026, 11, 9, 7, 30, 4)
    assert [t for t, _, _ in bc.due(s, monday, set())] == ['07:30']
    assert bc.due(s, monday, {'2026-11-09 07:30'}) == []
    assert bc.due(s, D(2026, 11, 9, 7, 29, 59), set()) == []
    assert bc.due(s, D(2026, 11, 9, 8, 0, 0), set()) == []          # 30 min later: given up
    assert bc.due(s, D(2026, 11, 8, 7, 31), set()) == []            # Sunday
    assert [t for t, _, _ in bc.due({**s, 'days': 'weekend'}, D(2026, 11, 8, 21, 5), set())] == ['21:00']
    assert bc.due({**s, 'enabled': False}, monday, set()) == []


def test_request_text_and_tag():
    s = bc.normalize(None, {'enabled': True, 'reminders': True, 'news': True})
    morning = bc.request_text('07:30', s, '上海市青浦区', ['08:30 给宝宝带水杯'])
    assert morning.startswith('【早安播报 07:30】早上好，先调用查天气工具查最新的天气')
    assert '08:30 给宝宝带水杯' in morning and '新闻' in morning
    assert '今天没有闹钟和提醒' in bc.request_text('07:30', s, None, [])
    noon = bc.request_text('12:30', s, '青浦区', ['13:00 开会'])
    assert noon.startswith('【天气播报 12:30】') and '开会' not in noon and '新闻' not in noon
    evening = bc.request_text('21:00', s, '青浦区')
    assert evening.startswith('【天气播报 21:00】') and '明天的天气' in evening
    assert bc.tag_of(morning) == '早安播报 07:30' and bc.tag_of(evening) == '天气播报 21:00'
    assert bc.tag_of(morning[1:]) == '早安播报 07:30'   # as the hub echoes it back
    assert bc.tag_of('【重要】明天开会') is None and bc.tag_of('明天下雨吗') is None
    assert '工作日' in bc.describe({**s, 'days': 'workdays'}) and bc.describe(bc.normalize(None)) == '天气播报关着'


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.cfg = {**config.DEFAULTS, 'device_id': 'dev1'}
    c.broadcast = bc.normalize(None)
    c.broadcast_fired = set()
    c.home = None
    c.rec_seconds = 4.0
    c.wake_rec, c.wake_rec_t = None, 0.0
    c.uploading = c.vad_ended = False
    c.state = 'idle'
    c.ring = c.preview = None
    c.backlog = None
    c.in_turn = lambda: False
    c.keys = types.SimpleNamespace(mic_on=True)
    c.screen = types.SimpleNamespace(poke=lambda: None)
    c.kws = types.SimpleNamespace(feed=lambda b: None)
    c.played = []
    c.play = c.played.append
    c.alarms = alarms.Alarms(tmp_path / 'alarms')
    c.alarms.load()
    c.clock_ok = True
    c.said = []

    async def say(text):
        c.said.append(text)
    c.say = say
    c.hub_calls = []

    async def hub_http(path, body=None):
        c.hub_calls.append(path)
        if 'q=%E9%9D%92%E5%9F%94' in path:   # 青埔
            return {'ok': False, 'error': '没找到「青埔」这个地方'}
        return {'ok': True, 'place': {'name': '浙江省杭州市西湖区', 'latitude': 30.26, 'longitude': 120.13}}
    c._hub_http = hub_http
    c.synced = []

    async def sync_home():
        c.synced.append(c.home)
        return True
    c._sync_home = sync_home
    return c


def test_settings_change_and_persist(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)

    async def run():
        view = await c.set_broadcast(enabled=True, add_time='21:00', news=True)
        assert view['times'] == ['07:30', '21:00'] and view['about'] == {'07:30': 'today', '21:00': 'tomorrow'}
        view = await c.set_broadcast(shift={'time': '07:30', 'minutes': -15})
        assert view['times'] == ['07:15', '21:00']
        with pytest.raises(ValueError, match='已经有'):
            await c.set_broadcast(shift={'time': '07:15', 'minutes': 13 * 60 + 45})
        saved = json.loads(config.PATH.read_text())['broadcast']
        assert saved['times'] == ['07:15', '21:00'] and saved['news'] and saved['enabled']
    asyncio.run(run())


def test_home_address_by_voice_recording(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(wake_verify.HubChecker, 'text', lambda self, audio: '杭州西湖区。')

    async def run():
        await c.home_record_start()
        assert c.rec_seconds == 6.0
        tone = (0.1 * np.sin(np.arange(320) / 3)).astype(np.float32)
        for _ in range(400):
            c._on_block(tone, tone)
        assert sum(len(b) for b in c.wake_rec) == 6 * 16000
        found = await c.home_record_stop()
        assert found['heard'] == '杭州西湖区' and found['place']['name'] == '浙江省杭州市西湖区'
        view = await c.set_home(found['place'])
        assert view['home']['name'] == '浙江省杭州市西湖区' and c.synced[-1] == view['home']
        assert json.loads(config.PATH.read_text())['home']['latitude'] == 30.26
        assert (await c.find_place('青埔'))['error'].startswith('没找到')
        view = await c.set_home(None)
        assert view['home'] is None and 'home' not in json.loads(config.PATH.read_text())
    asyncio.run(run())


def test_loop_plays_when_idle_and_waits_when_busy(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.home = HOME
    c.broadcast = bc.normalize(None, {'enabled': True, 'times': ['07:30']})
    now = {'t': D(2026, 11, 9, 7, 30, 3)}

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now['t']
    monkeypatch.setattr(app.datetime, 'datetime', Clock)
    monkeypatch.setattr(app.alarms, 'clock_synced', lambda: True)
    ticks = {'n': 0}

    async def sleep(_s):
        ticks['n'] += 1
        if ticks['n'] > 4:
            raise asyncio.CancelledError
    monkeypatch.setattr(app.asyncio, 'sleep', sleep)

    async def run(state, minute, second=3):
        c.state = state
        now['t'] = D(2026, 11, 9, 7, minute, second)
        ticks['n'] = 0
        with pytest.raises(asyncio.CancelledError):
            await c._broadcast_loop()

    asyncio.run(run('speaking', 31))
    assert c.said == [] and not c.alarms.missed            # busy: waits
    asyncio.run(run('idle', 32))
    assert len(c.said) == 1 and c.said[0].startswith('【早安播报 07:30】早上好，先调用查天气工具查最新的天气')
    assert c.played == ['report']
    asyncio.run(run('idle', 33))
    assert len(c.said) == 1                                  # once a day

    c.broadcast_fired.clear()
    asyncio.run(run('speaking', 59, 56))                        # still busy at the end of the wait
    assert len(c.said) == 1 and c.alarms.missed[-1]['kind'] == 'broadcast'
    assert c.alarms.missed[-1]['why'] == 'busy'


def call(c, name, **args):
    reply = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}},
                                   client=c))
    assert reply['result']['isError'] is False
    return json.loads(reply['result']['content'][0]['text'])


def test_voice_tools(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    names = [t['name'] for t in asyncio.run(mcp.handle({'id': 1, 'method': 'tools/list'}))['result']['tools']]
    assert {'self.weather_broadcast.update', 'self.location.set_home'} <= set(names) and len(names) == len(set(names))
    r = call(c, 'self.weather_broadcast.update', enabled=True, add_time='21:00', days='workdays')
    assert r['success'] and '工作日' in r['text'] and '21:00' in r['text']
    r = call(c, 'self.weather_broadcast.update', remove_time='09:00')
    assert not r['success'] and '没有' in r['reason']
    assert call(c, 'self.location.set_home')['home'] is None
    r = call(c, 'self.location.set_home', place='杭州西湖区')
    assert r == {'success': True, 'home': '浙江省杭州市西湖区'}
    assert call(c, 'self.location.set_home', place='青埔')['reason'].startswith('没找到')
    assert call(c, 'self.location.set_home')['home'] == '浙江省杭州市西湖区'


# ---- 拍照问答 (photo_start and its poller) ----

def test_photo_question_flow(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(app, 'PHOTOS', tmp_path / 'photos')
    monkeypatch.setattr(app, 'PHOTO_POLL', 0)
    c.photo = c.photo_task = c.photo_card = None
    c.cards, c._card_seq = [], 0
    c.threads, c.entry, c.models, c._seq = {}, None, {}, 0
    c._save_cards = lambda: None
    states = iter(['waiting', 'received', 'done'])
    calls = []

    async def hub_http(path, body=None):
        calls.append(path)
        if path == '/kiosk/photo/start':
            assert body == {'device': 'dev1', 'question': '这个能吃吗'}
            return {'ok': True, 'token': 'tok123', 'url': 'http://10.0.0.2:16688/photo/tok123', 'question': '这个能吃吗',
                    'expires': 9e9, 'vision_ready': True}
        state = next(states)
        return {'ok': True, 'state': state, 'question': '这个能吃吗', 'answer': '这是鲜贝，可以吃' if state == 'done' else None}
    c._hub_http = hub_http

    async def fetch(token):
        return '/api/photos/1-tok123.jpg'
    c._fetch_photo = fetch

    async def run():
        r = await c.photo_start('这个能吃吗')
        assert r == {'vision_ready': True} and c.photo_view()['state'] == 'waiting'
        await c.photo_task
    asyncio.run(run())
    assert c.photo is None and len(c.said) == 1
    assert c.said[0].startswith('【拍照问 ') and '「这个能吃吗」' in c.said[0] and '这是鲜贝，可以吃' in c.said[0]
    assert bc.tag_of(c.said[0][1:]).startswith('拍照问')
    assert c.photo_card == ('这个能吃吗', '这是鲜贝，可以吃', ['/api/photos/1-tok123.jpg'])
    # the card goes in when the hub echoes the request; a local photo path is allowed, other paths aren't
    m = c.show_card(*c.photo_card)
    assert m['card']['images'] == ['/api/photos/1-tok123.jpg']
    assert c.show_card('x', 'y', ['/etc/passwd', 'http://a/b.jpg', '/api/photos/../x.jpg'])['card']['images'] == []


def test_photo_tools(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    started = []

    async def photo_start(q):
        started.append(q)
        return {'vision_ready': False}

    async def photo_cancel():
        started.append('cancel')
    c.photo_start, c.photo_cancel = photo_start, photo_cancel
    r = call(c, 'self.photo.ask', question='这是什么')
    assert r['action'] == 'RESPONSE' and '二维码' in r['response'] and '看图模型' in r['response']
    assert call(c, 'self.photo.cancel')['action'] == 'RESPONSE' and started == ['这是什么', 'cancel']
