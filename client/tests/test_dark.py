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
    monkeypatch.setattr(app, 'STATE_PATH', tmp_path / 'no-state.json')
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


def test_dark_comes_from_the_blankers_state(tmp_path, monkeypatch):
    """熄屏后的亮度能调了：熄没熄听 idle-blanker 的状态文件，不看背光是不是 10。"""
    c = make_client(tmp_path, monkeypatch)
    c.dark = False
    light, state = tmp_path / 'brightness', tmp_path / 'state.json'
    light.write_text('400\n')
    state.write_text('{"blanked": true, "blank": 400}')
    monkeypatch.setattr(app, 'BACKLIGHT', light)
    monkeypatch.setattr(app, 'STATE_PATH', state)
    seen = []
    c.events.publish = lambda name, data: seen.append((name, data))

    async def run():
        t = asyncio.create_task(c._dark_loop())
        await asyncio.sleep(0.4)
        t.cancel()
    asyncio.run(run())
    assert seen == [('dark', {'dark': True})]


def test_screen_off_level_and_preview(tmp_path, monkeypatch):
    import json
    import time
    from thinksmart_client import device
    hint = tmp_path / 'hint.json'
    monkeypatch.setattr(device, 'HINT_PATH', hint)
    s = device.Screen()
    s.blank_level(device.BLANK_LEVELS[2])
    assert json.loads(hint.read_text())['blank'] == 400 and 'preview' not in json.loads(hint.read_text())
    s.preview(400)
    h = json.loads(hint.read_text())
    assert h['preview']['level'] == 400 and h['preview']['until'] > time.time()
    assert device.normalize_screen_off({}, {'blank': 4})['blank'] == 4
    for bad in (5, -1, True, '2'):
        try:
            device.normalize_screen_off({}, {'blank': bad})
            raise AssertionError(bad)
        except ValueError:
            pass
