"""AirPlay 隔空播放（设计画布第 49 块）：iPhone、iPad、Mac 的隔空播放里选这台设备，歌从它放出来。

shairport-sync（Ubuntu 24.04 是 3.3.8：经典 AirPlay，MIT）由系统层的 thinksmart-airplay-install 在页面上
第一次点「安装」时从软件源装上（它自带的系统服务被关掉）。打开开关时这里自己启动它：声音输出到 stdout
（44.1 kHz 16 位立体声），换成 48 kHz 送进客户端的 AirPlay 背景声道（audio.Audio.airplay），和蓝牙音箱一样：
说话时自动压低、闹钟响时静音、回声消除认得它，放歌时照样能唤醒。歌名、哪台手机在放从它的元数据管道读。
想法来自 fubin0478/thinksmart-view（MIT）；那边用 UxPlay 投屏，这里只放声音。
"""
import asyncio
import base64
import logging
import os
import re
import shutil
import time
from pathlib import Path

import numpy as np

from . import bg
from .audio import OUT_RATE

log = logging.getLogger(__name__)

INSTALLER = '/usr/local/sbin/thinksmart-airplay-install'
RATE = 44100
CHUNK = RATE // 50 * 4          # 20 ms of 16-bit stereo
RESTART_AFTER = 5               # seconds before starting shairport-sync again after it died
PLAYING_GAP = 2.0               # no sound for this long: not playing any more
NAME_MAX = 30
CONF = Path('~/.cache/thinksmart-client/airplay.conf').expanduser()
META = Path(os.environ.get('XDG_RUNTIME_DIR') or '/tmp') / 'thinksmart-airplay.meta'
ITEM = re.compile(rb'<item><type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code><length>(\d+)</length>'
                  rb'(?:\s*<data encoding="base64">\s*([^<]*)</data>)?\s*</item>')


def default_name(device_name):
    return f'小麦·{device_name}' if device_name else '小麦'


def clean_name(name):
    """AirPlay 里显示的名字：去掉引号、反斜杠和控制字符（要写进配置文件），最多 NAME_MAX 个字。"""
    name = re.sub(r'["\\\x00-\x1f]', '', str(name or '')).strip()
    return name[:NAME_MAX]


def config_text(name):
    return f'''// written by thinksmart-client (airplay.py); regenerated every start
general = {{
  name = "{clean_name(name) or '小麦'}";
  output_backend = "stdout";
  mdns_backend = "avahi";
  interpolation = "basic";
  volume_range_db = 50;
}};
sessioncontrol = {{
  session_timeout = 20;
  allow_session_interruption = "yes";
}};
metadata = {{
  enabled = "yes";
  include_cover_art = "no";
  pipe_name = "{META}";
}};
'''


def parse_items(buf):
    """元数据管道里攒的字节 → ([(type, code, bytes)], 没读完的尾巴)。"""
    items, end = [], 0
    for m in ITEM.finditer(buf):
        data = b''
        if m.group(4):
            try:
                data = base64.b64decode(m.group(4))
            except ValueError:
                data = b''
        items.append((bytes.fromhex(m.group(1).decode()).decode('latin-1'),
                      bytes.fromhex(m.group(2).decode()).decode('latin-1'), data))
        end = m.end()
    rest = buf[end:].lstrip()
    if len(rest) > 65536:      # garbage that never closes an item
        rest = b''
    return items, rest


def to_float(pcm):
    """16 位立体声字节 → float32 (n, 2)。"""
    return np.frombuffer(pcm, dtype='<i2').reshape(-1, 2).astype(np.float32) / 32768.0


