"""外观 (looks.py, Client.set_looks / make_avatar / avatar flow) and the appearance voice tool.

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

from thinksmart_client import app, config, looks, mcp  # noqa: E402


def test_normalize():
    assert looks.normalize_theme('晨光') == 'dawn' and looks.normalize_theme('auto') == 'auto'
    with pytest.raises(ValueError):
        looks.normalize_theme('彩虹')
    assert looks.normalize_avatar('小猫') == 'cat'
    with pytest.raises(ValueError):
        looks.normalize_avatar('photo:nope')
    blocks = looks.normalize_blocks([['weather', False], ['nope', True], {'id': 'clock', 'on': True}, ['weather', True]])
    assert blocks[:2] == [['weather', False], ['clock', True]] and [b for b, _ in blocks][2:] == ['date', 'alarms', 'suggest']
    assert looks.theme_now('auto', datetime.datetime(2026, 11, 9, 8)) == 'dawn'
    assert looks.theme_now('auto', datetime.datetime(2026, 11, 9, 20)) == 'night'


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    monkeypatch.setattr(looks, 'PHOTO_DIR', tmp_path / 'avatars')
    monkeypatch.setattr(app, 'PHOTOS', tmp_path / 'photos')
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.cfg = {**config.DEFAULTS, 'device_id': 'dev1', 'home_blocks': looks.normalize_blocks(None)}
    c.screen = types.SimpleNamespace(poke=lambda: None)
    c.play = lambda name: None
    return c


def test_set_looks_persist_and_reorder(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)

    async def run():
        v = await c.set_looks(theme='森林', avatar='小狗')
        assert v['theme'] == 'forest' and v['avatar'] == 'dog'
        v = await c.set_looks(move={'id': 'weather', 'by': -2})
        assert [b['id'] for b in v['blocks']][:3] == ['weather', 'clock', 'date']
        v = await c.set_looks(toggle='suggest')
        assert [b for b in v['blocks'] if b['id'] == 'suggest'][0]['on'] is False
        saved = json.loads(config.PATH.read_text())
        assert saved['theme'] == 'forest' and saved['avatar'] == 'dog' and saved['home_blocks'][0] == ['weather', True]
        await c.set_looks(theme='night', avatar='orb')
        saved = json.loads(config.PATH.read_text())
        assert 'theme' not in saved and 'avatar' not in saved    # back to the defaults: not stored
        with pytest.raises(ValueError):
            await c.set_looks(move={'id': 'nope', 'by': 1})
    asyncio.run(run())


def test_photo_avatars_keep_at_most_six(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    t = {'n': 1000}
    monkeypatch.setattr(app.time, 'time', lambda: t['n'])

    async def run():
        ids = []
        for i in range(8):
            t['n'] += 1
            ids.append(await c.make_avatar(b'\x89PNG' + bytes([i])))
            import os
            os.utime(looks.PHOTO_DIR / f'{ids[-1][6:]}.png', (t['n'], t['n']))
        photos = looks.photo_avatars()
        assert len(photos) == 6 and photos[0]['id'] == ids[-1]
        v = await c.set_looks(avatar=ids[-1])
        assert v['avatar'] == ids[-1]
        v = await c.delete_avatar(ids[-1])
        assert v['avatar'] == 'orb' and len(v['photos']) == 5
        with pytest.raises(ValueError):
            await c.delete_avatar('cat')
    asyncio.run(run())


def test_avatar_flow(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(app, 'PHOTO_POLL', 0)
    c.photo = c.photo_task = None
    states = iter(['waiting', 'received', 'received', 'generating', 'generated', 'generated'])
    calls = []

    async def hub_http(path, body=None):
        calls.append((path, body))
        if path == '/kiosk/photo/start':
            assert body['purpose'] == 'avatar'
            return {'ok': True, 'token': 'tokA', 'url': 'http://x/photo/tokA', 'question': '卡通形象', 'expires': 9e9,
                    'avatar_ready': True, 'styles': [{'id': 1, 'name': '3D童话'}]}
        if path.endswith('/avatar'):
            return {'ok': True}
        if path.endswith('/cancel'):
            return {'ok': True}
        return {'ok': True, 'state': next(states), 'question': '卡通形象', 'purpose': 'avatar'}
    c._hub_http = hub_http

    async def fetch(token, avatar=False):
        app.PHOTOS.mkdir(parents=True, exist_ok=True)
        name = 'p-av.jpg' if avatar else 'p.jpg'
        (app.PHOTOS / name).write_bytes(b'\x89PNGdrawn' if avatar else b'\xff\xd8photo')
        return f'/api/photos/{name}'
    c._fetch_photo = fetch

    async def run():
        await c.avatar_start()
        assert c.photo_view()['purpose'] == 'avatar' and c.photo_view()['styles'][0]['name'] == '3D童话'
        for _ in range(3):
            await asyncio.sleep(0)
        await c.avatar_style(1)
        for _ in range(20):
            await asyncio.sleep(0)
            if c.photo and c.photo.get('preview'):
                break
        assert c.photo['preview'] == '/api/photos/p-av.jpg'
        r = await c.avatar_use()
        assert r['looks']['avatar'].startswith('photo:') and c.photo is None
        assert looks.photo_avatars()[0]['id'] == r['looks']['avatar']
        c.photo_task.cancel()
    asyncio.run(run())
    assert ('/kiosk/photo/tokA/avatar', {'style': 1}) in calls


def test_avatar_needs_a_bailian_key(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.photo = c.photo_task = None

    async def hub_http(path, body=None):
        if path == '/kiosk/photo/start':
            return {'ok': True, 'token': 't', 'url': 'u', 'question': 'q', 'expires': 9e9, 'avatar_ready': False}
        return {'ok': True}
    c._hub_http = hub_http

    async def run():
        with pytest.raises(ValueError, match='百炼'):
            await c.avatar_start()
        assert c.photo is None
    asyncio.run(run())


def test_appearance_tool(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)

    def call(**args):
        r = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call',
                                    'params': {'name': 'self.appearance.set', 'arguments': args}}, client=c))
        return json.loads(r['result']['content'][0]['text'])
    assert call(avatar='小猫', theme='童趣') == {'success': True, 'theme': '童趣', 'avatar': '小猫'}
    assert not call(avatar='photo')['success']
    assert not call(theme='彩虹')['success']
