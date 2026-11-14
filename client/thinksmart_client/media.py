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
