"""视频对讲（intercom.py，设计画布第 51 块）：来电、接听（真的用 aiortc 和一个假手机连通）、挂断、未接。"""
import asyncio
import fractions
import sys
import types

import numpy as np
import pytest
import pytest_asyncio

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))
try:
    import thinksmart_client.speex_aec  # noqa: F401
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)

from thinksmart_client import intercom as ic  # noqa: E402

aiortc = pytest.importorskip('aiortc')
av = pytest.importorskip('av')


class Channel:
    def __init__(self):
        self.pushed = []

    def push(self, a):
        self.pushed.append(a)

    def clear(self):
        pass


@pytest_asyncio.fixture
async def hub():
    """假网关：设备来等事件、回应；测试往 queue 里放来电。"""
    from aiohttp import web
    state = {'queue': asyncio.Queue(), 'posted': []}

    async def wait(request):
        try:
            events = [await asyncio.wait_for(state['queue'].get(), 2)]
        except asyncio.TimeoutError:
            events = []
        return web.json_response({'ok': True, 'events': events})

    async def event(request):
        state['posted'].append(await request.json())
        return web.json_response({'ok': True})
    a = web.Application()
    a.router.add_get('/kiosk/call/wait', wait)
    a.router.add_post('/kiosk/call/event', event)
    runner = web.AppRunner(a)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    state['url'] = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    yield state
    await runner.cleanup()


def device(url, monkeypatch, **kw):
    monkeypatch.setattr(ic, 'CAMERA', '/nonexistent-camera')          # 没有摄像头：发黑画面
    published, active, rings = [], [], []
    d = ic.Intercom(url, 'dev1', Channel(), lambda k, v: published.append(v), lambda: '', on_active=active.append,
                    ring=lambda: rings.append(1), **kw)
    d.published, d.actives, d.rings = published, active, rings
    return d


class Tone(aiortc.MediaStreamTrack):
    kind = 'audio'

    def __init__(self):
        super().__init__()
        self.n = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        t = (self.n + np.arange(960)) / 48000
        pcm = (np.sin(2 * np.pi * 440 * t) * 8000).astype('<i2').reshape(1, -1)
        f = av.AudioFrame.from_ndarray(pcm, format='s16', layout='mono')
        f.sample_rate, f.pts, f.time_base = 48000, self.n, fractions.Fraction(1, 48000)
        self.n += 960
        return f


class Color(aiortc.VideoStreamTrack):
    async def recv(self):
        pts, tb = await self.next_timestamp()
        f = av.VideoFrame.from_ndarray(np.full((240, 320, 3), 200, np.uint8), format='rgb24')
        f.pts, f.time_base = pts, tb
        return f


async def until(cond, seconds=15):
    for _ in range(int(seconds * 20)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_ring_answer_talk_hangup(hub, monkeypatch):
    d = device(hub['url'], monkeypatch)
    d.start()
    phone = aiortc.RTCPeerConnection()
    phone.addTrack(Tone())
    phone.addTrack(Color())
    heard = []

    @phone.on('track')
    def on_track(track):
        async def eat():
            while True:
                heard.append((await track.recv()).__class__.__name__)
        asyncio.ensure_future(eat())
    await phone.setLocalDescription(await phone.createOffer())
    await hub['queue'].put({'type': 'ring', 'id': 'c1', 'caller': '妈妈', 'sdp': phone.localDescription.sdp})
    assert await until(lambda: d.state == 'ringing')
    assert d.view()['caller'] == '妈妈' and d.actives == [True] and await until(lambda: d.rings)
    await d.accept()
    answer = hub['posted'][-1]
    assert answer['type'] == 'answer' and answer['id'] == 'c1' and 'm=video' in answer['sdp'] and 'sendrecv' in answer['sdp']
    await phone.setRemoteDescription(aiortc.RTCSessionDescription(sdp=answer['sdp'], type='answer'))
    ok = await until(lambda: d.state == 'talking')
    assert ok, (d.state, d.pc and d.pc.connectionState, d.pc and d.pc.iceConnectionState, phone.connectionState, phone.iceConnectionState)
    assert await until(lambda: len(d.channel.pushed) > 10)                     # 对方的声音进了通话声道
    loud = np.concatenate(d.channel.pushed[-10:])
    assert loud.shape[1] == 2 and np.abs(loud).max() > 0.05
    assert await until(lambda: d.remote_view.jpeg is not None)                  # 对方的画面转成了 JPEG
    assert await until(lambda: d.self_view.jpeg is not None)                    # 自己的小窗
    assert d.remote_view.jpeg[:2] == b'\xff\xd8' and d.self_view.jpeg[:2] == b'\xff\xd8'
    d.mic_feed(np.full(320, 0.1, np.float32))
    assert await until(lambda: 'AudioFrame' in heard and 'VideoFrame' in heard)  # 手机收到了设备的声音和画面
    await d.hangup()
    assert hub['posted'][-1]['type'] == 'hangup' and d.state == 'ended' and d.actives[-1] is False
    assert d.history[0]['caller'] == '妈妈' and not d.history[0]['missed']
    await phone.close()
    d.task.cancel()


@pytest.mark.asyncio
async def test_missed_busy_and_auto_answer(hub, monkeypatch):
    saved = []
    d = device(hub['url'], monkeypatch, saved=saved.append)
    d.start()
    await hub['queue'].put({'type': 'ring', 'id': 'c1', 'caller': '爸爸', 'sdp': 'x'})
    assert await until(lambda: d.state == 'ringing')
    await hub['queue'].put({'type': 'ring', 'id': 'c2', 'caller': '奶奶', 'sdp': 'x'})       # 正在响：第二个占线
    assert await until(lambda: any(p['type'] == 'busy' and p['id'] == 'c2' for p in hub['posted']))
    await hub['queue'].put({'type': 'hangup', 'id': 'c1', 'reason': 'missed'})
    assert await until(lambda: d.state == 'ended')
    assert d.history[0] == {**d.history[0], 'caller': '爸爸', 'missed': True} and saved
    assert '没接到' in d.view()['ended']
    monkeypatch.setattr(ic, 'AUTO_ANSWER_AFTER', 0)
    d.set_auto_answer(True)
    d.state = 'idle'
    accepted = []

    async def fake_accept():
        accepted.append(1)
        d.state = 'connecting'
    monkeypatch.setattr(d, 'accept', fake_accept)
    await hub['queue'].put({'type': 'ring', 'id': 'c3', 'caller': '爸爸', 'sdp': 'x'})
    assert await until(lambda: accepted)
    d.task.cancel()


def test_sizes_and_settings():
    assert ic.frame_size('') == (640, 360) and ic.frame_size('transpose=1') == (360, 640)
    assert ic.normalize({'auto_answer': 'yes'}) == {'auto_answer': False}
    assert ic.load_history([{'t': 1, 'caller': 'x'}, 'bad']) == [{'t': 1, 'caller': 'x'}]
