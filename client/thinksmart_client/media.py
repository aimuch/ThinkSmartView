"""网络电台、助眠声和音乐 (设计画布第 11、58 块): all play through the background channel (audio.Background),
like a phone's music over Bluetooth: lowered while talking, muted while an alarm rings. One at a time.

Music (设计画布第 58 块): songs from the family's own library (Navidrome on the NAS, or the hub's music folder),
always through the hub's relay /kiosk/music/stream/<id>: the device never holds the library's password.
A Queue keeps the order (列表循环 / 单曲循环 / 随机); pausing remembers where it was and resumes with
ffmpeg -ss (the hub passes Range on). A timer stops it like the sleep sounds, fading the last seconds.

Radio: the system's ffmpeg decodes a public live stream (-re: read at its own pace) to 48 kHz stereo
float. The stations are ones tried on this device (2026-11-09); a stream that stops answering is
retried a few times, then given up with a toast.

Sleep sounds are made here (no network): white, rain, ocean, fan noise, with a timer that fades out
over the last FADE seconds."""
import asyncio
import logging
import time

import numpy as np

log = logging.getLogger(__name__)

RATE = 48000
CHUNK = 0.1          # seconds per push
FADE = 60.0
STATIONS = {
    'zgzs': ('中国之声', 'http://ngcdn001.cnr.cn/live/zgzs/index.m3u8'),
    'jjzs': ('经济之声', 'http://ngcdn002.cnr.cn/live/jjzs/index.m3u8'),
    'dfgb': ('东方广播（上海）', 'http://satellitepull.cnr.cn/live/wx32dfgbdt/playlist.m3u8'),
    'dycj': ('第一财经广播', 'http://satellitepull.cnr.cn/live/wx32dycjgb/playlist.m3u8'),
    'jsjt': ('江苏交通广播', 'http://satellitepull.cnr.cn/live/wx32jsjtgb/playlist.m3u8'),
    'bjxw': ('北京新闻广播', 'http://satellitepull.cnr.cn/live/wxbjxwgb/playlist.m3u8'),
}
BUILTIN = dict(STATIONS)   # 网关连不上时用这几个；连上了用网关后台「外部服务」里的清单（设计画布第 56 块）


def set_stations(items):
    """网关的 /kiosk/radio：[{"id", "name", "url"}]。原地改 STATIONS（别处拿的是同一个 dict）；空的或不对就回到自带的。"""
    fresh = {}
    for x in items or []:
        if isinstance(x, dict) and x.get('id') and x.get('name') and str(x.get('url') or '').startswith(('http://', 'https://')):
            fresh[str(x['id'])[:32]] = (str(x['name'])[:20], str(x['url']))
    fresh = fresh or BUILTIN
    if fresh == STATIONS:
        return False
    STATIONS.clear()
    STATIONS.update(fresh)
    return True


SOUNDS = {'rain': '雨声', 'ocean': '海浪', 'fan': '风扇', 'white': '白噪音', 'birds': '鸟叫'}
ALIASES = {'中国之声': 'zgzs', '经济之声': 'jjzs', '东方广播': 'dfgb', '上海东方广播': 'dfgb', '第一财经': 'dycj',
           '第一财经广播': 'dycj', '江苏交通': 'jsjt', '江苏交通广播': 'jsjt', '北京新闻': 'bjxw', '北京新闻广播': 'bjxw'}


def find_station(name):
    name = str(name or '').strip()
    if name in STATIONS:
        return name
    if name in ALIASES and ALIASES[name] in STATIONS:
        return ALIASES[name]
    for key, (label, _) in STATIONS.items():
        if name == label:
            return key
    for key, (label, _) in STATIONS.items():
        if name and (name in label or label.split('（')[0] in name):
            return key
    return None


def find_sound(name):
    name = str(name or '').strip()
    if name in SOUNDS:
        return name
    for key, label in SOUNDS.items():
        if name and (name in label or label in name):
            return key
    return {'下雨': 'rain', '大海': 'ocean', '电扇': 'fan', '白噪声': 'white', '鸟鸣': 'birds', '鸟声': 'birds'}.get(name)


LOOP = 2 ** 20       # samples (~22 s): noise made by FFT is periodic, so the buffer loops seamlessly


