"""首页、待机页的天气：网关这次没取到（天气服务偶尔超时）时接着显示上一次的，不消失。"""
import asyncio
import sys
import types

import numpy as np

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

from thinksmart_client import app  # noqa: E402

W = {'temp': 10.7, 'text': '小雨', 'icon': '🌧', 'today': {'min': 8, 'max': 15}}


def make(replies):
    c = app.Client.__new__(app.Client)
    c.cfg = {'device_id': 'dev1'}
    c.today = None
    c.airplay = None
    c.events = types.SimpleNamespace(publish=lambda *a: None)
    it = iter(replies)

    async def hub_http(path, body=None):
        return next(it)
    c._hub_http = hub_http
    return c


def test_keeps_the_last_weather_when_the_hub_has_none():
    c = make([{'ok': True, 'lunar': {}, 'weather': W},
              {'ok': True, 'lunar': {}, 'weather': None, 'weather_error': '天气服务连不上'}])
    asyncio.run(c.refresh_today())
    assert c.today['weather']['temp'] == 10.7 and not c.today['weather'].get('stale_min')
    c.today['weather']['_at'] -= 600
    asyncio.run(c.refresh_today())
    w = c.today['weather']
    assert w['temp'] == 10.7 and w['stale_min'] == 10 and c.today['weather_error'] == '天气服务连不上'


def test_nothing_to_keep_when_it_never_had_weather():
    c = make([{'ok': True, 'lunar': {}, 'weather': None, 'weather_error': '还没设住宅地址'}])
    asyncio.run(c.refresh_today())
    assert c.today['weather'] is None
