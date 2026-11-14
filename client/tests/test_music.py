"""放歌 (media.Queue / Player 的音乐部分, app.music_control, self.music.play; 设计画布第 58 块).

ffmpeg 换成假的：按请求的 -ss 吐出几帧 PCM；网关换成假的 _hub_http。不联网、不出声。
Run like test_barge_in.py (same stubs).
"""
import asyncio
import json
import sys
import time
import types

import numpy as np
import pytest

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

from thinksmart_client import app, config, kids, mcp, media  # noqa: E402

TRACKS = [{'id': f'n.s{i}', 'title': t, 'artist': '宝宝巴士', 'album': '儿歌', 'duration': 60, 'cover': 'n.al-1'}
          for i, t in enumerate(['两只老虎', '小星星', '小兔子乖乖', '数鸭子'])]


def test_queue_modes():
    q = media.Queue(TRACKS)
    assert q.current['title'] == '两只老虎' and q.upcoming()['title'] == '小星星'
    assert [q.advance()['title'] for _ in range(4)] == ['小星星', '小兔子乖乖', '数鸭子', '两只老虎']   # 列表循环
    assert q.back()['title'] == '数鸭子'
    q.set_mode('one')
    assert q.advance(auto=True)['title'] == '数鸭子' and q.upcoming()['title'] == '数鸭子'   # 单曲循环：自己放完还是这首
    assert q.advance()['title'] == '两只老虎'                                          # 说「下一首」照样换
    q = media.Queue(TRACKS, start=2, mode='shuffle', rng=np.random.default_rng(3))
    assert q.current['title'] == '小兔子乖乖'
    seen = [q.advance()['title'] for _ in range(3)]
    assert sorted(seen + ['小兔子乖乖']) == sorted(t['title'] for t in TRACKS)          # 一轮里每首一次
    for _ in range(20):
        before = q.current['id']
        assert q.advance()['id'] != before                                              # 重新打乱也不连着放同一首
    cur = q.current['id']
    q.set_mode('loop')
    assert q.order == [0, 1, 2, 3] and q.current['id'] == cur
    with pytest.raises(ValueError):
        q.set_mode('party')
    with pytest.raises(ValueError):
        media.Queue([])


class Background:
    def __init__(self):
        self.pushed = 0
        self.cleared = 0
        self.peak = []

    def push(self, x):
        self.pushed += len(x)
        self.peak.append(float(np.abs(x).max()))

    def clear(self):
        self.cleared += 1


