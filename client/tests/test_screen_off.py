"""Auto screen-off: the setting, the hint the blanker gets (idle-blanker.py) and the page's API.

  uv run --no-project --with numpy --with soxr --with websockets --with aiohttp --with pytest --with pytest-asyncio \\
      python -m pytest -q tests
"""
import json
import sys
import types

import pytest

sys.modules.setdefault('sounddevice', types.ModuleType('sounddevice'))
sys.modules.setdefault('thinksmart_client.webrtc_apm', types.ModuleType('thinksmart_client.webrtc_apm'))

from thinksmart_client import app, config, device  # noqa: E402


@pytest.fixture
def hint(tmp_path, monkeypatch):
    monkeypatch.setattr(device, 'HINT_PATH', tmp_path / 'screen.json')
    return lambda: json.loads((tmp_path / 'screen.json').read_text())


def test_normalize():
    assert device.normalize_screen_off(None) == {'enabled': True, 'minutes': 5}
    assert device.normalize_screen_off({'enabled': False, 'minutes': 30, 'x': 1}) == {'enabled': False, 'minutes': 30}
    assert device.normalize_screen_off({'minutes': 10}, {'enabled': False, 'minutes': None}) == \
        {'enabled': False, 'minutes': 10}
    for bad in (2, 0, True, '5', 60):
        with pytest.raises(ValueError):
            device.normalize_screen_off(None, {'minutes': bad})


def test_the_hint_carries_the_users_screen_off(hint):
    s = device.Screen()
    s.blank_after(180)
    s.release()
    assert hint()['idle'] == 180 and hint()['after'] == 180 and not hint()['hold']
    seq = hint()['seq']
    s.blank_after(0)                      # turned off: no "after" an older blanker would blank on
    assert hint()['idle'] == 0 and 'after' not in hint() and hint()['seq'] == seq
    s.hold()
    assert hint()['hold'] and 'after' not in hint() and hint()['seq'] == seq + 1


@pytest.mark.asyncio
async def test_set_screen_off_saves_and_hints(hint, monkeypatch):
    saved = {}
    monkeypatch.setattr(config, 'save', lambda **kw: saved.update(kw))
    c = app.Client.__new__(app.Client)
    c.screen, c.screen_off, c.state = device.Screen(), device.normalize_screen_off(None), 'idle'
    c.set_state = lambda state: None
    assert await c.set_screen_off(minutes=1) == {'enabled': True, 'minutes': 1}
    assert hint()['idle'] == 60 and saved['screen_off'] == {'enabled': True, 'minutes': 1}
    assert await c.set_screen_off(enabled=False) == {'enabled': False, 'minutes': 1}
    assert hint()['idle'] == 0
    with pytest.raises(ValueError):
        await c.set_screen_off(minutes=7)
    assert c.screen_off == {'enabled': False, 'minutes': 1}


@pytest.mark.asyncio
@pytest.mark.parametrize('changes, idle', [({'minutes': 1}, 60), ({'minutes': 30}, 1800), ({'enabled': False}, 0)])
async def test_sleep_hours_keep_the_users_screen_off(hint, monkeypatch, changes, idle):
    """The sleep hours dim the screen; screen-off still follows the setting (the user asked)."""
    monkeypatch.setattr(config, 'save', lambda **kw: None)
    c = app.Client.__new__(app.Client)
    c.screen, c.screen_off, c.state = device.Screen(), device.normalize_screen_off(None), 'idle'
    c.set_state = lambda state: None
    c.sleep, c.bright_in_sleep = {'enabled': True, 'start': '00:00', 'end': '00:00'}, False
    monkeypatch.setattr(app.sleep, 'active', lambda setting, now=None: True)
    c._apply_sleep()
    await c.set_screen_off(**changes)
    assert c.screen.dimmed and hint()['brightness'] == app.sleep.BRIGHTNESS and hint()['idle'] == idle   # 睡眠亮度（第 29 块）
    seq = hint()['seq']
    c._on_new_report()                    # a report in the sleep hours only lights the screen
    assert hint()['seq'] == seq + 1 and hint()['brightness'] == app.sleep.BRIGHTNESS and hint()['idle'] == idle
    assert hint().get('after') == (idle or None)


def test_sleep_setting_has_brightness_and_volume():
    """睡眠时段可以设亮度和音量（设计画布第 29 块）；超出范围的截到 0–100，写错的用默认值。"""
    from thinksmart_client import sleep
    s = sleep.normalize({'enabled': True}, {'brightness': 150, 'volume': 'x'})
    assert s['brightness'] == 100 and s['volume'] == sleep.VOLUME
    assert sleep.normalize(None)['brightness'] == sleep.BRIGHTNESS


def test_sleep_brightness_goes_to_the_blanker(tmp_path, monkeypatch):
    """调暗时把睡眠亮度当亮度发给熄屏程序（不用它固定的暗亮度）。"""
    import json
    from thinksmart_client import device
    monkeypatch.setattr(device, 'HINT_PATH', tmp_path / 'hint.json')
    s = device.Screen()
    s.sleep_level = 25
    s.dim(True)
    h = json.loads((tmp_path / 'hint.json').read_text())
    assert h['dim'] is False and h['brightness'] == 25
    s.dim(False)
    h = json.loads((tmp_path / 'hint.json').read_text())
    assert h['dim'] is False and h['brightness'] == 100