def _shaped(kind, rng):
    """A loopable noise buffer: white noise shaped in the frequency domain."""
    spec = rng.standard_normal(LOOP // 2 + 1) + 1j * rng.standard_normal(LOOP // 2 + 1)
    f = np.fft.rfftfreq(LOOP, 1 / RATE)
    f[0] = f[1]
    if kind == 'white':
        shape, rms = np.ones_like(f), 0.035
    elif kind == 'rain':      # hiss: pink, lifted above ~800 Hz
        shape, rms = (1 / np.sqrt(f)) * (1 + (f / 800) ** 1.5) / (1 + (f / 6000) ** 2), 0.05
    elif kind == 'ocean':     # rumble: brown, rolled off above 1 kHz
        shape, rms = (1 / f) / (1 + (f / 1000) ** 2), 0.09
    elif kind == 'birds':     # leaves in a light wind under the calls: soft pink, nothing below 300 Hz
        shape, rms = (1 / np.sqrt(f)) / (1 + (f / 5000) ** 2) * (f > 300), 0.008
    else:                     # fan: brown-ish with a soft bump around 300 Hz
        shape, rms = (1 / np.sqrt(f)) / (1 + (f / 1500) ** 2) * (1 + 2 * np.exp(-((f - 300) / 150) ** 2)), 0.05
    shape[f < 20] = 0
    x = np.fft.irfft(spec * shape, LOOP)
    return (x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * rms).astype(np.float32)


def birdcall(rng):
    """One call: 2 to 7 short notes swept up or down somewhere between 2.3 and 6 kHz, with a quick
    vibrato and a soft envelope, short gaps between them (synthesised: no recordings)."""
    base, parts = rng.uniform(2300, 5200), []
    for _ in range(int(rng.integers(2, 8))):
        m = int(RATE * rng.uniform(0.04, 0.15))
        t = np.arange(m) / RATE
        f0 = base * rng.uniform(0.9, 1.15)
        f = np.linspace(f0, f0 * rng.uniform(0.7, 1.4), m) * (1 + 0.04 * np.sin(2 * np.pi * rng.uniform(25, 45) * t))
        env = np.sin(np.pi * np.arange(m) / m) ** 2
        parts += [np.sin(2 * np.pi * np.cumsum(f) / RATE) * env * rng.uniform(0.06, 0.14),
                  np.zeros(int(RATE * rng.uniform(0.03, 0.12)))]
    return np.concatenate(parts).astype(np.float32)


class Noise:
    """Each call returns the next n samples (mono float32): a looped buffer, with the ocean's swell and
    the rain's drops added on top (cheap, vectorised)."""

    def __init__(self, kind, seed=None):
        self.kind = kind
        self.rng = np.random.default_rng(seed)
        self.buf = _shaped(kind, self.rng)
        self.pos = 0
        self.t = 0.0
        self.tail = np.zeros(0, np.float32)   # the part of a bird call that did not fit the last chunk

    def next(self, n):
        idx = (self.pos + np.arange(n)) % LOOP
        self.pos = (self.pos + n) % LOOP
        x = self.buf[idx].copy()
        t = self.t + np.arange(n) / RATE
        self.t += n / RATE
        if self.kind == 'ocean':
            x *= 0.3 + 0.7 * (0.5 + 0.5 * np.sin(2 * np.pi * t / 9.0)) ** 2
        elif self.kind == 'birds':        # a call every 2.5 s on average, at a random place in the chunk
            if self.rng.random() < n / (2.5 * RATE):
                at = int(self.rng.integers(0, n))
                call = birdcall(self.rng)
                grown = np.zeros(max(len(self.tail), at + len(call)), np.float32)
                grown[:len(self.tail)] = self.tail
                grown[at:at + len(call)] += call
                self.tail = grown
            k = min(n, len(self.tail))
            x[:k] += self.tail[:k]
            self.tail = self.tail[k:]
        elif self.kind == 'rain':
            drops = np.flatnonzero(self.rng.random(n) < 0.0006)
            for d in drops[:8]:
                k = min(n - d, 240)
                x[d:d + k] += self.rng.uniform(0.05, 0.18) * np.exp(-np.arange(k) / 40) * self.rng.choice((-1, 1))
        return np.clip(x, -0.9, 0.9).astype(np.float32)


class Mix:
    """几种声音叠在一起（专注的声景，最多两层）：各自的生成器加起来、压低一点免得削顶。"""

    def __init__(self, kinds, seed=None):
        self.gens = [Noise(k, seed) for k in kinds]
        self.t = 0.0

    def next(self, n):
        x = sum(g.next(n) for g in self.gens) * (0.75 if len(self.gens) > 1 else 1.0)
        self.t += n / RATE
        return np.clip(x, -0.9, 0.9).astype(np.float32)


MODES = {'loop': '列表循环', 'one': '单曲循环', 'shuffle': '随机播放'}
MUSIC_FADE = 8.0      # 定时停：最后几秒渐弱


class Queue:
    """播放顺序：order 是 tracks 的下标排出来的顺序，pos 指着现在这首。随机播放就是把 order 打乱（现在这首排第一）。"""

    def __init__(self, tracks, start=0, mode='loop', rng=None):
        if not tracks:
            raise ValueError('没有歌')
        self.tracks = list(tracks)
        self.rng = rng or np.random.default_rng()
        self.mode = 'loop'
        self.order = list(range(len(self.tracks)))
        self.pos = max(0, min(int(start or 0), len(self.tracks) - 1))
        self.set_mode(mode)

    @property
    def current(self):
        return self.tracks[self.order[self.pos]]

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(f"播放方式只有：{'、'.join(MODES.values())}")
        cur = self.order[self.pos]
        if mode == 'shuffle':
            rest = [i for i in range(len(self.tracks)) if i != cur]
            self.rng.shuffle(rest)
            self.order = [cur] + rest
            self.pos = 0
        elif self.mode == 'shuffle' or mode == 'loop':
            self.order = list(range(len(self.tracks)))
            self.pos = cur
        self.mode = mode

    def upcoming(self, auto=True):
        """下一首是哪首（只看不动）。单曲循环自己接着放时还是这首。"""
        if auto and self.mode == 'one':
            return self.current
        return self.tracks[self.order[(self.pos + 1) % len(self.order)]]

    def advance(self, auto=False):
        """换到下一首。auto：这首放完了自己往下走（单曲循环就不动）。随机放完一轮重新打乱，不连着放同一首。"""
        if auto and self.mode == 'one':
            return self.current
        self.pos += 1
        if self.pos >= len(self.order):
            self.pos = 0
            if self.mode == 'shuffle' and len(self.order) > 2:
                last = self.order[-1]
                rest = [i for i in range(len(self.tracks)) if i != last]
                self.rng.shuffle(rest)
                self.order = rest + [last]
        return self.current

    def back(self):
        self.pos = (self.pos - 1) % len(self.order)
        return self.current


class Player:
    def __init__(self, background, on_change=None, on_error=None):
        self.background = background
        self.on_change = on_change or (lambda view: None)
        self.on_error = on_error or (lambda text: None)
        self.task = None
        self.proc = None
        self.now = None     # {'kind': 'radio'|'sound'|'music'|…, 'id', 'name', 'ends'}；音乐另有 artist、cover、next…
        self.queue = None   # 音乐的播放顺序（Queue）
        self.stream_url = None      # 歌 id → 网关中转地址（app 设）
        self.music_info = {'ready': False, 'tags': {}}   # 网关有没有接音乐库、标了哪些（app 定时问）
        self._offset = 0.0  # 这首从第几秒开始放的（暂停后接着放）
        self._pushed = 0    # 这次放出去多少个采样

    def view(self):
        return {'playing': self.now, 'stations': [{'id': k, 'name': v[0]} for k, v in STATIONS.items()],
                'sounds': [{'id': k, 'name': v} for k, v in SOUNDS.items()], 'music': self.music_info,
                'modes': [{'id': k, 'name': v} for k, v in MODES.items()]}

    # ---- 音乐 (设计画布第 58 块)

    def position(self):
        """这首放到第几秒。"""
        return self._offset + self._pushed / RATE

    def _music_now(self, paused=False):
        q = self.queue
        t, nxt = q.current, q.upcoming()
        ends = self.now.get('ends') if self.now and self.now.get('kind') == 'music' else None
        self.now = {'kind': 'music', 'id': t['id'], 'name': t.get('title') or '', 'artist': t.get('artist') or '',
                    'album': t.get('album') or '', 'cover': t.get('cover') or '', 'duration': int(t.get('duration') or 0),
                    'list': getattr(self, '_list_name', ''), 'index': q.pos + 1, 'count': len(q.order), 'mode': q.mode,
                    'next': None if len(q.tracks) < 2 and q.mode != 'one' else {'name': nxt.get('title') or '', 'artist': nxt.get('artist') or ''},
                    'paused': paused, 'at': round(self.position(), 1), 'since': time.time(), 'ends': ends}
        return self.now

    async def play_music(self, tracks, start=0, mode='loop', name='', minutes=None):
        """放一串歌（找到的、按标签挑的）。minutes：多久后停（None/0 一直放）。"""
        if not self.stream_url:
            raise ValueError('还没连上网关，放不了歌')
        q = Queue(tracks, start, mode or 'loop')
        await self.stop(quiet=True)
        self.queue, self._list_name = q, name
        self.now = {'kind': 'music', 'ends': time.time() + float(minutes) * 60 if minutes else None}
        self._start_track(0.0)
        return self.now

    def _start_track(self, offset):
        self._offset, self._pushed = offset, 0
        self._music_now()
        self.task = asyncio.create_task(self._music())
        self.on_change(self.view())

    async def _restart(self, offset=0.0):
        task, self.task = self.task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._kill()
        self.background.clear()
        self._start_track(offset)

    def music_playing(self):
        return bool(self.now and self.now.get('kind') == 'music' and self.queue)

    async def music_next(self):
        self.queue.advance()
        await self._restart()
        return self.now

    async def music_prev(self):
        if self.position() > 5:            # 放了一会儿：回到这首开头（和手机一样）
            await self._restart()
        else:
            self.queue.back()
            await self._restart()
        return self.now

    async def music_pause(self):
        if not self.music_playing() or self.now.get('paused'):
            return self.now
        at = self.position()
        task, self.task = self.task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._kill()
        self.background.clear()
        self._offset, self._pushed = at, 0
        self._music_now(paused=True)
        self.on_change(self.view())
        return self.now

    async def music_resume(self):
        if not self.music_playing() or not self.now.get('paused'):
            return self.now
        ends = self.now.get('ends')
        if ends and ends <= time.time():
            self.now['ends'] = None          # 定时已经过了：接着放就不再定时
        self._start_track(self._offset)
        return self.now

    async def music_mode(self, mode):
        self.queue.set_mode(mode)
        self._music_now(paused=bool(self.now.get('paused')))
        self.on_change(self.view())
        return self.now

    def music_timer(self, minutes):
        """定时停：minutes 分钟后（0 / None 取消定时）。"""
        if not self.music_playing():
            raise ValueError('现在没在放歌')
        self.now['ends'] = time.time() + float(minutes) * 60 if minutes else None
        self.on_change(self.view())
        return self.now

    async def _music(self):
        failures = 0
        frame = int(RATE * CHUNK) * 2 * 4
        while self.music_playing():
            track = self.queue.current
            url = self.stream_url(track['id'])
            args = ['ffmpeg', '-nostdin', '-loglevel', 'error', '-re']
            if self._offset > 0.5:
                args += ['-ss', f'{self._offset:.1f}']
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *args, '-i', url, '-vn', '-sn', '-dn', '-f', 'f32le', '-ac', '2', '-ar', str(RATE), '-',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            except FileNotFoundError:
                self.on_error('这台设备上没有 ffmpeg，放不了歌')
                break
            got, cut = 0, False
            while True:
                data = await self.proc.stdout.read(frame)
                if not data:
                    break
                usable = len(data) // 8 * 8
                if not usable:
                    continue
                got += usable
                x = np.frombuffer(data[:usable], dtype='<f4').reshape(-1, 2).copy()
                ends = self.now.get('ends') if self.now else None
                if ends:
                    left = ends - time.time()
                    if left <= 0:
                        cut = True
                        break
                    if left < MUSIC_FADE:
                        x *= max(0.0, left / MUSIC_FADE)
                self.background.push(x)
                self._pushed += len(x)
            await self._kill()
            if cut:
                break
            if got < RATE * 8:             # 一秒都没放出来：这首放不了（文件坏了、网关连不上）
                failures += 1
                log.info('music: %s gave %d bytes', track.get('title'), got)
                if failures >= 3 or failures >= len(self.queue.order):
                    self.on_error(f"放不了「{track.get('title') or '这首歌'}」：网关或音乐库连不上")
                    break
            else:
                failures = 0
            self.queue.advance(auto=True)
            self._offset, self._pushed = 0.0, 0
            self._music_now()
            self.on_change(self.view())
        if self.now and self.now.get('kind') == 'music':
            self.now, self.queue = None, None
            self.on_change(self.view())

    async def stop(self, quiet=False):
        task, self.task = self.task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._kill()
        self.background.clear()
        self.queue = None
        if self.now:
            self.now = None
            if not quiet:
                self.on_change(self.view())

    async def _kill(self):
        p, self.proc = self.proc, None
        if p and p.returncode is None:
            try:
                p.kill()
                await p.wait()
            except ProcessLookupError:
                pass

    async def play_radio(self, key):
        if key not in STATIONS:
            raise ValueError(f"没有这个电台，能放的有：{'、'.join(v[0] for v in STATIONS.values())}")
        await self.stop(quiet=True)
        self.now = {'kind': 'radio', 'id': key, 'name': STATIONS[key][0], 'ends': None}
        self.task = asyncio.create_task(self._radio(key))
        self.on_change(self.view())
        return self.now

    async def play_sound(self, key, minutes=30):
        if key not in SOUNDS:
            raise ValueError(f"助眠声只有：{'、'.join(SOUNDS.values())}")
        minutes = float(minutes or 0)
        await self.stop(quiet=True)
        ends = time.time() + minutes * 60 if minutes > 0 else None
        self.now = {'kind': 'sound', 'id': key, 'name': SOUNDS[key], 'ends': ends}
        self.task = asyncio.create_task(self._sound(key, ends))
        self.on_change(self.view())
        return self.now

    async def gentle_birds(self, ends):
        """温柔叫醒（设计画布第 46 块）：鸟叫从无声慢慢起来（先慢后快），到 ends（闹钟响的时候）停。
        别的声音（电台、助眠声）正在放就不打扰它。"""
        if self.now and self.now.get('kind') != 'gentle':
            return None
        await self.stop(quiet=True)
        span = max(1.0, ends - time.time())
        self.now = {'kind': 'gentle', 'id': 'birds', 'name': '鸟叫（温柔叫醒）', 'ends': ends}
        self.task = asyncio.create_task(self._sound('birds', ends, fade_in=span, peak=0.7, fade_out=2.0))
        self.on_change(self.view())
        return self.now

    async def play_mix(self, keys, ends, name=''):
        """专注的声景（focus.py）：keys 是 SOUNDS 里的一到两种，放到 ends，最后渐弱。"""
        keys = [k for k in keys if k in SOUNDS][:2]
        if not keys:
            return None
        await self.stop(quiet=True)
        self.now = {'kind': 'focus', 'id': '+'.join(keys), 'name': name or ' + '.join(SOUNDS[k] for k in keys), 'ends': ends}
        self.task = asyncio.create_task(self._sound('+'.join(keys), ends, fade_out=min(FADE, 20.0)))
        self.on_change(self.view())
        return self.now

    async def _radio(self, key):
        url = STATIONS[key][1]
        failures = 0
        frame = int(RATE * CHUNK) * 2 * 4
        while failures < 3:
            started = time.monotonic()
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    'ffmpeg', '-nostdin', '-loglevel', 'error', '-re', '-reconnect', '1', '-reconnect_streamed', '1',
                    '-i', url, '-f', 'f32le', '-ac', '2', '-ar', str(RATE), '-',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            except FileNotFoundError:
                self.on_error('这台设备上没有 ffmpeg，放不了电台')
                break
            got = 0
            while True:
                data = await self.proc.stdout.read(frame)
                if not data:
                    break
                got += len(data)
                usable = len(data) // 8 * 8
                if usable:
                    self.background.push(np.frombuffer(data[:usable], dtype='<f4').reshape(-1, 2).copy())
            await self._kill()
            failures = 0 if time.monotonic() - started > 60 and got else failures + 1
            log.info('radio %s stopped after %.0f s, retrying', key, time.monotonic() - started)
            await asyncio.sleep(2)
        if self.now and self.now.get('id') == key:
            self.on_error(f'{STATIONS[key][0]}连不上了，稍后再试')
            self.now = None
            self.on_change(self.view())

    async def _sound(self, key, ends, fade_in=2.0, peak=1.0, fade_out=FADE):
        make = (lambda: Mix(key.split('+'))) if '+' in key else (lambda: Noise(key))
        gen = await asyncio.get_running_loop().run_in_executor(None, make)   # ~0.5 s of FFT per layer on this board
        n = int(RATE * CHUNK)
        gen.t = 0.0
        next_at = time.monotonic()
        while True:
            now = time.time()
            if ends and now >= ends:
                break
            gain = min(1.0, (ends - now) / fade_out) if ends else 1.0
            ramp = min(1.0, gen.t / fade_in)
            gain = min(gain, ramp if fade_in <= 2 else ramp * ramp) * peak   # long fade-ins: slow first
            mono = gen.next(n) * max(0.0, gain)
            self.background.push(np.repeat(mono[:, None], 2, axis=1))
            next_at += CHUNK
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
        self.now = None
        self.on_change(self.view())
