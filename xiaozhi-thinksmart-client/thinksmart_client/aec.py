"""Echo cancellation with WebRTC APM (adapted from py-xiaozhi's AecEngine, MIT, Copyright (c) 2025 Junsen).

near: microphone, 16 kHz mono float32. far: exactly what the output stream wrote (48 kHz, any
channels), downmixed and resampled to 16 kHz here. The output callback only queues copies
(feed_far); resampling and the APM calls happen on the capture side (process_near), so the two
real-time callbacks never share a lock.

Each mic block also goes through a linear-only canceller (speex_aec.py) on the same reference:
`linear` is what the wake word listens to while a reply plays, because WebRTC's suppressor removes
much of a voice talking over the reply.
"""
import ctypes
import logging
from collections import deque

import numpy as np
import soxr

from . import webrtc_apm
from .speex_aec import LinearAec

log = logging.getLogger(__name__)

_FAR_PENDING_MAX = 50  # output blocks (~20 ms each); dropped oldest-first if capture stalls
# reference waiting for the linear canceller: the output callback runs ahead of the mic, but in bursts
# (one PipeWire quantum, 43 ms); more than this means the mic stalled, and a reference running further
# ahead than the echo would put the echo outside the filter
_LINEAR_AHEAD_MAX = 0.2


class Aec:
    def __init__(self, near_rate=16000, far_rate=48000, delay_ms=100):
        self._frame = near_rate // 100  # APM works on 10 ms
        self._delay_ms = delay_ms
        self._far_pending = deque()
        self._far_buffer = np.empty(0, dtype=np.float32)
        self._rate = near_rate
        self._linear = LinearAec(near_rate, 320, tail_ms=500)
        self._linear_far = np.empty(0, dtype=np.float32)
        self.linear = None   # the last block through the linear canceller
        self._resampler = soxr.ResampleStream(far_rate, near_rate, 1, dtype='float32', quality='QQ')
        self._near_in = (ctypes.c_short * self._frame)()
        self._near_out = (ctypes.c_short * self._frame)()
        self._far_in = (ctypes.c_short * self._frame)()
        self._far_out = (ctypes.c_short * self._frame)()

        self._apm = webrtc_apm.WebRTCAudioProcessing()
        config = webrtc_apm.create_default_config()
        config.echo.enabled = True
        config.echo.mobile_mode = False
        config.high_pass.enabled = True
        config.noise_suppress.enabled = True
        config.noise_suppress.noise_level = webrtc_apm.NoiseSuppressionLevel.MODERATE
        if self._apm.apply_config(config) != 0:
            raise RuntimeError('WebRTC APM apply_config failed')
        self._cfg = self._apm.create_stream_config(near_rate, 1)
        self._apm.set_stream_delay_ms(delay_ms)

    def feed_far(self, outdata):
        """Output callback: just queue a mono copy (microseconds)."""
        mono = outdata.mean(axis=1, dtype=np.float32) if outdata.ndim > 1 else np.array(outdata, dtype=np.float32)
        self._far_pending.append(mono)
        while len(self._far_pending) > _FAR_PENDING_MAX:
            self._far_pending.popleft()

    def skip_far(self):
        """While cancellation is paused: drop the (silent) reference instead of piling it up."""
        self._far_pending.clear()
        self._linear_far = self._linear_far[:0]

    def process_near(self, block):
        """block: 16 kHz mono float32, a multiple of 10 ms. Returns the echo-cancelled block."""
        self._drain_far()
        self.linear = self._process_linear(block)
        i16 = _to_i16(block)
        out = np.empty(len(block), dtype=np.float32)
        self._apm.set_stream_delay_ms(self._delay_ms)
        for off in range(0, len(block), self._frame):
            ctypes.memmove(self._near_in, i16[off:off + self._frame].ctypes.data, self._frame * 2)
            if self._apm.process_stream(self._near_in, self._cfg, self._cfg, self._near_out) != 0:
                return block
            out[off:off + self._frame] = np.frombuffer(self._near_out, dtype=np.int16) / 32768.0
        return out

    def _process_linear(self, block):
        far = self._linear_far
        ahead = int(_LINEAR_AHEAD_MAX * self._rate)
        if len(far) > ahead + len(block):
            far = far[len(far) - ahead - len(block):]
        if len(far) >= len(block):
            ref, self._linear_far = far[:len(block)], far[len(block):]
        else:
            ref, self._linear_far = np.concatenate((far, np.zeros(len(block) - len(far), np.float32))), far[:0]
        out = np.empty_like(block)
        for off in range(0, len(block) - 319, 320):
            out[off:off + 320] = self._linear.process(block[off:off + 320], ref[off:off + 320])
        return out

    def _drain_far(self):
        while self._far_pending:
            res = self._resampler.resample_chunk(self._far_pending.popleft(), last=False)
            if len(res):
                self._far_buffer = np.concatenate((self._far_buffer, res))
                self._linear_far = np.concatenate((self._linear_far, res))
        usable = len(self._far_buffer) // self._frame * self._frame
        if not usable:
            return
        i16 = _to_i16(self._far_buffer[:usable])
        self._far_buffer = self._far_buffer[usable:]
        for off in range(0, usable, self._frame):
            ctypes.memmove(self._far_in, i16[off:off + self._frame].ctypes.data, self._frame * 2)
            self._apm.process_reverse_stream(self._far_in, self._cfg, self._cfg, self._far_out)


def _to_i16(x):
    return np.clip(x * 32768.0, -32768, 32767).astype(np.int16)
