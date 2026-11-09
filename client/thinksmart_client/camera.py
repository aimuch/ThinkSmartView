"""机身摄像头（设计画布第 27 块）：拍照问不用手机，直接用屏幕上方的前置摄像头。

系统层装好摄像头后（system/camera，上游 PR #7），它是一个普通的摄像头 /dev/video10（1280×720 YUYV），
只在有程序读取时才打开。这里用一个 ffmpeg 读它，同一份画面分两路：
- 预览：每秒 8 张、短边 360 的 JPEG（左右镜像，像照镜子好对准），页面用 multipart/x-mixed-replace 显示；
- 整图：每秒 2 张、1280×720（不镜像），原子地覆盖同一个文件；按「拍」时取最新那张，不用重开摄像头。
两路都先按屏幕方向转正：摄像头在横屏（ROTATION=90，放在支架上）时是正的，竖屏要顺时针转 90°。
没人看预览、也没在拍时，几秒后关掉 ffmpeg（摄像头跟着关）。
"""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

DEVICE = Path('/dev/video10')
STILL = Path('~/.cache/thinksmart-client/camera-still.jpg').expanduser()
# 屏幕方向（/etc/thinksmart-kiosk.conf 的 ROTATION）→ 把画面转正的 ffmpeg 滤镜
ROTATE = {'90': '', 'normal': 'transpose=1', '270': 'hflip,vflip', '180': 'transpose=2'}
IDLE_STOP = 4.0          # 秒：没人用之后多久关
PREVIEW_MAX = 120.0      # 秒：一次实时画面最多看这么久（页面忘了断开也不会一直开着摄像头）
STILL_FRESH = 1.5        # 秒：整图比这旧就再等一张
SOI, EOI = b'\xff\xd8', b'\xff\xd9'


def available():
    return DEVICE.exists() and bool(shutil.which('ffmpeg'))


def ffmpeg_args(rotation, still=STILL):
    rot = ROTATE.get(rotation, ROTATE['normal'])
    # 预览先缩到 640×360 再转向、镜像（转小图便宜）；整图每秒只转 2 张。
    # -use_wallclock_as_timestamps：虚拟摄像头的时间戳不连续，fps 滤镜会补出成千上万的重复帧去编码，
    # ffmpeg 占 4 个核（2026-11-10 实测 400% → 用读取时的时间后不到 1 个核）
    graph = (f'[0:v]fps=8,split=2[p][s];[p]scale=640:360,{rot + "," if rot else ""}hflip[pv];'
             f'[s]fps=2{"," + rot if rot else ""}[st]')
    return ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-use_wallclock_as_timestamps', '1',
            '-f', 'v4l2', '-input_format', 'yuyv422', '-video_size', '1280x720', '-i', str(DEVICE),
            '-filter_complex', graph,
            '-map', '[pv]', '-f', 'mjpeg', '-q:v', '7', 'pipe:1',
            '-map', '[st]', '-f', 'image2', '-update', '1', '-atomic_writing', '1', '-q:v', '3', '-y', str(still)]


def split_jpegs(buf):
    """Complete JPEG frames at the start of buf -> (frames, the rest)."""
    frames = []
    while True:
        a = buf.find(SOI)
        if a < 0:
            return frames, b''
        b = buf.find(EOI, a + 2)
        if b < 0:
            return frames, buf[a:]
        frames.append(buf[a:b + 2])
        buf = buf[b + 2:]


class Camera:
    def __init__(self, rotation):
        self.rotation = rotation          # () -> '90' | 'normal' | '270' | '180'
        self.proc = None
        self.frame = None                 # the latest preview JPEG
        self.seq = 0
        self.users = 0
        self.changed = asyncio.Condition()
        self._stopper = None
        self.stopping = False             # we terminated it (nobody watching): exit 255 is expected

    async def _start(self):
        if self.proc and self.proc.returncode is None:
            return
        STILL.parent.mkdir(parents=True, exist_ok=True)
        STILL.unlink(missing_ok=True)
        self.stopping = False
        self.proc = await asyncio.create_subprocess_exec(*ffmpeg_args(self.rotation()), stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
        log.info('camera on (ffmpeg %s, rotation %s)', self.proc.pid, self.rotation())
        asyncio.create_task(self._read(self.proc))

    async def _read(self, proc):
        buf = b''
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                frames, buf = split_jpegs(buf + chunk)
                if frames:
                    async with self.changed:
                        self.frame, self.seq = frames[-1], self.seq + 1
                        self.changed.notify_all()
        finally:
            async with self.changed:
                self.changed.notify_all()
            if self.proc is proc:
                code = await proc.wait()
                err = (await proc.stderr.read()).decode('utf-8', 'replace').strip()[-300:] if code not in (0, 255) or not self.stopping else ''
                log.info('camera off (ffmpeg exited %s%s)', code, f': {err}' if err else '')

    def _stop_later(self):
        if self._stopper:
            self._stopper.cancel()

        async def stop():
            await asyncio.sleep(IDLE_STOP)
            if self.users == 0 and self.proc and self.proc.returncode is None:
                self.stopping = True
                self.proc.terminate()
                self.frame = None
        self._stopper = asyncio.create_task(stop())

    async def acquire(self):
        self.users += 1
        if self._stopper:
            self._stopper.cancel()
            self._stopper = None
        await self._start()

    def release(self):
        self.users = max(0, self.users - 1)
        if self.users == 0:
            self._stop_later()

    async def frames(self):
        """Preview JPEGs as they come (an async generator; the camera is held while it runs)."""
        await self.acquire()
        log.info('camera preview: a page is watching')
        try:
            seen = self.seq if self.frame is None else self.seq - 1   # 刚打开还没出画面：等第一帧，别拿空的就走
            until = time.monotonic() + PREVIEW_MAX
            while time.monotonic() < until:
                async with self.changed:
                    await asyncio.wait_for(self.changed.wait_for(
                        lambda: (self.seq != seen and self.frame is not None) or self.proc.returncode is not None), 10)
                if self.proc.returncode is not None:
                    return
                seen = self.seq
                yield self.frame
        finally:
            log.info('camera preview: the page stopped watching')
            self.release()

    async def shoot(self, timeout=8.0):
        """A 1280×720 photo, upright (not mirrored), as JPEG bytes."""
        await self.acquire()
        try:
            give_up = time.monotonic() + timeout
            while time.monotonic() < give_up:
                try:
                    st = STILL.stat()
                    if time.time() - st.st_mtime < STILL_FRESH and st.st_size > 1000:
                        return STILL.read_bytes()
                except OSError:
                    pass
                if self.proc and self.proc.returncode is not None:
                    raise ValueError('摄像头打不开')
                await asyncio.sleep(0.2)
            raise ValueError('摄像头没出画面')
        finally:
            self.release()
