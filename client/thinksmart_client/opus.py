"""Minimal Opus encoder/decoder over the system libopus (ctypes, no extra package)."""
import ctypes
import ctypes.util

import numpy as np

_lib = ctypes.CDLL(ctypes.util.find_library('opus') or 'libopus.so.0')
_lib.opus_encoder_create.restype = ctypes.c_void_p
_lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_lib.opus_encode.restype = ctypes.c_int32
_lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int32]
_lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
_lib.opus_decoder_create.restype = ctypes.c_void_p
_lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
_lib.opus_decode.restype = ctypes.c_int
_lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]

APPLICATION_VOIP = 2048


class Encoder:
    def __init__(self, rate=16000, channels=1):
        err = ctypes.c_int()
        self._st = _lib.opus_encoder_create(rate, channels, APPLICATION_VOIP, ctypes.byref(err))
        if err.value != 0:
            raise RuntimeError(f'opus_encoder_create: {err.value}')
        self._buf = ctypes.create_string_buffer(4000)

    def encode(self, pcm16):
        """pcm16: int16 array of exactly one frame (e.g. 320 samples = 20 ms at 16 kHz)."""
        pcm16 = np.ascontiguousarray(pcm16, dtype=np.int16)
        n = _lib.opus_encode(self._st, pcm16.ctypes.data, len(pcm16), self._buf, len(self._buf))
        if n < 0:
            raise RuntimeError(f'opus_encode: {n}')
        return self._buf.raw[:n]

    def __del__(self):
        if getattr(self, '_st', None):
            _lib.opus_encoder_destroy(self._st)


class Decoder:
    def __init__(self, rate=24000, channels=1):
        err = ctypes.c_int()
        self._st = _lib.opus_decoder_create(rate, channels, ctypes.byref(err))
        if err.value != 0:
            raise RuntimeError(f'opus_decoder_create: {err.value}')
        self._out = np.zeros(rate * 120 // 1000 * channels, dtype=np.int16)  # 120 ms is Opus's longest frame

    def decode(self, packet):
        """Returns float32 samples in [-1, 1]."""
        n = _lib.opus_decode(self._st, packet, len(packet), self._out.ctypes.data, len(self._out), 0)
        if n < 0:
            raise RuntimeError(f'opus_decode: {n}')
        return self._out[:n].astype(np.float32) / 32768.0

    def __del__(self):
        if getattr(self, '_st', None):
            _lib.opus_decoder_destroy(self._st)


def read_ogg(path):
    """An Ogg Opus file (the alarm tones, all mono) as float32 samples at 48 kHz. Uses Decoder as it is
    (the replies' decoder is not changed for this).
    Just enough of Ogg: pages → packets; the first two packets are OpusHead and OpusTags."""
    data = open(path, 'rb').read()
    packets, partial, pos = [], b'', 0
    while pos < len(data):
        if data[pos:pos + 4] != b'OggS':
            raise ValueError(f'{path}: not an Ogg page at byte {pos}')
        nseg = data[pos + 26]
        lacing = data[pos + 27:pos + 27 + nseg]
        pos += 27 + nseg
        for n in lacing:
            partial += data[pos:pos + n]
            pos += n
            if n < 255:   # a packet ends with a segment shorter than 255
                packets.append(partial)
                partial = b''
    if len(packets) < 3 or not packets[0].startswith(b'OpusHead'):
        raise ValueError(f'{path}: not Ogg Opus')
    channels, pre_skip = packets[0][9], int.from_bytes(packets[0][10:12], 'little')
    if channels != 1:
        raise ValueError(f'{path}: {channels} channels, the tones are mono')
    dec = Decoder(48000, 1)
    return np.concatenate([dec.decode(p) for p in packets[2:]])[pre_skip:]