class FakeProc:
    """假的 ffmpeg：吐 seconds 秒的 0.5 振幅立体声（每次读一帧，中间让出），读完就结束。"""

    def __init__(self, seconds, chunk=0.1):
        self.returncode = None
        frames = int(media.RATE * seconds)
        self._left = frames * 8
        self._chunk = int(media.RATE * chunk) * 8
        self.stdout = self

    async def read(self, n):
        await asyncio.sleep(0.005)
        if self._left <= 0:
            return b''
        k = min(n, self._left, self._chunk)
        self._left -= k
        return (np.full(k // 4, 0.5, dtype='<f4')).tobytes()

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class Calls(list):
    lengths = None


@pytest.fixture
def ffmpeg(monkeypatch):
    calls = Calls()
    lengths = {}

    async def spawn(*args, **kw):
        calls.append(args)
        url = args[args.index('-i') + 1]
        return FakeProc(lengths.get(url.rsplit('/', 1)[-1], 1.2))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    calls.lengths = lengths
    return calls


def test_player_plays_through_and_pauses(ffmpeg):
    bg, views, errors = Background(), [], []
    p = media.Player(bg, on_change=views.append, on_error=errors.append)
    p.stream_url = lambda sid: f'http://hub/kiosk/music/stream/{sid}'

    async def run():
        now = await p.play_music(TRACKS[:2], mode='loop', name='儿歌')
        assert now['kind'] == 'music' and now['name'] == '两只老虎' and now['next']['name'] == '小星星'
        assert now['list'] == '儿歌' and now['index'] == 1 and now['count'] == 2 and not now['paused']
        await asyncio.sleep(0.02)
        assert ffmpeg[-1][ffmpeg[-1].index('-i') + 1] == 'http://hub/kiosk/music/stream/n.s0' and '-vn' in ffmpeg[-1]
        for _ in range(400):                       # 放完第一首自己接下一首
            await asyncio.sleep(0.01)
            if p.now['name'] == '小星星':
                break
        assert p.now['name'] == '小星星' and p.now['next']['name'] == '两只老虎'
        await asyncio.sleep(0.2)
        now = await p.music_pause()
        assert now['paused'] and now['at'] > 0.1 and p.task is None and bg.cleared >= 1
        at = now['at']
        await p.music_resume()
        await asyncio.sleep(0.02)
        assert '-ss' in ffmpeg[-1] and abs(float(ffmpeg[-1][ffmpeg[-1].index('-ss') + 1]) - at) < 0.11
        await p.music_mode('one')
        assert p.now['mode'] == 'one' and p.now['next']['name'] == '小星星'
        assert (await p.music_next())['name'] == '两只老虎'
        assert (await p.music_prev())['name'] == '小星星'
        await p.stop()
        assert p.now is None and p.queue is None
    asyncio.run(run())
    assert bg.pushed > media.RATE and errors == [] and views[-1]['playing'] is None


def test_timer_fades_and_stops(ffmpeg):
    ffmpeg.lengths['n.s0'] = 30
    bg, views = Background(), []
    p = media.Player(bg, on_change=views.append)
    p.stream_url = lambda sid: f'http://hub/{sid}'

    async def run():
        now = await p.play_music(TRACKS[:1], minutes=0.6 / 60)          # 0.6 秒后停
        assert now['ends']
        await asyncio.wait_for(p.task, 5)
        assert p.now is None
    asyncio.run(run())
    assert bg.peak[0] < 0.5 and bg.peak[-1] < bg.peak[0]               # 定时快到了：一直在变小


def test_broken_tracks_give_up(ffmpeg):
    for t in TRACKS:
        ffmpeg.lengths[t['id']] = 0.05
    errors = []
    p = media.Player(Background(), on_error=errors.append)
    p.stream_url = lambda sid: f'http://hub/{sid}'

    async def run():
        await p.play_music(TRACKS)
        await asyncio.wait_for(p.task, 5)
    asyncio.run(run())
    assert p.now is None and len(ffmpeg) == 3 and '放不了' in errors[0]


def test_radio_replaces_music_and_gentle_birds_leave_it(ffmpeg):
    ffmpeg.lengths['n.s0'] = 30
    p = media.Player(Background())
    p.stream_url = lambda sid: f'http://hub/{sid}'

    async def run():
        await p.play_music(TRACKS[:1])
        assert await p.gentle_birds(time.time() + 60) is None and p.music_playing()
        await p.stop()
    asyncio.run(run())


# ---------------------------------------------------------------- app.music_control 和 self.music.play

def make_client(tmp_path, monkeypatch, ffmpeg):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.cfg = {**config.DEFAULTS, 'device_id': 'dev1', 'hub_http': 'http://127.0.0.1:16688'}
    c.audio = types.SimpleNamespace(background=Background())
    c.media = None
    c.kids = kids.normalize(None)
    c.kids_seconds = 0
    c.music_info = {'ready': True, 'tags': {'kids': 1, 'bedtime': 0}}
    c.asked = []

    async def hub_http(path, body=None):
        c.asked.append(path)
        if path.startswith('/kiosk/music/find'):
            if '%E4%B8%A4' in path:                                    # 两只老虎
                return {'ok': True, 'tracks': TRACKS[:1], 'match': 'song', 'name': '两只老虎'}
            return {'ok': True, 'tracks': [], 'match': ''}
        if path.startswith('/kiosk/music/tag/kids'):
            return {'ok': True, 'tracks': TRACKS, 'name': '儿歌'}
        if path.startswith('/kiosk/music/tag/bedtime'):
            return {'ok': True, 'tracks': [], 'error': '还没在网关后台「音乐」页把哪个文件夹或歌单标成「睡前故事」'}
        return {'ok': True}
    c._hub_http = hub_http
    return c


async def call(c, **args):
    r = await mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'self.music.play', 'arguments': args}}, client=c)
    assert r['result']['isError'] is False
    return json.loads(r['result']['content'][0]['text'])


