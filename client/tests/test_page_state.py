"""屏幕上现在是哪一页（web/v2/screen.js 报给 /api/page-state）：记下现在和最近 20 次，坏的请求不收。

Run like test_barge_in.py.
"""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from thinksmart_client.web import page_state_routes


@pytest.mark.asyncio
async def test_page_state_keeps_the_last_views():
    app = web.Application()
    page_state_routes(app)
    async with TestClient(TestServer(app)) as c:
        assert (await (await c.get('/api/page-state')).json())['view'] is None
        for i in range(25):
            assert (await c.post('/api/page-state', data=f'{{"view": "settings/p{i}"}}')).status == 204
        assert (await c.post('/api/page-state', data='not json')).status == 400
        assert (await c.post('/api/page-state', data='[1]')).status == 400
        body = await (await c.get('/api/page-state')).json()
        assert body['view'] == 'settings/p24' and len(body['recent']) == 20 and body['recent'][0]['view'] == 'settings/p24'
