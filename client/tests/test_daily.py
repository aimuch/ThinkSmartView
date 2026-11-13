"""电台和助眠声 (media.py), 场景、留言 (daily.py), 清单 (the hub's), and their voice tools.

Run like test_barge_in.py (same stubs).
"""
import asyncio
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

from thinksmart_client import app, config, daily, mcp, media  # noqa: E402


def test_names_and_noise():
    assert media.find_station('东方广播') == 'dfgb' and media.find_station('中国之声') == 'zgzs'
    assert media.find_station('音乐台') is None
    assert media.find_sound('雨声') == 'rain' and media.find_sound('下雨') == 'rain' and media.find_sound('狗叫') is None and media.find_sound('鸟叫') == 'birds'
    for kind in media.SOUNDS:
        g = media.Noise(kind, seed=1)
        x = np.concatenate([g.next(4800) for _ in range(5)])
        assert x.dtype == np.float32 and 0.005 < float(np.sqrt(np.mean(x ** 2))) < 0.2 and np.abs(x).max() <= 0.9
    # the loop is seamless: the buffer's end leads into its start without a jump bigger than its usual steps
    b = media._shaped('ocean', np.random.default_rng(2))
    assert abs(float(b[-1] - b[0])) < 5 * float(np.abs(np.diff(b)).mean()) + 1e-3


class Background:
    def __init__(self):
        self.pushed = 0
        self.cleared = 0

    def push(self, x):
        self.pushed += len(x)

    def clear(self):
        self.cleared += 1


def test_sound_player_fades_and_stops(monkeypatch):
    bg = Background()
    views = []
    p = media.Player(bg, on_change=views.append)

    async def run():
        now = await p.play_sound('rain', minutes=0.01)       # 0.6 s
        assert now['name'] == '雨声' and now['ends']
        await asyncio.wait_for(p.task, 5)
        assert p.now is None and bg.pushed > 0.5 * media.RATE
        await p.play_sound('fan', minutes=0)
        await asyncio.sleep(0.3)
        await p.stop()
        assert p.now is None and bg.cleared >= 1
        with pytest.raises(ValueError):
            await p.play_radio('nope')
    asyncio.run(run())
    assert views[-1]['playing'] is None


def test_scenes_and_notes_pure():
    s = daily.save_scene([], '「我出门了」', '关掉所有灯，说一下天气')
    s = daily.save_scene(s, '我出门了。', '关掉所有灯和空调')
    assert len(s) == 1 and s[0]['steps'] == '关掉所有灯和空调'
    assert daily.find_scene(s, '出门了')['name'] == '我出门了'
    with pytest.raises(ValueError):
        daily.save_scene(s, '', 'x')
    notes, n = daily.new_note([], '爸爸', '晚上不回来吃饭', '妈妈')
    assert notes == [n] and n['to'] == '爸爸' and n['from'] == '妈妈'


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    monkeypatch.setattr(daily, 'NOTES', tmp_path / 'notes.json')
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.cfg = {**config.DEFAULTS, 'device_id': 'dev1', 'scenes': []}
    c.notes = []
    c.lists = None
    c.screen = types.SimpleNamespace(poke=lambda: None)
    c.said = []

    async def say_local(text):
        c.said.append(text)
    c._say_local = say_local
    store = {'version': 0, 'lists': {'购物清单': [], '待办': []}}

    async def hub_http(path, body=None):
        if body is None:
            return {'ok': True, **store, 'url': 'http://hub/lists/s'}
        lst = store['lists'].setdefault(body['name'], [])
        if body['op'] == 'add':
            lst += [{'text': t, 'done': False} for t in body['items']]
        elif body['op'] == 'check':
            for x in lst:
                x['done'] = x['done'] or x['text'] in body['items']
        store['version'] += 1
        return {'ok': True, **store, 'url': 'http://hub/lists/s'}
    c._hub_http = hub_http
    played = []

    async def media_play(kind, name, minutes=None):
        played.append((kind, name, minutes))
        return {'kind': kind, 'name': '东方广播（上海）' if kind == 'radio' else '雨声'}

    async def media_stop():
        played.append('stop')
    c.media_play, c.media_stop, c.played = media_play, media_stop, played
    return c


def call(c, tool, **args):
    r = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}, client=c))
    assert r['result']['isError'] is False
    return json.loads(r['result']['content'][0]['text'])


def test_voice_tools(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    assert call(c, 'self.scene.save', name='我出门了', steps='关掉所有灯和空调，播报天气')['success']
    r = call(c, 'self.scene.run', name='我出门了')
    assert r['steps'] == '关掉所有灯和空调，播报天气' and 'instruction' in r
    assert not call(c, 'self.scene.run', name='我回来了')['success']
    assert json.loads(config.PATH.read_text())['scenes'][0]['name'] == '我出门了'
    assert call(c, 'self.message.leave', to='爸爸', text='晚上不回来吃饭', sender='妈妈')['success']
    notes = call(c, 'self.message.leave', action='list', to='爸爸')['notes']
    assert notes[0]['text'] == '晚上不回来吃饭'
    assert call(c, 'self.message.leave', action='done', id=notes[0]['id'])['success'] and c.notes == []
    r = call(c, 'self.list.update', op='add', name='购物清单', items=['酱油', '鸡蛋'])
    assert r['todo'] == ['酱油', '鸡蛋']
    call(c, 'self.list.update', op='check', name='购物清单', items=['鸡蛋'])
    assert call(c, 'self.list.update', op='show', name='购物清单') == {'name': '购物清单', 'todo': ['酱油'], 'done': ['鸡蛋']}
    r = call(c, 'self.media.play', kind='radio', name='东方广播')
    assert r['action'] == 'RESPONSE' and '东方广播' in r['response']
    assert '30 分钟后停' in call(c, 'self.media.play', kind='sound', name='雨声')['response']
    assert call(c, 'self.media.play', action='stop')['response'] == '好，停了。' and c.played[-1] == 'stop'
