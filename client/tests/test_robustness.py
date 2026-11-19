"""Things that must survive this device's conditions (code review 2026-11-19): no battery clock (the
system clock jumps about a minute after a boot), power cuts without warning, and one unexpected error in
a standing loop.

Run like test_barge_in.py.
"""
import asyncio
import sys
import time
import types

import pytest

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))
if 'thinksmart_client.opus' not in sys.modules:
    opus = types.ModuleType('thinksmart_client.opus')
    opus.Encoder = opus.Decoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'', decode=lambda p: b'')
    sys.modules['thinksmart_client.opus'] = opus

from thinksmart_client import app, bg, device, media  # noqa: E402


@pytest.mark.asyncio
async def test_a_standing_loop_starts_again_after_an_error(monkeypatch):
    monkeypatch.setattr(bg, 'RESTART_AFTER', (0, 0))
    runs = []

    async def loop():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError('boom')
        await asyncio.sleep(10)

    task = bg.forever(loop, 'test-loop')
    for _ in range(50):
        await asyncio.sleep(0)
        if len(runs) == 3:
            break
    assert len(runs) == 3 and not task.done()   # failed twice, running again
    task.cancel()


@pytest.mark.asyncio
async def test_a_loop_that_returns_is_not_started_again():
    runs = []

    async def once():
        runs.append(1)

    await bg.forever(once, 'once')
    assert runs == [1]


def sleep_client(monkeypatch, tmp_path, saved):
    monkeypatch.setattr(device, 'HINT_PATH', tmp_path / 'screen.json')
    c = app.Client.__new__(app.Client)
    c.screen = device.Screen()
    c.cfg = {'sleep_saved_volume': saved} if saved is not None else {}
    c.sleep, c.bright_in_sleep, c.sleeping = {'enabled': True, 'start': '22:00', 'end': '07:00'}, False, False
    started = []
    monkeypatch.setattr(bg, 'spawn', lambda coro, name=None: (started.append(name), coro.close()))
    return c, started


def test_sleep_hours_wait_for_the_clock(monkeypatch, tmp_path):
    """断电重启后一分钟里系统时间还是断电那会儿的：不按钟点判断、不动音量，接着断电前的样子。"""
    monkeypatch.setattr(app.alarms, 'clock_synced', lambda: False)
    monkeypatch.setattr(app.sleep, 'active', lambda setting, now=None: False)   # the stale clock says daytime
    c, started = sleep_client(monkeypatch, tmp_path, saved=70)                  # but it was night at the cut
    c._apply_sleep()
    assert c.sleeping and c.screen.dimmed and started == []                     # still asleep, volume untouched
    c, started = sleep_client(monkeypatch, tmp_path, saved=None)
    c._apply_sleep()
    assert not c.sleeping and started == []


def test_sleep_hours_go_by_the_clock_once_it_is_set(monkeypatch, tmp_path):
    monkeypatch.setattr(app.alarms, 'clock_synced', lambda: True)
    monkeypatch.setattr(app.sleep, 'active', lambda setting, now=None: True)
    c, started = sleep_client(monkeypatch, tmp_path, saved=None)
    c._apply_sleep()
    assert c.sleeping and started == ['sleep-volume']


@pytest.mark.asyncio
async def test_a_sleep_timer_ignores_a_jump_of_the_system_clock(monkeypatch):
    """「放半小时雨声」后系统时间往前跳了几个小时（开机后对上时间）：照样放满半小时。"""
    m = media.Player.__new__(media.Player)
    m.now, m.music_info = {'kind': 'sound', 'ends': None}, None
    m._set_ends(time.monotonic() + 1800)
    real = time.time
    monkeypatch.setattr(media.time, 'time', lambda: real() + 5 * 3600)
    assert 1790 < m._left() <= 1800
    assert abs(m.view()['playing']['ends'] - (real() + 5 * 3600 + 1800)) < 5   # the page's countdown follows the new clock


def test_health_loop_reports_and_keeps_api_health(monkeypatch):
    """健康上报（设计画布第 83 块）：报到网关；而且不能把 Client.health（/api/health 用的）盖掉——
    0.1.709 第一次发布时就是这样，升级自检不过、两台都退回了 0.1.708。"""
    from thinksmart_client import quiet
    c = app.Client.__new__(app.Client)
    c.cfg = {'device_id': 'dev1'}
    c.state, c.quiet = 'idle', quiet.normalize(None)
    sent = []

    async def hub_http(path, body=None):
        sent.append((path, body))
        return {'ok': True}
    c._hub_http = hub_http
    ticks = {'n': 0}

    async def fake_sleep(_s):
        ticks['n'] += 1
        if ticks['n'] > 1:
            raise asyncio.CancelledError
    monkeypatch.setattr(app.asyncio, 'sleep', fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(c._health_loop())
    assert sent and sent[0][0] == '/kiosk/health' and sent[0][1]['device'] == 'dev1' and 'cpu' in sent[0][1]
    assert 'health' not in c.__dict__ and callable(c.health)


def test_wifi_reading(monkeypatch):
    from thinksmart_client import health
    import subprocess
    out = 'Connected to 94:08:53:2a:fc:87 (on wlan0)\n\tSSID: Magic\n\tfreq: 5180\n\tsignal: -52 dBm\n'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: types.SimpleNamespace(stdout=out))
    assert health.wifi() == {'ssid': 'Magic', 'signal': -52}
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: types.SimpleNamespace(stdout='Not connected.\n'))
    assert health.wifi() is None
