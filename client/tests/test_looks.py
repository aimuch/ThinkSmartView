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
    assert looks.normalize_avatar('光球') == 'orb'
    for gone in ('小猫', 'cat', 'photo:nope', 'pack:nope'):   # 小动物去掉了（设计画布第 41 块）
        with pytest.raises(ValueError):
            looks.normalize_avatar(gone)
    blocks = looks.normalize_blocks([['weather', False], ['nope', True], {'id': 'clock', 'on': True}, ['weather', True]])
    assert [b for b, _ in blocks] == ['weather', 'devices', 'clock', 'date', 'alarms', 'suggest', 'work', 'lights'] and blocks[0] == ['weather', False]
    assert blocks[-2:] == [['work', False], ['lights', False]]   # 接入网关服务的两块默认关（设计画布第 38 块）
    assert looks.theme_now('auto', datetime.datetime(2026, 11, 9, 8)) == 'dawn'
    assert looks.theme_now('auto', datetime.datetime(2026, 11, 9, 20)) == 'night'


PACK = {'version': 'v1', 'groups': [{'name': '星星人', 'count': 2}, {'name': 'LABUBU', 'count': 1}, {'name': '节日', 'count': 1}],
        'avatars': [
            {'id': 'pop-mart.twinkle-twinkle', 'name': '星星人 · 经典', 'short': '经典', 'group': '星星人', 'tag': '', 'series': '助手基础形象'},
            {'id': 'pop-mart.box.1448.06', 'name': '星星人 · Magic', 'short': 'Magic', 'group': '星星人', 'tag': '', 'series': 'We are Twinkle Twinkle'},
            {'id': 'pop-mart.labubu', 'name': 'LABUBU · 经典', 'short': '经典', 'group': 'LABUBU', 'tag': '', 'series': '助手基础形象'},
            {'id': 'festivals.labubu.spring-festival', 'name': 'LABUBU · 春节', 'short': 'LABUBU · 春节', 'group': '节日', 'tag': '春节',
             'series': '节日适配 · 春节'},
        ]}


def install_pack(tmp_path, monkeypatch):
    d = tmp_path / 'avatar-pack'
    (d / 'img').mkdir(parents=True)
    for a in PACK['avatars']:
        a['files'] = {k: f"img/{a['id']}-{k}.webp" for k in ('idle', 'awake', 'sleep', 'thumb')}
        for f in a['files'].values():
            (d / f).write_bytes(b'RIFF')
    (d / 'catalog.json').write_text(json.dumps(PACK, ensure_ascii=False))
    monkeypatch.setattr(looks, 'PACK_DIR', d)
    return d


def test_avatar_pack(tmp_path, monkeypatch):
    """形象库（设计画布第 41 块）：按名字找、五个样子、最近用过的。"""
    monkeypatch.setattr(looks, 'PACK_DIR', tmp_path / 'none')
    assert looks.pack() is None and looks.pack_view() == {'available': False, 'groups': [], 'avatars': []}
    install_pack(tmp_path, monkeypatch)
    assert looks.normalize_avatar('星星人') == 'pack:pop-mart.twinkle-twinkle'     # 说分组名用经典款
    assert looks.normalize_avatar('星星人 Magic') == 'pack:pop-mart.box.1448.06'
    assert looks.normalize_avatar('LABUBU 春节的') == 'pack:festivals.labubu.spring-festival'
    assert looks.normalize_avatar('pack:pop-mart.labubu') == 'pack:pop-mart.labubu'
    with pytest.raises(ValueError, match='好几款'):
        looks.normalize_avatar('经典的')
    v = looks.pack_view()
    a = v['avatars'][0]
    assert v['available'] and a['id'] == 'pack:pop-mart.twinkle-twinkle' and a['thumb'] == '/api/avatar-pack/img/pop-mart.twinkle-twinkle-thumb.webp?v=v1'
    assert a['poses'][1] == a['poses'][3] and a['poses'][4].endswith('-sleep.webp?v=v1') and len(a['poses']) == 5
    r = looks.remember_recent(['pack:a', 'cat', 'pack:b'], 'pack:b')
    assert r == ['pack:b', 'pack:a'] and len(looks.remember_recent([f'pack:{i}' for i in range(9)], 'pack:x')) == looks.RECENT_MAX


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    monkeypatch.setattr(looks, 'PHOTO_DIR', tmp_path / 'avatars')
    install_pack(tmp_path, monkeypatch)
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
        v = await c.set_looks(theme='森林', avatar='LABUBU')
        assert v['theme'] == 'forest' and v['avatar'] == 'pack:pop-mart.labubu'
        v = await c.set_looks(avatar='星星人')
        assert [x['id'] for x in v['pack']['recent']] == ['pack:pop-mart.twinkle-twinkle', 'pack:pop-mart.labubu']
        v = await c.set_looks(move={'id': 'weather', 'by': -2})
        assert [b['id'] for b in v['blocks']][:3] == ['weather', 'clock', 'date']
        v = await c.set_looks(toggle='suggest')
        assert [b for b in v['blocks'] if b['id'] == 'suggest'][0]['on'] is False
        saved = json.loads(config.PATH.read_text())
        assert saved['theme'] == 'forest' and saved['avatar'] == 'pack:pop-mart.twinkle-twinkle' and saved['home_blocks'][0] == ['weather', True]
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


