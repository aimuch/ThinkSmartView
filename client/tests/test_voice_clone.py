"""复刻我的声音（设计画布第 50 块）：设备上录、太短不要、上传成 16 kHz WAV 给网关。"""
import io
import sys
import types
import wave

import numpy as np
import pytest
import pytest_asyncio

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))
if 'thinksmart_client.opus' not in sys.modules:
    opus = types.ModuleType('thinksmart_client.opus')
    opus.Encoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'')
    opus.Decoder = lambda *a: types.SimpleNamespace(decode=lambda p: np.zeros(0, np.float32))
    sys.modules['thinksmart_client.opus'] = opus
try:
    import thinksmart_client.speex_aec  # noqa: F401
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)

from thinksmart_client import app  # noqa: E402


@pytest_asyncio.fixture
async def hub():
    from aiohttp import web
    seen = {}

    async def clone(request):
        seen['query'], seen['body'] = dict(request.query), await request.read()
        return web.json_response({'ok': True, 'clone': {'name': request.query['name'], 'voice_id': 'cosyvoice-v3-flash-xm1-a'}})
    a = web.Application()
    a.router.add_post('/kiosk/voice/clone', clone)
    runner = web.AppRunner(a)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    seen['url'] = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    yield seen
    await runner.cleanup()


def client(url):
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.events.publish = lambda *a: None
    c.cfg = {'device_id': 'dev1', 'hub_http': url}
    c.wake_view = lambda: {}
    return c


def speech(seconds):
    t = np.arange(int(16000 * seconds)) / 16000
    return [(0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)]


@pytest.mark.asyncio
async def test_record_and_upload(hub):
    c = client(hub['url'])
    c.wake_rec = speech(15.3)
    assert c.clone_record_stop() == {'seconds': 15}
    v = await c.clone_upload('妈妈')
    assert v['name'] == '妈妈' and hub['query'] == {'device': 'dev1', 'name': '妈妈'}
    with wave.open(io.BytesIO(hub['body'])) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert abs(w.getnframes() / 16000 - 15.0) < 0.05          # 开头的提示音那 0.3 秒去掉了
    assert c.clone_audio is None
    with pytest.raises(ValueError, match='还没录'):
        await c.clone_upload('妈妈')


def test_too_short_or_silent():
    c = client('http://127.0.0.1:9')
    c.wake_rec = speech(5)
    with pytest.raises(ValueError, match='只录到 5 秒'):
        c.clone_record_stop()
    c.wake_rec = [np.zeros(16000 * 15, np.float32)]
    with pytest.raises(ValueError, match='没听到'):
        c.clone_record_stop()
