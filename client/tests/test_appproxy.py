"""家居里的智能中枢、机柜网页（设计画布第 62 块）：设备本机的小代理把整个网页原样转给网关；家居分页的设置。

Run like test_barge_in.py.
"""
import gzip
import socket

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from thinksmart_client import appproxy


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_work_setting():
    """工作台的设置（设计画布第 75 块）：四页都在 order 里；以前存的「先看」带过来；栏宽限在 30%–70%。"""
    w = appproxy.normalize_work(None, 'rack')
    assert w == {'order': ['desk', 'hub', 'rack', 'repo'], 'hidden': [], 'first': 'rack', 'rack': 'screen', 'hub': 'tiles',
                 'split': {}, 'idle_min': 3, 'idle_close': True, 'keep': 10, 'usage': 'show', 'usage_order': [], 'usage_hidden': []}
    w = appproxy.normalize_work({'order': ['repo', 'x', 'repo', 'desk'], 'hidden': ['hub', 'y'], 'first': 'nope', 'rack': 'list',
                                 'hub': 'web', 'split': {'desk': 0.9, 'repo': 0.2, 'z': 0.5}, 'idle_close': False})
    assert w == {'order': ['repo', 'desk', 'hub', 'rack'], 'hidden': ['hub'], 'first': '', 'rack': 'list', 'hub': 'web',
                 'split': {'desk': 0.7, 'repo': 0.3}, 'idle_min': 0, 'idle_close': False, 'keep': 10, 'usage': 'show',
                 'usage_order': [], 'usage_hidden': []}
    # 切走后保留网页几分钟（设计画布第 79 块）：只认那几档，写错的回到默认 10
    assert [appproxy.normalize_work({'keep': k})['keep'] for k in (0, 5, 30, -1, 7, '10', True, None)] == [0, 5, 30, -1, 10, 10, 10, 10]
    # 没人碰几分钟回首页（设计画布第 89 块）：几档；0 不回首页；没存过的看老的 idle_close
    assert [appproxy.normalize_work({'idle_min': m})['idle_min'] for m in (1, 3, 5, 10, 30, 0, 7, '5', True, None)] == \
        [1, 3, 5, 10, 30, 0, 3, 3, 3, 3]
    assert appproxy.normalize_work({'idle_close': False})['idle_min'] == 0
    assert appproxy.normalize_work({'idle_close': False, 'idle_min': 10}) == {**appproxy.normalize_work({'idle_min': 10})}
    assert not appproxy.normalize_work({'idle_min': 0})['idle_close'] and appproxy.normalize_work({'idle_min': 30})['idle_close']
    # 智能桌面的 AI 用量显示不显示（设计画布第 91 块）：只认 hide，别的都算显示
    assert [appproxy.normalize_work({'usage': u})['usage'] for u in ('hide', 'show', 'x', None, False)] == ['hide', 'show', 'show', 'show', 'show']
    # AI 用量显示哪些服务、顺序（设计画布第 92 块）：去重、只要字符串、最多 16 个
    w = appproxy.normalize_work({'usage_order': ['claude', 'codex', 'claude', 7, '', 'x' * 17], 'usage_hidden': 'copilot'})
    assert w['usage_order'] == ['claude', 'codex'] and w['usage_hidden'] == []
    assert len(appproxy.normalize_work({'usage_hidden': [f'p{i}' for i in range(30)]})['usage_hidden']) == 16
    assert appproxy.url('rack') == 'http://127.0.0.1:8091/kiosk' and appproxy.url('hub').endswith(':8092/#/')


