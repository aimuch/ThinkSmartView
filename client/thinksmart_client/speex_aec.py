"""Linear-only echo cancellation (speexdsp's MDF adaptive filter, no residual suppression), for the
wake word while a reply plays.

WebRTC's canceller (aec.py) removes the reply's echo almost completely, but while the speaker is
playing its suppressor also takes a large part of the user's voice: in eval/wake/echo.py a wake word
at -30 dBFS was missed 29% of the time during a reply (11% in a quiet room), and that stayed so even
with 20 dB less echo. Without any canceller the same wake word with 20 dB less echo was missed 14%.
A linear filter only subtracts its estimate of the echo, so the voice stays as it was.

libspeexdsp1 is already on the system (PipeWire/PulseAudio depend on it): ctypes, no extra package.
"""
import ctypes
import ctypes.util

import numpy as np

def _load():
    # the device: libspeexdsp.so.1; a Mac (eval/wake/echo.py): Homebrew's, which find_library
    # doesn't see from uv's Python
    for name in (ctypes.util.find_library('speexdsp'), 'libspeexdsp.so.1', '/opt/homebrew/lib/libspeexdsp.dylib'):
        if name:
            try:
                return ctypes.CDLL(name)
            except OSError:
                pass
    raise OSError('libspeexdsp not found')


_lib = _load()
_lib.speex_echo_state_init.restype = ctypes.c_void_p
_lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
_lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
_lib.speex_echo_cancellation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
_lib.speex_echo_state_reset.argtypes = [ctypes.c_void_p]
SPEEX_ECHO_SET_SAMPLING_RATE = 24


class LinearAec:
    """frame: samples per call (both signals 16 kHz mono float32); tail_ms: how long an echo
    (output latency + room) the filter covers."""

    def __init__(self, rate=16000, frame=320, tail_ms=500):
        self.frame = frame
        self._st = _lib.speex_echo_state_init(frame, int(rate * tail_ms / 1000))
        r = ctypes.c_int(rate)
        _lib.speex_echo_ctl(self._st, SPEEX_ECHO_SET_SAMPLING_RATE, ctypes.byref(r))
        self._out = np.zeros(frame, np.int16)

    def process(self, near, far):
        """near: the mic block, far: what was sent to the speaker over the same 20 ms."""
        n = _i16(near)
        f = _i16(far)
        _lib.speex_echo_cancellation(self._st, n.ctypes.data, f.ctypes.data, self._out.ctypes.data)
        return self._out.astype(np.float32) / 32768

    def reset(self):
        _lib.speex_echo_state_reset(self._st)

    def __del__(self):
        if getattr(self, '_st', None):
            _lib.speex_echo_state_destroy(self._st)
            self._st = None


def _i16(x):
    return np.ascontiguousarray(np.clip(x * 32768, -32768, 32767).astype(np.int16))
