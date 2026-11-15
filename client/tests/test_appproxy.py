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


def test_tabs_setting():
    assert appproxy.normalize_tabs(None) == {'show': ['mi'], 'first': ''}
    assert appproxy.normalize_tabs({'show': ['rack', 'hub', 'mi', 'x'], 'first': 'hub'}) == {'show': ['mi'], 'first': ''}


def test_work_setting():
    """工作台的设置（设计画布第 75 块）：四页都在 order 里；以前存的「先看」带过来；栏宽限在 30%–70%。"""
    w = appproxy.normalize_work(None, 'rack')
    assert w == {'order': ['desk', 'hub', 'rack', 'repo'], 'hidden': [], 'first': 'rack', 'rack': 'screen', 'hub': 'tiles',
                 'split': {}, 'idle_close': True}
    w = appproxy.normalize_work({'order': ['repo', 'x', 'repo', 'desk'], 'hidden': ['hub', 'y'], 'first': 'nope', 'rack': 'list',
                                 'hub': 'web', 'split': {'desk': 0.9, 'repo': 0.2, 'z': 0.5}, 'idle_close': False})
    assert w == {'order': ['repo', 'desk', 'hub', 'rack'], 'hidden': ['hub'], 'first': '', 'rack': 'list', 'hub': 'web',
                 'split': {'desk': 0.7, 'repo': 0.3}, 'idle_close': False}
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
