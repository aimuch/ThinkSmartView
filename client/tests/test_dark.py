"""息屏时页面涂黑：客户端看背光（idle-blanker 息屏写 10），变了就告诉页面。"""
import asyncio

from thinksmart_client import app
from test_kids_cook import make_client


def test_dark_follows_the_backlight(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.dark = False
    light = tmp_path / 'brightness'
    light.write_text('2424\n')
    monkeypatch.setattr(app, 'BACKLIGHT', light)
    seen = []
    c.events.publish = lambda name, data: seen.append((name, data))

    async def run():
        t = asyncio.create_task(c._dark_loop())
        await asyncio.sleep(0.3)
        light.write_text('10\n')
        await asyncio.sleep(0.4)
        light.write_text('2424\n')
        await asyncio.sleep(0.4)
        t.cancel()
    asyncio.run(run())
    assert [d['dark'] for n, d in seen if n == 'dark'] == [True, False]


def test_no_backlight_no_loop(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.dark = False
    monkeypatch.setattr(app, 'BACKLIGHT', tmp_path / 'missing')
    asyncio.run(asyncio.wait_for(c._dark_loop(), 1))
