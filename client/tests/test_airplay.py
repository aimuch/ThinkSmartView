"""AirPlay（airplay.py，设计画布第 49 块）：配置、元数据、声音进背景声道、安装和开关。"""
import asyncio
import base64
import sys
import types

import numpy as np
import pytest

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):   # 不用真的声卡
    sys.modules.setdefault(name, types.ModuleType(name))
try:
    import thinksmart_client.speex_aec  # noqa: F401  (needs libspeexdsp)
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)

from thinksmart_client import airplay  # noqa: E402
from thinksmart_client.audio import OUT_RATE  # noqa: E402


def item(typ, code, data=None):
    head = f'<item><type>{typ.encode().hex()}</type><code>{code.encode().hex()}</code><length>{len(data or b"")}</length>'
    body = f'\n<data encoding="base64">\n{base64.b64encode(data).decode()}</data>' if data is not None else ''
    return (head + body + '</item>\n').encode()


class Channel:
    def __init__(self):
        self.pushed, self.cleared, self.target = [], 0, 1.0

    def push(self, a):
        self.pushed.append(a)

    def clear(self):
        self.cleared += 1


def test_names_and_config():
    assert airplay.default_name('书房') == '小麦·书房' and airplay.default_name('') == '小麦'
    assert airplay.clean_name(' 我的"音箱"\\\n ') == '我的音箱'
    assert len(airplay.clean_name('长' * 99)) == airplay.NAME_MAX
    conf = airplay.config_text('小麦·客厅')
    assert 'name = "小麦·客厅";' in conf and 'output_backend = "stdout";' in conf
    assert f'pipe_name = "{airplay.META}";' in conf


def test_parse_metadata_items():
    buf = item('ssnc', 'pbeg') + item('ssnc', 'snam', 'andy 的 iPhone'.encode()) + item('core', 'minm', '晴天'.encode())
    items, rest = airplay.parse_items(buf + b'<item><type>63')
    assert items == [('ssnc', 'pbeg', b''), ('ssnc', 'snam', 'andy 的 iPhone'.encode()), ('core', 'minm', '晴天'.encode())]
    assert rest == b'<item><type>63'
    a = airplay.AirPlay(Channel(), lambda *a: None)
    assert a._apply(items + [('core', 'asar', '周杰伦'.encode())])
    assert a.now == {'client': 'andy 的 iPhone', 'title': '晴天', 'artist': '周杰伦'}
    assert not a._apply([('core', 'minm', '晴天'.encode())])        # 没变
    a._apply([('ssnc', 'pend', b'')])
    assert a.now == {}


def test_view_shows_song_only_while_playing(monkeypatch):
    a = airplay.AirPlay(Channel(), lambda *a: None, {'enabled': True, 'name': ''})
    a.device_name = '书房'
    a.now = {'client': 'iPad', 'title': '稻香'}
    v = a.view()
    assert v['name'] == '小麦·书房' and v['custom_name'] == '' and v['playing'] is False and v['title'] == ''
    a.playing_until = airplay.time.monotonic() + 1
    assert a.view()['title'] == '稻香' and a.view()['client'] == 'iPad'


@pytest.mark.asyncio
async def test_audio_is_resampled_into_the_channel():
    ch = Channel()
    a = airplay.AirPlay(ch, lambda *a: None)
    t = np.arange(airplay.RATE // 2) / airplay.RATE                   # 0.5 s of a 440 Hz tone
    tone = (np.sin(2 * np.pi * 440 * t) * 16000).astype('<i2')
    stream = asyncio.StreamReader()
    stream.feed_data(np.repeat(tone[:, None], 2, axis=1).tobytes())
    stream.feed_eof()
    await a._read_audio(type('P', (), {'stdout': stream})())
    out = np.concatenate(ch.pushed)
    assert out.shape[1] == 2 and out.dtype == np.float32
    assert abs(len(out) - OUT_RATE // 2) < OUT_RATE // 50                # 44.1 kHz → 48 kHz
    assert 0.4 < np.abs(out).max() < 0.55 and a.playing()


@pytest.mark.asyncio
async def test_switch_needs_install_and_restarts(monkeypatch):
    changes = []
    a = airplay.AirPlay(Channel(), lambda kind, v: changes.append(v))
    a.installed = False
    with pytest.raises(ValueError):
        await a.set(enabled=True)
    a.installed = True
    mdns = []

    async def fake_mdns(on):
        mdns.append(on)
        return True
    monkeypatch.setattr(a, '_mdns', fake_mdns)
    started = []
    monkeypatch.setattr(a, 'start', lambda: started.append(a.shown_name()))
    await a.set(enabled=True)
    await a.set(name='书桌音箱')
    assert started == ['小麦', '书桌音箱'] and a.saved() == {'enabled': True, 'name': '书桌音箱'}
    await a.set_device_name('书房')                    # 自己起过名字：不变
    assert a.shown_name() == '书桌音箱'
    await a.set(enabled=False)
    assert changes[-1]['enabled'] is False and mdns == [False]       # 关掉时把 avahi 也关了


@pytest.mark.asyncio
async def test_install_runs_the_system_script(tmp_path, monkeypatch):
    script = tmp_path / 'install'
    script.write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/ran"\necho \'{"installed": true}\'\n')
    script.chmod(0o755)
    sudo = tmp_path / 'sudo'
    sudo.write_text('#!/bin/sh\nshift\nexec "$@"\n')        # sudo -n X install -> X install
    sudo.chmod(0o755)
    monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')
    monkeypatch.setattr(airplay, 'INSTALLER', str(script))
    which = {'v': None}
    monkeypatch.setattr(airplay.shutil, 'which', lambda name: which['v'])
    a = airplay.AirPlay(Channel(), lambda *a: None)
    assert a.available and not a.installed
    which['v'] = '/usr/bin/shairport-sync'
    v = await a.install()
    assert (tmp_path / 'ran').read_text().strip() == 'install'
    assert v['installed'] and not v['installing'] and v['error'] is None


@pytest.mark.asyncio
async def test_install_failure_is_reported(tmp_path, monkeypatch):
    script = tmp_path / 'install'
    script.write_text('#!/bin/sh\necho 没连上 Ubuntu 软件源 >&2\nexit 1\n')
    script.chmod(0o755)
    sudo = tmp_path / 'sudo'
    sudo.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    sudo.chmod(0o755)
    monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')
    monkeypatch.setattr(airplay, 'INSTALLER', str(script))
    monkeypatch.setattr(airplay.shutil, 'which', lambda name: None)
    a = airplay.AirPlay(Channel(), lambda *a: None)
    with pytest.raises(ValueError, match='没连上'):
        await a.install()
    assert a.view()['error'] == '没连上 Ubuntu 软件源' and not a.installing
