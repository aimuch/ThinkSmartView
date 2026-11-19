"""静音和勿扰（quiet.py，设计画布第 81 块）：设置的规矩、锁住的静音语音取消不了、什么时候喇叭该没声。

Run like test_barge_in.py.
"""
import datetime
import sys
import types

import numpy as np
import pytest

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))
if 'thinksmart_client.opus' not in sys.modules:
    opus = types.ModuleType('thinksmart_client.opus')
    opus.Encoder = opus.Decoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'', decode=lambda p: b'')
    sys.modules['thinksmart_client.opus'] = opus

from thinksmart_client import app, quiet  # noqa: E402

NOW = datetime.datetime(2026, 11, 20, 21, 30).timestamp()


def test_settings():
    q = quiet.apply(None, {'mute': True, 'lock': True}, now=NOW)
    assert q['mute'] and q['lock']
    with pytest.raises(ValueError):
        quiet.apply(q, {'mute': False}, now=NOW, by_voice=True)        # 锁住的：说「取消静音」不行
    assert quiet.apply(q, {'mute': False}, now=NOW) == {**q, 'mute': False, 'lock': False}   # 屏幕上点可以
    assert not quiet.apply(None, {'lock': True}, now=NOW)['lock']       # 没静音时锁不上
    q = quiet.apply(None, {'dnd': 60}, now=NOW)
    assert q['dnd_until'] == NOW + 3600 and quiet.dnd_active(q, NOW + 3599) and not quiet.dnd_active(q, NOW + 3601)
    q = quiet.apply(None, {'dnd': 'morning'}, now=NOW)
    assert datetime.datetime.fromtimestamp(q['dnd_until']) == datetime.datetime(2026, 11, 21, 7, 0)
    assert quiet.view(q, NOW)['dnd_text'] == '到 明天 07:00'
    q = quiet.apply(q, {'dnd': 'forever'}, now=NOW)
    assert quiet.dnd_active(q, NOW + 10 ** 9) and quiet.view(q, NOW)['dnd_text'] == '一直'
    assert quiet.apply(q, {'dnd': 'off'}, now=NOW)['dnd_until'] is None
    for bad in (-5, 99999, 'soon'):
        with pytest.raises(ValueError):
            quiet.apply(None, {'dnd': bad}, now=NOW)
    assert quiet.normalize({'mute': 'yes', 'dnd_until': True, 'alarms': 0}) == {'mute': False, 'lock': False, 'dnd_until': None, 'alarms': True}


def client(q, ring=None, call=None):
    c = app.Client.__new__(app.Client)
    c.quiet = quiet.normalize(q)
    c.ring = ring
    c.intercom = types.SimpleNamespace(state=call) if call else None
    return c


ALARM = {'items': [{'kind': 'alarm'}]}
REMINDER = {'items': [{'kind': 'reminder'}]}


def test_what_is_heard():
    mute, dnd = {'mute': True}, {'dnd_until': quiet.FOREVER}
    assert not client({})._silenced()
    assert client(mute)._silenced()                                         # 静音：回答、提示音、音乐都没声
    assert not client(mute, ring=ALARM)._silenced()                         # 闹钟照响
    assert client({**mute, 'alarms': False}, ring=ALARM)._silenced()        # 设了闹钟也不响
    assert client(mute, ring=REMINDER)._silenced()                          # 提醒不响
    assert not client(mute, call='talking')._silenced()                     # 自己接起来的对讲照常说话
    assert not client(dnd)._silenced()                                      # 勿扰：叫它照常回答
    assert client(dnd, ring=REMINDER)._silenced() and not client(dnd, ring=ALARM)._silenced()
    assert client(dnd, call='ringing')._silenced()                          # 对讲来电只亮屏不响


def test_the_mixer_goes_silent():
    from thinksmart_client import audio
    a = audio.Audio.__new__(audio.Audio)
    a.muted = False
    a.player = audio.Player()
    a.background = a.airplay = a.call = types.SimpleNamespace(pull=lambda n: None)
    a.aec = types.SimpleNamespace(feed_far=lambda out: None)
    a.player.push(np.full((480, 2), 0.5, dtype=np.float32))
    out = np.zeros((240, 2), dtype=np.float32)
    a._out_cb(out, 240, None, None)
    assert out.max() > 0.1
    a.muted = True
    a._out_cb(out, 240, None, None)
    assert out.max() == 0 and a.player._size - a.player._pos == 0          # 声音照样取走，只是不出声