@pytest.mark.asyncio
async def test_pages_go_to_the_hub_with_this_device(monkeypatch):
    seen = []

    async def proxy(request):
        seen.append((request.method, request.match_info['app'], request.path_qs, request.headers.get('X-Device-Id'),
                     await request.text()))
        if request.path.endswith('/app.js'):
            return web.Response(body=gzip.compress(b'ok'), headers={'Content-Encoding': 'gzip'})
        if request.path.endswith('/stream'):
            r = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await r.prepare(request)
            await r.write(b'data: 1\n\n')
            return r
        return web.Response(text='page', headers={'X-From': 'hub'})

    hub = web.Application()
    hub.router.add_route('*', '/kiosk/proxy/{app}/{tail:.*}', proxy)
    server = TestServer(hub)
    await server.start_server()
    ports = {'rack': free_port(), 'hub': free_port()}
    monkeypatch.setattr(appproxy, 'APPS', ports)
    p = appproxy.AppProxy(str(server.make_url('')), 'dev-1')
    await p.start()
    try:
        async with ClientSession(auto_decompress=False) as s:
            async with s.get(f'http://127.0.0.1:{ports["rack"]}/setup?x=1') as r:
                assert await r.text() == 'page' and r.headers['X-From'] == 'hub'
            async with s.post(f'http://127.0.0.1:{ports["hub"]}/api/devices/lamp/on', data='{}') as r:
                assert r.status == 200
            async with s.get(f'http://127.0.0.1:{ports["hub"]}/static/app.js') as r:
                assert r.headers['Content-Encoding'] == 'gzip' and gzip.decompress(await r.read()) == b'ok'
            async with s.get(f'http://127.0.0.1:{ports["hub"]}/api/stream') as r:
                assert await r.text() == 'data: 1\n\n'
        assert seen[0] == ('GET', 'rack', '/kiosk/proxy/rack/setup?x=1', 'dev-1', '')
        assert seen[1] == ('POST', 'hub', '/kiosk/proxy/hub/api/devices/lamp/on', 'dev-1', '{}')
    finally:
        await p.stop()
        await server.close()


@pytest.mark.asyncio
async def test_hub_unreachable(monkeypatch):
    ports = {'rack': free_port(), 'hub': free_port()}
    monkeypatch.setattr(appproxy, 'APPS', ports)
    p = appproxy.AppProxy('http://127.0.0.1:9', 'dev-1')
    await p.start()
    try:
        async with ClientSession() as s:
            async with s.get(f'http://127.0.0.1:{ports["rack"]}/setup') as r:
                assert r.status == 502 and '网关' in await r.text()
    finally:
        await p.stop()


def test_set_work_idle_minutes(monkeypatch):
    """页面选几分钟回首页；老页面只发 idle_close 时当成 3 分钟 / 不回首页（设计画布第 89 块）。"""
    import asyncio
    import types
    from thinksmart_client import app, config

    saved, published = [], []
    monkeypatch.setattr(config, 'save', lambda **kw: saved.append(kw))
    stub = types.SimpleNamespace(cfg={'work': appproxy.normalize_work({'idle_close': False})},
                                 events=types.SimpleNamespace(publish=lambda k, v: published.append((k, v))))
    stub.work_view = lambda: stub.cfg['work']
    run = lambda changes: asyncio.new_event_loop().run_until_complete(app.Client.set_work(stub, changes))
    assert stub.cfg['work']['idle_min'] == 0                       # 以前关着的：不回首页
    assert run({'idle_min': 10})['idle_min'] == 10 and saved[-1]['work']['idle_min'] == 10
    assert run({'idle_close': True})['idle_min'] == 3               # 老页面的开关
    assert run({'idle_close': False})['idle_min'] == 0
    assert run({'idle_min': 99})['idle_min'] == 0                  # 不认的：照之前的（不回首页）
    assert run({'usage': 'hide'})['usage'] == 'hide' and saved[-1]['work']['usage'] == 'hide'
    v = run({'usage_order': ['claude', 'codex'], 'usage_hidden': ['copilot']})
    assert v['usage_order'] == ['claude', 'codex'] and v['usage_hidden'] == ['copilot']
    assert published[-1][0] == 'work'
