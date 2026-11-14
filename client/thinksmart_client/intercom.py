"""视频对讲（设计画布第 51 块）：家人在手机上（经 Tailscale 打开网关的 /call）打给这台设备。

网关只转交呼叫和连接描述：这里长轮询 /kiosk/call/wait 等来电，接听时用 aiortc 回一个 answer
（非 trickle：候选地址收齐了一次发完），音视频和手机直接连。
- 摄像头：一个 ffmpeg 读 /dev/video10（按屏幕方向转正），每秒 15 张、640×360（竖屏 360×640）送 VP8；
  同一路画面每隔几张转成 JPEG 给页面当自己的小窗，不再开第二个读摄像头的程序。
- 麦克风：客户端的麦克风（16 kHz，通话中开着回声消除）由 Client._on_block 交给 mic_feed，通话时唤醒词暂停。
- 对方的声音：进客户端的通话声道（audio.Audio.call），回声消除认得它；对方的画面转成 JPEG 给页面。
设备上测过：VP8 640×360 每秒 15 张编解码约 0.9 个核，读摄像头约 0.5 个核（8 核）。
想法来自 fubin0478/thinksmart-view（MIT）。
"""
import asyncio
import collections
import fractions
import logging
import time

import aiohttp
import numpy as np

from . import bg

log = logging.getLogger(__name__)

FPS = 15
LONG = 640
SHORT = 360
AUTO_ANSWER_AFTER = 5       # 自动接通：先响这么多秒、显示是谁
JPEG_EVERY = 0.12           # 给页面的画面最多每秒 8 张
HISTORY = 10
DEFAULT = {'auto_answer': False}
CAMERA = '/dev/video10'


def normalize(value):
    v = dict(DEFAULT)
    if isinstance(value, dict) and isinstance(value.get('auto_answer'), bool):
        v['auto_answer'] = value['auto_answer']
    return v


def frame_size(rotation_filter):
    """转正以后的尺寸：横着放（不转）是 640×360，转 90° 是 360×640。"""
    return (SHORT, LONG) if 'transpose' in rotation_filter else (LONG, SHORT)


class JpegEncoder:
    """av 的 mjpeg 编码器：VideoFrame → JPEG 字节（同一尺寸复用一个编码器）。"""

    def __init__(self, quality=5):
        self.ctx = None
        self.size = None
        self.quality = quality

    def encode(self, frame, max_width=640):
        import av
        w = min(max_width, frame.width) // 2 * 2
        h = int(frame.height * w / frame.width) // 2 * 2
        if self.size != (w, h):
            self.ctx = av.CodecContext.create('mjpeg', 'w')
            self.ctx.width, self.ctx.height = w, h
            self.ctx.pix_fmt = 'yuvj420p'
            self.ctx.time_base = fractions.Fraction(1, 25)
            self.ctx.options = {'qmin': str(self.quality), 'qmax': str(self.quality + 3)}
            self.size = (w, h)
        packets = self.ctx.encode(frame.reformat(width=w, height=h, format='yuvj420p'))
        return b''.join(bytes(p) for p in packets)


class Picture:
    """一路给页面看的画面（最新一张 JPEG），页面用 multipart/x-mixed-replace 拉。"""

    def __init__(self):
        self.jpeg = None
        self.seq = 0
        self.changed = asyncio.Condition()
        self.last = 0.0

    def due(self):
        return time.monotonic() - self.last >= JPEG_EVERY

    async def put(self, jpeg):
        self.last = time.monotonic()
        self.jpeg = jpeg
        self.seq += 1
        async with self.changed:
            self.changed.notify_all()

    def clear(self):
        self.jpeg = None
        self.seq += 1

    async def frames(self, until):
        seen = -1
        while until():
            if self.jpeg is not None and self.seq != seen:
                seen = self.seq
                yield self.jpeg
            try:
                async with self.changed:
                    await asyncio.wait_for(self.changed.wait(), 1.0)
            except asyncio.TimeoutError:
                pass


