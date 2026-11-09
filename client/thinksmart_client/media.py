"""网络电台和助眠声 (设计画布第 11 块): both play through the background channel (audio.Background), like a
phone's music over Bluetooth: lowered while talking, muted while an alarm rings. One at a time.

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
SOUNDS = {'rain': '雨声', 'ocean': '海浪', 'fan': '风扇', 'white': '白噪音'}
ALIASES = {'中国之声': 'zgzs', '经济之声': 'jjzs', '东方广播': 'dfgb', '上海东方广播': 'dfgb', '第一财经': 'dycj',
           '第一财经广播': 'dycj', '江苏交通': 'jsjt', '江苏交通广播': 'jsjt', '北京新闻': 'bjxw', '北京新闻广播': 'bjxw'}


def find_station(name):
    name = str(name or '').strip()
    if name in STATIONS:
        return name
    if name in ALIASES:
        return ALIASES[name]
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
    return {'下雨': 'rain', '大海': 'ocean', '电扇': 'fan', '白噪声': 'white'}.get(name)


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
    else:                     # fan: brown-ish with a soft bump around 300 Hz
        shape, rms = (1 / np.sqrt(f)) / (1 + (f / 1500) ** 2) * (1 + 2 * np.exp(-((f - 300) / 150) ** 2)), 0.05
    shape[f < 20] = 0
    x = np.fft.irfft(spec * shape, LOOP)
    return (x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * rms).astype(np.float32)


class Noise:
    """Each call returns the next n samples (mono float32): a looped buffer, with the ocean's swell and
    the rain's drops added on top (cheap, vectorised)."""

    def __init__(self, kind, seed=None):
        self.kind = kind
        self.rng = np.random.default_rng(seed)
        self.buf = _shaped(kind, self.rng)
        self.pos = 0
        self.t = 0.0

    def next(self, n):
        idx = (self.pos + np.arange(n)) % LOOP
        self.pos = (self.pos + n) % LOOP
        x = self.buf[idx].copy()
        t = self.t + np.arange(n) / RATE
        self.t += n / RATE
        if self.kind == 'ocean':
            x *= 0.3 + 0.7 * (0.5 + 0.5 * np.sin(2 * np.pi * t / 9.0)) ** 2
        elif self.kind == 'rain':
            drops = np.flatnonzero(self.rng.random(n) < 0.0006)
            for d in drops[:8]:
                k = min(n - d, 240)
                x[d:d + k] += self.rng.uniform(0.05, 0.18) * np.exp(-np.arange(k) / 40) * self.rng.choice((-1, 1))
        return np.clip(x, -0.9, 0.9).astype(np.float32)


class Player:
    def __init__(self, background, on_change=None, on_error=None):
        self.background = background
        self.on_change = on_change or (lambda view: None)
        self.on_error = on_error or (lambda text: None)
        self.task = None
        self.proc = None
        self.now = None     # {'kind': 'radio'|'sound', 'id', 'name', 'ends'}

    def view(self):
        return {'playing': self.now, 'stations': [{'id': k, 'name': v[0]} for k, v in STATIONS.items()],
                'sounds': [{'id': k, 'name': v} for k, v in SOUNDS.items()]}

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

    async def _sound(self, key, ends):
        gen = await asyncio.get_running_loop().run_in_executor(None, Noise, key)   # ~0.5 s of FFT on this board
        n = int(RATE * CHUNK)
        gen.t = 0.0
        next_at = time.monotonic()
        while True:
            now = time.time()
            if ends and now >= ends:
                break
            gain = min(1.0, (ends - now) / FADE) if ends else 1.0
            gain = min(gain, gen.t / 2)          # fade in over the first 2 s
            mono = gen.next(n) * max(0.0, gain)
            self.background.push(np.repeat(mono[:, None], 2, axis=1))
            next_at += CHUNK
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
        self.now = None
        self.on_change(self.view())
