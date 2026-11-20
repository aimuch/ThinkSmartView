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
VIDEOS = [{'id': f'j.e{i}', 'title': f'第 {i} 集', 'artist': 'Peppa Pig', 'duration': 300, 'cover': 'j.series', 'media': 'video'}
          for i in (1, 2)] + [{'id': 'j.v1', 'title': '红烧肉的做法', 'duration': 600, 'media': 'video'}]


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
    p.stream_url = lambda sid: f'http://hub/kiosk/media/stream/{sid}'

    async def run():
        now = await p.play_music(TRACKS[:2], mode='loop', name='儿歌')
        assert now['kind'] == 'music' and now['name'] == '两只老虎' and now['next']['name'] == '小星星'
        assert now['list'] == '儿歌' and now['index'] == 1 and now['count'] == 2 and not now['paused']
        await asyncio.sleep(0.02)
        assert ffmpeg[-1][ffmpeg[-1].index('-i') + 1] == 'http://hub/kiosk/media/stream/n.s0' and '-vn' in ffmpeg[-1]
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
    c.publish_cook = lambda: None
    c.kids_seconds = 0
    c.music_info = {'ready': True, 'sections': [{'id': 'songs', 'name': '儿歌', 'kid': True, 'play': 'shuffle'}]}
    c.screen = types.SimpleNamespace(video=lambda on: c.lit.append(on), poke=lambda: None)
    c.lit = []
    c.cooking = None
    c.events.publish = lambda *a: None
    c.asked = []

    async def hub_http(path, body=None):
        c.asked.append((path, body))
        if path.startswith('/kiosk/media/find'):
            if '%E4%B8%A4' in path:                                    # 两只老虎
                return {'ok': True, 'tracks': TRACKS[:1], 'match': 'song', 'name': '两只老虎'}
            if 'video=1' in path:
                return {'ok': True, 'tracks': VIDEOS[2:], 'match': 'song', 'name': '红烧肉的做法'}
            return {'ok': True, 'tracks': [], 'match': ''}
        if path.startswith('/kiosk/media/section/songs'):
            return {'ok': True, 'tracks': TRACKS, 'start': 0, 'at': 0, 'section': {'id': 'songs', 'name': '儿歌', 'kid': True, 'play': 'shuffle'}}
        if path.startswith('/kiosk/media/section/%E8%8B%B1%E8%AF%AD'):   # 英语
            lesson = 2 if 'lesson=3' in path else 1
            return {'ok': True, 'tracks': VIDEOS[:2] + TRACKS[:1], 'start': lesson, 'at': 0 if lesson == 2 else 42.0, 'lesson': lesson + 1,
                    'section': {'id': 'kid_english', 'name': '英语启蒙', 'kid': True, 'play': 'course'}}
        if path.startswith('/kiosk/media/section/stories'):
            return {'ok': True, 'tracks': [], 'error': '「睡前故事」还是空的：在网关后台「媒体」页把文件夹或歌单加进去'}
        if path.startswith('/kiosk/media/info/'):
            return {'ok': True, 'direct': 'e1' in path}
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
        assert url == 'http://127.0.0.1:16688/kiosk/media/stream/n.s0'          # 走网关中转，设备没有密码
        r = await c.music_control('play', tag='kids', minutes=20)                 # 58 块的说法：分区「儿歌」
        assert r['response'] == '好，放儿歌，第一首' + c.media.now['name'] + '，20 分钟后停。' and c.media.now['section']['id'] == 'songs'
        assert c.media.now['ends'] > time.time() + 19 * 60
        assert (await c.music_control('next'))['response'].startswith('下一首')
        assert (await c.music_control('pause'))['response'] == '好，暂停了。' and c.media.now['paused']
        assert (await c.music_control('resume'))['response'] == '好，接着放。'
        assert (await c.music_control('mode', mode='one'))['response'] == '好，单曲循环。'
        assert (await c.music_control('status'))['playing'] == c.media.now['name']
        r = await c.music_control('play', query='没有这首')
        assert r['pass'] and '没找到' in r['reason'] and c.media.music_playing()      # 没找到：照旧放着
        r = await c.music_control('play', section='stories')
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
    assert v['music']['ready'] and [m['id'] for m in v['modes']] == ['loop', 'one', 'shuffle']


# ---------------------------------------------------------------- 视频、按顺序的分区、做菜的视频（设计画布第 60 块）

def test_video_items_wait_for_the_page(tmp_path, monkeypatch, ffmpeg):
    ffmpeg.lengths['n.s0'] = 0.3
    c = make_client(tmp_path, monkeypatch, ffmpeg)
    reported = []

    async def run():
        r = await c.music_control('play', section='英语')
        assert r['response'] == '好，英语启蒙接着上次的，第 2 集。'
        p = c.media
        p.on_progress = reported.append
        now = p.now
        assert now['media'] == 'video' and now['section']['play'] == 'course' and now['index'] == 2
        await asyncio.sleep(0.05)
        v = p.now['video']
        assert v['src'].endswith('/kiosk/media/stream/j.e2?t=42') and v['base'] == 42 and not v['direct']   # 转码：从 42 秒开始
        assert c.lit[-1] is True and not ffmpeg                         # 屏幕常亮；视频不走 ffmpeg
        c.video_event('pos', 100, 300)
        assert p.position() == 100
        await p.music_pause()
        assert p.now['paused'] and p.now['at'] == 100 and c.lit[-1] is False
        assert reported[-1] == {'section': 'kid_english', 'id': 'j.e2', 'index': 1, 'at': 100, 'duration': 300.0, 'title': '第 2 集'}
        await p.music_resume()
        await asyncio.sleep(0.05)
        assert p.now['video']['src'].endswith('?t=100')
        c.video_event('ended')                                          # 网页报放完了：记进度、下一个（音频）
        for _ in range(100):
            await asyncio.sleep(0.01)
            if p.now['media'] == 'audio':
                break
        assert reported[-1]['at'] == 300 and p.now['name'] == '两只老虎' and c.lit[-1] is False and ffmpeg
        await p.stop()
    asyncio.run(run())


def test_lesson_and_cook_video(tmp_path, monkeypatch, ffmpeg):
    c = make_client(tmp_path, monkeypatch, ffmpeg)

    async def run():
        r = await c.music_control('play', section='英语', lesson=3)
        assert r['response'] == '好，英语启蒙第 3 个，两只老虎。'
        assert any('lesson=3' in a for a, _ in c.asked) and any('device=dev1' in a for a, _ in c.asked)
        await c.media.stop()
        assert (await c.music_control('cook_video'))['pass']             # 没在做菜
        c.cooking = {'title': '红烧肉', 'steps': ['a', 'b'], 'i': 0}
        await c._cook_video('红烧肉')
        assert c.cooking['video']['id'] == 'j.v1' and any('section=recipes' in a and 'video=1' in a for a, _ in c.asked)
        r = await c.music_control('cook_video')
        assert r['response'] == '好，放红烧肉的做法。' and c.media.now['media'] == 'video'
        await c.media.stop()
        c.kids = kids.normalize({'enabled': True, 'bedtime': ''})        # 不管现在几点
        await c.music_control('play', query='两只老虎')
        assert 'kids=1' in [a for a, _ in c.asked if a.startswith('/kiosk/media/find')][-1]   # 儿童模式只找儿童区
        await c.media.stop()
    asyncio.run(run())