def test_avatar_starts_without_a_bailian_key(tmp_path, monkeypatch):
    """「原图」不用百炼 Key（拍自己的公仔，设计画布第 26 块）：没有 Key 也能开始，别的画风由网关说要 Key。"""
    c = make_client(tmp_path, monkeypatch)
    c.photo = c.photo_task = None

    async def hub_http(path, body=None):
        if path == '/kiosk/photo/start':
            return {'ok': True, 'token': 't', 'url': 'u', 'question': 'q', 'expires': 9e9, 'avatar_ready': False}
        return {'ok': True}
    c._hub_http = hub_http

    async def run():
        r = await c.avatar_start()
        assert c.photo and c.photo['purpose'] == 'avatar' and r['avatar_ready'] is False
        if c.photo_task:
            c.photo_task.cancel()
    asyncio.run(run())


def test_photo_avatar_with_poses(tmp_path, monkeypatch):
    """一个照片形象存 4 张：页面按状态换；透明 PNG 当全身公仔；删掉时几张一起删。"""
    from thinksmart_client import looks
    monkeypatch.setattr(looks, 'PHOTO_DIR', tmp_path / 'avatars')
    monkeypatch.setattr(looks, 'POSE_DIR', tmp_path / 'avatars' / 'poses')
    c = make_client(tmp_path, monkeypatch)
    rgba = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00\x00\x00\x10\x00\x00\x00\x10' + b'\x08\x06' + b'rest'

    async def run():
        avatar = await c.make_avatar([rgba, rgba + b'1', rgba + b'2', rgba + b'3'])
        a = next(x for x in looks.photo_avatars() if x['id'] == avatar)
        assert len(a['poses']) == 4 and a['cutout'] is True and a['poses'][2].endswith('-2.png')
        await c.delete_avatar(avatar)
        assert not list((tmp_path / 'avatars').rglob('*.png'))
    asyncio.run(run())


def test_appearance_tool(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)

    def call(**args):
        r = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call',
                                    'params': {'name': 'self.appearance.set', 'arguments': args}}, client=c))
        return json.loads(r['result']['content'][0]['text'])
    assert call(avatar='星星人', theme='童趣') == {'success': True, 'theme': '童趣', 'avatar': '星星人 · 经典', 'layout': '对话'}
    r = call(avatar='LABUBU')
    assert r['avatar'] == 'LABUBU · 经典'
    r = call(avatar='小猫')                       # 去掉的小动物：说清楚能选什么
    assert not r['success'] and '星星人' in r['reason']
    assert call(avatar='光球')['avatar'] == '光球'
    assert not call(avatar='photo')['success']
    assert not call(theme='彩虹')['success']