def test_music_tool(tmp_path, monkeypatch, ffmpeg):
    ffmpeg.lengths.update({t['id']: 30 for t in TRACKS})
    c = make_client(tmp_path, monkeypatch, ffmpeg)
    assert any(t['name'] == 'self.music.play' for t in mcp.TOOLS)

    async def run():
        r = await c.music_control('play', query='两只老虎')
        assert r == {'success': True, 'response': '好，放两只老虎。'}
        assert c.media.now['name'] == '两只老虎' and c.media.now['mode'] == 'loop'
        await asyncio.sleep(0.02)
        url = ffmpeg[-1][ffmpeg[-1].index('-i') + 1]
        assert url == 'http://127.0.0.1:16688/kiosk/music/stream/n.s0'          # 走网关中转，设备没有密码
        r = await c.music_control('play', tag='kids', minutes=20)
        assert r['response'] == '好，放儿歌，第一首' + c.media.now['name'] + '，20 分钟后停。' and c.media.now['mode'] == 'shuffle'
        assert c.media.now['ends'] > time.time() + 19 * 60
        assert (await c.music_control('next'))['response'].startswith('下一首')
        assert (await c.music_control('pause'))['response'] == '好，暂停了。' and c.media.now['paused']
        assert (await c.music_control('resume'))['response'] == '好，接着放。'
        assert (await c.music_control('mode', mode='one'))['response'] == '好，单曲循环。'
        assert (await c.music_control('status'))['playing'] == c.media.now['name']
        r = await c.music_control('play', query='没有这首')
        assert r['pass'] and '没找到' in r['reason'] and c.media.music_playing()      # 没找到：照旧放着
        r = await c.music_control('play', tag='bedtime')
        assert r['pass'] and '睡前故事' in r['reason']
        r = await c.music_control('play', query='东方广播')                          # 电台的名字：放电台
        assert '东方广播' in r['response'] and c.media.now['kind'] == 'radio'
        assert (await c.music_control('next'))['pass']                             # 没在放歌
        assert (await c.music_control('pause'))['response'] == '好，电台停了。'
        assert (await c.music_control('stop'))['response'] == '好，停了。'
    asyncio.run(run())


def test_music_tool_over_mcp_and_kids_bedtime(tmp_path, monkeypatch, ffmpeg):
    ffmpeg.lengths['n.s0'] = 30
    c = make_client(tmp_path, monkeypatch, ffmpeg)

    async def run():
        r = await call(c, action='play', query='两只老虎')
        assert r == {'action': 'RESPONSE', 'response': '好，放两只老虎。'}
        await c.media.stop()
        r = await call(c, action='play', query='没有这首')
        assert r['success'] is False and r['pass'] and '没找到' in r['reason']
        c.kids = kids.normalize({'enabled': True, 'bedtime': '00:00'})                   # 一直是睡觉时间
        monkeypatch.setattr(kids, 'MORNING', '23:59')
        r = await call(c, action='play', tag='kids')
        assert r['success'] is False and r['reason'] == kids.SAY_BEDTIME
        r = await call(c, action='play', query='雨声')                                       # 助眠声睡觉时间照放
        assert '雨声' in r['response']
        await c.media.stop()
    asyncio.run(run())
    v = c.media_view()
    assert v['music'] == {'ready': True, 'tags': {'kids': 1, 'bedtime': 0}} and [m['id'] for m in v['modes']] == ['loop', 'one', 'shuffle']