class AirPlay:
    def __init__(self, channel, publish, settings=None):
        self.channel = channel               # audio.Background
        self.publish = publish
        settings = settings if isinstance(settings, dict) else {}
        self.enabled = settings.get('enabled') is True
        self.name = clean_name(settings.get('name'))
        self.device_name = ''
        self.installed = bool(shutil.which('shairport-sync'))
        self.installing = False
        self.error = None
        self.proc = None
        self.task = None
        self.meta_task = None
        self.playing_until = 0.0
        self.now = {}                         # 'client', 'title', 'artist'
        self._resampler = None

    @property
    def available(self):
        return os.path.exists(INSTALLER) or self.installed

    def shown_name(self):
        return self.name or default_name(self.device_name)

    def saved(self):
        return {'enabled': self.enabled, 'name': self.name}

    def playing(self):
        return time.monotonic() < self.playing_until

    def view(self):
        playing = self.playing()
        return {'available': self.available, 'installed': self.installed, 'installing': self.installing,
                'enabled': self.enabled, 'name': self.shown_name(), 'custom_name': self.name,
                'default_name': default_name(self.device_name), 'playing': playing,
                'client': playing and self.now.get('client') or '', 'title': playing and self.now.get('title') or '',
                'artist': playing and self.now.get('artist') or '', 'error': self.error}

    def _changed(self):
        self.publish('airplay', self.view())

    # ---- 安装（系统层的脚本，sudo -n） ----

    async def install(self):
        if self.installed:
            return self.view()
        if not os.path.exists(INSTALLER):
            raise ValueError('这台设备的系统层还没有 AirPlay（升级系统层后再试）')
        if self.installing:
            raise ValueError('正在装')
        self.installing, self.error = True, None
        self._changed()
        try:
            proc = await asyncio.create_subprocess_exec('sudo', '-n', INSTALLER, 'install',
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), 600)
            except asyncio.TimeoutError:
                proc.kill()
                raise ValueError('装了太久没装完，检查一下网络再试')
            if proc.returncode:
                lines = err.decode(errors='replace').strip().splitlines()
                raise ValueError(lines[-1][:120] if lines else '没装上')
            self.installed = bool(shutil.which('shairport-sync'))
            if not self.installed:
                raise ValueError('没装上')
            log.info('airplay: shairport-sync installed (%s)', out.decode(errors='replace').strip())
        except ValueError as e:
            self.error = str(e)
            raise
        finally:
            self.installing = False
            self._changed()
        if self.enabled:
            self.start()
        return self.view()

    async def _mdns(self, on):
        """系统层把 avahi（让 iPhone 找得到它）默认关着：AirPlay 开时开，关时关。"""
        if not os.path.exists(INSTALLER):
            return True
        try:
            proc = await asyncio.create_subprocess_exec('sudo', '-n', INSTALLER, 'on' if on else 'off',
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), 30)
        except (OSError, asyncio.TimeoutError) as e:
            log.warning('airplay: avahi %s: %s', on, e)
            return False
        if proc.returncode:
            log.warning('airplay: avahi %s: %s', on, err.decode(errors='replace').strip()[:200])
        return proc.returncode == 0

    # ---- 开关、名字 ----

    async def set(self, enabled=None, name=None):
        restart = False
        if name is not None:
            new = clean_name(name)
            restart = new != self.name
            self.name = new
        if enabled is not None:
            if enabled and not self.installed:
                raise ValueError('先装好 AirPlay')
            restart = restart or (bool(enabled) != self.enabled)
            self.enabled = bool(enabled)
        if restart:
            await self.stop()
            if self.enabled:
                self.start()
            elif enabled is False:
                await self._mdns(False)
        self._changed()
        return self.view()

    async def set_device_name(self, device_name):
        """网关后台给这台设备起的名字到了（/kiosk/today）：没自己改过名字时用「小麦·<名字>」。"""
        device_name = clean_name(device_name)
        if device_name == self.device_name:
            return
        self.device_name = device_name
        if not self.name and self.proc:
            await self.stop()
            self.start()
        self._changed()

    # ---- 跑 shairport-sync ----

    def start(self):
        if self.task and not self.task.done():
            return
        self.task = bg.spawn(self._run(), name='airplay')

    async def stop(self):
        task, self.task = self.task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.channel.clear()
        self.playing_until = 0.0

    async def _run(self):
        while self.enabled and self.installed:
            if not await self._mdns(True):
                self.error = '没开起 avahi，iPhone 可能找不到它'
                self._changed()
            CONF.parent.mkdir(parents=True, exist_ok=True)
            CONF.write_text(config_text(self.shown_name()), encoding='utf-8')
            self._make_fifo()
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    'shairport-sync', '-c', str(CONF), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            except OSError as e:
                self.error = f'启动不了：{e}'
                self._changed()
                return
            log.info('airplay: shairport-sync started as %r', self.shown_name())
            self.error = None
            self._resampler = None
            self.meta_task = bg.spawn(self._read_meta(), name='airplay-meta')
            errors = bg.spawn(self._read_errors(self.proc), name='airplay-log')
            try:
                await self._read_audio(self.proc)
                await self.proc.wait()
            finally:
                if self.proc.returncode is None:
                    self.proc.terminate()
                    try:
                        await asyncio.wait_for(self.proc.wait(), 3)
                    except asyncio.TimeoutError:
                        self.proc.kill()
                self.meta_task.cancel()
                errors.cancel()
                self.playing_until = 0.0
                self._changed()
            log.warning('airplay: shairport-sync exited (%s); again in %d s', self.proc.returncode, RESTART_AFTER)
            await asyncio.sleep(RESTART_AFTER)

    async def _read_audio(self, proc):
        import soxr
        try:
            while True:
                data = await proc.stdout.readexactly(CHUNK)
                if self._resampler is None:
                    self._resampler = soxr.ResampleStream(RATE, OUT_RATE, 2, dtype='float32', quality='MQ')
                out = self._resampler.resample_chunk(to_float(data))
                if len(out):
                    self.channel.push(np.ascontiguousarray(out, dtype=np.float32))
                was = self.playing()
                if np.abs(out).max(initial=0.0) > 1e-4:
                    self.playing_until = time.monotonic() + PLAYING_GAP
                    if not was:
                        self._changed()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def _read_errors(self, proc):
        async for line in proc.stderr:
            text = line.decode(errors='replace').strip()
            if text:
                log.info('shairport-sync: %s', text[:200])

    def _make_fifo(self):
        try:
            if META.exists() and not META.is_fifo():
                META.unlink()
            if not META.exists():
                os.mkfifo(META, 0o600)
        except OSError as e:
            log.warning('airplay metadata pipe: %s', e)

    async def _read_meta(self):
        """元数据：哪台手机（snam）、歌名（minm）、歌手（asar）；暂停、结束（pfls、pend）。"""
        loop = asyncio.get_running_loop()
        try:
            fd = os.open(META, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.warning('airplay metadata pipe: %s', e)
            return
        ready = asyncio.Event()
        loop.add_reader(fd, ready.set)
        buf = b''
        try:
            while True:
                await ready.wait()
                ready.clear()
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:                 # the writer closed: wait for it to open again
                    await asyncio.sleep(0.5)
                    continue
                items, buf = parse_items(buf + chunk)
                if self._apply(items):
                    self._changed()
        finally:
            loop.remove_reader(fd)
            os.close(fd)

    def _apply(self, items):
        changed = False
        for typ, code, data in items:
            text = data.decode('utf-8', errors='replace').strip()
            key = {('ssnc', 'snam'): 'client', ('core', 'minm'): 'title', ('core', 'asar'): 'artist'}.get((typ, code))
            if key and self.now.get(key) != text:
                self.now[key] = text
                changed = True
            elif (typ, code) == ('ssnc', 'pbeg'):
                self.now = {'client': self.now.get('client', '')}
                changed = True
            elif (typ, code) == ('ssnc', 'pend'):
                self.now = {}
                changed = True
        return changed