def make_tracks(intercom):
    """aiortc 的两条发出去的轨道（导入 aiortc 放在这里：没装它时客户端照样能跑，只是不能对讲）。"""
    import av
    from aiortc import MediaStreamTrack, VideoStreamTrack

    class CameraTrack(VideoStreamTrack):
        def __init__(self):
            super().__init__()
            self.proc = None
            self.dead = False          # 摄像头读不了：一直发黑画面
            self.n = 0
            rot = intercom.rotation_filter()
            self.w, self.h = frame_size(rot)
            self.filter = f"fps={FPS},{rot + ',' if rot else ''}scale={self.w}:{self.h}"
            self.enc = JpegEncoder()

        async def recv(self):
            if self.proc is None and not self.dead:
                try:
                    self.proc = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-v', 'error', '-use_wallclock_as_timestamps', '1', '-f', 'v4l2', '-i', CAMERA,
                        '-vf', self.filter, '-pix_fmt', 'yuv420p', '-f', 'rawvideo', '-',
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                except OSError as e:
                    log.warning('intercom: camera: %s', e)
                    self.dead = True
            size = self.w * self.h
            data = None
            if self.proc and not self.dead:
                try:
                    data = await self.proc.stdout.readexactly(size * 3 // 2)
                except (asyncio.IncompleteReadError, ConnectionError):
                    log.warning('intercom: camera stopped')
                    self.dead = True
            f = av.VideoFrame(self.w, self.h, 'yuv420p')
            if data is None:                             # 摄像头没出画面：别空转
                await asyncio.sleep(1 / FPS)
            if data is None or intercom.camera_off:      # 黑屏
                data = bytes(size) + bytes([128]) * (size // 2)
            for plane, off, n in zip(f.planes, (0, size, size * 5 // 4), (size, size // 4, size // 4)):
                plane.update(data[off:off + n])
            f.pts, f.time_base = self.n, fractions.Fraction(1, FPS)
            self.n += 1
            if intercom.self_view.due():
                await intercom.self_view.put(await asyncio.to_thread(self.enc.encode, f, 320))
            return f

        def stop(self):
            super().stop()
            if self.proc and self.proc.returncode is None:
                self.proc.kill()

    class MicTrack(MediaStreamTrack):
        kind = 'audio'

        def __init__(self):
            super().__init__()
            self.n = 0

        async def recv(self):
            want = 320                                   # 20 ms at 16 kHz
            for _ in range(50):
                if sum(len(b) for b in intercom.mic) >= want:
                    break
                await asyncio.sleep(0.005)
            pcm = np.zeros(want, np.float32)
            got = 0
            while got < want and intercom.mic:
                b = intercom.mic.popleft()
                take = min(len(b), want - got)
                pcm[got:got + take] = b[:take]
                if take < len(b):
                    intercom.mic.appendleft(b[take:])
                got += take
            if intercom.muted:
                pcm[:] = 0
            f = av.AudioFrame.from_ndarray(np.clip(pcm * 32768, -32768, 32767).astype('<i2').reshape(1, -1),
                                           format='s16', layout='mono')
            f.sample_rate, f.pts, f.time_base = 16000, self.n, fractions.Fraction(1, 16000)
            self.n += want
            return f

    return CameraTrack(), MicTrack()


class Intercom:
    def __init__(self, hub_http, device_id, channel, publish, rotation_filter, settings=None, on_active=None,
                 ring=None, saved=None):
        self.hub_http = hub_http
        self.device_id = device_id
        self.channel = channel                    # audio.Background：对方的声音
        self.publish = publish
        self.rotation_filter = rotation_filter    # () -> ffmpeg 转正滤镜（camera.ROTATE[...]）
        self.settings = normalize(settings)
        self.on_active = on_active or (lambda active: None)
        self.ring_sound = ring or (lambda: None)
        self.save = saved or (lambda history: None)
        self.state = 'idle'                       # idle / ringing / connecting / talking / ended
        self.call = None                          # {'id', 'caller', 'offer', 'rang', 'started'}
        self.history = []
        self.pc = None
        self.tracks = []
        self.mic = collections.deque()
        self.muted = False
        self.camera_off = False
        self.remote_view = Picture()
        self.self_view = Picture()
        self.ended_text = ''
        self.task = None
        self.ring_task = None

    # ---- 状态 ----

    @property
    def active(self):
        """响铃或通话中：背景音乐静音、回声消除开着。"""
        return self.state in ('ringing', 'connecting', 'talking')

    @property
    def wants_mic(self):
        return self.state in ('connecting', 'talking')

    def mic_feed(self, block):
        """音频线程：麦克风的 20 ms（16 kHz float32，回声消除过）。最多攒 1 秒。"""
        self.mic.append(block.copy())
        while len(self.mic) > 50:
            self.mic.popleft()

    def view(self):
        c = self.call
        return {'state': self.state, 'caller': c and c['caller'], 'started': c and c.get('started'),
                'muted': self.muted, 'camera_off': self.camera_off, 'auto_answer': self.settings['auto_answer'],
                'auto_in': c and self.state == 'ringing' and self.settings['auto_answer']
                and max(0, round(c['rang'] + AUTO_ANSWER_AFTER - time.time())),
                'ended': self.ended_text, 'history': self.history[:HISTORY]}

    def _changed(self):
        self.publish('intercom', self.view())

    def _set(self, state):
        was = self.active
        self.state = state
        if self.active != was:
            self.on_active(self.active)
        self._changed()

    # ---- 等来电 ----

    def start(self):
        if not self.task or self.task.done():
            self.task = bg.spawn(self._run(), name='intercom')

    async def _run(self):
        delay = 2
        while True:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as s:
                    async with s.get(f'{self.hub_http}/kiosk/call/wait', params={'device': self.device_id}) as r:
                        if r.status == 404:            # 网关还没有对讲：过一会儿再问
                            await asyncio.sleep(300)
                            continue
                        data = await r.json(content_type=None)
                for e in data.get('events') or []:
                    await self._event(e)
                delay = 2
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug('intercom wait: %s', e)
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)

    async def _post(self, kind, call_id, sdp=None):
        body = {'device': self.device_id, 'id': call_id, 'type': kind}
        if sdp:
            body['sdp'] = sdp
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.post(f'{self.hub_http}/kiosk/call/event', json=body) as r:
                    return (await r.json(content_type=None)).get('ok', False)
        except Exception as e:
            log.warning('intercom %s: %s', kind, e)
            return False

    async def _event(self, e):
        kind, call_id = e.get('type'), e.get('id')
        if kind == 'ring':
            if self.state != 'idle' and self.state != 'ended':
                await self._post('busy', call_id)
                return
            self.call = {'id': call_id, 'caller': str(e.get('caller') or '家人')[:20], 'offer': e.get('sdp') or '',
                         'rang': time.time(), 'started': None}
            self.muted = self.camera_off = False
            self.ended_text = ''
            log.info('intercom: %s is calling', self.call['caller'])
            self._set('ringing')
            self.ring_task = bg.spawn(self._ringing(call_id), name='intercom-ring')
        elif kind == 'hangup' and self.call and call_id == self.call['id']:
            await self._end('missed' if self.state == 'ringing' else 'hangup', tell_hub=False)

    async def _ringing(self, call_id):
        while self.state == 'ringing' and self.call and self.call['id'] == call_id:
            self.ring_sound()
            if self.settings['auto_answer'] and time.time() - self.call['rang'] >= AUTO_ANSWER_AFTER:
                await self.accept()
                return
            self._changed()                         # 自动接通的倒数
            await asyncio.sleep(1)

    # ---- 接听、挂断 ----

    async def accept(self):
        if self.state != 'ringing' or not self.call:
            raise ValueError('现在没有来电')
        from aiortc import RTCPeerConnection, RTCSessionDescription
        self._set('connecting')
        call = self.call
        self.mic.clear()
        pc = self.pc = RTCPeerConnection()
        cam, mic = make_tracks(self)
        self.tracks = [cam, mic]

        @pc.on('track')
        def on_track(track):
            bg.spawn(self._play(track) if track.kind == 'audio' else self._show(track), name=f'intercom-{track.kind}')

        @pc.on('connectionstatechange')
        async def on_state():
            if pc is not self.pc:
                return
            if pc.connectionState == 'connected' and self.state == 'connecting':
                call['started'] = time.time()
                log.info('intercom: talking with %s', call['caller'])
                self._set('talking')
            elif pc.connectionState in ('failed', 'closed') and self.state in ('connecting', 'talking'):
                await self._end('failed' if pc.connectionState == 'failed' else 'hangup')

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=call['offer'], type='offer'))
            pc.addTrack(cam)        # 挂到手机发来的那两路上（aiortc 按种类配对），变成双向
            pc.addTrack(mic)
            await pc.setLocalDescription(await pc.createAnswer())
        except Exception as e:
            log.warning('intercom: cannot answer: %s', e)
            await self._end('failed')
            return
        if not await self._post('answer', call['id'], pc.localDescription.sdp):
            await self._end('hangup', tell_hub=False)

    async def decline(self):
        if self.state != 'ringing' or not self.call:
            return
        await self._post('decline', self.call['id'])
        await self._end('declined', tell_hub=False)

    async def hangup(self):
        if self.state in ('ringing', 'connecting', 'talking') and self.call:
            await self._post('hangup', self.call['id'])
            await self._end('hangup', tell_hub=False)

    async def _end(self, reason, tell_hub=True):
        call, pc = self.call, self.pc
        self.pc = None
        if tell_hub and call and reason == 'failed':
            await self._post('hangup', call['id'])
        for t in self.tracks:
            t.stop()
        self.tracks = []
        if pc:
            try:
                await pc.close()
            except Exception:
                pass
        self.channel.clear()
        self.mic.clear()
        self.remote_view.clear()
        self.self_view.clear()
        if call:
            seconds = round(time.time() - call['started']) if call.get('started') else 0
            missed = not call.get('started') and reason in ('missed', 'declined')
            self.history.insert(0, {'t': round(call['rang']), 'caller': call['caller'], 'seconds': seconds, 'missed': missed})
            self.history = self.history[:HISTORY]
            self.save(self.history)
            self.ended_text = ({'missed': f"{call['caller']} 打来，没接到", 'declined': '已挂断',
                                'failed': '没连上（检查网关的 Tailscale 子网路由）'}.get(reason)
                               or f'通话结束 {seconds // 60}:{seconds % 60:02d}')
            log.info('intercom: call with %s ended (%s, %d s)', call['caller'], reason, seconds)
        self.call = None
        self._set('ended')
        bg.spawn(self._back_to_idle(), name='intercom-idle')

    async def _back_to_idle(self):
        await asyncio.sleep(4)
        if self.state == 'ended':
            self._set('idle')

    def set_muted(self, on):
        self.muted = bool(on)
        self._changed()

    def set_camera_off(self, on):
        self.camera_off = bool(on)
        self._changed()

    def set_auto_answer(self, on):
        self.settings['auto_answer'] = bool(on)
        self._changed()
        return self.settings

    # ---- 对方的声音、画面 ----

    async def _play(self, track):
        """对方的声音（Opus 解出来是 48 kHz）→ 通话声道。"""
        from .audio import OUT_RATE
        try:
            while True:
                f = await track.recv()
                a = f.to_ndarray()
                ch = len(f.layout.channels)
                pcm = (a.reshape(-1, ch) if f.format.is_packed else a.T).astype(np.float32)
                if f.format.name.startswith('s16'):
                    pcm /= 32768.0
                if ch == 1:
                    pcm = np.repeat(pcm, 2, axis=1)
                if f.sample_rate != OUT_RATE:      # Opus is 48 kHz: never in practice
                    idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * OUT_RATE / f.sample_rate))
                    pcm = pcm[idx.astype(int)]
                self.channel.push(np.ascontiguousarray(pcm[:, :2], dtype=np.float32))
        except Exception:
            pass

    async def _show(self, track):
        enc = JpegEncoder()
        try:
            while True:
                f = await track.recv()
                if self.remote_view.due():
                    await self.remote_view.put(await asyncio.to_thread(enc.encode, f, 640))
        except Exception:
            pass

    def frames(self, which):
        pic = self.remote_view if which == 'remote' else self.self_view
        return pic.frames(lambda: self.state in ('connecting', 'talking'))


def load_history(value):
    if not isinstance(value, list):
        return []
    return [h for h in value if isinstance(h, dict) and isinstance(h.get('t'), (int, float))][:HISTORY]

