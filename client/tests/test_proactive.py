"""定时任务、主动告警 (Mijia sensors)、翻译模式.

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

from thinksmart_client import app, broadcast, config, daily, mcp  # noqa: E402

D = datetime.datetime


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    monkeypatch.setattr(daily, 'NOTES', tmp_path / 'notes.json')
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.cfg = {**config.DEFAULTS, 'device_id': 'dev1', 'tasks': [], 'alerts': dict(config.DEFAULTS['alerts']),
             'translate': dict(config.DEFAULTS['translate'])}
    c.broadcast = broadcast.normalize(None)
    c.broadcast_fired = set()
    c.notes = []
    c.ring = None
    c.sleeping = False
    c.state = 'idle'
    c.screen = types.SimpleNamespace(poke=lambda: None)
    c.played = []
    c.play = c.played.append
    c.said, c.spoken = [], []

    async def say(text):
        c.said.append(text)
    c.say = say

    async def say_local(text):
        c.spoken.append(text)
    c._say_local = say_local
    c.hub_calls = []

    async def hub_http(path, body=None):
        c.hub_calls.append((path, body))
        return {'ok': True}
    c._hub_http = hub_http
    closed = []

    async def close():
        closed.append(1)
    c.hub = types.SimpleNamespace(connected=True, close=close)
    c.closed = closed
    c.in_turn = lambda: False
    return c


def test_tasks_add_due_and_run(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)

    async def run():
        t = await c.task_add('7:00', '查一下今天会不会下雨，下雨就提醒带伞', 'workdays')
        assert t['time'] == '07:00' and json.loads(config.PATH.read_text())['tasks'][0]['prompt'].startswith('查一下')
        once = await c.task_add('21:30', '提醒我关窗', 'once')
        due = c._due_tasks(D(2026, 11, 9, 7, 0, 5))      # a Monday
        assert [x[0]['id'] for x in due] == [t['id']]
        assert c._due_tasks(D(2026, 11, 8, 7, 0, 5)) == []   # Sunday: not a workday
        await c._run_task(once)
        assert c.said[-1].startswith('【定时任务 21:30】提醒我关窗') and broadcast.tag_of(c.said[-1]) == '定时任务 21:30'
        assert [x['id'] for x in c.tasks_view()['tasks']] == [t['id']]   # 'once' removed after running
        with pytest.raises(ValueError):
            await c.task_add('25:00', 'x')
    asyncio.run(run())


def test_two_tasks_in_the_same_millisecond_get_their_own_ids(tmp_path, monkeypatch):
    """一句话里加两个定时任务（同一毫秒）：id 不能一样，否则删掉「一次」的那个会把另一个也删了。"""
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(app.time, 'time', lambda: 1_700_000_000.0)

    async def run():
        a = await c.task_add('7:00', '查天气', 'workdays')
        b = await c.task_add('21:30', '提醒我关窗', 'once')
        assert a['id'] != b['id']
        await c._run_task(b)
        assert [x['id'] for x in c.tasks_view()['tasks']] == [a['id']]
    asyncio.run(run())


def test_alert_from_a_leak_sensor(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    card = {'id': 'k1', 'name': '厨房水浸传感器', 'online': True, 'feats': {'leak': {}}, 'values': {'leak': False}}
    polls = []

    async def poll(cards):
        polls.append(len(cards))
    c.mijia = types.SimpleNamespace(session=object(), state='ready', visible=False, cards={'k1': card}, poll=poll)
    ticks = {'n': 0}

    async def sleep(_s):
        ticks['n'] += 1
        if ticks['n'] == 2:
            card['values']['leak'] = True
        if ticks['n'] > 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(app.asyncio, 'sleep', sleep)

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await c._alert_loop()
    asyncio.run(run())
    assert c.alert['text'] == '厨房水浸传感器检测到漏水' and c.alert['urgent']
    assert c.spoken and '漏水' in c.spoken[0] and c.notes[-1]['text'].startswith('⚠')
    assert polls and c.alerts_view()['sensors'] == 1


def test_translate_mode(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    r = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'self.translate.set',
                                'arguments': {'enabled': True, 'a': '中文', 'b': '日文'}}}, client=c))
    out = json.loads(r['result']['content'][0]['text'])
    assert out['action'] == 'RESPONSE' and '日文' in out['response']
    assert c.hub_calls[-1] == ('/kiosk/prefs', {'device': 'dev1', 'translate': {'enabled': True, 'a': '中文', 'b': '日文'}})
    assert c.follow_up_seconds() == 30 and c.closed   # idle: reconnects at once for the new prompt
    with pytest.raises(ValueError):
        asyncio.run(c.set_translate(True, '中文', '中文'))


# ---- 断网兜底 (offline.py) ----

def test_offline_phrases():
    from thinksmart_client import offline
    p = offline.phrases()
    assert p['h2'] == '两点' and p['h21'] == '二十一点' and p['m7'] == '零七分。' and p['m45'] == '四十五分。'
    assert offline.time_clips(9, 0) == ['now', 'h9', 'm0']


def test_offline_commands(tmp_path, monkeypatch):
    from thinksmart_client import offline
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(offline, 'CLIPS', tmp_path / 'offline')
    played, volumes = [], []
    c.audio = types.SimpleNamespace(player=types.SimpleNamespace(trim=lambda n: None, push=played.append))
    c.volume = 50

    async def set_volume(v, source='page'):
        volumes.append(v)
        c.volume = v
    c.set_volume = set_volume
    controlled = []

    async def control(card_id, op, value=None, confirm=False, by_voice=False):
        controlled.append((card_id, op))
        return {'ok': True}
    c.mijia = types.SimpleNamespace(session=object(), control=control,
                                    cards={'a': {'id': 'a', 'kind': 'light', 'online': True}, 'b': {'id': 'b', 'kind': 'plug', 'online': True}})

    async def run():
        await c.offline_command('音量大一点')
        assert volumes == [65] and c.played == ['miss']        # no clips yet: a tone instead of words
        offline.CLIPS.mkdir()
        import io
        import wave
        for name in ('ok', 'now', 'h9', 'm5'):
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\x00\x00' * 240)
            (offline.CLIPS / f'{name}.wav').write_bytes(buf.getvalue())
        await c.offline_command('关掉所有灯')
        assert controlled == [('a', 'off')] and len(played) == 1
        monkeypatch.setattr(app.datetime, 'datetime', type('D', (datetime.datetime,), {'now': classmethod(lambda cls, tz=None: D(2026, 11, 9, 9, 5))}))
        await c.offline_command('现在几点')
        assert len(played) == 4
    asyncio.run(run())
